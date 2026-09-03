from __future__ import annotations

from datetime import date

import pytest

from app.models.accommodation import (
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.gateway import ProviderGateway
from app.services.accommodation_inventory_service import AccommodationInventoryService

# Step 167D: AccommodationInventoryService tests. Every test here injects a
# ProviderGateway with either the default not_connected accommodation
# provider or a deterministic in-memory fake -- never a real lodging API,
# matching backend/app/tests/providers/test_provider_gateway_accommodation.py's
# own no-network guarantee.


class _FakeAccommodationInventoryProvider(AccommodationInventoryProvider):
    provider_name = "fake_accommodation_inventory_provider"

    def __init__(
        self, result: AccommodationSearchResult | None = None, *, raises: bool = False
    ) -> None:
        self._result = result
        self._raises = raises
        self.calls: list[AccommodationSearchRequest] = []

    def search_accommodations(
        self, request: AccommodationSearchRequest
    ) -> AccommodationSearchResult:
        self.calls.append(request)
        if self._raises:
            raise RuntimeError("simulated unexpected provider failure")
        if self._result is not None:
            return self._result
        return AccommodationSearchResult(
            provider=self.provider_name,
            status=AccommodationSearchStatus.NOT_CONNECTED,
            offers=[],
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
    trip_request: TripRequest, *, candidate_accommodation_pois: list[dict[str, object]] | None = None
) -> PlanningState:
    planning_state = PlanningState(trip_request=trip_request)
    planning_state.destination_context = DestinationContext(
        destination_name=trip_request.primary_destination,
        candidate_accommodation_pois=candidate_accommodation_pois or [],
    )
    return planning_state


# ---------------------------------------------------------------------------
# 4. Returns not_connected/unavailable safely when provider is
#    not_connected or the request can't be safely built.
# ---------------------------------------------------------------------------


def test_build_report_returns_not_connected_by_default() -> None:
    service = AccommodationInventoryService()
    planning_state = _planning_state(_trip_request())

    result = service.build_report(planning_state)

    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert result.offers == []


def test_build_report_returns_unavailable_for_zero_night_trip_without_calling_provider() -> None:
    fake_provider = _FakeAccommodationInventoryProvider()
    gateway = ProviderGateway(accommodation_inventory=fake_provider)
    service = AccommodationInventoryService(gateway=gateway)
    trip_request = _trip_request(start_date=date(2026, 8, 10), end_date=date(2026, 8, 10))
    planning_state = _planning_state(trip_request)

    result = service.build_report(planning_state)

    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []
    # The provider is never called with an invalid (zero-night) request.
    assert fake_provider.calls == []


def test_build_report_returns_failed_on_unexpected_exception() -> None:
    fake_provider = _FakeAccommodationInventoryProvider(raises=True)
    gateway = ProviderGateway(accommodation_inventory=fake_provider)
    service = AccommodationInventoryService(gateway=gateway)
    planning_state = _planning_state(_trip_request())

    result = service.build_report(planning_state)

    assert result.status == AccommodationSearchStatus.FAILED
    assert result.offers == []


def test_build_report_no_network_call_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "AccommodationInventoryService must not open a real httpx.Client here"
        )

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    service = AccommodationInventoryService()
    planning_state = _planning_state(_trip_request())

    result = service.build_report(planning_state)

    assert result.status == AccommodationSearchStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# Request is built honestly from the trip's own request fields.
# ---------------------------------------------------------------------------


def test_build_report_passes_trip_fields_into_request() -> None:
    fake_provider = _FakeAccommodationInventoryProvider()
    gateway = ProviderGateway(accommodation_inventory=fake_provider)
    service = AccommodationInventoryService(gateway=gateway)
    trip_request = _trip_request(travelers_count=4, budget_currency="EUR")
    planning_state = _planning_state(trip_request)

    service.build_report(planning_state)

    assert len(fake_provider.calls) == 1
    request = fake_provider.calls[0]
    assert request.destination == "Lisbon, Portugal"
    assert request.check_in_date == date(2026, 10, 10)
    assert request.check_out_date == date(2026, 10, 14)
    assert request.adults == 4
    assert request.currency == "EUR"


# ---------------------------------------------------------------------------
# 5. Never converts OSM accommodation-like POIs into bookable inventory --
#    this service does not read destination_context at all.
# ---------------------------------------------------------------------------


def test_build_report_never_reads_destination_context_candidate_pois() -> None:
    fake_provider = _FakeAccommodationInventoryProvider()
    gateway = ProviderGateway(accommodation_inventory=fake_provider)
    service = AccommodationInventoryService(gateway=gateway)
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
    assert request.destination == "Lisbon, Portugal"
    assert result.offers == []
    # The OSM POI's name never leaks into the request or a fabricated offer.
    assert "OSM Guesthouse" not in str(request.model_dump())
    assert all("OSM Guesthouse" not in str(value) for value in result.model_dump().values())


# ---------------------------------------------------------------------------
# Injected fake provider success passes through unmodified.
# ---------------------------------------------------------------------------


def test_build_report_returns_injected_provider_success_unmodified() -> None:
    from app.models.accommodation import AccommodationOffer
    from app.models.common import DataStatus

    offer = AccommodationOffer(
        provider="fake_accommodation_inventory_provider",
        provider_property_id="prop_1",
        property_name="Fake Property",
        data_status=DataStatus.LIVE,
    )
    fake_result = AccommodationSearchResult(
        provider="fake_accommodation_inventory_provider",
        status=AccommodationSearchStatus.SUCCESS,
        offers=[offer],
    )
    fake_provider = _FakeAccommodationInventoryProvider(result=fake_result)
    gateway = ProviderGateway(accommodation_inventory=fake_provider)
    service = AccommodationInventoryService(gateway=gateway)
    planning_state = _planning_state(_trip_request())

    result = service.build_report(planning_state)

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].property_name == "Fake Property"
