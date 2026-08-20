from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.providers.weather import open_meteo_adapter
from app.providers.weather.open_meteo_adapter import OpenMeteoWeatherAdapter
from app.storage.provider_cache_store import ProviderCacheStore, make_query_hash

_DATES = {"start_date": "2026-08-10", "end_date": "2026-08-12"}
_COORDS = GeoPoint(lat=34.0522, lng=-118.2437)


@pytest.fixture(autouse=True)
def _isolated_default_provider_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test in this file gets a fresh, isolated `ProviderCacheStore`
    whenever an adapter is constructed *without* an explicit `cache_store`
    (Step 164B lazily resolves one via `get_provider_cache_store`). Without
    this, the adapter's default lazy resolution would share one real,
    process-wide cache file (`get_provider_cache_store` is a singleton
    keyed by resolved path) across every test function in this file --
    including the pre-existing tests below that construct
    `OpenMeteoWeatherAdapter()` with no arguments -- which would let an
    earlier test's cached payload silently satisfy a later test's HTTP-call
    assertions instead of exercising the fake HTTP client, and would write
    to the real repo-local `.data/provider_cache.sqlite3` file.
    """
    fresh_store = ProviderCacheStore(tmp_path / "isolated_default_cache.sqlite3")
    monkeypatch.setattr(open_meteo_adapter, "get_provider_cache_store", lambda path: fresh_store)
    return fresh_store


def _query_hash(
    lat: float = _COORDS.lat,
    lng: float = _COORDS.lng,
    start_date: str = _DATES["start_date"],
    end_date: str = _DATES["end_date"],
) -> str:
    return make_query_hash(
        {
            "latitude": lat,
            "longitude": lng,
            "start_date": start_date,
            "end_date": end_date,
            "timezone": "auto",
        }
    )


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
    """Stands in for `httpx.Client`. Records the request params it was
    called with so tests can assert on latitude/longitude/date range."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.get_call_count = 0
        self.last_params: dict[str, Any] | None = None

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        self.get_call_count += 1
        self.last_params = params
        return self._response


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, response: _FakeResponse
) -> _FakeClient:
    fake_client = _FakeClient(response)
    monkeypatch.setattr(open_meteo_adapter.httpx, "Client", lambda **kwargs: fake_client)
    return fake_client


def _daily_payload(
    dates: list[str],
    temps_max: list[float],
    temps_min: list[float],
    precip_prob_max: list[float],
    precip_sum: list[float],
    weather_codes: list[int],
) -> dict[str, Any]:
    return {
        "daily": {
            "time": dates,
            "temperature_2m_max": temps_max,
            "temperature_2m_min": temps_min,
            "precipitation_probability_max": precip_prob_max,
            "precipitation_sum": precip_sum,
            "weather_code": weather_codes,
        }
    }


def test_success_with_daily_data(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _daily_payload(
        dates=["2026-08-10", "2026-08-11", "2026-08-12"],
        temps_max=[28.5, 29.0, 27.1],
        temps_min=[18.2, 19.0, 17.5],
        precip_prob_max=[10, 20, 60],
        precip_sum=[0.0, 0.2, 4.5],
        weather_codes=[1, 2, 61],
    )
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    adapter = OpenMeteoWeatherAdapter()
    response = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.provider_name == "open_meteo"
    assert len(response.data) == 3

    first = response.data[0]
    assert str(first.date) == "2026-08-10"
    assert first.temperature_max_c == 28.5
    assert first.temperature_min_c == 18.2
    assert first.precipitation_probability_max == 10
    assert first.precipitation_sum_mm == 0.0
    assert first.weather_code == 1
    assert first.source == "open_meteo"
    assert first.data_status == DataStatus.LIVE

    # Only real Open-Meteo daily fields were requested -- no rating,
    # condition text, humidity, UV, or alert field was ever asked for.
    assert fake_client.get_call_count == 1
    assert fake_client.last_params is not None
    assert fake_client.last_params["latitude"] == _COORDS.lat
    assert fake_client.last_params["longitude"] == _COORDS.lng
    assert fake_client.last_params["start_date"] == "2026-08-10"
    assert fake_client.last_params["end_date"] == "2026-08-12"
    requested_fields = set(fake_client.last_params["daily"].split(","))
    assert requested_fields == {
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_probability_max",
        "precipitation_sum",
        "weather_code",
    }


def test_no_fake_fields_added(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _daily_payload(
        dates=["2026-08-10"],
        temps_max=[28.5],
        temps_min=[18.2],
        precip_prob_max=[10],
        precip_sum=[0.0],
        weather_codes=[1],
    )
    _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    adapter = OpenMeteoWeatherAdapter()
    response = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    day = response.data[0]
    dumped = day.model_dump()
    assert set(dumped.keys()) == {
        "date",
        "temperature_max_c",
        "temperature_min_c",
        "precipitation_probability_max",
        "precipitation_sum_mm",
        "weather_code",
        "source",
        "data_status",
    }
    for forbidden_field in (
        "condition",
        "description",
        "humidity",
        "uv_index",
        "alert",
        "alerts",
        "severe_weather",
        "rating",
        "price",
    ):
        assert forbidden_field not in dumped


def test_no_usable_daily_data_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(json_data={"daily": {"time": []}}))

    adapter = OpenMeteoWeatherAdapter()
    response = adapter.get_weather_forecast("Nowhere", _DATES, coordinates=_COORDS)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None
    assert "weather_forecast" in response.unavailable_fields


def test_missing_daily_key_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(json_data={}))

    adapter = OpenMeteoWeatherAdapter()
    response = adapter.get_weather_forecast("Nowhere", _DATES, coordinates=_COORDS)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data is None


def test_provider_reported_error_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulates Open-Meteo reporting an error for a date range outside what
    # the forecast endpoint supports, without an HTTP-level failure.
    _install_fake_client(
        monkeypatch,
        _FakeResponse(json_data={"error": True, "reason": "date range too far in the future"}),
    )

    adapter = OpenMeteoWeatherAdapter()
    response = adapter.get_weather_forecast("Somewhere", _DATES, coordinates=_COORDS)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert "date range too far in the future" in (response.message or "")


def test_request_failure_returns_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(should_fail=True))

    adapter = OpenMeteoWeatherAdapter()
    response = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    assert response.status == ProviderStatus.FAILED
    assert response.data_status == DataStatus.FAILED
    assert response.data is None
    assert "weather_forecast" in response.unavailable_fields


def test_missing_coordinates_returns_unavailable_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data={}))

    adapter = OpenMeteoWeatherAdapter()
    response = adapter.get_weather_forecast("Nowhere", _DATES, coordinates=None)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None
    assert fake_client.get_call_count == 0


def test_missing_dates_returns_unavailable_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data={}))

    adapter = OpenMeteoWeatherAdapter()
    response = adapter.get_weather_forecast("Los Angeles", {}, coordinates=_COORDS)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert fake_client.get_call_count == 0


# ---------------------------------------------------------------------------
# Provider cache wiring (Step 164B, docs/12_provider_architecture.md
# "Provider Cache Foundation" section). Every test below injects its own
# `ProviderCacheStore` via the constructor -- never the real repo-local
# `.data/provider_cache.sqlite3` file -- and never makes a real network
# call; only the existing `_FakeClient`/`_FakeResponse` doubles are used.
# ---------------------------------------------------------------------------


def test_cache_hit_returns_cached_response_without_http_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data={}))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set(
        "open_meteo",
        _query_hash(),
        [
            {
                "date": "2026-08-10",
                "temperature_max_c": 28.5,
                "temperature_min_c": 18.2,
                "precipitation_probability_max": 10,
                "precipitation_sum_mm": 0.0,
                "weather_code": 1,
                "source": "open_meteo",
                "data_status": "live",
            }
        ],
        ttl_seconds=3600,
    )

    adapter = OpenMeteoWeatherAdapter(cache_store=store)
    response = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    assert fake_client.get_call_count == 0
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.CACHED
    assert response.provider_name == "open_meteo"
    assert len(response.data) == 1
    day = response.data[0]
    assert str(day.date) == "2026-08-10"
    assert day.temperature_max_c == 28.5
    assert day.data_status == DataStatus.CACHED


def test_cache_miss_calls_http_once_and_writes_normalized_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _daily_payload(
        dates=["2026-08-10"],
        temps_max=[28.5],
        temps_min=[18.2],
        precip_prob_max=[10],
        precip_sum=[0.0],
        weather_codes=[1],
    )
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenMeteoWeatherAdapter(cache_store=store)
    response = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    assert fake_client.get_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE

    entry = store.get("open_meteo", _query_hash())
    assert entry is not None
    assert entry.payload == [
        {
            "date": "2026-08-10",
            "temperature_max_c": 28.5,
            "temperature_min_c": 18.2,
            "precipitation_probability_max": 10.0,
            "precipitation_sum_mm": 0.0,
            "weather_code": 1,
            "source": "open_meteo",
            "data_status": "live",
        }
    ]


def test_second_identical_call_uses_cache_and_does_not_call_http_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _daily_payload(
        dates=["2026-08-10"],
        temps_max=[28.5],
        temps_min=[18.2],
        precip_prob_max=[10],
        precip_sum=[0.0],
        weather_codes=[1],
    )
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    adapter = OpenMeteoWeatherAdapter(cache_store=store)

    first = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)
    second = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    assert fake_client.get_call_count == 1
    assert first.data_status == DataStatus.LIVE
    assert second.data_status == DataStatus.CACHED
    assert second.data[0].temperature_max_c == first.data[0].temperature_max_c


def test_expired_cache_entry_behaves_like_miss_and_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timedelta, timezone

    payload = _daily_payload(
        dates=["2026-08-10"],
        temps_max=[30.0],
        temps_min=[19.0],
        precip_prob_max=[5],
        precip_sum=[0.0],
        weather_codes=[2],
    )
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    already_expired = datetime.now(timezone.utc) - timedelta(hours=1)
    store.set(
        "open_meteo",
        _query_hash(),
        [
            {
                "date": "2026-08-10",
                "temperature_max_c": 99.9,
                "temperature_min_c": 99.9,
                "precipitation_probability_max": 99,
                "precipitation_sum_mm": 99.9,
                "weather_code": 99,
                "source": "open_meteo",
                "data_status": "live",
            }
        ],
        ttl_seconds=1,
        now=already_expired,
    )

    adapter = OpenMeteoWeatherAdapter(cache_store=store)
    response = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    assert fake_client.get_call_count == 1
    assert response.data_status == DataStatus.LIVE
    assert response.data[0].temperature_max_c == 30.0

    refreshed_entry = store.get("open_meteo", _query_hash())
    assert refreshed_entry is not None
    assert refreshed_entry.payload[0]["temperature_max_c"] == 30.0


def test_provider_cache_disabled_bypasses_cache_and_calls_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _daily_payload(
        dates=["2026-08-10"],
        temps_max=[28.5],
        temps_min=[18.2],
        precip_prob_max=[10],
        precip_sum=[0.0],
        weather_codes=[1],
    )
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    disabled_settings = _settings(monkeypatch, provider_cache_enabled=False)
    monkeypatch.setattr(open_meteo_adapter, "get_settings", lambda: disabled_settings)
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    # Even with an explicit cache_store injected, provider_cache_enabled=False
    # must skip the cache completely.
    adapter = OpenMeteoWeatherAdapter(cache_store=store)
    first = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)
    second = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    assert fake_client.get_call_count == 2
    assert first.data_status == DataStatus.LIVE
    assert second.data_status == DataStatus.LIVE
    assert store.get("open_meteo", _query_hash()) is None


def test_cache_read_failure_falls_back_to_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _daily_payload(
        dates=["2026-08-10"],
        temps_max=[28.5],
        temps_min=[18.2],
        precip_prob_max=[10],
        precip_sum=[0.0],
        weather_codes=[1],
    )
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    class _RaisingGetCacheStore:
        def get(self, source: str, query_hash: str, now: Any = None) -> None:
            raise RuntimeError("cache backend unavailable")

        def set(self, *args: Any, **kwargs: Any) -> None:
            pass

    adapter = OpenMeteoWeatherAdapter(cache_store=_RaisingGetCacheStore())
    response = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    assert fake_client.get_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.data[0].temperature_max_c == 28.5


def test_cache_write_failure_still_returns_live_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _daily_payload(
        dates=["2026-08-10"],
        temps_max=[28.5],
        temps_min=[18.2],
        precip_prob_max=[10],
        precip_sum=[0.0],
        weather_codes=[1],
    )
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    class _RaisingSetCacheStore:
        def get(self, source: str, query_hash: str, now: Any = None) -> None:
            return None

        def set(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("cache write failed")

    adapter = OpenMeteoWeatherAdapter(cache_store=_RaisingSetCacheStore())
    response = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    assert fake_client.get_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.data[0].temperature_max_c == 28.5


def test_cache_key_is_deterministic_for_equivalent_normalized_queries() -> None:
    query_a = {
        "latitude": _COORDS.lat,
        "longitude": _COORDS.lng,
        "start_date": _DATES["start_date"],
        "end_date": _DATES["end_date"],
        "timezone": "auto",
    }
    query_b = {
        "timezone": "auto",
        "end_date": _DATES["end_date"],
        "start_date": _DATES["start_date"],
        "longitude": _COORDS.lng,
        "latitude": _COORDS.lat,
    }

    assert make_query_hash(query_a) == make_query_hash(query_b)


def test_different_coordinates_or_dates_produce_different_query_hashes() -> None:
    base_hash = _query_hash()

    different_coords_hash = _query_hash(lat=40.7128, lng=-74.0060)
    different_dates_hash = _query_hash(start_date="2026-09-01", end_date="2026-09-03")

    assert base_hash != different_coords_hash
    assert base_hash != different_dates_hash


def test_cache_entry_stores_no_secrets_in_payload_or_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _daily_payload(
        dates=["2026-08-10"],
        temps_max=[28.5],
        temps_min=[18.2],
        precip_prob_max=[10],
        precip_sum=[0.0],
        weather_codes=[1],
    )
    _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenMeteoWeatherAdapter(cache_store=store)
    adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    entry = store.get("open_meteo", _query_hash())
    assert entry is not None
    assert entry.metadata == {}

    dumped = str(entry.payload)
    for forbidden in ("api_key", "token", "secret", "authorization", "bearer"):
        assert forbidden not in dumped.lower()


def test_unavailable_response_from_provider_error_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(
        monkeypatch,
        _FakeResponse(json_data={"error": True, "reason": "date range too far in the future"}),
    )
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenMeteoWeatherAdapter(cache_store=store)
    response = adapter.get_weather_forecast("Somewhere", _DATES, coordinates=_COORDS)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert store.get("open_meteo", _query_hash()) is None


def test_failed_response_from_request_failure_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(should_fail=True))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenMeteoWeatherAdapter(cache_store=store)
    response = adapter.get_weather_forecast("Los Angeles", _DATES, coordinates=_COORDS)

    assert response.status == ProviderStatus.FAILED
    assert store.get("open_meteo", _query_hash()) is None


def test_no_usable_daily_data_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(json_data={"daily": {"time": []}}))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenMeteoWeatherAdapter(cache_store=store)
    response = adapter.get_weather_forecast("Nowhere", _DATES, coordinates=_COORDS)

    assert response.status == ProviderStatus.UNAVAILABLE
    assert store.get("open_meteo", _query_hash()) is None
