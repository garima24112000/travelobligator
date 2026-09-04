from __future__ import annotations

from datetime import date

import pytest

from app.models.common import DataStatus
from app.models.flight import (
    FlightOffer,
    FlightSearchRequest,
    FlightSearchResult,
    FlightSearchStatus,
    FlightSegment,
)
from app.providers.flights.base import FlightInventoryProvider
from app.providers.flights.scraped_adapter import ScrapedLocalFlightProvider
from app.providers.gateway import ProviderGateway

# Tests for Step 169E: `ProviderGateway.flight_inventory` +
# `ProviderGateway.search_flights`. The default flight provider is
# `ScrapedLocalFlightProvider` (config default `flight_provider=
# "scraped_local"`), which -- with no local HTML file present at its
# default path -- honestly reports `unavailable` rather than
# `not_connected`. Every test here uses either that default provider or
# an injected fake -- none makes a real network call, mirroring
# backend/app/tests/providers/test_provider_gateway_accommodation.py.


def _request(**overrides: object) -> FlightSearchRequest:
    fields: dict[str, object] = {
        "origin": "JFK",
        "destination": "LIS",
        "departure_date": date(2026, 10, 10),
    }
    fields.update(overrides)
    return FlightSearchRequest(**fields)


class _FakeFlightInventoryProvider(FlightInventoryProvider):
    """Deterministic test double -- never makes a network call, and its
    result is trivially distinguishable from a real not_connected result
    so tests can prove the gateway actually delegates to whatever provider
    was injected."""

    provider_name = "fake_flight_inventory_provider"

    def __init__(self, result: FlightSearchResult | None = None) -> None:
        self._result = result
        self.calls: list[FlightSearchRequest] = []

    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResult:
        self.calls.append(request)
        if self._result is not None:
            return self._result
        segment = FlightSegment(
            origin_airport=request.origin,
            destination_airport=request.destination,
            carrier_name="TEST_ONLY_AIRLINE_ALPHA",
            flight_number="TEST_ONLY_FLIGHT_123",
            data_status=DataStatus.LIVE,
        )
        offer = FlightOffer(
            offer_id="TEST_ONLY_FLIGHT_OFFER_ALPHA",
            provider=self.provider_name,
            data_status=DataStatus.LIVE,
            outbound_segments=[segment],
        )
        return FlightSearchResult(
            provider=self.provider_name,
            status=FlightSearchStatus.SUCCESS,
            offers=[offer],
            message="Fake provider for test purposes only.",
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
        )


# ---------------------------------------------------------------------------
# 1/2. Provider gateway default flight lookup uses scraped_local provider,
# and with no local HTML file present returns an honest, non-fabricated
# unavailable result with empty offers.
# ---------------------------------------------------------------------------


def test_gateway_default_search_flights_returns_unavailable() -> None:
    gateway = ProviderGateway()

    result = gateway.search_flights(_request())

    assert isinstance(result, FlightSearchResult)
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert isinstance(gateway.flight_inventory, ScrapedLocalFlightProvider)


def test_gateway_default_search_flights_returns_empty_offers() -> None:
    gateway = ProviderGateway()
    result = gateway.search_flights(_request())
    assert result.offers == []


def test_gateway_default_search_flights_never_fabricates_fields() -> None:
    gateway = ProviderGateway()
    result = gateway.search_flights(_request())

    assert result.offers == []
    for forbidden_attr in (
        "airline",
        "flight_number",
        "total_price_amount",
        "booking_url",
    ):
        # No offer exists to carry a fabricated value in the first place --
        # the empty offers list itself is the guarantee.
        assert not hasattr(result, forbidden_attr)


def test_gateway_default_flight_provider_name() -> None:
    gateway = ProviderGateway()
    result = gateway.search_flights(_request())
    assert result.provider == "scraped_flight_provider"


# ---------------------------------------------------------------------------
# 3. Provider gateway can use an injected fake flight provider and returns
#    its provider-backed result unmodified.
# ---------------------------------------------------------------------------


def test_gateway_uses_injected_flight_provider() -> None:
    fake_provider = _FakeFlightInventoryProvider()
    gateway = ProviderGateway(flight_inventory=fake_provider)

    request = _request()
    result = gateway.search_flights(request)

    assert result.provider == "fake_flight_inventory_provider"
    assert result.status == FlightSearchStatus.SUCCESS
    assert result.offers[0].offer_id == "TEST_ONLY_FLIGHT_OFFER_ALPHA"
    assert fake_provider.calls == [request]


def test_gateway_passes_flight_request_through_unchanged() -> None:
    fake_provider = _FakeFlightInventoryProvider()
    gateway = ProviderGateway(flight_inventory=fake_provider)

    request = _request(children=2, currency="EUR")
    gateway.search_flights(request)

    assert fake_provider.calls[0].children == 2
    assert fake_provider.calls[0].currency == "EUR"


def test_gateway_other_provider_slots_are_unaffected_by_injected_flight_inventory() -> None:
    """Injecting a fake `flight_inventory` provider must not disturb any
    other gateway slot's default construction."""
    fake_provider = _FakeFlightInventoryProvider()
    gateway = ProviderGateway(flight_inventory=fake_provider)

    assert gateway.places is not None
    assert gateway.weather is not None
    assert gateway.holiday is not None
    assert gateway.currency is not None
    assert gateway.routing is not None
    assert gateway.accommodation is not None
    assert gateway.accommodation_inventory is not None
    assert gateway.flight is not None
    assert gateway.flight_inventory is fake_provider


# ---------------------------------------------------------------------------
# Provider gateway flight lookup does not call network by default.
# ---------------------------------------------------------------------------


def test_gateway_search_flights_makes_no_network_call_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "ProviderGateway.search_flights must not open a real httpx.Client here"
        )

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    gateway = ProviderGateway()
    result = gateway.search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# Provider gateway construction remains backward compatible.
# ---------------------------------------------------------------------------


def test_gateway_construction_with_no_arguments_still_works() -> None:
    gateway = ProviderGateway()

    assert gateway.flight is not None
    assert gateway.flight_inventory is not None


def test_gateway_does_not_inspect_destination_to_invent_flight_data() -> None:
    gateway = ProviderGateway()

    result_lisbon = gateway.search_flights(_request(destination="LIS"))
    result_tokyo = gateway.search_flights(_request(destination="NRT"))

    assert result_lisbon.offers == result_tokyo.offers == []
    assert result_lisbon.status == result_tokyo.status == FlightSearchStatus.UNAVAILABLE


def test_gateway_does_not_transform_default_result_into_fake_offers() -> None:
    gateway = ProviderGateway()
    request = _request()

    first = gateway.search_flights(request)
    second = gateway.search_flights(request)

    assert first.model_dump(exclude={"searched_at"}) == second.model_dump(
        exclude={"searched_at"}
    )
    assert first.offers == [] and second.offers == []


# ---------------------------------------------------------------------------
# PlanningOrchestrator/PlanValidatorService consume this report -- assert
# only that the wiring exists here; behavior is covered in
# test_flight_inventory_service.py and test_plan_validator_service.py.
# ---------------------------------------------------------------------------


def test_planning_orchestrator_calls_flight_inventory_service() -> None:
    import inspect

    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "FlightInventoryService" in source
    assert "flight_inventory_report" in source
    assert "provider_coverage.flights" in source


def test_plan_validator_references_flight_inventory_report() -> None:
    import inspect

    import app.services.plan_validator_service as validator_module

    source = inspect.getsource(validator_module)
    assert "flight_inventory_report" in source
    assert "flight_inventory" in source
