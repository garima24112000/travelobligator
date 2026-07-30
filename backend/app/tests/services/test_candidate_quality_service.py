from __future__ import annotations

import copy
import inspect
from typing import Any

from app.models.candidate_quality import CandidateQualityTier, CandidateRejectReason
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
from app.services import candidate_quality_service as candidate_quality_service_module
from app.services.candidate_quality_service import CandidateQualityService

_FORBIDDEN_FACTUAL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "safety_score",
}


def _place(
    name: str,
    category: str | None,
    *,
    lat: float | None = 40.7,
    lng: float | None = -74.0,
    confidence: float = 0.6,
    source: str = "openstreetmap_places",
    data_status: str = "live",
    address: str | None = None,
    place_id: str | None = None,
) -> dict[str, Any]:
    return {
        "place_id": place_id or f"way/{abs(hash(name)) % 100000}",
        "name": name,
        "category": category,
        "coordinates": {"lat": lat, "lng": lng} if lat is not None and lng is not None else None,
        "address": address,
        "source": source,
        "data_status": data_status,
        "confidence": confidence,
    }


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "New York",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _planning_state_with_context(
    candidate_pois: list[dict[str, Any]] | None = None,
    candidate_restaurants: list[dict[str, Any]] | None = None,
    candidate_accommodation_pois: list[dict[str, Any]] | None = None,
    **trip_request_overrides: Any,
) -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request(**trip_request_overrides))
    planning_state.destination_context = DestinationContext(
        destination_name="New York",
        candidate_pois=candidate_pois or [],
        candidate_restaurants=candidate_restaurants or [],
        candidate_accommodation_pois=candidate_accommodation_pois or [],
    )
    return planning_state


# ---------------------------------------------------------------------------
# 5. museum attraction scores higher than generic historic district.
# ---------------------------------------------------------------------------


def test_museum_scores_higher_than_generic_historic_district() -> None:
    service = CandidateQualityService()

    museum = service.score_attraction(_place("City Museum of Art", "museum"))
    historic_district = service.score_attraction(
        _place("Old Town Historic District", "historic district")
    )

    assert museum.total_score > historic_district.total_score
    assert museum.quality_tier in (
        CandidateQualityTier.PRIMARY_ANCHOR,
        CandidateQualityTier.GOOD_CANDIDATE,
    )


# ---------------------------------------------------------------------------
# 6. Empire State Building with must_visit match becomes primary_anchor or
#    good_candidate.
# ---------------------------------------------------------------------------


def test_must_visit_match_becomes_primary_anchor_or_good_candidate() -> None:
    service = CandidateQualityService()

    score = service.score_attraction(
        _place("Empire State Building", "attraction", confidence=0.5),
        must_visit_names=["Empire State Building"],
    )

    assert score.quality_tier in (
        CandidateQualityTier.PRIMARY_ANCHOR,
        CandidateQualityTier.GOOD_CANDIDATE,
    )
    assert score.reject_reasons == []


# ---------------------------------------------------------------------------
# 7. school/reservoir/generic district becomes low_priority or rejected.
# ---------------------------------------------------------------------------


def test_school_becomes_low_priority_or_rejected() -> None:
    service = CandidateQualityService()
    score = service.score_attraction(_place("PS 123 School", "school"))

    assert score.quality_tier in (CandidateQualityTier.LOW_PRIORITY, CandidateQualityTier.REJECTED)
    assert CandidateRejectReason.SCHOOL_OR_NON_TOURIST_LOCAL_USE in score.reject_reasons


def test_reservoir_becomes_low_priority_or_rejected() -> None:
    service = CandidateQualityService()
    score = service.score_attraction(_place("Central Park Reservoir", "reservoir"))

    assert score.quality_tier in (CandidateQualityTier.LOW_PRIORITY, CandidateQualityTier.REJECTED)
    assert CandidateRejectReason.ADMINISTRATIVE_OR_INFRASTRUCTURE in score.reject_reasons


def test_generic_historic_district_becomes_low_priority_or_rejected() -> None:
    service = CandidateQualityService()
    score = service.score_attraction(_place("Downtown Historic District", "historic district"))

    assert score.quality_tier in (CandidateQualityTier.LOW_PRIORITY, CandidateQualityTier.REJECTED)
    assert CandidateRejectReason.GENERIC_HISTORIC_DISTRICT in score.reject_reasons


# ---------------------------------------------------------------------------
# 8. restaurant candidate does not include price/rating/review/opening-hours
#    fields.
# ---------------------------------------------------------------------------


def test_restaurant_score_has_no_price_rating_review_or_hours_fields() -> None:
    service = CandidateQualityService()
    score = service.score_restaurant(_place("Downtown Cafe", "cafe"))

    field_names = set(score.model_dump().keys())
    assert field_names & _FORBIDDEN_FACTUAL_FIELD_NAMES == set()


def test_restaurant_casual_category_scores_lower_unless_interest_matches() -> None:
    service = CandidateQualityService()
    default_score = service.score_restaurant(_place("Corner Bar", "bar"))
    nightlife_score = service.score_restaurant(
        _place("Corner Bar", "bar"), user_interests=["nightlife"]
    )

    assert nightlife_score.score_components["category_signal"] > (
        default_score.score_components["category_signal"]
    )


# ---------------------------------------------------------------------------
# 9. accommodation POI score never claims bookable inventory.
# ---------------------------------------------------------------------------


def test_accommodation_poi_score_never_claims_bookable_inventory() -> None:
    service = CandidateQualityService()
    score = service.score_accommodation_poi(_place("Riverside Hotel", "hotel"))

    field_names = set(score.model_dump().keys())
    assert field_names & _FORBIDDEN_FACTUAL_FIELD_NAMES == set()
    assert any(
        "never bookable inventory" in signal for signal in score.negative_signals
    )


def test_accommodation_poi_with_booking_like_input_fields_is_rejected() -> None:
    service = CandidateQualityService()
    candidate = _place("Riverside Hotel", "hotel")
    candidate["price"] = 199

    score = service.score_accommodation_poi(candidate)

    assert score.quality_tier == CandidateQualityTier.REJECTED
    assert CandidateRejectReason.UNSUPPORTED_ACCOMMODATION_INVENTORY in score.reject_reasons


# ---------------------------------------------------------------------------
# 10. missing coordinates rejects.
# ---------------------------------------------------------------------------


def test_attraction_missing_coordinates_is_rejected() -> None:
    service = CandidateQualityService()
    score = service.score_attraction(_place("Some Museum", "museum", lat=None, lng=None))

    assert score.quality_tier == CandidateQualityTier.REJECTED
    assert CandidateRejectReason.MISSING_COORDINATES in score.reject_reasons


def test_restaurant_missing_coordinates_is_rejected() -> None:
    service = CandidateQualityService()
    score = service.score_restaurant(_place("Some Cafe", "cafe", lat=None, lng=None))

    assert score.quality_tier == CandidateQualityTier.REJECTED
    assert CandidateRejectReason.MISSING_COORDINATES in score.reject_reasons


def test_accommodation_poi_missing_coordinates_is_rejected() -> None:
    service = CandidateQualityService()
    score = service.score_accommodation_poi(_place("Some Hotel", "hotel", lat=None, lng=None))

    assert score.quality_tier == CandidateQualityTier.REJECTED
    assert CandidateRejectReason.MISSING_COORDINATES in score.reject_reasons


# ---------------------------------------------------------------------------
# 11. build_report creates scores for candidate_pois, candidate_restaurants,
#     candidate_accommodation_pois.
# ---------------------------------------------------------------------------


def test_build_report_scores_all_three_candidate_lists() -> None:
    service = CandidateQualityService()
    planning_state = _planning_state_with_context(
        candidate_pois=[_place("City Museum", "museum"), _place("Old Town District", "historic district")],
        candidate_restaurants=[_place("Downtown Cafe", "cafe")],
        candidate_accommodation_pois=[_place("Riverside Hotel", "hotel")],
    )

    report = service.build_report(planning_state)

    assert report.destination_name == "New York"
    assert len(report.attraction_scores) == 2
    assert len(report.restaurant_scores) == 1
    assert len(report.accommodation_poi_scores) == 1


def test_build_report_handles_missing_destination_context() -> None:
    service = CandidateQualityService()
    planning_state = PlanningState(trip_request=_trip_request())

    report = service.build_report(planning_state)

    assert report.attraction_scores == []
    assert report.restaurant_scores == []
    assert report.accommodation_poi_scores == []


# ---------------------------------------------------------------------------
# 12. build_report summary counts tiers deterministically.
# ---------------------------------------------------------------------------


def test_build_report_summary_counts_tiers_deterministically() -> None:
    service = CandidateQualityService()
    planning_state = _planning_state_with_context(
        candidate_pois=[_place("City Museum", "museum"), _place("Old Town District", "historic district")],
        candidate_restaurants=[_place("Downtown Cafe", "cafe")],
        candidate_accommodation_pois=[_place("Riverside Hotel", "hotel")],
    )

    report_one = service.build_report(planning_state)
    report_two = service.build_report(planning_state)

    assert report_one.summary == report_two.summary
    total_candidates = (
        len(report_one.attraction_scores)
        + len(report_one.restaurant_scores)
        + len(report_one.accommodation_poi_scores)
    )
    tier_total = sum(report_one.summary[tier.value] for tier in CandidateQualityTier)
    assert tier_total == total_candidates


# ---------------------------------------------------------------------------
# 13. service does not mutate PlanningState.
# ---------------------------------------------------------------------------


def test_build_report_does_not_mutate_planning_state() -> None:
    service = CandidateQualityService()
    planning_state = _planning_state_with_context(
        candidate_pois=[_place("City Museum", "museum")],
        candidate_restaurants=[_place("Downtown Cafe", "cafe")],
        candidate_accommodation_pois=[_place("Riverside Hotel", "hotel")],
    )
    before = copy.deepcopy(planning_state.model_dump(mode="json"))

    service.build_report(planning_state)

    after = planning_state.model_dump(mode="json")
    assert before == after


# ---------------------------------------------------------------------------
# 14. service does not import provider adapters, LLM clients, LangGraph, or
#     LangSmith.
# ---------------------------------------------------------------------------


def test_service_module_has_no_forbidden_imports() -> None:
    source = inspect.getsource(candidate_quality_service_module)

    import_lines = [
        line.strip().lower()
        for line in source.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]

    forbidden_substrings = [
        "app.providers",
        "langgraph",
        "langsmith",
        "openai",
        "anthropic",
    ]
    for forbidden in forbidden_substrings:
        assert not any(forbidden in line for line in import_lines), (
            f"Found forbidden import: {forbidden}"
        )


# ---------------------------------------------------------------------------
# 15. no score output contains forbidden factual fields.
# ---------------------------------------------------------------------------


def test_no_scores_in_report_contain_forbidden_factual_fields() -> None:
    service = CandidateQualityService()
    planning_state = _planning_state_with_context(
        candidate_pois=[_place("City Museum", "museum")],
        candidate_restaurants=[_place("Downtown Cafe", "cafe")],
        candidate_accommodation_pois=[_place("Riverside Hotel", "hotel")],
    )

    report = service.build_report(planning_state)

    for score in (
        *report.attraction_scores,
        *report.restaurant_scores,
        *report.accommodation_poi_scores,
    ):
        field_names = set(score.model_dump().keys())
        assert field_names & _FORBIDDEN_FACTUAL_FIELD_NAMES == set()
