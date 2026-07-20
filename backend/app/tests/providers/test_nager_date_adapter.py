from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.models.common import DataStatus, ProviderStatus
from app.providers.holidays import nager_date_adapter
from app.providers.holidays.nager_date_adapter import NagerDateHolidaysAdapter, infer_country_code

_DATES = {"start_date": "2026-08-10", "end_date": "2026-08-12"}


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
