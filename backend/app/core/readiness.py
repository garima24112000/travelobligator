"""Readiness evaluation (Section 200D).

`GET /health`  = liveness only (no dependency is touched).
`GET /ready`   = "can this instance correctly serve authoritative requests?" -- built from THIS module.

Semantics
  * PostgreSQL unreachable, or its schema not at the code's Alembic head  -> NOT_READY (HTTP 503).
  * Redis (provider-response cache) unreachable                          -> DEGRADED (HTTP 200): the
    cache is an optimisation, requests are still served correctly, uncached.
  * Redis explicitly disabled / explicit local SQLite cache              -> reported as such (not failed).
  * External travel providers (Overpass, Nominatim, OSRM, Open-Meteo, Nager.Date, Frankfurter, Kiwi,
    AI providers) are NEVER called here; their failures are visible in metrics/logs and are handled
    by the app's own honest provider-failure semantics.

Bounded: every probe has its own driver timeout (`DB_CONNECT_TIMEOUT_SECONDS`, a per-probe
`statement_timeout`, the Redis socket timeout) AND the whole evaluation is capped by
`READINESS_TIMEOUT_SECONDS`, so a dead/blackholed dependency can never hang `/ready`. Probes use a
DEDICATED one-connection PostgreSQL engine (never the request pool), so a saturated application pool
cannot block the probe and a probe cannot starve requests. Nothing here restarts or terminates the
process; recovery is picked up automatically on the next call (the engine pre-pings, the Redis probe
clears its bypass cooldown).

Output is content-safe: fixed enums, backend names and Alembic revision ids only (schemas/readiness.py).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core import ops_events
from app.core.config import Settings, get_settings
from app.core.metrics import registry
from app.core.persistence import (
    PersistenceConfigurationError,
    alembic_head_revisions,
    require_database_url,
)
from app.schemas.readiness import (
    PersistenceCheck,
    ProviderCacheCheck,
    ReadinessChecks,
    ReadinessResponse,
    ReadinessStatus,
    SchemaCheck,
)

logger = logging.getLogger("app.readiness")

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="readiness")
_engines: dict[str, Engine] = {}
_engines_lock = threading.Lock()
_last_state: dict[str, str] = {}


@dataclass(frozen=True)
class _PostgresProbe:
    reachable: bool
    current_heads: frozenset[str] | None  # None when the alembic_version table is unreadable


def _probe_engine(settings: Settings) -> Engine:
    """One dedicated, tiny engine per configured URL (never the request pool)."""
    from app.db.session import normalize_database_url

    url = normalize_database_url(require_database_url(settings))
    with _engines_lock:
        engine = _engines.get(url)
        if engine is None:
            for old_url, old in list(_engines.items()):
                old.dispose()
                del _engines[old_url]
            engine = create_engine(
                url,
                pool_size=1,
                max_overflow=0,
                pool_timeout=1,
                pool_pre_ping=True,
                connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
            )
            _engines[url] = engine
        return engine


def reset_readiness_engines() -> None:
    """Dispose the probe engine(s) (shutdown / tests)."""
    with _engines_lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()


def _probe_postgres(settings: Settings, statement_timeout_ms: int) -> _PostgresProbe:
    engine = _probe_engine(settings)
    with engine.connect() as connection:
        # parameterised (SET does not accept binds; set_config does), transaction-local
        connection.execute(text("SELECT set_config('statement_timeout', :ms, true)"), {"ms": str(statement_timeout_ms)})
        connection.execute(text("SELECT 1"))
        try:
            rows = connection.execute(text("SELECT version_num FROM alembic_version")).all()
            return _PostgresProbe(True, frozenset(str(r[0]) for r in rows))
        except Exception:
            return _PostgresProbe(True, None)


def _within(future_fn, timeout: float):
    future = _executor.submit(future_fn)
    try:
        return future.result(timeout=max(timeout, 0.05))
    except FutureTimeout:
        future.cancel()
        raise


def _persistence_and_schema(settings: Settings, timeout: float) -> tuple[PersistenceCheck, SchemaCheck]:
    backend = settings.persistence_backend
    if backend != "postgres":
        # Explicit development backend: nothing external to check. Not production-grade.
        return PersistenceCheck(status="ready", backend=backend), SchemaCheck(status="not_applicable")
    try:
        expected = alembic_head_revisions()
    except Exception:
        expected = set()
    expected_head = next(iter(expected)) if len(expected) == 1 else None
    try:
        require_database_url(settings)
    except PersistenceConfigurationError:
        return (
            PersistenceCheck(status="unavailable", backend=backend),
            SchemaCheck(status="unknown", expected_head=expected_head),
        )
    try:
        probe = _within(
            lambda: _probe_postgres(settings, max(int(timeout * 1000) - 100, 100)), timeout
        )
    except Exception:  # unreachable, timed out, auth failure ... -> only the class of outcome is exposed
        return (
            PersistenceCheck(status="unavailable", backend=backend),
            SchemaCheck(status="unknown", expected_head=expected_head),
        )
    if probe.current_heads is None:
        return PersistenceCheck(status="ready", backend=backend), SchemaCheck(
            status="mismatch", expected_head=expected_head, current_head=None
        )
    current_head = next(iter(probe.current_heads)) if len(probe.current_heads) == 1 else None
    ok = bool(expected) and set(probe.current_heads) == expected
    return PersistenceCheck(status="ready", backend=backend), SchemaCheck(
        status="ok" if ok else "mismatch", expected_head=expected_head, current_head=current_head
    )


def _provider_cache(settings: Settings, timeout: float) -> ProviderCacheCheck:
    if not settings.provider_cache_enabled:
        return ProviderCacheCheck(status="disabled", backend="none")
    if settings.provider_cache_backend == "sqlite":
        return ProviderCacheCheck(status="local", backend="sqlite")
    try:
        from app.storage.redis_provider_cache_store import get_redis_provider_cache_store

        state = _within(lambda: get_redis_provider_cache_store().probe(), timeout)
    except Exception:
        state = "degraded"
    return ProviderCacheCheck(status=state, backend="redis")


def evaluate_readiness(settings: Settings | None = None) -> ReadinessResponse:
    resolved = settings or get_settings()
    deadline = time.monotonic() + resolved.readiness_timeout_seconds

    def remaining() -> float:
        return deadline - time.monotonic()

    persistence, schema = _persistence_and_schema(resolved, remaining())
    cache = _provider_cache(resolved, remaining())

    if persistence.status != "ready" or schema.status in ("mismatch", "unknown"):
        status = ReadinessStatus.NOT_READY
    elif cache.status == "degraded":
        status = ReadinessStatus.DEGRADED
    else:
        status = ReadinessStatus.READY

    response = ReadinessResponse(
        status=status,
        checks=ReadinessChecks(persistence=persistence, schema_=schema, provider_cache=cache),
    )
    _observe(response)
    return response


def _observe(response: ReadinessResponse) -> None:
    """Gauge per dependency + a log event ONLY when the state changes (probes run every few seconds;
    logging each one would be a flood)."""
    checks = response.checks
    registry.set("travelobligator_dependency_up", 1.0 if checks.persistence.status == "ready" else 0.0, {"dependency": "postgres"})
    registry.set(
        "travelobligator_dependency_up",
        {"ok": 1.0, "not_applicable": 1.0}.get(checks.schema_.status, 0.0),
        {"dependency": "schema"},
    )
    registry.set(
        "travelobligator_dependency_up",
        {"healthy": 1.0, "recovering": 1.0, "local": 1.0, "disabled": 1.0}.get(checks.provider_cache.status, 0.5),
        {"dependency": "redis"},
    )
    current = "|".join((response.status.value, checks.persistence.status, checks.schema_.status, checks.provider_cache.status))
    previous = _last_state.get("state")
    _last_state["state"] = current
    if previous == current:
        return
    if (
        checks.persistence.status == "ready"
        and checks.schema_.status in ("ok", "not_applicable")
        and previous is not None
        and previous.split("|")[1:3] != ["ready", checks.schema_.status]
    ):
        ops_events.log_event(
            logger, logging.INFO, ops_events.PERSISTENCE_READY, "PostgreSQL is reachable and at the expected schema head.",
            dependency="postgres", backend=checks.persistence.backend,
        )
    if checks.persistence.status != "ready":
        ops_events.log_event(
            logger, logging.ERROR, ops_events.PERSISTENCE_UNAVAILABLE, "PostgreSQL is not reachable.",
            dependency="postgres", check_status=checks.persistence.status, backend=checks.persistence.backend,
        )
    elif checks.schema_.status == "mismatch":
        ops_events.log_event(
            logger, logging.ERROR, ops_events.PERSISTENCE_SCHEMA_MISMATCH,
            "PostgreSQL schema is not at the expected migration head.",
            dependency="schema", check_status="mismatch", migration_head=checks.schema_.expected_head,
        )
    ops_events.log_event(
        logger,
        logging.INFO if response.status == ReadinessStatus.READY else logging.WARNING,
        ops_events.READINESS_CHANGED,
        "Readiness state changed.",
        check_status=response.status.value,
        cache_state=checks.provider_cache.status,
    )
