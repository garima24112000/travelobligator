from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus
from app.models.flight import FlightSearchResult, FlightSearchStatus
from app.models.planning_state import (
    DestinationContext,
    PlanningState,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.models.common import UnavailableDataItem
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.itinerary_narrative_request_builder import (
    ItineraryNarrativeRequestBuilder,
)

# Step 182F: proves the request builder includes only allow-listed
# PlanningState fields and structurally excludes every forbidden one
# (price, rating, route/travel-time duration). Never calls a
# provider/LLM/network service -- ExperiencePlannerService.run() here
# only does deterministic, real scheduling, never a provider call.


def _place(place_id: str, name: str, category: str, lat: float = 0.0, lng: float = 0.0) -> dict[str, Any]:
    return {
        "place_id": place_id,
        "name": name,
        "category": category,
        "coordinates": {"lat": lat, "lng": lng},
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": 0.6,
    }


def _planning_state_with_schedule() -> PlanningState:
    trip_request = TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-11",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
        pace=TripPace.BALANCED,
        interests=["museums"],
    )
    planning_state = PlanningState(trip_request=trip_request)
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_pois=[_place("poi1", "Belem Tower", "landmark")],
        candidate_restaurants=[_place("r1", "Time Out Market", "restaurant")],
        candidate_accommodation_pois=[_place("a1", "Lisbon Guesthouse", "guest_house")],
    )
    ExperiencePlannerService().run(planning_state)
    return planning_state


def test_request_includes_only_allow_listed_top_level_fields() -> None:
    planning_state = _planning_state_with_schedule()
    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    allowed_fields = set(type(request).model_fields.keys())
    forbidden_field_names = {
        "price",
        "nightly_price_amount",
        "total_price_amount",
        "rating",
        "review_count",
        "route_duration_seconds",
        "travel_time",
        "booking_url",
        "availability_status",
        "opening_hours",
        "flight_number",
        "coordinates",
        "provider_property_id",
        "api_key",
        "raw_prompt",
    }
    assert forbidden_field_names.isdisjoint(allowed_fields)


def test_request_experience_input_has_only_name_category_reason() -> None:
    planning_state = _planning_state_with_schedule()
    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert len(request.days) == 2
    day_with_experience = next(day for day in request.days if day.experiences)
    experience = day_with_experience.experiences[0]
    assert set(type(experience).model_fields.keys()) == {"name", "category", "reason"}
    assert experience.name == "Belem Tower"


def test_request_never_includes_coordinates_or_provider_ids_in_the_dumped_payload() -> None:
    """Dumps the full request to a plain dict/JSON string and greps for
    forbidden substrings -- a structural proof, not just a schema check,
    that nothing provider-internal leaked into the payload actually sent
    to a narrator provider."""
    planning_state = _planning_state_with_schedule()
    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    dumped_json = request.model_dump_json()
    for forbidden in ("place_id", "provider_place_id", "coordinates", "lat", "lng", "api_key"):
        assert forbidden not in dumped_json


def test_request_omits_restaurant_names_when_none_scheduled() -> None:
    trip_request = TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-10",
        travelers_count=1,
        travel_group_type=TravelGroupType.SOLO,
    )
    planning_state = PlanningState(trip_request=trip_request)
    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.days == []
    assert request.stay_area_names == []
    assert request.accommodation_offer_count == 0
    assert request.flight_offer_count == 0
    assert request.weather_available is False


def test_accommodation_offer_count_only_counts_real_success_offers() -> None:
    planning_state = _planning_state_with_schedule()
    planning_state.accommodation_inventory_report = AccommodationSearchResult(
        provider="scraped:manual_local_scraped_accommodation",
        status=AccommodationSearchStatus.SUCCESS,
        offers=[
            AccommodationOffer(
                provider="scraped:manual_local_scraped_accommodation",
                provider_property_id="prop-1",
                property_name="Test Hotel",
                availability_status="available",
                data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
            )
        ],
    )

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.accommodation_offer_count == 1
    # The count is the only accommodation-offer field -- no price/rating
    # ever reaches the request.
    assert "nightly_price_amount" not in request.model_dump_json()


def test_accommodation_offer_count_is_zero_when_not_connected() -> None:
    planning_state = _planning_state_with_schedule()
    planning_state.accommodation_inventory_report = AccommodationSearchResult(
        provider="not_connected_accommodation_provider",
        status=AccommodationSearchStatus.NOT_CONNECTED,
        offers=[],
    )

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.accommodation_offer_count == 0


def test_flight_offer_count_only_counts_real_success_offers() -> None:
    planning_state = _planning_state_with_schedule()
    planning_state.flight_inventory_report = FlightSearchResult(
        provider="not_connected_flight_provider",
        status=FlightSearchStatus.NOT_CONNECTED,
        offers=[],
        destination="Lisbon, Portugal",
        departure_date=planning_state.trip_request.start_date,
    )

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.flight_offer_count == 0


def test_unavailable_data_fields_are_carried_as_plain_strings() -> None:
    planning_state = _planning_state_with_schedule()
    planning_state.unavailable_data = [
        UnavailableDataItem(
            field="hotel_prices", reason="No provider connected.", data_status=DataStatus.NOT_CONNECTED
        )
    ]

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.unavailable_data_fields == ["hotel_prices"]


def test_truncation_caps_days_and_marks_truncated_true() -> None:
    trip_request = TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-25",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    planning_state = PlanningState(trip_request=trip_request)
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_pois=[_place(f"poi{i}", f"Place {i}", "landmark", lng=0.01 * i) for i in range(20)],
    )
    ExperiencePlannerService().run(planning_state)
    total_days = len(planning_state.experience_plan.daily_plans)
    assert total_days > 10  # sanity: this trip really is longer than the default cap

    import app.services.itinerary_narrative_request_builder as builder_module

    builder_module.get_settings = lambda: Settings(_env_file=None, itinerary_narrator_max_days=3)
    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert len(request.days) == 3
    assert request.truncated is True
