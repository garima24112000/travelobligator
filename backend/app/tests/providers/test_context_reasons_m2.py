"""M2 Phase 1 (D1/D5): holiday and currency lookups use the geocoder's
country code, and every no-data answer from the weather, holiday and
currency adapters carries a structured reason. Every request goes to an
`httpx.MockTransport` -- no network."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import httpx
import pytest

from app.models.common import GeoPoint, ProviderStatus
from app.providers import missing_data_reasons as reasons
from app.providers.currency import frankfurter_adapter
from app.providers.currency.frankfurter_adapter import FrankfurterCurrencyAdapter
from app.providers.holidays import nager_date_adapter
from app.providers.holidays.nager_date_adapter import NagerDateHolidaysAdapter
from app.providers.weather import open_meteo_adapter
from app.providers.weather.open_meteo_adapter import OpenMeteoWeatherAdapter
from app.storage.provider_cache_store import ProviderCacheStore

Handler = Callable[[httpx.Request], httpx.Response]

_HOLIDAY_DATES = {"start_date": "2026-12-30", "end_date": "2027-01-02"}


def _mock_http(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> list[httpx.Request]:
    sent: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(recording), **kwargs)
    )
    return sent


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("simulated timeout", request=request)


def _nager(tmp_path: Path) -> NagerDateHolidaysAdapter:
    return NagerDateHolidaysAdapter(cache_store=ProviderCacheStore(tmp_path / "nager.sqlite3"))


def _frankfurter(tmp_path: Path) -> FrankfurterCurrencyAdapter:
    return FrankfurterCurrencyAdapter(cache_store=ProviderCacheStore(tmp_path / "fx.sqlite3"))


# -- holidays ---------------------------------------------------------------------------------


def test_holidays_use_the_resolved_country_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = [{"date": "2027-01-01", "localName": "Ganjitsu", "name": "New Year's Day", "countryCode": "JP"}]
    sent = _mock_http(
        monkeypatch,
        lambda request: httpx.Response(200, json=payload if "/2027/" in request.url.path else []),
    )

    # "Kyoto" alone resolves to no country by name; the geocoder's code does.
    response = _nager(tmp_path).get_public_holidays("Kyoto", _HOLIDAY_DATES, country_code="jp")

    assert response.status == ProviderStatus.SUCCESS
    assert [holiday.local_name for holiday in response.data] == ["Ganjitsu"]
    assert sent and all(request.url.path.endswith("/JP") for request in sent)


def test_holidays_without_a_code_still_use_the_name_inference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sent = _mock_http(monkeypatch, lambda request: httpx.Response(200, json=[]))
    _nager(tmp_path).get_public_holidays("Lisbon, Portugal", _HOLIDAY_DATES)
    assert sent and all(request.url.path.endswith("/PT") for request in sent)


def test_holidays_with_an_invalid_code_fall_back_to_the_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sent = _mock_http(monkeypatch, lambda request: httpx.Response(200, json=[]))
    _nager(tmp_path).get_public_holidays("Lisbon, Portugal", _HOLIDAY_DATES, country_code="P1")
    assert sent and all(request.url.path.endswith("/PT") for request in sent)


def test_holidays_unresolved_country_makes_no_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _mock_http(monkeypatch, lambda request: httpx.Response(200, json=[]))
    response = _nager(tmp_path).get_public_holidays("Kyoto", _HOLIDAY_DATES)
    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.failure_reason == reasons.COUNTRY_UNRESOLVED
    assert sent == []


def test_holidays_404_is_an_unsupported_country(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_http(monkeypatch, lambda request: httpx.Response(404))
    response = _nager(tmp_path).get_public_holidays("Somewhere", _HOLIDAY_DATES, country_code="AQ")
    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.failure_reason == reasons.COUNTRY_NOT_SUPPORTED


def _nager_204(available: Callable[[httpx.Request], httpx.Response]) -> Handler:
    """Nager.Date answering every year with 204, and its covered-country list
    with `available` (the live run showed 204 -- not 404 -- for an uncovered country)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/AvailableCountries"):
            return available(request)
        return httpx.Response(204)

    return handler


_COVERS_JP_ONLY = lambda request: httpx.Response(200, json=[{"countryCode": "JP", "name": "Japan"}])  # noqa: E731


def test_holidays_204_for_an_uncovered_country_is_unsupported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _mock_http(monkeypatch, _nager_204(_COVERS_JP_ONLY))
    response = _nager(tmp_path).get_public_holidays("Somewhere", _HOLIDAY_DATES, country_code="AQ")
    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.failure_reason == reasons.COUNTRY_NOT_SUPPORTED
    assert sum(1 for request in sent if request.url.path.endswith("/AvailableCountries")) == 1


def test_holidays_204_for_a_covered_country_stays_no_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A covered country without data for the trip's years: neutral, never "unsupported".
    _mock_http(monkeypatch, _nager_204(_COVERS_JP_ONLY))
    response = _nager(tmp_path).get_public_holidays("Nara", _HOLIDAY_DATES, country_code="JP")
    assert response.failure_reason == reasons.HOLIDAY_DATA_NOT_PROVIDED


@pytest.mark.parametrize(
    "available",
    [
        lambda request: httpx.Response(503),
        lambda request: httpx.Response(204),
        lambda request: httpx.Response(200, json={"not": "a list"}),
        lambda request: httpx.Response(200, json=[]),
        _timeout,
    ],
    ids=["503", "204", "not-a-list", "empty-list", "timeout"],
)
def test_holidays_204_with_an_unreadable_country_list_claims_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, available: Handler
) -> None:
    _mock_http(monkeypatch, _nager_204(available))
    response = _nager(tmp_path).get_public_holidays("Somewhere", _HOLIDAY_DATES, country_code="AQ")
    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.failure_reason == reasons.HOLIDAY_DATA_NOT_PROVIDED  # neither "unsupported" nor a failure


def test_holidays_country_list_is_read_once_and_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _mock_http(monkeypatch, _nager_204(_COVERS_JP_ONLY))
    store = ProviderCacheStore(tmp_path / "nager.sqlite3")
    NagerDateHolidaysAdapter(cache_store=store).get_public_holidays("A", _HOLIDAY_DATES, country_code="AQ")
    # a NEW adapter (a new process) reads the list from the provider cache
    second = NagerDateHolidaysAdapter(cache_store=store).get_public_holidays("B", _HOLIDAY_DATES, country_code="GS")
    assert second.failure_reason == reasons.COUNTRY_NOT_SUPPORTED
    assert sum(1 for request in sent if request.url.path.endswith("/AvailableCountries")) == 1


def test_holidays_with_data_never_read_the_country_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = [{"date": "2027-01-01", "localName": "Ganjitsu", "name": "New Year's Day", "countryCode": "JP"}]
    sent = _mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    _nager(tmp_path).get_public_holidays("Nara", _HOLIDAY_DATES, country_code="JP")
    assert not any(request.url.path.endswith("/AvailableCountries") for request in sent)


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (_timeout, reasons.REQUEST_TIMED_OUT),
        (lambda request: httpx.Response(503), reasons.REQUEST_FAILED),
        (lambda request: httpx.Response(200, content=b"<html>not json</html>"), reasons.MALFORMED_RESPONSE),
    ],
    ids=["timeout", "http-503", "malformed"],
)
def test_holidays_request_failures_name_their_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, handler: Handler, expected: str
) -> None:
    _mock_http(monkeypatch, handler)
    response = _nager(tmp_path).get_public_holidays("Kyoto", _HOLIDAY_DATES, country_code="JP")
    assert response.status == ProviderStatus.FAILED
    assert response.failure_reason == expected


def test_holidays_invalid_dates_have_a_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _mock_http(monkeypatch, lambda request: httpx.Response(200, json=[]))
    response = _nager(tmp_path).get_public_holidays(
        "Kyoto", {"start_date": "not-a-date", "end_date": "2027-01-02"}, country_code="JP"
    )
    assert response.failure_reason == reasons.INVALID_DATES
    assert sent == []


# -- currency ---------------------------------------------------------------------------------


def test_currency_comes_from_the_resolved_country(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _mock_http(
        monkeypatch,
        lambda request: httpx.Response(200, json={"base": "USD", "date": "2026-10-21", "rates": {"JPY": 150.5}}),
    )
    response = _frankfurter(tmp_path).get_exchange_rate("USD", "Kyoto", country_code="JP")

    assert response.status == ProviderStatus.SUCCESS
    assert response.data.destination_currency == "JPY"
    assert response.data.exchange_rate == 150.5
    assert len(sent) == 1 and sent[0].url.params["to"] == "JPY"


def test_same_currency_needs_no_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _mock_http(monkeypatch, lambda request: httpx.Response(500))
    response = _frankfurter(tmp_path).get_exchange_rate("EUR", "Berlin", country_code="DE")
    assert response.status == ProviderStatus.SUCCESS
    assert response.data.exchange_rate == 1.0
    assert sent == []


@pytest.mark.parametrize(
    ("country_code", "expected"),
    [("PA", reasons.CURRENCY_AMBIGUOUS_REASON), ("AQ", reasons.CURRENCY_NOT_DETERMINED_REASON)],
)
def test_ambiguous_or_undetermined_currency_makes_no_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, country_code: str, expected: str
) -> None:
    sent = _mock_http(monkeypatch, lambda request: httpx.Response(500))
    response = _frankfurter(tmp_path).get_exchange_rate("USD", "Somewhere", country_code=country_code)
    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.failure_reason == expected
    assert response.data is None
    assert sent == []


def test_currency_unresolved_country_without_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _mock_http(monkeypatch, lambda request: httpx.Response(500))
    response = _frankfurter(tmp_path).get_exchange_rate("USD", "Kyoto")
    assert response.failure_reason == reasons.COUNTRY_UNRESOLVED
    assert sent == []


@pytest.mark.parametrize("status_code", [404, 422])
def test_currency_not_published_is_unsupported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status_code: int
) -> None:
    _mock_http(monkeypatch, lambda request: httpx.Response(status_code, json={"message": "not found"}))
    response = _frankfurter(tmp_path).get_exchange_rate("USD", "Hanoi", country_code="VN")
    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.failure_reason == reasons.CURRENCY_NOT_SUPPORTED
    assert response.data is None


@pytest.mark.parametrize(
    ("handler", "status", "expected"),
    [
        (_timeout, ProviderStatus.FAILED, reasons.REQUEST_TIMED_OUT),
        (lambda request: httpx.Response(503), ProviderStatus.FAILED, reasons.REQUEST_FAILED),
        (lambda request: httpx.Response(200, content=b"oops"), ProviderStatus.FAILED, reasons.MALFORMED_RESPONSE),
        (lambda request: httpx.Response(200, json=["not", "an", "object"]), ProviderStatus.UNAVAILABLE,
         reasons.MALFORMED_RESPONSE),
        (lambda request: httpx.Response(200, json={"base": "USD", "rates": {}}), ProviderStatus.UNAVAILABLE,
         reasons.NO_USABLE_DATA),
    ],
    ids=["timeout", "http-503", "not-json", "not-an-object", "rate-missing"],
)
def test_currency_failures_name_their_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, handler: Handler, status: ProviderStatus, expected: str
) -> None:
    _mock_http(monkeypatch, handler)
    response = _frankfurter(tmp_path).get_exchange_rate("USD", "Kyoto", country_code="JP")
    assert response.status == status
    assert response.failure_reason == expected
    assert response.data is None


def test_currency_failure_log_never_contains_the_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _mock_http(monkeypatch, lambda request: httpx.Response(503))
    _frankfurter(tmp_path).get_exchange_rate("USD", "Kyoto", country_code="JP")
    assert "http" not in caplog.text.lower().replace("http 503", "")


# -- weather ----------------------------------------------------------------------------------


def _weather_dates(days_ahead: int) -> dict[str, str]:
    start = date.today() + timedelta(days=days_ahead)
    return {"start_date": start.isoformat(), "end_date": (start + timedelta(days=2)).isoformat()}


@pytest.fixture()
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(open_meteo_adapter.time, "sleep", lambda seconds: None)


def _weather() -> OpenMeteoWeatherAdapter:
    return OpenMeteoWeatherAdapter(cache_store=None)


def test_weather_without_coordinates_has_a_reason() -> None:
    response = _weather().get_weather_forecast("Kyoto", _weather_dates(3), coordinates=None)
    assert response.failure_reason == reasons.COORDINATES_UNAVAILABLE


def test_weather_beyond_the_horizon_has_a_reason_and_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _mock_http(monkeypatch, lambda request: httpx.Response(500))
    response = _weather().get_weather_forecast("Kyoto", _weather_dates(60), coordinates=GeoPoint(lat=35.0, lng=135.7))
    assert response.failure_reason == reasons.FORECAST_NOT_YET_AVAILABLE
    assert sent == []


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (_timeout, reasons.REQUEST_TIMED_OUT),
        (lambda request: httpx.Response(503), reasons.REQUEST_FAILED),
        (lambda request: httpx.Response(200, content=b"not json"), reasons.MALFORMED_RESPONSE),
    ],
    ids=["timeout", "http-503", "not-json"],
)
def test_weather_request_failures_name_their_reason(
    monkeypatch: pytest.MonkeyPatch, _no_retry_sleep: None, handler: Handler, expected: str
) -> None:
    _mock_http(monkeypatch, handler)
    response = _weather().get_weather_forecast("Kyoto", _weather_dates(3), coordinates=GeoPoint(lat=35.0, lng=135.7))
    assert response.status == ProviderStatus.FAILED
    assert response.failure_reason == expected


def test_weather_non_object_body_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    # Before M2 Phase 1 a JSON list here raised AttributeError out of the adapter.
    _mock_http(monkeypatch, lambda request: httpx.Response(200, json=["unexpected"]))
    response = _weather().get_weather_forecast("Kyoto", _weather_dates(3), coordinates=GeoPoint(lat=35.0, lng=135.7))
    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.failure_reason == reasons.MALFORMED_RESPONSE


@pytest.mark.parametrize(
    ("body", "expected"),
    [({"error": True, "reason": "bad"}, reasons.PROVIDER_REPORTED_ERROR), ({"daily": {}}, reasons.NO_USABLE_DATA)],
    ids=["provider-error", "no-daily-data"],
)
def test_weather_provider_error_and_empty_data_have_reasons(
    monkeypatch: pytest.MonkeyPatch, body: dict, expected: str
) -> None:
    _mock_http(monkeypatch, lambda request: httpx.Response(200, json=body))
    response = _weather().get_weather_forecast("Kyoto", _weather_dates(3), coordinates=GeoPoint(lat=35.0, lng=135.7))
    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.failure_reason == expected


def test_every_reason_an_adapter_sets_is_a_known_reason() -> None:
    for module in (nager_date_adapter, frankfurter_adapter, open_meteo_adapter):
        assert module.reasons is reasons
    assert reasons.HOLIDAY_DATA_NOT_PROVIDED in reasons.MISSING_DATA_REASONS
    assert reasons.FORECAST_NOT_YET_AVAILABLE in reasons.MISSING_DATA_REASONS
