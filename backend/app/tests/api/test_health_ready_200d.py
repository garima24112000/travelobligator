from __future__ import annotations

import socket
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import readiness as readiness_module
from app.core.config import get_settings
from app.core.readiness import reset_readiness_engines
from app.main import app
from app.storage import redis_provider_cache_store as redis_store_module
from app.storage.redis_provider_cache_store import RedisProviderCacheStore

# Section 200D: /health = liveness only; /ready = authoritative readiness.

_PW = "SENTINEL_DB_PASSWORD_9f3a"
_REDIS_PW = "SENTINEL_REDIS_PASSWORD_77c1"


@pytest.fixture()
def http() -> TestClient:
    reset_readiness_engines()
    readiness_module._last_state.clear()
    yield TestClient(app, raise_server_exceptions=False)
    reset_readiness_engines()


def _postgres(monkeypatch: pytest.MonkeyPatch, url: str, **env: str) -> None:
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", url)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_readiness_engines()


# -- /health: liveness, no dependency call ------------------------------------------------------------------------


def test_health_is_tiny_stable_and_reveals_no_configuration(http: TestClient) -> None:
    body = http.get("/health").json()
    assert body["success"] is True and body["data"] == {"status": "ok", "service": "TravelObligator"}
    text = http.get("/health").text
    for needle in ("environment", "development", "use_real_providers", "database", "redis", "postgres", "127.0.0.1"):
        assert needle not in text.lower(), needle


def test_health_performs_no_dependency_call_even_when_every_dependency_is_broken(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*a: Any, **k: Any):
        raise AssertionError("/health must not touch any dependency")

    forbidden.cache_clear = lambda: None  # type: ignore[attr-defined]  # conftest teardown clears get_engine

    _postgres(monkeypatch, f"postgresql+psycopg://u:{_PW}@127.0.0.1:1/db")
    monkeypatch.setattr(readiness_module, "evaluate_readiness", forbidden)
    monkeypatch.setattr(readiness_module, "_probe_postgres", forbidden)
    monkeypatch.setattr(redis_store_module, "get_redis_provider_cache_store", forbidden)
    import app.db.session as session_module

    monkeypatch.setattr(session_module, "get_engine", forbidden)
    monkeypatch.setattr(session_module, "build_engine", forbidden)
    for _ in range(3):
        assert http.get("/health").status_code == 200


def test_health_source_makes_no_dependency_reference() -> None:
    import inspect

    from app.api.routes import ops

    source = inspect.getsource(ops.health_check)
    for needle in ("engine", "redis", "session", "provider", "readiness", "requests", "httpx"):
        assert needle not in source.lower(), needle


# -- /ready ----------------------------------------------------------------------------------------------------------


def test_ready_local_json_dev_backend_is_ready_and_says_schema_not_applicable(http: TestClient) -> None:
    response = http.get("/ready")
    body = response.json()
    assert response.status_code == 200 and body["data"]["status"] == "ready"
    assert body["data"]["checks"]["persistence"] == {"status": "ready", "backend": "local_json"}
    assert body["data"]["checks"]["schema"]["status"] == "not_applicable"
    assert body["data"]["checks"]["provider_cache"] == {"status": "local", "backend": "sqlite"}


def test_ready_response_never_calls_an_external_provider(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def forbidden(*a: Any, **k: Any):
        raise AssertionError("readiness must not call external providers")

    # the real network transports (the TestClient itself uses an in-process ASGI transport)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    from app.providers.gateway import provider_gateway

    monkeypatch.setattr(provider_gateway, "get_route", forbidden)
    assert http.get("/ready").status_code == 200


def test_ready_is_503_not_ready_when_postgres_is_unreachable_and_leaks_nothing(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _postgres(monkeypatch, f"postgresql+psycopg://appuser:{_PW}@127.0.0.1:1/secretdb")
    response = http.get("/ready")
    body = response.json()
    assert response.status_code == 503 and body["success"] is False and body["data"]["status"] == "not_ready"
    assert body["data"]["checks"]["persistence"]["status"] == "unavailable"
    assert body["data"]["checks"]["schema"]["status"] == "unknown"
    for needle in (_PW, "appuser", "secretdb", "127.0.0.1", "postgresql", "psycopg", "Traceback", "OperationalError"):
        assert needle not in response.text, needle
    # /health is unaffected
    assert http.get("/health").status_code == 200


def test_ready_missing_database_url_is_not_ready_without_a_traceback(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    response = http.get("/ready")
    assert response.status_code == 503 and "Traceback" not in response.text


def _fake_probe(heads: frozenset[str] | None):
    return lambda settings, ms: readiness_module._PostgresProbe(True, heads)


def test_ready_detects_a_schema_behind_or_ahead_and_exposes_only_revision_ids(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _postgres(monkeypatch, f"postgresql+psycopg://u:{_PW}@db.example:5432/app")
    expected = next(iter(readiness_module.alembic_head_revisions()))
    for heads in (frozenset({"deadbeef0001"}), frozenset(), None, frozenset({expected, "deadbeef0002"})):
        monkeypatch.setattr(readiness_module, "_probe_postgres", _fake_probe(heads))
        response = http.get("/ready")
        schema = response.json()["data"]["checks"]["schema"]
        assert response.status_code == 503 and schema["status"] == "mismatch", heads
        assert schema["expected_head"] == expected
        assert schema["current_head"] in (None, "deadbeef0001")
        assert _PW not in response.text and "db.example" not in response.text


def test_ready_ok_when_the_schema_is_at_the_expected_head(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _postgres(monkeypatch, f"postgresql+psycopg://u:{_PW}@db.example:5432/app")
    expected = readiness_module.alembic_head_revisions()
    monkeypatch.setattr(readiness_module, "_probe_postgres", _fake_probe(frozenset(expected)))
    monkeypatch.setenv("PROVIDER_CACHE_ENABLED", "false")
    get_settings.cache_clear()
    body = http.get("/ready").json()["data"]
    assert body["status"] == "ready" and body["checks"]["schema"]["status"] == "ok"
    assert body["checks"]["schema"]["current_head"] == body["checks"]["schema"]["expected_head"]
    assert set(body["checks"]["schema"]) == {"status", "expected_head", "current_head"}
    import re

    assert re.fullmatch(r"[0-9a-f]{12}", body["checks"]["schema"]["current_head"])  # the identifier only


class _FakeProbeStore:
    def __init__(self, state: str) -> None:
        self.state = state

    def probe(self) -> str:
        return self.state


def _redis_mode(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    monkeypatch.setenv("PROVIDER_CACHE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", f"redis://:{_REDIS_PW}@redis.example:6379/0")
    get_settings.cache_clear()
    monkeypatch.setattr(redis_store_module, "get_redis_provider_cache_store", lambda: _FakeProbeStore(state))


@pytest.mark.parametrize(
    "state,overall,http_status",
    [("healthy", "ready", 200), ("recovering", "ready", 200), ("degraded", "degraded", 200)],
)
def test_redis_state_maps_to_ready_or_degraded_never_not_ready(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, state: str, overall: str, http_status: int
) -> None:
    _redis_mode(monkeypatch, state)
    response = http.get("/ready")
    assert response.status_code == http_status and response.json()["data"]["status"] == overall
    assert response.json()["data"]["checks"]["provider_cache"] == {"status": state, "backend": "redis"}
    assert _REDIS_PW not in response.text and "redis.example" not in response.text


def test_a_disabled_cache_is_reported_as_disabled_not_failed(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_CACHE_ENABLED", "false")
    get_settings.cache_clear()
    body = http.get("/ready").json()["data"]
    assert body["status"] == "ready" and body["checks"]["provider_cache"] == {"status": "disabled", "backend": "none"}


def test_an_unexpected_probe_error_becomes_degraded_not_a_500(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _redis_mode(monkeypatch, "healthy")
    monkeypatch.setattr(redis_store_module, "get_redis_provider_cache_store", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    response = http.get("/ready")
    assert response.status_code == 200 and response.json()["data"]["status"] == "degraded"


# -- bounded time ----------------------------------------------------------------------------------------------------------


def test_ready_does_not_hang_on_a_blackholed_postgres(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A listener that accepts TCP but never speaks PostgreSQL: the connection would otherwise wait."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    port = server.getsockname()[1]
    _postgres(
        monkeypatch,
        f"postgresql+psycopg://u:{_PW}@127.0.0.1:{port}/db",
        READINESS_TIMEOUT_SECONDS="1",
        DB_CONNECT_TIMEOUT_SECONDS="1",
        PROVIDER_CACHE_ENABLED="false",
    )
    try:
        started = time.monotonic()
        response = http.get("/ready")
        elapsed = time.monotonic() - started
    finally:
        server.close()
    assert response.status_code == 503 and response.json()["data"]["checks"]["persistence"]["status"] == "unavailable"
    assert elapsed < 3.0, elapsed


def test_ready_does_not_hang_on_a_slow_redis(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _redis_mode(monkeypatch, "healthy")
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()
    release = threading.Event()

    class Slow:
        def probe(self) -> str:
            release.wait(5)
            return "healthy"

    monkeypatch.setattr(redis_store_module, "get_redis_provider_cache_store", lambda: Slow())
    started = time.monotonic()
    body = http.get("/ready").json()["data"]
    release.set()
    assert time.monotonic() - started < 2.5 and body["checks"]["provider_cache"]["status"] == "degraded"


# -- Redis recovery visible without a restart -----------------------------------------------------------------------------


class _FlakyRedis:
    def __init__(self) -> None:
        self.up = True

    def ping(self) -> bool:
        import redis

        if not self.up:
            raise redis.exceptions.ConnectionError("down")
        return True


def test_redis_outage_and_recovery_are_visible_through_probe_without_restart() -> None:
    now = [1000.0]
    client = _FlakyRedis()
    store = RedisProviderCacheStore(client, key_prefix="t", clock=lambda: now[0], cooldown_seconds=15)
    assert store.probe() == "healthy"
    client.up = False
    assert store.probe() == "degraded"
    client.up = True  # Redis is back: the SAME store object, no restart
    assert store.probe() == "recovering"  # back, but within the recovery window
    now[0] += 16
    assert store.probe() == "healthy"


# -- state-change logging ----------------------------------------------------------------------------------------------------


def test_readiness_logs_only_on_state_changes(http: TestClient) -> None:
    import logging

    seen: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    handler = Collect(level=logging.DEBUG)
    logger = logging.getLogger("app.readiness")
    logger.addHandler(handler)
    try:
        for _ in range(5):
            http.get("/ready")
    finally:
        logger.removeHandler(handler)
    assert len([r for r in seen if getattr(r, "event", "") == "readiness.changed"]) == 1  # not one per probe


def test_operational_events_carry_the_same_request_id_as_the_response(http: TestClient) -> None:
    """Correlation (Section 187C) still works for the new events: a log line emitted while /ready runs
    has the request id the client received in `X-Request-Id`."""
    import io
    import json
    import logging

    from app.core.logging_config import JsonFormatter, RequestIdLogFilter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdLogFilter())
    logger = logging.getLogger("app.readiness")
    logger.addHandler(handler)
    try:
        response = http.get("/ready", headers={"X-Request-Id": "req_ops_correlation_1"})
    finally:
        logger.removeHandler(handler)
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    changed = [l for l in lines if l.get("event") == "readiness.changed"]
    assert response.headers["X-Request-Id"] == "req_ops_correlation_1"
    assert changed and changed[0]["request_id"] == "req_ops_correlation_1"
