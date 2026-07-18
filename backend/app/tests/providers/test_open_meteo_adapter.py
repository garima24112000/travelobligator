from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.providers.weather import open_meteo_adapter
from app.providers.weather.open_meteo_adapter import OpenMeteoWeatherAdapter

_DATES = {"start_date": "2026-08-10", "end_date": "2026-08-12"}
_COORDS = GeoPoint(lat=34.0522, lng=-118.2437)


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
