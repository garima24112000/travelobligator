from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.models.common import DataStatus, ProviderStatus
from app.providers.currency import frankfurter_adapter
from app.providers.currency.frankfurter_adapter import (
    FrankfurterCurrencyAdapter,
    infer_destination_currency,
)


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
    called with so tests can assert on from/to currency codes."""

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
    monkeypatch.setattr(frankfurter_adapter.httpx, "Client", lambda **kwargs: fake_client)
    return fake_client


def test_infer_destination_currency_matches_examples() -> None:
    assert infer_destination_currency("Lisbon, Portugal") == "EUR"
    assert infer_destination_currency("Paris, France") == "EUR"
    assert infer_destination_currency("Madrid, Spain") == "EUR"
    assert infer_destination_currency("Rome, Italy") == "EUR"
    assert infer_destination_currency("New York, United States") == "USD"
    assert infer_destination_currency("New York, USA") == "USD"
    assert infer_destination_currency("London, United Kingdom") == "GBP"
    assert infer_destination_currency("London, UK") == "GBP"
    assert infer_destination_currency("Mumbai, India") == "INR"
    assert infer_destination_currency("Toronto, Canada") == "CAD"
    assert infer_destination_currency("Testville, Testland") is None
    assert infer_destination_currency("Los Angeles") is None
    assert infer_destination_currency("") is None


def test_success_usd_to_eur(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.provider_name == "frankfurter"

    rate = response.data
    assert rate.base_currency == "USD"
    assert rate.destination_currency == "EUR"
    assert rate.exchange_rate == 0.92
    assert str(rate.rate_date) == "2026-08-10"
    assert rate.source == "frankfurter"
    assert rate.data_status == DataStatus.LIVE

    assert fake_client.get_call_count == 1
    assert fake_client.last_params == {"from": "USD", "to": "EUR"}


def test_same_currency_returns_one_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data={}))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "New York, USA")

    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE

    rate = response.data
    assert rate.base_currency == "USD"
    assert rate.destination_currency == "USD"
    assert rate.exchange_rate == 1.0

    # No network call was needed for a same-currency identity result.
    assert fake_client.get_call_count == 0


def test_no_fake_fields_added(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}
    _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    dumped = response.data.model_dump()
    assert set(dumped.keys()) == {
        "base_currency",
        "destination_currency",
        "exchange_rate",
        "rate_date",
        "source",
        "data_status",
    }
    for forbidden_field in (
        "cost",
        "price",
        "budget",
        "fee",
        "tax",
        "total",
        "hotel",
        "restaurant",
        "attraction",
        "rating",
    ):
        assert forbidden_field not in dumped


def test_unknown_destination_currency_returns_unavailable_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data={}))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Testville, Testland")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None
    assert "exchange_rate" in response.unavailable_fields
    assert fake_client.get_call_count == 0


def test_request_failure_returns_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(should_fail=True))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.FAILED
    assert response.data_status == DataStatus.FAILED
    assert response.data is None
    assert "exchange_rate" in response.unavailable_fields


def test_no_usable_rate_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {}}
    _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data is None


def test_malformed_payload_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(json_data={"error": "not usable"}))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data is None
