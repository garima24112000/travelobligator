from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pytest
import redis

from app.storage.provider_cache_store import ProviderCacheValueError, make_query_hash
from app.storage.redis_provider_cache_store import (
    CACHE_NAMESPACE_VERSION,
    CacheStatus,
    RedisProviderCacheStore,
    cache_stats_snapshot,
    reset_cache_stats,
)

# Section 200B: hermetic tests of the Redis provider-response cache with a fake
# client (no Redis needed). The same behaviours run against a REAL Redis in
# test_redis_provider_cache_integration.py.


class FakeRedis:
    """Minimal redis-py stand-in: bytes values, `ex` TTL on an injectable clock."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.data: dict[str, tuple[bytes, float | None]] = {}
        self.fail_get: Exception | None = None
        self.fail_set: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    def _live(self, key: str) -> bytes | None:
        item = self.data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and self.now >= expires:
            del self.data[key]
            return None
        return value

    def get(self, key: str):
        self.calls.append(("get", key))
        if self.fail_get:
            raise self.fail_get
        return self._live(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self.calls.append(("set", key))
        if self.fail_set:
            raise self.fail_set
        self.data[key] = (value.encode("utf-8"), self.now + ex if ex else None)
        return True

    def delete(self, key: str):
        self.calls.append(("delete", key))
        return int(self.data.pop(key, None) is not None)

    def ping(self):
        if self.fail_get:
            raise self.fail_get
        return True


@pytest.fixture()
def fake() -> FakeRedis:
    reset_cache_stats()
    return FakeRedis()


def _store(fake: FakeRedis, **kw) -> RedisProviderCacheStore:
    return RedisProviderCacheStore(fake, key_prefix="tobl", clock=lambda: fake.now, **kw)


H = make_query_hash({"lat": 1.0, "lon": 2.0})


# -- keys ---------------------------------------------------------------------------------------


def test_key_is_namespaced_versioned_provider_scoped_and_contains_only_a_digest(fake: FakeRedis) -> None:
    key = _store(fake).make_key("openstreetmap_poi", H)
    assert key == f"tobl:provider-cache:{CACHE_NAMESPACE_VERSION}:openstreetmap_poi:{H}"
    assert CACHE_NAMESPACE_VERSION == "v1"


def test_dictionary_ordering_does_not_change_the_key_and_different_requests_do() -> None:
    a = make_query_hash({"lat": 1.0, "lon": 2.0, "tags": ["a", "b"]})
    b = make_query_hash({"tags": ["a", "b"], "lon": 2.0, "lat": 1.0})
    assert a == b and len(a) == 64
    assert make_query_hash({"lat": 1.0, "lon": 2.0, "tags": ["b", "a"]}) != a
    assert make_query_hash({"lat": 1.0, "lon": 2.5, "tags": ["a", "b"]}) != a


def test_provider_and_operation_never_collide_for_the_same_request(fake: FakeRedis) -> None:
    store = _store(fake)
    keys = {store.make_key(s, H) for s in ("openstreetmap_geocode", "openstreetmap_poi", "osrm_route", "open_meteo")}
    assert len(keys) == 4
    store.set("open_meteo", H, {"v": 1}, ttl_seconds=60)
    assert store.get("osrm_route", H) is None  # same digest, other provider: isolated


def test_no_raw_request_text_or_secret_can_reach_a_key(fake: FakeRedis) -> None:
    store = _store(fake)
    with pytest.raises(ProviderCacheValueError):
        store.make_key("source with spaces", H)
    with pytest.raises(ProviderCacheValueError):
        store.make_key("osrm_route", "Lisbon, Portugal (my secret note)")  # raw text is not an opaque digest
    with pytest.raises(ProviderCacheValueError):
        store.make_key("osrm_route", "")


# -- hit / miss / ttl / envelope ------------------------------------------------------------------------


def test_miss_then_set_then_hit_with_the_exact_payload_and_a_ttl(fake: FakeRedis) -> None:
    store = _store(fake)
    assert store.get("open_meteo", H) is None
    payload = [{"date": "2026-08-10", "temperature_max_c": 24.0, "data_status": "live"}]
    store.set("open_meteo", H, payload, ttl_seconds=3600, metadata={"note": "m"})
    entry = store.get("open_meteo", H)
    assert entry is not None and entry.payload == payload and entry.metadata == {"note": "m"}
    key = store.make_key("open_meteo", H)
    assert fake.data[key][1] == fake.now + 3600  # Redis TTL is the expiry authority
    stats = cache_stats_snapshot()
    assert stats["open_meteo:MISS"] == 1 and stats["open_meteo:HIT"] == 1


def test_expiry_is_redis_ttl_after_which_the_entry_is_a_miss(fake: FakeRedis) -> None:
    store = _store(fake)
    store.set("osrm_route", H, {"d": 1}, ttl_seconds=60)
    fake.now += 59
    assert store.get("osrm_route", H) is not None
    fake.now += 2
    assert store.get("osrm_route", H) is None


def test_a_missing_ttl_is_capped_and_a_non_positive_ttl_is_not_stored(fake: FakeRedis) -> None:
    store = _store(fake)
    store.set("open_meteo", H, {"x": 1})
    assert fake.data[store.make_key("open_meteo", H)][1] == fake.now + 24 * 3600  # never immortal
    other = make_query_hash({"z": 1})
    store.set("open_meteo", other, {"x": 1}, ttl_seconds=0)
    assert store.make_key("open_meteo", other) not in fake.data
    assert cache_stats_snapshot()["open_meteo:BYPASS"] == 1


def test_envelope_carries_only_cache_metadata_and_the_payload(fake: FakeRedis) -> None:
    store = _store(fake)
    store.set("frankfurter", H, {"rate": 0.92}, ttl_seconds=10, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    envelope = json.loads(fake.data[store.make_key("frankfurter", H)][0])
    assert set(envelope) == {"schema", "source", "query_hash", "stored_at", "ttl_seconds", "payload", "metadata"}
    assert envelope["payload"] == {"rate": 0.92} and envelope["schema"] == 1


def test_non_json_payload_is_rejected_without_touching_redis(fake: FakeRedis) -> None:
    with pytest.raises(ProviderCacheValueError):
        _store(fake).set("open_meteo", H, {"bad": object()}, ttl_seconds=5)
    assert fake.calls == []


# -- corruption -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"\xff\xfe",
        b'["a list"]',
        b'{"schema": 2, "source": "open_meteo", "query_hash": "%s", "stored_at": "2026-01-01T00:00:00+00:00", "ttl_seconds": 5, "payload": 1, "metadata": {}}' % H.encode(),
        b'{"schema": 1, "source": "OTHER", "query_hash": "%s", "stored_at": "2026-01-01T00:00:00+00:00", "ttl_seconds": 5, "payload": 1, "metadata": {}}' % H.encode(),
        b'{"schema": 1, "source": "open_meteo", "query_hash": "%s", "stored_at": "garbage", "ttl_seconds": 5, "payload": 1, "metadata": {}}' % H.encode(),
        b'{"schema": 1, "source": "open_meteo", "query_hash": "%s", "stored_at": "2026-01-01T00:00:00+00:00", "ttl_seconds": 5, "metadata": {}}' % H.encode(),
    ],
)
def test_corrupt_or_mismatched_entries_are_a_miss_and_are_deleted(fake: FakeRedis, raw: bytes) -> None:
    store = _store(fake)
    key = store.make_key("open_meteo", H)
    fake.data[key] = (raw, None)

    assert store.get("open_meteo", H) is None  # never partially decoded data
    assert key not in fake.data  # dropped so the caller repopulates
    store.set("open_meteo", H, {"ok": True}, ttl_seconds=5)
    assert store.get("open_meteo", H).payload == {"ok": True}


# -- Redis failures degrade, never raise, never switch backend ------------------------------------------------


def test_get_error_is_reported_as_error_and_behaves_like_a_miss(fake: FakeRedis, caplog: pytest.LogCaptureFixture) -> None:
    fake.fail_get = redis.exceptions.ConnectionError("boom host=secret-host password=hunter2")
    store = _store(fake)
    with caplog.at_level(logging.DEBUG, logger="app.provider_cache"):
        assert store.get("open_meteo", H) is None
    assert cache_stats_snapshot()["open_meteo:ERROR"] == 1
    joined = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "hunter2" not in joined and "secret-host" not in joined and "boom" not in joined
    assert any(r.__dict__.get("cache_reason") == "ConnectionError" for r in caplog.records)


def test_after_a_connection_failure_redis_is_bypassed_for_a_cooldown_then_retried(fake: FakeRedis) -> None:
    fake.fail_get = redis.exceptions.ConnectionError("down")
    store = _store(fake, cooldown_seconds=15)
    assert store.get("open_meteo", H) is None
    calls_after_first = len(fake.calls)
    assert store.get("open_meteo", H) is None and store.set("open_meteo", H, {"x": 1}, ttl_seconds=5)
    assert len(fake.calls) == calls_after_first  # bypassed: no second timeout paid
    assert cache_stats_snapshot()["open_meteo:BYPASS"] >= 2
    fake.fail_get = None
    fake.now += 16
    assert store.get("open_meteo", H) is None  # cooldown over: Redis is asked again (a plain miss)
    assert len(fake.calls) == calls_after_first + 1


def test_set_error_still_returns_the_entry_and_never_raises(fake: FakeRedis) -> None:
    fake.fail_set = redis.exceptions.TimeoutError("slow")
    entry = _store(fake).set("frankfurter", H, {"rate": 0.9}, ttl_seconds=30)
    assert entry.payload == {"rate": 0.9}  # the provider response is not discarded
    assert cache_stats_snapshot()["frankfurter:ERROR"] == 1


def test_a_non_connection_redis_error_does_not_trigger_the_cooldown(fake: FakeRedis) -> None:
    fake.fail_get = redis.exceptions.ResponseError("WRONGTYPE")
    store = _store(fake)
    store.get("open_meteo", H)
    fake.fail_get = None
    assert store.get("open_meteo", H) is None
    assert cache_stats_snapshot().get("open_meteo:BYPASS", 0) == 0


def test_health_reports_ok_or_degraded_without_raising(fake: FakeRedis) -> None:
    store = _store(fake)
    assert store.health() == "ok"
    fake.fail_get = redis.exceptions.ConnectionError("x")
    assert store.health() == "degraded"


def test_status_vocabulary_is_exactly_hit_miss_bypass_error() -> None:
    assert {s.value for s in CacheStatus} == {"HIT", "MISS", "BYPASS", "ERROR"}


def test_oversized_values_are_not_stored(fake: FakeRedis) -> None:
    store = _store(fake)
    store.set("open_meteo", H, {"blob": "x" * 1_100_000}, ttl_seconds=5)
    assert fake.data == {}
    assert cache_stats_snapshot()["open_meteo:BYPASS"] == 1
