from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.metrics import registry
from app.core.persistence import PersistenceUnavailableError
from app.core.readiness import reset_readiness_engines
from app.main import app
from app.providers.currency import frankfurter_adapter
from app.providers.currency.frankfurter_adapter import FrankfurterCurrencyAdapter
from app.storage.provider_cache_store import get_provider_cache_store
from app.storage.redis_provider_cache_store import close_redis_provider_cache_store

# Section 200E.1: Redis is OPTIONAL. It must not be required to START, whether it fails after startup or is already
# unavailable before it. PostgreSQL, by contrast, is authoritative.


class _ControllableRedis:
    """A redis-py stand-in whose availability can be flipped without restarting anything."""

    up = False
    data: dict[str, bytes] = {}

    def _check(self) -> None:
        if not type(self).up:
            raise redis.exceptions.ConnectionError("unavailable")

    def ping(self) -> bool:
        self._check()
        return True

    def get(self, key: str):
        self._check()
        return type(self).data.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self._check()
        type(self).data[key] = value.encode()
        return True

    def delete(self, key: str):
        self._check()
        return int(type(self).data.pop(key, None) is not None)

    def close(self) -> None: ...


@pytest.fixture()
def redis_down_at_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _ControllableRedis.up = False
    _ControllableRedis.data = {}
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url, **kw: _ControllableRedis()))
    monkeypatch.setenv("PROVIDER_CACHE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("PROVIDER_CACHE_PATH", str(tmp_path / "must_not_exist.sqlite3"))
    get_settings.cache_clear()
    close_redis_provider_cache_store()
    reset_readiness_engines()
    yield tmp_path
    close_redis_provider_cache_store()


def _fake_provider(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    class _Resp:
        def raise_for_status(self) -> None: ...
        def json(self) -> Any:
            return {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}

    class _Client:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            calls.append(1)
            return _Resp()

    monkeypatch.setattr(frankfurter_adapter.httpx, "Client", lambda **kw: _Client())
    monkeypatch.setattr(frankfurter_adapter, "get_provider_cache_store", get_provider_cache_store)
    return calls


def _cache(status: str) -> float:
    return registry.value("travelobligator_provider_cache_total", {"provider": "frankfurter", "status": status})


def test_the_application_starts_degraded_when_redis_is_already_down_and_never_falls_back(
    redis_down_at_startup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_provider(monkeypatch)
    with TestClient(app) as client:  # the lifespan (startup checks) MUST complete with Redis down
        assert client.get("/health").status_code == 200
        ready = client.get("/ready")
        body = ready.json()["data"]
        assert ready.status_code == 200 and body["status"] == "degraded"
        assert body["checks"]["provider_cache"] == {"status": "degraded", "backend": "redis"}
        assert body["checks"]["persistence"]["status"] == "ready"  # the authoritative store is unaffected

        # provider-backed work still runs, uncached and correct
        adapter = FrankfurterCurrencyAdapter()
        first = adapter.get_exchange_rate("USD", "Lisbon, Portugal")
        second = adapter.get_exchange_rate("USD", "Lisbon, Portugal")
        assert first.status.value == second.status.value == "success"
        assert first.data_status.value == second.data_status.value == "live"  # nothing pretends to be cached
        assert len(calls) == 2  # every call reached the provider

    # no hidden fallback: no SQLite cache file, and the backend selector is still redis
    assert not (redis_down_at_startup / "must_not_exist.sqlite3").exists()
    assert list(redis_down_at_startup.iterdir()) == []
    assert get_settings().provider_cache_backend == "redis"


def test_redis_recovery_needs_no_restart_and_caching_resumes_miss_then_hit(
    redis_down_at_startup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_provider(monkeypatch)
    with TestClient(app) as client:
        assert client.get("/ready").json()["data"]["status"] == "degraded"
        adapter = FrankfurterCurrencyAdapter()
        adapter.get_exchange_rate("USD", "Lisbon, Portugal")  # while down: uncached

        _ControllableRedis.up = True  # Redis becomes available; the SAME app/store objects keep running
        state = client.get("/ready").json()["data"]
        assert state["status"] == "ready" and state["checks"]["provider_cache"]["status"] in ("recovering", "healthy")

        miss0, hit0 = _cache("MISS"), _cache("HIT")
        calls_before = len(calls)
        first = adapter.get_exchange_rate("USD", "Lisbon, Portugal")  # first after recovery: MISS -> provider
        second = adapter.get_exchange_rate("USD", "Lisbon, Portugal")  # second: HIT
        assert _cache("MISS") - miss0 == 1 and _cache("HIT") - hit0 == 1
        assert len(calls) - calls_before == 1  # the provider was called once
        assert (first.data_status.value, second.data_status.value) == ("live", "cached")


def test_postgres_is_not_treated_like_redis_an_unreachable_database_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:pw@127.0.0.1:1/db")
    monkeypatch.setenv("PROVIDER_CACHE_BACKEND", "sqlite")  # a healthy cache cannot compensate
    get_settings.cache_clear()
    with pytest.raises(PersistenceUnavailableError):
        with TestClient(app):
            pass


def test_the_startup_cache_check_only_validates_config_it_never_requires_a_reachable_redis(
    redis_down_at_startup: Path,
) -> None:
    from app.core.provider_cache import startup_provider_cache_check

    assert startup_provider_cache_check(get_settings()) == {"backend": "redis", "state": "degraded"}  # returns, not raises
    monkeypatch_malformed = pytest.MonkeyPatch()
    try:
        monkeypatch_malformed.setenv("REDIS_URL", "not-a-url")
        get_settings.cache_clear()
        from app.core.provider_cache import RedisConfigurationError

        with pytest.raises(RedisConfigurationError):  # a MALFORMED url is an operator error, unlike an unreachable server
            startup_provider_cache_check(get_settings())
    finally:
        monkeypatch_malformed.undo()
