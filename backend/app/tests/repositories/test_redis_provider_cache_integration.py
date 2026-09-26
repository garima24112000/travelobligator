from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import pytest
import redis

from app.core.config import get_settings
from app.models.common import DataStatus, ProviderStatus
from app.providers.currency import frankfurter_adapter
from app.providers.currency.frankfurter_adapter import FrankfurterCurrencyAdapter
from app.storage.provider_cache_store import get_provider_cache_store, make_query_hash
from app.storage.redis_provider_cache_store import (
    CACHE_NAMESPACE_VERSION,
    RedisProviderCacheStore,
    cache_stats_snapshot,
    close_redis_provider_cache_store,
    get_redis_provider_cache_store,
    reset_cache_stats,
)

# Section 200B: the Redis provider-response cache against a REAL, disposable Redis.
# Gated: never part of the hermetic default run. Enable with
#   TRAVELOB_RUN_REDIS_TESTS=1 REDIS_URL=redis://127.0.0.1:<port>/0 pytest -m redis_integration
# Every test uses a unique key prefix and removes only its own keys.

_URL = os.environ.get("REDIS_URL", "")
pytestmark = [
    pytest.mark.redis_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_REDIS_TESTS") != "1" or not _URL,
        reason="real-Redis tests need TRAVELOB_RUN_REDIS_TESTS=1 and REDIS_URL",
    ),
]

H = make_query_hash({"lat": 38.7, "lon": -9.1})


@pytest.fixture()
def prefix(monkeypatch: pytest.MonkeyPatch):
    p = f"tobl-test-{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("REDIS_KEY_PREFIX", p)
    get_settings.cache_clear()
    close_redis_provider_cache_store()
    reset_cache_stats()
    yield p
    raw = redis.Redis.from_url(_URL)
    for key in raw.scan_iter(f"{p}:*"):
        raw.delete(key)
    raw.close()
    close_redis_provider_cache_store()


@pytest.fixture()
def raw(prefix: str):
    client = redis.Redis.from_url(_URL)
    yield client
    client.close()


def test_server_is_reachable_and_store_uses_the_real_backend(prefix: str) -> None:
    store = get_provider_cache_store(None)  # type: ignore[arg-type]  # path is ignored in redis mode
    assert isinstance(store, RedisProviderCacheStore) and store.health() == "ok"


def test_miss_then_set_then_hit_round_trips_the_payload(prefix: str, raw) -> None:
    store = get_redis_provider_cache_store()
    assert store.get("open_meteo", H) is None
    store.set("open_meteo", H, {"temp": [1, 2, 3]}, ttl_seconds=60, metadata={"k": "v"})
    entry = store.get("open_meteo", H)
    assert entry is not None and entry.payload == {"temp": [1, 2, 3]} and entry.metadata == {"k": "v"}
    key = store.make_key("open_meteo", H)
    assert key.startswith(f"{prefix}:provider-cache:{CACHE_NAMESPACE_VERSION}:open_meteo:")
    envelope = json.loads(raw.get(key))
    assert envelope["payload"] == {"temp": [1, 2, 3]}
    stats = cache_stats_snapshot()
    assert stats["open_meteo:MISS"] == 1 and stats["open_meteo:HIT"] == 1


def test_a_different_request_or_provider_never_hits(prefix: str) -> None:
    store = get_redis_provider_cache_store()
    store.set("open_meteo", H, {"a": 1}, ttl_seconds=60)
    assert store.get("open_meteo", make_query_hash({"lat": 0, "lon": 0})) is None
    assert store.get("frankfurter", H) is None  # same digest, other provider


def test_namespace_prefix_isolates_environments(prefix: str, monkeypatch: pytest.MonkeyPatch) -> None:
    store = get_redis_provider_cache_store()
    store.set("open_meteo", H, {"a": 1}, ttl_seconds=60)
    monkeypatch.setenv("REDIS_KEY_PREFIX", prefix + "-other")
    get_settings.cache_clear()
    other = get_redis_provider_cache_store()
    assert other is not store and other.get("open_meteo", H) is None


def test_real_redis_ttl_expires_the_entry(prefix: str, raw) -> None:
    store = get_redis_provider_cache_store()
    store.set("open_meteo", H, {"a": 1}, ttl_seconds=1)
    assert 0 < raw.ttl(store.make_key("open_meteo", H)) <= 1
    time.sleep(1.3)
    assert store.get("open_meteo", H) is None  # expired by Redis, not by app logic


def test_default_ttl_is_bounded_never_immortal(prefix: str, raw) -> None:
    store = get_redis_provider_cache_store()
    store.set("open_meteo", H, {"a": 1}, ttl_seconds=None)
    assert 0 < raw.ttl(store.make_key("open_meteo", H)) <= 24 * 3600


def test_a_corrupted_entry_is_a_miss_and_is_replaced(prefix: str, raw) -> None:
    store = get_redis_provider_cache_store()
    key = store.make_key("open_meteo", H)
    raw.set(key, b"{not json")
    assert store.get("open_meteo", H) is None
    assert raw.get(key) is None  # corrupt value removed
    store.set("open_meteo", H, {"ok": True}, ttl_seconds=60)
    assert store.get("open_meteo", H).payload == {"ok": True}


def test_a_mismatched_envelope_is_a_miss(prefix: str, raw) -> None:
    store = get_redis_provider_cache_store()
    store.set("open_meteo", H, {"ok": True}, ttl_seconds=60)
    key = store.make_key("open_meteo", H)
    envelope = json.loads(raw.get(key))
    envelope["source"] = "frankfurter"
    raw.set(key, json.dumps(envelope))
    assert store.get("open_meteo", H) is None


def test_a_hit_survives_a_process_restart(prefix: str) -> None:
    first = get_redis_provider_cache_store()
    first.set("open_meteo", H, {"a": 1}, ttl_seconds=60)
    close_redis_provider_cache_store()  # simulates process exit: the client is gone, Redis remains
    get_settings.cache_clear()
    second = get_redis_provider_cache_store()
    assert second is not first and second.get("open_meteo", H).payload == {"a": 1}


def test_one_process_level_client_is_reused(prefix: str) -> None:
    assert get_redis_provider_cache_store() is get_redis_provider_cache_store()
    assert get_provider_cache_store(None) is get_redis_provider_cache_store()  # type: ignore[arg-type]


def test_delete_removes_the_entry(prefix: str) -> None:
    store = get_redis_provider_cache_store()
    store.set("open_meteo", H, {"a": 1}, ttl_seconds=60)
    store.delete("open_meteo", H)
    assert store.get("open_meteo", H) is None


def test_get_and_set_failures_degrade_without_raising(prefix: str, monkeypatch: pytest.MonkeyPatch) -> None:
    store = get_redis_provider_cache_store()
    store.set("open_meteo", H, {"a": 1}, ttl_seconds=60)

    def boom(*a: Any, **k: Any):
        raise redis.exceptions.ConnectionError("down")

    monkeypatch.setattr(store._client, "get", boom)  # noqa: SLF001
    monkeypatch.setattr(store._client, "set", boom)  # noqa: SLF001
    assert store.get("open_meteo", H) is None  # read failure -> uncached provider call
    store.set("open_meteo", H, {"b": 2}, ttl_seconds=60)  # write failure -> no exception
    stats = cache_stats_snapshot()
    assert stats.get("open_meteo:ERROR", 0) + stats.get("open_meteo:BYPASS", 0) >= 1


def test_an_unreachable_server_bypasses_quickly_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()
    close_redis_provider_cache_store()
    reset_cache_stats()
    store = get_redis_provider_cache_store()
    started = time.monotonic()
    for _ in range(5):
        assert store.get("open_meteo", H) is None
        store.set("open_meteo", H, {"a": 1}, ttl_seconds=60)
    assert time.monotonic() - started < 3.0  # cooldown avoids one timeout per call
    close_redis_provider_cache_store()


def test_a_real_adapter_hits_redis_on_the_second_call(prefix: str, monkeypatch: pytest.MonkeyPatch) -> None:
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
    adapter = FrankfurterCurrencyAdapter()
    first = adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    second = adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    assert len(calls) == 1
    assert first.status == second.status == ProviderStatus.SUCCESS
    assert (first.data_status, second.data_status) == (DataStatus.LIVE, DataStatus.CACHED)
    assert first.data.exchange_rate == second.data.exchange_rate
