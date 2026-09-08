from __future__ import annotations

from datetime import date

from app.graphs.planning_graph_nodes import build_accommodation_inventory_node
from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus
from app.models.hotel_ratings import (
    AccommodationRating,
    HotelRatingLookupItem,
    HotelRatingsRequest,
    HotelRatingsResult,
    HotelRatingsStatus,
)
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.gateway import ProviderGateway
from app.providers.hotel_ratings import HotelRatingsProvider
from app.services.accommodation_inventory_service import AccommodationInventoryService
from app.services.hotel_rating_enrichment_service import HotelRatingEnrichmentService

# Step 177D: LangGraph accommodation_inventory_node must set
# provider_coverage.hotel_ratings consistently with
# PlanningOrchestrator.run_stay_transport_stage (see
# test_planning_orchestrator_accommodation_inventory.py's equivalent
# tests). Every fake provider here is an in-memory test double -- never a
# real network/LLM call.


class _FakeAccommodationInventoryProvider(AccommodationInventoryProvider):
    provider_name = "fake_accommodation_inventory_provider"

    def __init__(self, result: AccommodationSearchResult) -> None:
        self._result = result

    def search_accommodations(
        self, request: AccommodationSearchRequest
    ) -> AccommodationSearchResult:
        return self._result


class _FakeHotelRatingsProvider(HotelRatingsProvider):
    provider_name = "fake_hotel_ratings_provider"

    def __init__(self, result: HotelRatingsResult) -> None:
        self._result = result

    def get_ratings(self, requests: list[HotelRatingsRequest]) -> HotelRatingsResult:
        return self._result


def _trip_request() -> TripRequest:
    return TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date=date(2026, 10, 10),
        end_date=date(2026, 10, 14),
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )


def _success_report_with_offers(count: int) -> AccommodationSearchResult:
    offers = [
        AccommodationOffer(
            provider="fake_accommodation_inventory_provider",
            provider_property_id=f"prop_{index}",
            property_name=f"Fake Property {index}",
            data_status=DataStatus.LIVE,
        )
        for index in range(count)
    ]
    return AccommodationSearchResult(
        provider="fake_accommodation_inventory_provider",
        status=AccommodationSearchStatus.SUCCESS,
        offers=offers,
    )


def _run_node(service: AccommodationInventoryService) -> PlanningState:
    node = build_accommodation_inventory_node(service)
    planning_state = PlanningState(trip_request=_trip_request())
    result = node({"planning_state": planning_state, "trip_id": "t1", "trip_request": _trip_request()})
    return result["planning_state"]


def test_node_sets_hotel_ratings_not_connected_by_default() -> None:
    gateway = ProviderGateway(
        accommodation_inventory=_FakeAccommodationInventoryProvider(
            result=_success_report_with_offers(1)
        )
    )
    service = AccommodationInventoryService(gateway=gateway)

    planning_state = _run_node(service)

    assert planning_state.provider_coverage.hotel_prices == "success"
    assert planning_state.provider_coverage.hotel_ratings == "not_connected"


def test_node_sets_hotel_ratings_none_when_no_offers() -> None:
    gateway = ProviderGateway(
        accommodation_inventory=_FakeAccommodationInventoryProvider(
            result=AccommodationSearchResult(
                provider="fake_accommodation_inventory_provider",
                status=AccommodationSearchStatus.UNAVAILABLE,
                offers=[],
            )
        )
    )
    service = AccommodationInventoryService(gateway=gateway)

    planning_state = _run_node(service)

    assert planning_state.provider_coverage.hotel_ratings is None


def test_node_sets_hotel_ratings_success_when_all_offers_enriched() -> None:
    gateway = ProviderGateway(
        accommodation_inventory=_FakeAccommodationInventoryProvider(
            result=_success_report_with_offers(1)
        )
    )
    ratings_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[
                HotelRatingLookupItem(
                    offer_id="offer_0",
                    matched=True,
                    rating=AccommodationRating(value=4.7, data_status=DataStatus.LIVE),
                )
            ],
        )
    )
    service = AccommodationInventoryService(
        gateway=gateway,
        hotel_rating_enrichment_service_override=HotelRatingEnrichmentService(
            provider=ratings_provider
        ),
    )

    planning_state = _run_node(service)

    assert planning_state.provider_coverage.hotel_prices == "success"
    assert planning_state.provider_coverage.hotel_ratings == "success"
    assert planning_state.accommodation_inventory_report.offers[0].rating_details is not None


def test_node_sets_hotel_ratings_partial_when_some_offers_enriched() -> None:
    gateway = ProviderGateway(
        accommodation_inventory=_FakeAccommodationInventoryProvider(
            result=_success_report_with_offers(2)
        )
    )
    ratings_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[
                HotelRatingLookupItem(
                    offer_id="offer_0",
                    matched=True,
                    rating=AccommodationRating(value=4.0, data_status=DataStatus.LIVE),
                )
            ],
        )
    )
    service = AccommodationInventoryService(
        gateway=gateway,
        hotel_rating_enrichment_service_override=HotelRatingEnrichmentService(
            provider=ratings_provider
        ),
    )

    planning_state = _run_node(service)

    assert planning_state.provider_coverage.hotel_ratings == "partial"


def test_node_hotel_prices_and_accommodations_stay_independent_of_hotel_ratings() -> None:
    """Required test 3 pairing: hotel_prices and the (untouched by this
    node) OSM-backed accommodations coverage value stay independent of
    the new hotel_ratings coverage value."""
    gateway = ProviderGateway(
        accommodation_inventory=_FakeAccommodationInventoryProvider(
            result=_success_report_with_offers(1)
        )
    )
    ratings_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.FAILED,
            items=[],
        )
    )
    service = AccommodationInventoryService(
        gateway=gateway,
        hotel_rating_enrichment_service_override=HotelRatingEnrichmentService(
            provider=ratings_provider
        ),
    )

    planning_state = _run_node(service)

    assert planning_state.provider_coverage.hotel_prices == "success"
    assert planning_state.provider_coverage.hotel_ratings == "failed"
    assert planning_state.provider_coverage.accommodations is None
