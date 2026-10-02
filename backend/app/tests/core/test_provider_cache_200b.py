from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.provider_cache import (
    provider_cache_status,
    startup_provider_cache_check,
    validate_provider_cache_configuration,
)
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import NormalizedPlace
from app.providers.currency import frankfurter_adapter
from app.providers.currency.frankfurter_adapter import FrankfurterCurrencyAdapter
from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter
from app.storage import provider_cache_store as store_module
from app.storage.provider_cache_store import ProviderCacheStore, get_provider_cache_store, make_query_hash
from app.storage.redis_provider_cache_store import (
    RedisConfigurationError,
    RedisProviderCacheStore,
    cache_stats_snapshot,
    close_redis_provider_cache_store,
    reset_cache_stats,
)

# Section 200B acceptance tests A-H, cached-result semantics, no-fabrication and startup policy.
# Hermetic: a fake Redis client, plus a REAL client pointed at a closed port for the "unreachable" cases.

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


_SECRET = "hunter2secret"
_DEAD = f"redis://:{_SECRET}@127.0.0.1:1/0"  # port 1: connection refused immediately


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)


# -- A / B / G: selector, explicit sqlite, hermetic tests -------------------------------------------------


def test_A_default_provider_cache_backend_is_redis_and_caching_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROVIDER_CACHE_BACKEND")
    settings = _settings()
    assert settings.provider_cache_backend == "redis" and settings.provider_cache_enabled is True
    assert Settings.model_fields["provider_cache_backend"].default == "redis"


def test_G_ordinary_tests_choose_the_hermetic_backend_explicitly() -> None:
    assert os.environ["PROVIDER_CACHE_BACKEND"] == "sqlite"
    assert "REDIS_URL" not in os.environ
    assert get_settings().provider_cache_backend == "sqlite"


def test_B_explicit_sqlite_still_works_and_never_touches_redis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_CACHE_BACKEND", "sqlite")
    get_settings.cache_clear()
    store = store_module.get_sqlite_provider_cache_store(tmp_path / "c.sqlite3")
    assert isinstance(store, ProviderCacheStore)
    store.set("open_meteo", make_query_hash({"a": 1}), {"x": 1}, ttl_seconds=60)
    assert store.get("open_meteo", make_query_hash({"a": 1})).payload == {"x": 1}
    assert (tmp_path / "c.sqlite3").exists()


@pytest.mark.parametrize("value", ["memcached", "", "disabled", "none", "postgres"])
def test_an_unknown_backend_is_a_configuration_error_not_a_silent_switch(value: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(PROVIDER_CACHE_BACKEND=value)
    assert "PROVIDER_CACHE_BACKEND must be" in str(excinfo.value)


def test_the_redis_default_never_creates_a_sqlite_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_CACHE_BACKEND", "redis")
    monkeypatch.setenv("PROVIDER_CACHE_PATH", str(tmp_path / "must_not_exist.sqlite3"))
    monkeypatch.setenv("REDIS_URL", _DEAD)
    get_settings.cache_clear()
    assert isinstance(get_provider_cache_store(Path(tmp_path / "x")), RedisProviderCacheStore)
    assert list(tmp_path.iterdir()) == []


# -- C / D: no exposure, malformed config -------------------------------------------------------------------------


def test_C_redis_url_is_never_in_settings_repr_status_or_health() -> None:
    settings = _settings(REDIS_URL=_DEAD)
    assert _SECRET not in repr(settings) and _SECRET not in str(settings)
    status = provider_cache_status(_settings(PROVIDER_CACHE_BACKEND="redis", REDIS_URL=_DEAD))
    assert set(status) == {"backend", "state"} and _SECRET not in str(status) and status["state"] == "degraded"


@pytest.mark.parametrize(
    "bad",
    [f"http://:{_SECRET}@host:6379/0", "redis://", f"redis://:{_SECRET}@host:notaport/0", "not a url", "", f"redis://:{_SECRET}@host/abc", "postgresql://u:p@h/db"],
)
def test_D_a_structurally_invalid_redis_url_fails_safely_without_echoing_it(bad: str) -> None:
    settings = _settings(PROVIDER_CACHE_BACKEND="redis", REDIS_URL=bad)
    with pytest.raises(RedisConfigurationError) as excinfo:
        validate_provider_cache_configuration(settings)
    with pytest.raises(RedisConfigurationError):
        startup_provider_cache_check(settings)
    assert _SECRET not in str(excinfo.value)
    assert "not shown" in str(excinfo.value)  # the message says the value is withheld


@pytest.mark.parametrize("good", ["redis://redis:6379/0", "rediss://:pw@cache.example:6380/2", "redis://localhost", "unix:///tmp/redis.sock"])
def test_valid_redis_urls_pass_validation(good: str) -> None:
    validate_provider_cache_configuration(_settings(PROVIDER_CACHE_BACKEND="redis", REDIS_URL=good))


def test_sqlite_or_disabled_caching_needs_no_valid_redis_url() -> None:
    validate_provider_cache_configuration(_settings(PROVIDER_CACHE_BACKEND="sqlite", REDIS_URL="garbage"))
    validate_provider_cache_configuration(_settings(PROVIDER_CACHE_BACKEND="redis", REDIS_URL="garbage", PROVIDER_CACHE_ENABLED="false"))
    assert provider_cache_status(_settings(PROVIDER_CACHE_ENABLED="false")) == {"backend": "none", "state": "disabled"}
    assert provider_cache_status(_settings(PROVIDER_CACHE_BACKEND="sqlite")) == {"backend": "sqlite", "state": "local"}


# -- E / F: unreachable Redis ----------------------------------------------------------------------------------------


@pytest.fixture()
def dead_redis(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROVIDER_CACHE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", _DEAD)
    get_settings.cache_clear()
    close_redis_provider_cache_store()
    reset_cache_stats()
    yield
    close_redis_provider_cache_store()


def test_E_an_unreachable_redis_does_not_select_sqlite_and_startup_still_succeeds(dead_redis: None, tmp_path: Path) -> None:
    store = get_provider_cache_store(tmp_path / "unused.sqlite3")
    assert isinstance(store, RedisProviderCacheStore)  # still Redis: no hidden SQLite fallback
    status = startup_provider_cache_check(get_settings())  # does not raise
    assert status == {"backend": "redis", "state": "degraded"}
    assert store.get("open_meteo", make_query_hash({"a": 1})) is None
    assert not (tmp_path / "unused.sqlite3").exists()
    assert list(tmp_path.iterdir()) == []


def _install_fake_http(monkeypatch: pytest.MonkeyPatch, calls: list[int]) -> None:
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


def test_F_a_provider_request_runs_uncached_when_redis_is_unreachable(dead_redis: None, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    _install_fake_http(monkeypatch, calls)
    monkeypatch.setattr(frankfurter_adapter, "get_provider_cache_store", get_provider_cache_store)
    adapter = FrankfurterCurrencyAdapter()

    first = adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    second = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert first.status == ProviderStatus.SUCCESS and second.status == ProviderStatus.SUCCESS
    assert first.data_status == DataStatus.LIVE and second.data_status == DataStatus.LIVE  # nothing pretends to be cached
    assert len(calls) == 2  # every call reached the provider; no fabrication, no failure
    stats = cache_stats_snapshot()
    assert stats.get("frankfurter:ERROR", 0) + stats.get("frankfurter:BYPASS", 0) >= 2


# -- cached-result semantics and no fabrication (fake Redis + REAL adapters) -----------------------------------------------


@pytest.fixture()
def redis_backed_adapter_env(monkeypatch: pytest.MonkeyPatch):
    fake = FakeRedis()
    reset_cache_stats()
    store = RedisProviderCacheStore(fake, key_prefix="tobl", clock=lambda: fake.now)
    yield fake, store
    reset_cache_stats()


def test_repeated_provider_call_is_a_hit_that_never_touches_the_provider_and_is_marked_cached(
    redis_backed_adapter_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, store = redis_backed_adapter_env
    calls: list[int] = []
    _install_fake_http(monkeypatch, calls)
    adapter = FrankfurterCurrencyAdapter(cache_store=store)

    live = adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    hit = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert len(calls) == 1
    assert live.data_status == DataStatus.LIVE
    assert hit.data_status == DataStatus.CACHED  # a hit is never re-labelled "live"/verified
    assert hit.data is not None and live.data is not None and hit.data.exchange_rate == live.data.exchange_rate
    assert cache_stats_snapshot()["frankfurter:HIT"] == 1


def test_a_corrupt_redis_entry_falls_through_to_the_provider_and_is_repaired(
    redis_backed_adapter_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, store = redis_backed_adapter_env
    calls: list[int] = []
    _install_fake_http(monkeypatch, calls)
    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    key = next(iter(fake.data))
    fake.data[key] = (b"{corrupt", None)

    result = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert result.status == ProviderStatus.SUCCESS and len(calls) == 2
    assert adapter.get_exchange_rate("USD", "Lisbon, Portugal").data_status == DataStatus.CACHED  # repaired


def test_a_structurally_valid_but_wrong_shaped_payload_is_treated_as_a_miss(
    redis_backed_adapter_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, store = redis_backed_adapter_env
    calls: list[int] = []
    _install_fake_http(monkeypatch, calls)
    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    key = next(iter(fake.data))
    import json

    envelope = json.loads(fake.data[key][0])
    envelope["payload"] = {"unexpected": "shape"}
    fake.data[key] = (json.dumps(envelope).encode(), None)

    assert adapter.get_exchange_rate("USD", "Lisbon, Portugal").status == ProviderStatus.SUCCESS
    assert len(calls) == 2


def test_place_round_trip_through_redis_keeps_provider_identity_and_gains_no_unsupported_fields(
    redis_backed_adapter_env,
) -> None:
    fake, store = redis_backed_adapter_env
    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    place = NormalizedPlace(
        place_id="node/1", name="Museu X", category="museum", coordinates=GeoPoint(lat=38.7, lng=-9.1),
        address=None, source="openstreetmap_places", data_status=DataStatus.LIVE, confidence=0.6,
        provider_tags={"tourism": "museum", "wikidata": "Q1"},
    )
    query_hash = make_query_hash({"lat": 38.7, "lon": -9.1, "tags": ["t"], "schema": "test"})

    adapter._write_poi_cache(store, query_hash, [place])
    cached = adapter._read_poi_cache(store, query_hash)

    assert cached is not None and len(cached) == 1
    back = cached[0]
    assert (back.place_id, back.name, back.source, back.category) == ("node/1", "Museu X", "openstreetmap_places", "museum")
    assert back.coordinates == place.coordinates and back.provider_tags == place.provider_tags
    assert back.data_status == DataStatus.CACHED  # marked cached, not re-verified
    # exactly the validated boundary: every SERIALISED field (the internal de-duplication
    # identity is excluded from dumps by design and never leaves the provider layer)
    serialised = {name for name, field in NormalizedPlace.model_fields.items() if not field.exclude}
    assert set(back.model_dump()) == serialised
    assert serialised == set(NormalizedPlace.model_fields) - {"source_entity_id", "alt_names"}
    for forbidden in ("price", "rating", "opening_hours", "hours", "availability", "booking_url", "safety"):
        assert forbidden not in back.model_dump()
        assert forbidden not in fake.data[next(iter(fake.data))][0].decode()


def test_transient_provider_failures_are_never_cached(redis_backed_adapter_env, monkeypatch: pytest.MonkeyPatch) -> None:
    fake, store = redis_backed_adapter_env
    calls: list[int] = []

    class _Client:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            calls.append(1)
            raise httpx.HTTPError("429/5xx/timeout")

    monkeypatch.setattr(frankfurter_adapter.httpx, "Client", lambda **kw: _Client())
    adapter = FrankfurterCurrencyAdapter(cache_store=store)

    assert adapter.get_exchange_rate("USD", "Lisbon, Portugal").status != ProviderStatus.SUCCESS
    assert adapter.get_exchange_rate("USD", "Lisbon, Portugal").status != ProviderStatus.SUCCESS
    assert len(calls) == 2 and fake.data == {}  # a later retry reaches the provider again


# -- H: AI is never provider-response cache content --------------------------------------------------------------------------


def test_H_no_ai_module_uses_the_provider_response_cache() -> None:
    root = Path(store_module.__file__).resolve().parents[1]
    ai_files = [
        p
        for p in root.rglob("*.py")
        if "tests" not in p.parts
        and (
            any(part.startswith("ai_") or part in {"itinerary_narrator"} for part in p.parts)
            or "narrative" in p.name
            or p.name.startswith("ai_")
        )
    ]
    assert ai_files, "expected AI modules to scan"
    for path in ai_files:
        text = path.read_text().lower()
        for needle in ("provider_cache", "get_provider_cache_store", "import redis", "redis_url"):
            assert needle not in text, (path, needle)


def test_only_the_generic_provider_adapters_import_the_cache_and_none_import_redis_directly() -> None:
    root = Path(store_module.__file__).resolve().parents[1]
    users = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "tests" not in p.parts and "provider_cache_store" in p.read_text() and p.parent.name != "storage"
    )
    # Section 200D: `core/readiness.py` reads the Redis store's live status (probe) for /ready.
    assert all(
        u.startswith("providers/") or u in {"core/provider_cache.py", "core/readiness.py"} for u in users
    ), users
    assert not [
        p for p in root.rglob("*.py")
        if "tests" not in p.parts and "storage" not in p.parts and p.name != "provider_cache.py" and "import redis" in p.read_text()
    ]


def test_scraped_local_source_caches_stay_off_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.accommodation.scraped_adapter import ScrapedAccommodationProvider
    from app.providers.flights.scraped_adapter import ScrapedLocalFlightProvider
    from app.providers.hotel_ratings.scraped_adapter import ScrapedLocalHotelRatingsProvider

    settings = _settings(PROVIDER_CACHE_BACKEND="redis")
    for provider in (ScrapedAccommodationProvider(), ScrapedLocalFlightProvider(), ScrapedLocalHotelRatingsProvider()):
        assert provider._resolve_cache_store(settings) is None  # parsed local files never enter the shared cache
