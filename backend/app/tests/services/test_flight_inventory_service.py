from __future__ import annotations

from datetime import date

import pytest

from app.models.flight import FlightSearchRequest, FlightSearchResult, FlightSearchStatus
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
from app.providers.flights.base import FlightInventoryProvider
from app.providers.gateway import ProviderGateway
from app.services.flight_inventory_service import FlightInventoryService

# Step 169E: FlightInventoryService tests. Every test here injects a
# ProviderGateway with either the default scraped_local flight provider
# (no local file present, so it's always honestly unavailable) or a
# deterministic in-memory fake -- never a real flight API, matching
# backend/app/tests/providers/test_provider_gateway_flights.py's own
# no-network guarantee.


class _FakeFlightInventoryProvider(FlightInventoryProvider):
    provider_name = "fake_flight_inventory_provider"

    def __init__(
        self, result: FlightSearchResult | None = None, *, raises: bool = False
    ) -> None:
        self._result = result
        self._raises = raises
        self.calls: list[FlightSearchRequest] = []

    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResult:
        self.calls.append(request)
        if self._raises:
            raise RuntimeError("simulated unexpected provider failure")
        if self._result is not None:
            return self._result
        return FlightSearchResult(
            provider=self.provider_name,
            status=FlightSearchStatus.NOT_CONNECTED,
            offers=[],
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
        )


def _trip_request(**overrides: object) -> TripRequest:
    fields: dict[str, object] = {
        "primary_destination": "Lisbon, Portugal",
        "start_date": date(2026, 10, 10),
        "end_date": date(2026, 10, 14),
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _planning_state(
    trip_request: TripRequest,
    *,
    candidate_accommodation_pois: list[dict[str, object]] | None = None,
) -> PlanningState:
    planning_state = PlanningState(trip_request=trip_request)
    planning_state.destination_context = DestinationContext(
        destination_name=trip_request.primary_destination,
        candidate_accommodation_pois=candidate_accommodation_pois or [],
    )
    return planning_state


# ---------------------------------------------------------------------------
# 2/6. Returns unavailable safely by default -- no local HTML file present
# at the default scraped_local path.
# ---------------------------------------------------------------------------


def test_build_report_returns_unavailable_by_default() -> None:
    service = FlightInventoryService()
    planning_state = _planning_state(_trip_request())

    result = service.build_report(planning_state)

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


def test_build_report_returns_failed_on_unexpected_exception() -> None:
    fake_provider = _FakeFlightInventoryProvider(raises=True)
    gateway = ProviderGateway(flight_inventory=fake_provider)
    service = FlightInventoryService(gateway=gateway)
    planning_state = _planning_state(_trip_request())

    result = service.build_report(planning_state)

    assert result.status == FlightSearchStatus.FAILED
    assert result.offers == []


def test_build_report_no_network_call_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("FlightInventoryService must not open a real httpx.Client here")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    service = FlightInventoryService()
    planning_state = _planning_state(_trip_request())

    result = service.build_report(planning_state)

    assert result.status == FlightSearchStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# 4. Request is built honestly from the trip's own request fields.
# ---------------------------------------------------------------------------


def test_build_report_passes_trip_fields_into_request() -> None:
    fake_provider = _FakeFlightInventoryProvider()
    gateway = ProviderGateway(flight_inventory=fake_provider)
    service = FlightInventoryService(gateway=gateway)
    trip_request = _trip_request(
        origin_city="Home City", travelers_count=4, budget_currency="EUR"
    )
    planning_state = _planning_state(trip_request)

    service.build_report(planning_state)

    assert len(fake_provider.calls) == 1
    request = fake_provider.calls[0]
    assert request.origin == "Home City"
    assert request.destination == "Lisbon, Portugal"
    assert request.departure_date == date(2026, 10, 10)
    assert request.return_date == date(2026, 10, 14)
    assert request.adults == 4
    assert request.currency == "EUR"
    assert request.trip_id == planning_state.trip_id


def test_build_report_treats_same_day_trip_as_one_way() -> None:
    fake_provider = _FakeFlightInventoryProvider()
    gateway = ProviderGateway(flight_inventory=fake_provider)
    service = FlightInventoryService(gateway=gateway)
    trip_request = _trip_request(start_date=date(2026, 8, 10), end_date=date(2026, 8, 10))
    planning_state = _planning_state(trip_request)

    service.build_report(planning_state)

    request = fake_provider.calls[0]
    assert request.departure_date == date(2026, 8, 10)
    assert request.return_date is None


# ---------------------------------------------------------------------------
# 5. FlightInventoryService allows origin=None without guessing an
#    airport -- TripRequest.origin_city is optional and may be unset.
# ---------------------------------------------------------------------------


def test_build_report_allows_missing_origin_city_without_guessing() -> None:
    fake_provider = _FakeFlightInventoryProvider()
    gateway = ProviderGateway(flight_inventory=fake_provider)
    service = FlightInventoryService(gateway=gateway)
    trip_request = _trip_request(origin_city=None)
    planning_state = _planning_state(trip_request)

    service.build_report(planning_state)

    request = fake_provider.calls[0]
    assert request.origin is None


# ---------------------------------------------------------------------------
# Never converts OSM accommodation-like POIs (or any destination_context
# data) into flight inventory -- this service does not read
# destination_context at all.
# ---------------------------------------------------------------------------


def test_build_report_never_reads_destination_context_candidate_pois() -> None:
    fake_provider = _FakeFlightInventoryProvider()
    gateway = ProviderGateway(flight_inventory=fake_provider)
    service = FlightInventoryService(gateway=gateway)
    osm_poi = {
        "place_id": "osm/1",
        "name": "OSM Guesthouse",
        "category": "hotel",
        "coordinates": {"lat": 38.7, "lng": -9.1},
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": 0.6,
    }
    planning_state = _planning_state(_trip_request(), candidate_accommodation_pois=[osm_poi])

    result = service.build_report(planning_state)

    request = fake_provider.calls[0]
    assert result.offers == []
    assert "OSM Guesthouse" not in str(request.model_dump())
    assert all("OSM Guesthouse" not in str(value) for value in result.model_dump().values())


# ---------------------------------------------------------------------------
# Injected fake provider success passes through unmodified.
# ---------------------------------------------------------------------------


def test_build_report_returns_injected_provider_success_unmodified() -> None:
    from app.models.common import DataStatus
    from app.models.flight import FlightOffer, FlightSegment

    segment = FlightSegment(
        origin_airport="JFK",
        destination_airport="LIS",
        carrier_name="TEST_ONLY_AIRLINE_ALPHA",
        flight_number="TEST_ONLY_FLIGHT_123",
        data_status=DataStatus.LIVE,
    )
    offer = FlightOffer(
        offer_id="TEST_ONLY_FLIGHT_OFFER_ALPHA",
        provider="fake_flight_inventory_provider",
        data_status=DataStatus.LIVE,
        outbound_segments=[segment],
    )
    fake_result = FlightSearchResult(
        provider="fake_flight_inventory_provider",
        status=FlightSearchStatus.SUCCESS,
        offers=[offer],
        destination="Lisbon, Portugal",
        departure_date=date(2026, 10, 10),
    )
    fake_provider = _FakeFlightInventoryProvider(result=fake_result)
    gateway = ProviderGateway(flight_inventory=fake_provider)
    service = FlightInventoryService(gateway=gateway)
    planning_state = _planning_state(_trip_request())

    result = service.build_report(planning_state)

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].offer_id == "TEST_ONLY_FLIGHT_OFFER_ALPHA"
