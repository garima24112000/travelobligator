from __future__ import annotations

from datetime import date

from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.gateway import ProviderGateway
from app.services.accommodation_inventory_service import AccommodationInventoryService
from app.services.planning_orchestrator import PlanningOrchestrator
from app.services.stay_transport_service import StayTransportService

# Step 167D: PlanningOrchestrator.run_stay_transport_stage now builds and
# stores accommodation_inventory_report plus the derived
# provider_coverage.hotel_prices value. Every test here injects a
# ProviderGateway with either the default not_connected accommodation
# provider or a deterministic in-memory fake -- never a real lodging API.


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


def _orchestrator_with_gateway(gateway: ProviderGateway) -> PlanningOrchestrator:
    return PlanningOrchestrator(
        stay_transport_service=StayTransportService(gateway=gateway),
        accommodation_inventory_service=AccommodationInventoryService(gateway=gateway),
    )


# ---------------------------------------------------------------------------
# 1/2/3. Default generation succeeds, stores not_connected + empty offers,
# and never fabricates a factual field.
# ---------------------------------------------------------------------------


def test_run_stay_transport_stage_default_stores_unavailable() -> None:
    """As of Step 168F, the default accommodation provider is
    `ScrapedAccommodationProvider` (config default `accommodation_
    provider="scraped_local"`) -- with no local HTML file present at its
    default path, it honestly reports `unavailable`, never a fabricated
    offer."""
    orchestrator = PlanningOrchestrator()
    planning_state = PlanningState(trip_request=_trip_request())

    planning_state = orchestrator.run_stay_transport_stage(planning_state)

    report = planning_state.accommodation_inventory_report
    assert report is not None
    assert report.status == AccommodationSearchStatus.UNAVAILABLE
    assert report.offers == []
    assert planning_state.provider_coverage.hotel_prices == "unavailable"


def test_default_generation_never_fabricates_offer_fields() -> None:
    orchestrator = PlanningOrchestrator()
    planning_state = PlanningState(trip_request=_trip_request())

    planning_state = orchestrator.run_stay_transport_stage(planning_state)

    # No offer exists to carry a fabricated property_name, price, rating,
    # availability, amenities, cancellation_policy, or booking_url -- the
    # empty offers list itself is the guarantee.
    assert planning_state.accommodation_inventory_report.offers == []


def test_run_stay_transport_stage_does_not_raise_and_keeps_stay_transport_set() -> None:
    orchestrator = PlanningOrchestrator()
    planning_state = PlanningState(trip_request=_trip_request())

    planning_state = orchestrator.run_stay_transport_stage(planning_state)

    assert planning_state.stay_transport is not None


# ---------------------------------------------------------------------------
# 6. Injected fake provider success is stored without gateway/service-side
# mutation.
# ---------------------------------------------------------------------------


def test_injected_fake_provider_success_is_stored_unmodified() -> None:
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
    orchestrator = _orchestrator_with_gateway(gateway)
    planning_state = PlanningState(trip_request=_trip_request())

    planning_state = orchestrator.run_stay_transport_stage(planning_state)

    report = planning_state.accommodation_inventory_report
    assert report.status == AccommodationSearchStatus.SUCCESS
    assert len(report.offers) == 1
    assert report.offers[0].property_name == "Fake Property"
    assert fake_provider.calls[0].destination == "Lisbon, Portugal"


# ---------------------------------------------------------------------------
# 7/8. ProviderCoverage.hotel_prices reflects the accommodation inventory
# result honestly -- success only with real offers, never upgraded when
# empty, downgraded to failed on an unexpected exception.
# ---------------------------------------------------------------------------


def test_provider_coverage_hotel_prices_unavailable_by_default() -> None:
    orchestrator = PlanningOrchestrator()
    planning_state = PlanningState(trip_request=_trip_request())

    planning_state = orchestrator.run_stay_transport_stage(planning_state)

    assert planning_state.provider_coverage.hotel_prices == "unavailable"


def test_provider_coverage_hotel_prices_success_only_with_real_offers() -> None:
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
    gateway = ProviderGateway(
        accommodation_inventory=_FakeAccommodationInventoryProvider(result=fake_result)
    )
    orchestrator = _orchestrator_with_gateway(gateway)
    planning_state = PlanningState(trip_request=_trip_request())

    planning_state = orchestrator.run_stay_transport_stage(planning_state)

    assert planning_state.provider_coverage.hotel_prices == "success"


def test_provider_coverage_hotel_prices_unavailable_when_success_has_no_offers() -> None:
    """A `success` status with zero offers must never be reported as
    `success` coverage -- that would imply bookable inventory exists when
    it doesn't."""
    fake_result = AccommodationSearchResult(
        provider="fake_accommodation_inventory_provider",
        status=AccommodationSearchStatus.SUCCESS,
        offers=[],
        message="No properties matched this search.",
    )
    gateway = ProviderGateway(
        accommodation_inventory=_FakeAccommodationInventoryProvider(result=fake_result)
    )
    orchestrator = _orchestrator_with_gateway(gateway)
    planning_state = PlanningState(trip_request=_trip_request())

    planning_state = orchestrator.run_stay_transport_stage(planning_state)

    assert planning_state.provider_coverage.hotel_prices == "unavailable"
    assert planning_state.accommodation_inventory_report.offers == []


def test_unexpected_provider_exception_does_not_crash_generation() -> None:
    gateway = ProviderGateway(
        accommodation_inventory=_FakeAccommodationInventoryProvider(raises=True)
    )
    orchestrator = _orchestrator_with_gateway(gateway)
    planning_state = PlanningState(trip_request=_trip_request())

    planning_state = orchestrator.run_stay_transport_stage(planning_state)

    report = planning_state.accommodation_inventory_report
    assert report is not None
    assert report.status == AccommodationSearchStatus.FAILED
    assert report.offers == []
    assert planning_state.provider_coverage.hotel_prices == "failed"


def test_provider_coverage_accommodations_and_hotel_prices_stay_independent() -> None:
    """Step 167E integration check (required test 5): running the
    accommodation-inventory half with an injected successful fake provider
    must not disturb the OSM-backed `accommodations` coverage value
    already set by `DestinationContextService`/`StayTransportService`, and
    the two fields must never be conflated -- they track two structurally
    different concepts (open-data location candidates vs. bookable
    inventory)."""
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
    gateway = ProviderGateway(
        accommodation_inventory=_FakeAccommodationInventoryProvider(result=fake_result)
    )
    orchestrator = _orchestrator_with_gateway(gateway)

    planning_state = orchestrator.create_trip(_trip_request())
    planning_state = orchestrator.run_traveler_profile_stage(planning_state)
    planning_state = orchestrator.run_destination_context_stage(planning_state)
    accommodations_before_stay_transport = planning_state.provider_coverage.accommodations

    planning_state = orchestrator.run_stay_transport_stage(planning_state)

    # The OSM-backed accommodations coverage value is unaffected by the
    # unrelated, separately-tracked hotel_prices result.
    assert (
        planning_state.provider_coverage.accommodations
        == accommodations_before_stay_transport
    )
    assert planning_state.provider_coverage.hotel_prices == "success"


def test_zero_night_trip_is_handled_safely_without_calling_provider() -> None:
    fake_provider = _FakeAccommodationInventoryProvider()
    gateway = ProviderGateway(accommodation_inventory=fake_provider)
    orchestrator = _orchestrator_with_gateway(gateway)
    trip_request = _trip_request(start_date=date(2026, 8, 10), end_date=date(2026, 8, 10))
    planning_state = PlanningState(trip_request=trip_request)

    planning_state = orchestrator.run_stay_transport_stage(planning_state)

    assert fake_provider.calls == []
    assert planning_state.accommodation_inventory_report.status == AccommodationSearchStatus.UNAVAILABLE
    assert planning_state.accommodation_inventory_report.offers == []
    assert planning_state.provider_coverage.hotel_prices == "unavailable"
