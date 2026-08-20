from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.models.common import DataStatus, ProviderStatus
from app.providers.holidays import nager_date_adapter
from app.providers.holidays.nager_date_adapter import NagerDateHolidaysAdapter, infer_country_code
from app.storage.provider_cache_store import ProviderCacheStore, make_query_hash

_DATES = {"start_date": "2026-08-10", "end_date": "2026-08-12"}


@pytest.fixture(autouse=True)
def _isolated_default_provider_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test in this file gets a fresh, isolated `ProviderCacheStore`
    whenever an adapter is constructed *without* an explicit `cache_store`
    (Step 164C lazily resolves one via `get_provider_cache_store`). Without
    this, the adapter's default lazy resolution would share one real,
    process-wide cache file (`get_provider_cache_store` is a singleton
    keyed by resolved path) across every test function in this file --
    including the pre-existing tests below that construct
    `NagerDateHolidaysAdapter()` with no arguments and reuse the same
    `_DATES`/destination (so the same `(country_code, year)` cache key)
    across multiple tests with *different* expected payloads. Without
    isolation, an earlier test's cached year would silently satisfy a later
    test's HTTP-call assertions instead of exercising the fake HTTP client,
    and would write to the real repo-local `.data/provider_cache.sqlite3`
    file.
    """
    fresh_store = ProviderCacheStore(tmp_path / "isolated_default_cache.sqlite3")
    monkeypatch.setattr(nager_date_adapter, "get_provider_cache_store", lambda path: fresh_store)
    return fresh_store


def _query_hash(country_code: str = "PT", year: int = 2026) -> str:
    return make_query_hash({"country_code": country_code, "year": year})


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Settings:
    """A `Settings` instance for adapter construction, isolated from any
    real `.env` file so cache-enabled/TTL behavior in these tests is
    deterministic regardless of local dev environment contents."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    return Settings(**overrides)


class _FakeResponse:
    """Stands in for an `httpx.Response`. `should_fail=True` makes
    `raise_for_status` raise, simulating a request-level failure."""

    def __init__(self, json_data: Any = None, should_fail: bool = False) -> None:
        self._json_data = json_data
        self._should_fail = should_fail

    def raise_for_status(self) -> None:
        if self._should_fail:
            raise httpx.HTTPError("simulated request failure")

    def json(self) -> Any:
        return self._json_data


class _FakeClient:
    """Stands in for `httpx.Client`. `responses` is consumed in order, one
    per requested year."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.get_call_count = 0
        self.requested_urls: list[str] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        response = self._responses[self.get_call_count]
        self.requested_urls.append(url)
        self.get_call_count += 1
        return response


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, responses: list[_FakeResponse]
) -> _FakeClient:
    fake_client = _FakeClient(responses)
    monkeypatch.setattr(nager_date_adapter.httpx, "Client", lambda **kwargs: fake_client)
    return fake_client


def _holiday_entry(
    date: str,
    local_name: str,
    name: str,
    country_code: str = "PT",
    is_global: bool = True,
    counties: list[str] | None = None,
    types: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "date": date,
        "localName": local_name,
        "name": name,
        "countryCode": country_code,
        "fixed": False,
        "global": is_global,
        "counties": counties,
        "launchYear": None,
        "types": types or ["Public"],
    }


def test_infer_country_code_matches_examples() -> None:
    assert infer_country_code("Lisbon, Portugal") == "PT"
    assert infer_country_code("New York, United States") == "US"
    assert infer_country_code("New York, USA") == "US"
    assert infer_country_code("Paris, France") == "FR"
    assert infer_country_code("Madrid, Spain") == "ES"
    assert infer_country_code("Rome, Italy") == "IT"
    assert infer_country_code("London, United Kingdom") == "GB"
    assert infer_country_code("London, UK") == "GB"
    assert infer_country_code("Portugal") == "PT"
    assert infer_country_code("Testville, Testland") is None
    assert infer_country_code("Los Angeles") is None
    assert infer_country_code("") is None


def test_success_with_holidays_inside_date_range(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        _holiday_entry("2026-01-01", "Ano Novo", "New Year's Day"),
        _holiday_entry("2026-08-11", "Feriado de Teste", "Test Holiday"),
        _holiday_entry("2026-12-25", "Natal", "Christmas Day"),
    ]
    fake_client = _install_fake_client(monkeypatch, [_FakeResponse(json_data=payload)])

    adapter = NagerDateHolidaysAdapter()
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.provider_name == "nager_date"
    assert len(response.data) == 1

    holiday = response.data[0]
    assert str(holiday.date) == "2026-08-11"
    assert holiday.local_name == "Feriado de Teste"
    assert holiday.name == "Test Holiday"
    assert holiday.country_code == "PT"
    assert holiday.is_global is True
    assert holiday.types == ["Public"]
    assert holiday.source == "nager_date"
    assert holiday.data_status == DataStatus.LIVE

    assert fake_client.get_call_count == 1
    assert fake_client.requested_urls[0].endswith("/api/v3/PublicHolidays/2026/PT")


def test_success_with_no_holidays_inside_date_range(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        _holiday_entry("2026-01-01", "Ano Novo", "New Year's Day"),
        _holiday_entry("2026-12-25", "Natal", "Christmas Day"),
    ]
    _install_fake_client(monkeypatch, [_FakeResponse(json_data=payload)])

    adapter = NagerDateHolidaysAdapter()
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    # The provider genuinely has data for the year -- this is a successful,
    # usable response, not an unavailable one, even though nothing falls
    # inside the trip's specific date range.
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.data == []


def test_multi_year_trip_requests_each_year(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = {"start_date": "2026-12-30", "end_date": "2027-01-02"}
    responses = [
        _FakeResponse(json_data=[_holiday_entry("2026-12-31", "Ano Novo", "New Year's Eve")]),
        _FakeResponse(json_data=[_holiday_entry("2027-01-01", "Ano Novo", "New Year's Day")]),
    ]
    fake_client = _install_fake_client(monkeypatch, responses)

    adapter = NagerDateHolidaysAdapter()
    response = adapter.get_public_holidays("Lisbon, Portugal", dates)

    assert response.status == ProviderStatus.SUCCESS
    assert fake_client.get_call_count == 2
    assert fake_client.requested_urls[0].endswith("/api/v3/PublicHolidays/2026/PT")
    assert fake_client.requested_urls[1].endswith("/api/v3/PublicHolidays/2027/PT")
    assert {str(holiday.date) for holiday in response.data} == {"2026-12-31", "2027-01-01"}


def test_no_fake_fields_added(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [_holiday_entry("2026-08-11", "Feriado de Teste", "Test Holiday")]
    _install_fake_client(monkeypatch, [_FakeResponse(json_data=payload)])

    adapter = NagerDateHolidaysAdapter()
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    holiday = response.data[0]
    dumped = holiday.model_dump()
    assert set(dumped.keys()) == {
        "date",
        "local_name",
        "name",
        "country_code",
        "is_global",
        "counties",
        "types",
        "source",
        "data_status",
    }
    for forbidden_field in (
        "closure",
        "closed",
        "crowd",
        "opening_hour",
        "opening_hours",
        "event",
        "festival",
        "strike",
        "risk",
        "rating",
        "price",
    ):
        assert forbidden_field not in dumped


def test_unknown_country_returns_unavailable_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client(monkeypatch, [_FakeResponse(json_data=[])])

    adapter = NagerDateHolidaysAdapter()
    response = adapter.get_public_holidays("Testville, Testland", _DATES)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None
    assert "public_holidays" in response.unavailable_fields
    assert fake_client.get_call_count == 0


def test_missing_dates_returns_unavailable_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client(monkeypatch, [_FakeResponse(json_data=[])])

    adapter = NagerDateHolidaysAdapter()
    response = adapter.get_public_holidays("Lisbon, Portugal", {})

    assert response.status == ProviderStatus.UNAVAILABLE
    assert fake_client.get_call_count == 0


def test_request_failure_returns_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, [_FakeResponse(should_fail=True)])

    adapter = NagerDateHolidaysAdapter()
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert response.status == ProviderStatus.FAILED
    assert response.data_status == DataStatus.FAILED
    assert response.data is None
    assert "public_holidays" in response.unavailable_fields


def test_no_usable_holiday_data_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, [_FakeResponse(json_data=[])])

    adapter = NagerDateHolidaysAdapter()
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None


def test_malformed_payload_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, [_FakeResponse(json_data={"error": "not a list"})])

    adapter = NagerDateHolidaysAdapter()
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data is None


# ---------------------------------------------------------------------------
# Provider cache wiring (Step 164C, docs/12_provider_architecture.md
# "Provider Cache Foundation" section). Every test below injects its own
# `ProviderCacheStore` via the constructor -- never the real repo-local
# `.data/provider_cache.sqlite3` file -- and never makes a real network
# call; only the existing `_FakeClient`/`_FakeResponse` doubles are used.
# Caching is per (country_code, year), matching Nager.Date's own per-year
# API shape.
# ---------------------------------------------------------------------------


def test_cache_hit_returns_cached_response_without_http_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = _install_fake_client(monkeypatch, [_FakeResponse(json_data=[])])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set(
        "nager_date",
        _query_hash(),
        [
            {
                "date": "2026-08-11",
                "local_name": "Feriado de Teste",
                "name": "Test Holiday",
                "country_code": "PT",
                "is_global": True,
                "counties": [],
                "types": ["Public"],
                "source": "nager_date",
                "data_status": "live",
            }
        ],
        ttl_seconds=2592000,
    )

    adapter = NagerDateHolidaysAdapter(cache_store=store)
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert fake_client.get_call_count == 0
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.CACHED
    assert response.provider_name == "nager_date"
    assert len(response.data) == 1
    holiday = response.data[0]
    assert str(holiday.date) == "2026-08-11"
    assert holiday.name == "Test Holiday"
    assert holiday.data_status == DataStatus.CACHED


def test_cache_miss_calls_http_once_and_writes_normalized_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = [_holiday_entry("2026-08-11", "Feriado de Teste", "Test Holiday")]
    fake_client = _install_fake_client(monkeypatch, [_FakeResponse(json_data=payload)])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = NagerDateHolidaysAdapter(cache_store=store)
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert fake_client.get_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE

    entry = store.get("nager_date", _query_hash())
    assert entry is not None
    assert entry.payload == [
        {
            "date": "2026-08-11",
            "local_name": "Feriado de Teste",
            "name": "Test Holiday",
            "country_code": "PT",
            "is_global": True,
            "counties": [],
            "types": ["Public"],
            "source": "nager_date",
            "data_status": "live",
        }
    ]


def test_second_identical_call_uses_cache_and_does_not_call_http_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = [_holiday_entry("2026-08-11", "Feriado de Teste", "Test Holiday")]
    fake_client = _install_fake_client(monkeypatch, [_FakeResponse(json_data=payload)])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    adapter = NagerDateHolidaysAdapter(cache_store=store)

    first = adapter.get_public_holidays("Lisbon, Portugal", _DATES)
    second = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert fake_client.get_call_count == 1
    assert first.data_status == DataStatus.LIVE
    assert second.data_status == DataStatus.CACHED
    assert second.data[0].name == first.data[0].name


def test_expired_cache_entry_behaves_like_miss_and_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timedelta, timezone

    payload = [_holiday_entry("2026-08-11", "Novo Feriado", "New Holiday")]
    fake_client = _install_fake_client(monkeypatch, [_FakeResponse(json_data=payload)])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    already_expired = datetime.now(timezone.utc) - timedelta(hours=1)
    store.set(
        "nager_date",
        _query_hash(),
        [
            {
                "date": "2026-08-11",
                "local_name": "Feriado Antigo",
                "name": "Stale Holiday",
                "country_code": "PT",
                "is_global": True,
                "counties": [],
                "types": ["Public"],
                "source": "nager_date",
                "data_status": "live",
            }
        ],
        ttl_seconds=1,
        now=already_expired,
    )

    adapter = NagerDateHolidaysAdapter(cache_store=store)
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert fake_client.get_call_count == 1
    assert response.data_status == DataStatus.LIVE
    assert response.data[0].name == "New Holiday"

    refreshed_entry = store.get("nager_date", _query_hash())
    assert refreshed_entry is not None
    assert refreshed_entry.payload[0]["name"] == "New Holiday"


def test_provider_cache_disabled_bypasses_cache_and_calls_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = [_holiday_entry("2026-08-11", "Feriado de Teste", "Test Holiday")]
    fake_client = _install_fake_client(
        monkeypatch, [_FakeResponse(json_data=payload), _FakeResponse(json_data=payload)]
    )
    disabled_settings = _settings(monkeypatch, provider_cache_enabled=False)
    monkeypatch.setattr(nager_date_adapter, "get_settings", lambda: disabled_settings)
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    # Even with an explicit cache_store injected, provider_cache_enabled=False
    # must skip the cache completely.
    adapter = NagerDateHolidaysAdapter(cache_store=store)
    first = adapter.get_public_holidays("Lisbon, Portugal", _DATES)
    second = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert fake_client.get_call_count == 2
    assert first.data_status == DataStatus.LIVE
    assert second.data_status == DataStatus.LIVE
    assert store.get("nager_date", _query_hash()) is None


def test_cache_read_failure_falls_back_to_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [_holiday_entry("2026-08-11", "Feriado de Teste", "Test Holiday")]
    fake_client = _install_fake_client(monkeypatch, [_FakeResponse(json_data=payload)])

    class _RaisingGetCacheStore:
        def get(self, source: str, query_hash: str, now: Any = None) -> None:
            raise RuntimeError("cache backend unavailable")

        def set(self, *args: Any, **kwargs: Any) -> None:
            pass

    adapter = NagerDateHolidaysAdapter(cache_store=_RaisingGetCacheStore())
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert fake_client.get_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.data[0].name == "Test Holiday"


def test_cache_write_failure_still_returns_live_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [_holiday_entry("2026-08-11", "Feriado de Teste", "Test Holiday")]
    fake_client = _install_fake_client(monkeypatch, [_FakeResponse(json_data=payload)])

    class _RaisingSetCacheStore:
        def get(self, source: str, query_hash: str, now: Any = None) -> None:
            return None

        def set(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("cache write failed")

    adapter = NagerDateHolidaysAdapter(cache_store=_RaisingSetCacheStore())
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert fake_client.get_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.data[0].name == "Test Holiday"


def test_cache_key_is_deterministic_for_equivalent_normalized_queries() -> None:
    query_a = {"country_code": "PT", "year": 2026}
    query_b = {"year": 2026, "country_code": "PT"}

    assert make_query_hash(query_a) == make_query_hash(query_b)


def test_different_country_code_or_year_produce_different_query_hashes() -> None:
    base_hash = _query_hash()

    different_country_hash = _query_hash(country_code="US")
    different_year_hash = _query_hash(year=2027)

    assert base_hash != different_country_hash
    assert base_hash != different_year_hash


def test_cache_entry_stores_no_secrets_in_payload_or_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = [_holiday_entry("2026-08-11", "Feriado de Teste", "Test Holiday")]
    _install_fake_client(monkeypatch, [_FakeResponse(json_data=payload)])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = NagerDateHolidaysAdapter(cache_store=store)
    adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    entry = store.get("nager_date", _query_hash())
    assert entry is not None
    assert entry.metadata == {}

    dumped = str(entry.payload)
    for forbidden in ("api_key", "token", "secret", "authorization", "bearer"):
        assert forbidden not in dumped.lower()


def test_unknown_country_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(monkeypatch, [_FakeResponse(json_data=[])])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = NagerDateHolidaysAdapter(cache_store=store)
    response = adapter.get_public_holidays("Testville, Testland", _DATES)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert store.get("nager_date", _query_hash()) is None


def test_no_usable_holiday_data_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(monkeypatch, [_FakeResponse(json_data=[])])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = NagerDateHolidaysAdapter(cache_store=store)
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert store.get("nager_date", _query_hash()) is None


def test_malformed_payload_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(monkeypatch, [_FakeResponse(json_data={"error": "not a list"})])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = NagerDateHolidaysAdapter(cache_store=store)
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert store.get("nager_date", _query_hash()) is None


def test_request_failure_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(monkeypatch, [_FakeResponse(should_fail=True)])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = NagerDateHolidaysAdapter(cache_store=store)
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)

    assert response.status == ProviderStatus.FAILED
    assert store.get("nager_date", _query_hash()) is None


def test_multi_year_trip_caches_each_year_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dates = {"start_date": "2026-12-30", "end_date": "2027-01-02"}
    responses = [
        _FakeResponse(json_data=[_holiday_entry("2026-12-31", "Ano Novo", "New Year's Eve")]),
        _FakeResponse(json_data=[_holiday_entry("2027-01-01", "Ano Novo", "New Year's Day")]),
    ]
    fake_client = _install_fake_client(monkeypatch, responses)
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    adapter = NagerDateHolidaysAdapter(cache_store=store)

    first = adapter.get_public_holidays("Lisbon, Portugal", dates)
    assert fake_client.get_call_count == 2
    assert first.data_status == DataStatus.LIVE
    assert store.get("nager_date", _query_hash(year=2026)) is not None
    assert store.get("nager_date", _query_hash(year=2027)) is not None

    # A second identical multi-year call is served entirely from cache.
    second = adapter.get_public_holidays("Lisbon, Portugal", dates)
    assert fake_client.get_call_count == 2
    assert second.data_status == DataStatus.CACHED
    assert {str(holiday.date) for holiday in second.data} == {"2026-12-31", "2027-01-01"}
