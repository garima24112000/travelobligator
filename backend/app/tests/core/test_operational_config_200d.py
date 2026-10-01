from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.operational_config import (
    OperationalConfigurationError,
    heartbeat_interval_seconds,
    startup_config_summary,
    validate_runtime_configuration,
)
from app.main import app

# Section 200D: configuration contradictions fail explicitly; startup logs a safe summary.

_DB_SECRET = "SENTINEL_CFG_DB_PASSWORD_4471"
_REDIS_SECRET = "SENTINEL_CFG_REDIS_PASSWORD_9982"
_KEY_SECRET = "SENTINEL_CFG_API_KEY_5530"


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)


def test_defaults_are_consistent() -> None:
    validate_runtime_configuration(_settings(DATABASE_URL="postgresql://u:p@h/d"))


def test_postgres_rejects_a_job_max_above_the_database_enforced_one() -> None:
    with pytest.raises(OperationalConfigurationError) as excinfo:
        validate_runtime_configuration(
            _settings(PERSISTENCE_BACKEND="postgres", DATABASE_URL="postgresql://u:p@h/d", GENERATION_JOB_MAX_RUNNING_PER_TRIP=2)
        )
    message = str(excinfo.value)
    assert "one active job per trip" in message and "postgresql" not in message.lower().replace("persistence_backend=postgres", "")


def test_local_json_still_honours_a_higher_job_max() -> None:
    validate_runtime_configuration(_settings(PERSISTENCE_BACKEND="local_json", GENERATION_JOB_MAX_RUNNING_PER_TRIP=3))


def test_lease_must_be_renewable_and_shorter_than_the_stale_threshold() -> None:
    from pydantic import ValidationError

    for lease in (0, -5, 1, 2):
        with pytest.raises(ValidationError):
            _settings(GENERATION_JOB_LEASE_SECONDS=lease)
    with pytest.raises(OperationalConfigurationError):
        validate_runtime_configuration(
            _settings(GENERATION_JOB_LEASE_SECONDS=600, GENERATION_JOB_STALE_AFTER_SECONDS=300, PERSISTENCE_BACKEND="local_json")
        )


@pytest.mark.parametrize("lease", [3, 4, 30, 120, 900])
def test_the_heartbeat_interval_is_always_shorter_than_the_lease(lease: int) -> None:
    assert 1.0 <= heartbeat_interval_seconds(lease) < lease
    validate_runtime_configuration(_settings(GENERATION_JOB_LEASE_SECONDS=lease, PERSISTENCE_BACKEND="local_json"))


def test_the_readiness_timeout_must_cover_the_redis_probe() -> None:
    with pytest.raises(OperationalConfigurationError):
        validate_runtime_configuration(
            _settings(PERSISTENCE_BACKEND="local_json", PROVIDER_CACHE_BACKEND="redis", READINESS_TIMEOUT_SECONDS=1, REDIS_SOCKET_TIMEOUT_SECONDS=1)
        )
    validate_runtime_configuration(
        _settings(PERSISTENCE_BACKEND="local_json", PROVIDER_CACHE_BACKEND="redis", READINESS_TIMEOUT_SECONDS=2, REDIS_SOCKET_TIMEOUT_SECONDS=1)
    )
    validate_runtime_configuration(  # a disabled/sqlite cache has no Redis probe to wait for
        _settings(PERSISTENCE_BACKEND="local_json", PROVIDER_CACHE_BACKEND="sqlite", READINESS_TIMEOUT_SECONDS=1, REDIS_SOCKET_TIMEOUT_SECONDS=1)
    )


@pytest.mark.parametrize("kw", [{"READINESS_TIMEOUT_SECONDS": 0}, {"READINESS_TIMEOUT_SECONDS": 99}, {"DB_CONNECT_TIMEOUT_SECONDS": 0}, {"DB_CONNECT_TIMEOUT_SECONDS": 999}])
def test_operational_timeouts_are_bounded(kw: dict[str, int]) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(**kw)


def test_startup_summary_is_a_fixed_set_of_safe_scalars() -> None:
    summary = startup_config_summary(
        _settings(PERSISTENCE_BACKEND="postgres", DATABASE_URL=f"postgresql://u:{_DB_SECRET}@h/d", REDIS_URL=f"redis://:{_REDIS_SECRET}@h/0"),
        "abc123def456",
    )
    assert set(summary) == {
        "persistence_backend", "provider_cache_backend", "geocoding_provider", "async_generation_enabled", "metrics_enabled", "migration_head", "lease_seconds",
        "db_pool_size", "db_max_overflow", "db_max_connections", "db_statement_timeout_ms", "db_lock_timeout_ms",
    }
    text = repr(summary)
    assert _DB_SECRET not in text and _REDIS_SECRET not in text and "postgresql://" not in text
    assert summary["persistence_backend"] == "postgres" and summary["migration_head"] == "abc123def456"


def _capture(name: str = "app.lifecycle") -> tuple[list[logging.LogRecord], logging.Handler, logging.Logger]:
    seen: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    handler = Collect(level=logging.DEBUG)
    logger = logging.getLogger(name)
    logger.addHandler(handler)
    return seen, handler, logger


def test_the_running_app_logs_one_safe_startup_and_one_shutdown_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:{_DB_SECRET}@h/d")  # present but unused (local_json)
    monkeypatch.setenv("REDIS_URL", f"redis://:{_REDIS_SECRET}@h/0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", _KEY_SECRET)
    get_settings.cache_clear()
    seen, handler, logger = _capture()
    try:
        with TestClient(app) as client:  # runs the lifespan: startup ... shutdown
            assert client.get("/health").status_code == 200
    finally:
        logger.removeHandler(handler)

    events = [r for r in seen if getattr(r, "event", None) in ("app.startup", "app.shutdown")]
    assert [r.event for r in events] == ["app.startup", "app.shutdown"]
    startup = events[0]
    assert startup.persistence_backend == "local_json" and startup.provider_cache_backend == "sqlite"
    assert startup.async_generation_enabled is False and startup.metrics_enabled is True
    rendered = " ".join(str(v) for v in startup.__dict__.values())
    for secret in (_DB_SECRET, _REDIS_SECRET, _KEY_SECRET, "postgresql://", "redis://"):
        assert secret not in rendered, secret


def test_a_contradictory_configuration_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSISTENCE_BACKEND", "local_json")
    monkeypatch.setenv("GENERATION_JOB_LEASE_SECONDS", "600")
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "60")
    get_settings.cache_clear()
    with pytest.raises(OperationalConfigurationError):
        with TestClient(app):
            pass


def test_shutdown_stops_heartbeats_and_closes_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import generation_job_service as svc

    calls: list[str] = []
    monkeypatch.setattr(svc, "stop_all_heartbeats", lambda timeout=1.0: calls.append("heartbeats") or 0)
    import app.main as main_module

    monkeypatch.setattr("app.core.provider_cache.shutdown_provider_cache", lambda: calls.append("cache"))
    with TestClient(main_module.app):
        pass
    assert calls == ["heartbeats", "cache"]
