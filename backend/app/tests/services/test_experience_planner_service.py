from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.ai_candidate_promotion import AICandidatePromotionReport, PromotedAICandidate
from app.models.candidate_quality import CandidateQualityReport
from app.models.common import GeoPoint
from app.models.planning_state import (
    DestinationContext,
    PlanningState,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService

_FORBIDDEN_FACTUAL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
}


def _place(
    place_id: str,
    name: str,
    category: str | None,
    *,
    lat: float | None = 0.0,
    lng: float | None = 0.0,
    confidence: float = 0.6,
    address: str | None = None,
) -> dict[str, Any]:
    return {
        "place_id": place_id,
        "name": name,
        "category": category,
        "coordinates": {"lat": lat, "lng": lng} if lat is not None and lng is not None else None,
        "address": address,
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": confidence,
    }


def _planning_state(
    candidate_pois: list[dict[str, Any]] | None = None,
    candidate_restaurants: list[dict[str, Any]] | None = None,
    candidate_accommodation_pois: list[dict[str, Any]] | None = None,
    *,
    must_visit: list[str] | None = None,
    pace: TripPace = TripPace.BALANCED,
    start_date: str = "2026-08-10",
    end_date: str = "2026-08-10",
    with_quality_report: bool = True,
) -> PlanningState:
    trip_request = TripRequest(
        primary_destination="Testville",
        start_date=start_date,
        end_date=end_date,
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
        pace=pace,
        must_visit=must_visit or [],
    )
    planning_state = PlanningState(trip_request=trip_request)
    planning_state.destination_context = DestinationContext(
        destination_name="Testville",
        candidate_pois=candidate_pois or [],
        candidate_restaurants=candidate_restaurants or [],
        candidate_accommodation_pois=candidate_accommodation_pois or [],
    )
    if with_quality_report:
        planning_state.candidate_quality_report = CandidateQualityService().build_report(
            planning_state
        )
    return planning_state


def _scheduled_names(planning_state: PlanningState) -> list[str]:
    return [
        experience.name
        for day_plan in planning_state.experience_plan.daily_plans
        for experience in day_plan.experiences
    ]


# ---------------------------------------------------------------------------
# 1. Low-priority candidates are not scheduled even when slots are
#    available (Step 156E: trust-over-fullness, never used as filler).
# ---------------------------------------------------------------------------


def test_low_priority_candidate_is_excluded_even_with_available_slots() -> None:
    candidates = [
        _place("p1", "Good Museum", "museum", lat=0.0, lng=0.0),
        _place("p2", "Weak Reservoir", "reservoir", lat=0.0, lng=0.001),
    ]
    # balanced pace caps 3/day, 1 day requested -> 3 slots available, but
    # only 1 non-low-priority candidate exists. Trust-over-fullness means
    # the reservoir is never pulled in as filler -- the day is left lighter
    # instead, with an honest warning explaining why.
    planning_state = _planning_state(
        candidate_pois=candidates,
        pace=TripPace.BALANCED,
        start_date="2026-08-10",
        end_date="2026-08-10",
    )

    ExperiencePlannerService().run(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    assert scheduled_names == ["Good Museum"]
    assert "Weak Reservoir" not in scheduled_names

    day_plan = planning_state.experience_plan.daily_plans[0]
    assert any(
        "low-priority or rejected" in warning and "lighter" in warning
        for warning in day_plan.warnings
    )


def test_weak_category_candidates_are_excluded_regardless_of_geographic_order() -> None:
    candidates = [
        _place("p1", "Empire State Building", "attraction", lat=0.0, lng=0.0),
        _place("p2", "City Museum", "museum", lat=0.0, lng=0.001),
        _place("p3", "Random Historic District", "historic district", lat=0.0, lng=0.002),
        _place("p4", "Reservoir", "reservoir", lat=0.0, lng=0.003),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Empire State Building"],
        pace=TripPace.BALANCED,
        start_date="2026-08-10",
        end_date="2026-08-12",
    )

    ExperiencePlannerService().run(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    # The must-visit and museum candidates (primary_anchor quality) are
    # scheduled; the historic-district/reservoir candidates (demoted to
    # low_priority by CandidateQualityService) are excluded entirely, not
    # just ordered after -- even though there are enough day/pace slots
    # (2 days x 3/day = 6) to have fit all four candidates.
    assert set(scheduled_names) == {"Empire State Building", "City Museum"}
    assert "Random Historic District" not in scheduled_names
    assert "Reservoir" not in scheduled_names


# ---------------------------------------------------------------------------
# 2. Rejected candidates are not scheduled.
# ---------------------------------------------------------------------------


def test_missing_coordinates_candidate_is_not_scheduled() -> None:
    candidates = [
        _place("p1", "Good Museum", "museum", lat=0.0, lng=0.0),
        _place("p2", "No Coordinates Museum", "museum", lat=None, lng=None),
    ]
    planning_state = _planning_state(candidate_pois=candidates)

    ExperiencePlannerService().run(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    assert "Good Museum" in scheduled_names
    assert "No Coordinates Museum" not in scheduled_names


def test_insufficient_confidence_candidate_is_not_scheduled() -> None:
    candidates = [
        _place("p1", "Good Museum", "museum", lat=0.0, lng=0.0, confidence=0.6),
        _place("p2", "Low Confidence Museum", "museum", lat=0.0, lng=0.001, confidence=0.05),
    ]
    planning_state = _planning_state(candidate_pois=candidates)

    ExperiencePlannerService().run(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    assert "Good Museum" in scheduled_names
    assert "Low Confidence Museum" not in scheduled_names


# ---------------------------------------------------------------------------
# 3. Low-priority candidates are never used as fallback filler, regardless
#    of how many day/pace slots would otherwise go unfilled.
# ---------------------------------------------------------------------------


def test_low_priority_candidates_excluded_when_enough_better_candidates_exist() -> None:
    candidates = [
        _place("p1", "First Museum", "museum", lat=0.0, lng=0.0),
        _place("p2", "Second Museum", "museum", lat=0.0, lng=0.001),
        _place("p3", "Weak Reservoir", "reservoir", lat=0.0, lng=0.002),
    ]
    # relaxed pace caps 2/day, 1 day requested -> exactly 2 slots, and there
    # are already 2 non-low-priority candidates, so the low_priority
    # reservoir must not be scheduled at all.
    planning_state = _planning_state(
        candidate_pois=candidates,
        pace=TripPace.RELAXED,
        start_date="2026-08-10",
        end_date="2026-08-10",
    )

    ExperiencePlannerService().run(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    assert set(scheduled_names) == {"First Museum", "Second Museum"}
    assert "Weak Reservoir" not in scheduled_names


def test_low_priority_candidates_not_used_as_fallback_even_when_not_enough_better_candidates() -> (
    None
):
    candidates = [
        _place("p1", "Only Museum", "museum", lat=0.0, lng=0.0),
        _place("p2", "Weak Reservoir", "reservoir", lat=0.0, lng=0.001),
    ]
    # balanced pace caps 3/day, 1 day requested -> 3 slots, and only 1
    # non-low-priority candidate exists to fill them. Trust-over-fullness
    # (Step 156E) means the low_priority reservoir is still never pulled in
    # as filler -- the day is deliberately left lighter instead.
    planning_state = _planning_state(
        candidate_pois=candidates,
        pace=TripPace.BALANCED,
        start_date="2026-08-10",
        end_date="2026-08-10",
    )

    ExperiencePlannerService().run(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    assert scheduled_names == ["Only Museum"]
    assert "Weak Reservoir" not in scheduled_names


# ---------------------------------------------------------------------------
# 4. Grounded must-visit is still scheduled when its quality tier ends up
#    primary_anchor/good_candidate/secondary_candidate; a must-visit that
#    is rejected for a severe reason (e.g. missing coordinates) is not.
# ---------------------------------------------------------------------------


def test_grounded_must_visit_is_scheduled_despite_weak_generic_category() -> None:
    candidates = [
        _place("p1", "Historic Fort", "historic district", lat=0.0, lng=0.0),
        _place("p2", "Unrelated Museum", "museum", lat=0.0, lng=0.001),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Historic Fort"],
    )

    ExperiencePlannerService().run(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    assert "Historic Fort" in scheduled_names
    by_name = {
        experience.name: experience
        for day_plan in planning_state.experience_plan.daily_plans
        for experience in day_plan.experiences
    }
    assert by_name["Historic Fort"].why_included == "Matches your must-visit request."

    # The must-visit override keeps this candidate's quality tier out of
    # low_priority/rejected (docs/18_candidate_quality.md), which is why
    # trust-over-fullness scheduling still includes it despite the generic
    # "historic district" category that would otherwise demote it.
    attraction_score = next(
        score
        for score in planning_state.candidate_quality_report.attraction_scores
        if score.candidate_name == "Historic Fort"
    )
    assert attraction_score.quality_tier.value in {
        "primary_anchor",
        "good_candidate",
        "secondary_candidate",
    }


def test_must_visit_with_missing_coordinates_is_not_scheduled() -> None:
    candidates = [
        _place("p1", "Unrelated Museum", "museum", lat=0.0, lng=0.0),
        _place("p2", "Ungrounded Fort", "historic district", lat=None, lng=None),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Ungrounded Fort"],
    )

    ExperiencePlannerService().run(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    # Missing coordinates is a severe reject reason that the must-visit
    # override does not clear, so this must-visit candidate is never
    # scheduled -- and no unrelated attraction is substituted in its place.
    assert "Ungrounded Fort" not in scheduled_names
    assert "Unrelated Museum" in scheduled_names

    attraction_score = next(
        score
        for score in planning_state.candidate_quality_report.attraction_scores
        if score.candidate_name == "Ungrounded Fort"
    )
    assert attraction_score.quality_tier.value == "rejected"


# ---------------------------------------------------------------------------
# 5. Missing candidate_quality_report preserves old behavior.
# ---------------------------------------------------------------------------


def test_missing_candidate_quality_report_preserves_legacy_scheduling() -> None:
    candidates = [
        _place("p1", "Good Museum", "museum", lat=0.0, lng=0.0),
        _place("p2", "Weak Reservoir", "reservoir", lat=0.0, lng=0.001),
        _place("p3", "No Coordinates Museum", "museum", lat=None, lng=None),
    ]
    planning_state = _planning_state(candidate_pois=candidates, with_quality_report=False)
    assert planning_state.candidate_quality_report is None

    ExperiencePlannerService().run(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    # Without a candidate_quality_report, nothing is filtered/reordered by
    # quality -- every candidate (even the weak reservoir and the
    # coordinate-less one) is scheduled exactly like pre-156C behavior.
    assert set(scheduled_names) == {"Good Museum", "Weak Reservoir", "No Coordinates Museum"}


# ---------------------------------------------------------------------------
# 6. Restaurant suggestions prefer quality when proximity is similar.
# ---------------------------------------------------------------------------


def test_restaurant_suggestions_prefer_quality_when_proximity_is_similar() -> None:
    attraction = _place("anchor", "Anchor Attraction", "landmark", lat=0.0, lng=0.0)
    # Quick Snack Bar is objectively closer to the anchor, but both
    # restaurants sit within the "similar distance" tolerance (well under
    # 0.05km), so the higher-quality "restaurant" category candidate should
    # be preferred over the lower-quality "fast_food" one.
    high_quality_restaurant = _place(
        "r1", "Quality Bistro", "restaurant", lat=0.0, lng=0.00027
    )
    low_quality_restaurant = _place(
        "r2", "Quick Snack Bar", "fast_food", lat=0.0, lng=0.00009
    )

    planning_state = _planning_state(
        candidate_pois=[attraction],
        candidate_restaurants=[high_quality_restaurant, low_quality_restaurant],
    )

    ExperiencePlannerService().run(planning_state)
    suggestions = planning_state.experience_plan.daily_plans[0].restaurant_suggestions
    suggested_names = [suggestion.name for suggestion in suggestions]

    assert suggested_names[0] == "Quality Bistro"


# ---------------------------------------------------------------------------
# 7. Accommodation POI suggestions prefer quality when proximity is similar.
# ---------------------------------------------------------------------------


def test_accommodation_suggestions_prefer_quality_when_proximity_is_similar() -> None:
    attraction = _place("anchor", "Anchor Attraction", "landmark", lat=0.0, lng=0.0)
    # "Nearby Office" is objectively closer to the anchor, but both
    # candidates sit within the "similar distance" tolerance, so the
    # higher-quality "hotel" category candidate should be preferred over
    # the unrecognized "office" category (weak_category-demoted) one.
    high_quality_hotel = _place("a1", "Central Hotel", "hotel", lat=0.0, lng=0.00027)
    low_quality_office = _place("a2", "Nearby Office", "office", lat=0.0, lng=0.00009)

    planning_state = _planning_state(
        candidate_pois=[attraction],
        candidate_accommodation_pois=[high_quality_hotel, low_quality_office],
    )

    ExperiencePlannerService().run(planning_state)
    suggestions = planning_state.experience_plan.daily_plans[0].accommodation_suggestions
    suggested_names = [suggestion.name for suggestion in suggestions]

    assert suggested_names[0] == "Central Hotel"


# ---------------------------------------------------------------------------
# 8. Candidate quality report is not mutated by ExperiencePlannerService.
# ---------------------------------------------------------------------------


def test_run_does_not_mutate_candidate_quality_report() -> None:
    candidates = [
        _place("p1", "Good Museum", "museum", lat=0.0, lng=0.0),
        _place("p2", "Weak Reservoir", "reservoir", lat=0.0, lng=0.001),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        candidate_restaurants=[_place("r1", "Cafe One", "cafe", lat=0.0, lng=0.0005)],
        candidate_accommodation_pois=[_place("a1", "Hotel One", "hotel", lat=0.0, lng=0.0005)],
    )
    assert isinstance(planning_state.candidate_quality_report, CandidateQualityReport)
    before = planning_state.candidate_quality_report.model_dump(mode="json")

    ExperiencePlannerService().run(planning_state)

    after = planning_state.candidate_quality_report.model_dump(mode="json")
    assert before == after


# ---------------------------------------------------------------------------
# 9. No new forbidden factual fields appear in experience_plan.
# ---------------------------------------------------------------------------


def _assert_no_forbidden_fields(node: Any) -> None:
    if isinstance(node, dict):
        overlap = set(node.keys()) & _FORBIDDEN_FACTUAL_FIELD_NAMES
        assert not overlap, f"Forbidden field(s) found: {overlap}"
        for value in node.values():
            _assert_no_forbidden_fields(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_forbidden_fields(item)


def test_experience_plan_has_no_forbidden_factual_fields() -> None:
    candidates = [
        _place("p1", "Good Museum", "museum", lat=0.0, lng=0.0),
        _place("p2", "Weak Reservoir", "reservoir", lat=0.0, lng=0.001),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        candidate_restaurants=[_place("r1", "Cafe One", "cafe", lat=0.0, lng=0.0005)],
        candidate_accommodation_pois=[_place("a1", "Hotel One", "hotel", lat=0.0, lng=0.0005)],
    )

    ExperiencePlannerService().run(planning_state)
    plan_dump = planning_state.experience_plan.model_dump(mode="json")

    _assert_no_forbidden_fields(plan_dump)


# ---------------------------------------------------------------------------
# Step 170D: safe scheduling integration for promoted AI candidates.
# ---------------------------------------------------------------------------


def _promoted_candidate(
    candidate_id: str,
    name: str,
    *,
    provider_place_id: str | None = None,
    provider_source: str = "openstreetmap_places",
    quality_bucket: str = "good_candidate",
    lat: float | None = 0.0,
    lng: float | None = 0.0,
    confidence: float | None = 0.6,
    category: str | None = "attraction",
) -> PromotedAICandidate:
    return PromotedAICandidate(
        candidate_id=f"promoted_{candidate_id}",
        name=name,
        category=category,
        provider_place_id=provider_place_id,
        provider_source=provider_source,
        original_ai_candidate_id=candidate_id,
        quality_bucket=quality_bucket,
        grounding_status="exact_name",
        coordinates=GeoPoint(lat=lat, lng=lng) if lat is not None and lng is not None else None,
        confidence=confidence,
        data_status="live",
        promotion_reasons=["Quality tier 'good_candidate' is acceptable for scheduling."],
    )


def _promotion_report(*candidates: PromotedAICandidate, trip_id: str = "trip_test") -> AICandidatePromotionReport:
    return AICandidatePromotionReport(
        trip_id=trip_id,
        status="promoted" if candidates else "no_eligible_candidates",
        total_reviewed_candidates=len(candidates),
        promoted_count=len(candidates),
        skipped_count=0,
        promoted_candidates=list(candidates),
        skipped_candidate_ids=[],
        generated_at=datetime.now(timezone.utc),
    )


# 1. No promotion report means itinerary output is unchanged.
def test_no_promotion_report_leaves_itinerary_unchanged() -> None:
    candidates = [_place("p1", "Real Museum", "museum", lat=0.0, lng=0.0)]
    baseline_state = _planning_state(candidate_pois=candidates)
    ExperiencePlannerService().run(baseline_state)
    baseline_dump = baseline_state.experience_plan.model_dump(
        mode="json", exclude={"experience_plan_id", "daily_plans"}
    )
    baseline_names = _scheduled_names(baseline_state)

    with_none_state = _planning_state(candidate_pois=candidates)
    assert with_none_state.ai_candidate_promotion_report is None
    ExperiencePlannerService().run(with_none_state)

    assert _scheduled_names(with_none_state) == baseline_names
    assert with_none_state.experience_plan.model_dump(
        mode="json", exclude={"experience_plan_id", "daily_plans"}
    ) == baseline_dump


# 2. Empty promotion report means itinerary output is unchanged.
def test_empty_promotion_report_leaves_itinerary_unchanged() -> None:
    candidates = [_place("p1", "Real Museum", "museum", lat=0.0, lng=0.0)]
    baseline_state = _planning_state(candidate_pois=candidates)
    ExperiencePlannerService().run(baseline_state)
    baseline_names = _scheduled_names(baseline_state)

    empty_report_state = _planning_state(candidate_pois=candidates)
    empty_report_state.ai_candidate_promotion_report = _promotion_report()
    ExperiencePlannerService().run(empty_report_state)

    assert _scheduled_names(empty_report_state) == baseline_names


# 3. Promoted eligible provider-grounded candidate can appear in the
#    itinerary scheduling pool when there is room for it.
def test_promoted_candidate_can_appear_in_schedule_when_room_available() -> None:
    candidates = [_place("p1", "Real Museum", "museum", lat=0.0, lng=0.0)]
    planning_state = _planning_state(candidate_pois=candidates, pace=TripPace.RELAXED)
    planning_state.ai_candidate_promotion_report = _promotion_report(
        _promoted_candidate("proposal_1", "Promoted Landmark", provider_place_id="osm/way/999", lat=0.0, lng=0.01)
    )

    ExperiencePlannerService().run(planning_state)

    assert "Promoted Landmark" in _scheduled_names(planning_state)


# 4. Scheduled promoted candidate preserves promoted_from_ai/source
#    metadata.
def test_scheduled_promoted_candidate_preserves_provenance_metadata() -> None:
    candidates = [_place("p1", "Real Museum", "museum", lat=0.0, lng=0.0)]
    planning_state = _planning_state(candidate_pois=candidates, pace=TripPace.RELAXED)
    planning_state.ai_candidate_promotion_report = _promotion_report(
        _promoted_candidate(
            "proposal_1",
            "Promoted Landmark",
            provider_place_id="osm/way/999",
            provider_source="openstreetmap_places",
            lat=0.0,
            lng=0.01,
        )
    )

    ExperiencePlannerService().run(planning_state)

    promoted_experience = next(
        experience
        for day_plan in planning_state.experience_plan.daily_plans
        for experience in day_plan.experiences
        if experience.name == "Promoted Landmark"
    )
    assert promoted_experience.promoted_from_ai is True
    assert promoted_experience.original_ai_candidate_id == "proposal_1"
    assert promoted_experience.provider_place_id == "osm/way/999"
    assert promoted_experience.provider_source == "openstreetmap_places"

    # A real (non-promoted) experience must not carry these markers.
    real_experience = next(
        experience
        for day_plan in planning_state.experience_plan.daily_plans
        for experience in day_plan.experiences
        if experience.name == "Real Museum"
    )
    assert real_experience.promoted_from_ai is False
    assert real_experience.original_ai_candidate_id is None
    assert real_experience.provider_place_id is None
    assert real_experience.provider_source is None


# 11. Duplicate provider_place_id/name is not scheduled twice.
def test_promoted_duplicate_of_real_candidate_is_not_scheduled_twice() -> None:
    candidates = [_place("osm/way/1", "Real Museum", "museum", lat=0.0, lng=0.0)]
    planning_state = _planning_state(candidate_pois=candidates, pace=TripPace.PACKED)
    planning_state.ai_candidate_promotion_report = _promotion_report(
        _promoted_candidate("proposal_1", "Real Museum", provider_place_id="osm/way/1", lat=0.0, lng=0.0)
    )

    ExperiencePlannerService().run(planning_state)

    names = _scheduled_names(planning_state)
    assert names.count("Real Museum") == 1


def test_promoted_duplicate_by_name_is_not_scheduled_twice() -> None:
    """Same rule, but the duplicate is detected by normalized name rather
    than a shared provider_place_id (e.g. a promoted candidate whose
    provider_place_id wasn't carried over for some reason)."""
    candidates = [_place("osm/way/1", "Real Museum", "museum", lat=0.0, lng=0.0)]
    planning_state = _planning_state(candidate_pois=candidates, pace=TripPace.PACKED)
    planning_state.ai_candidate_promotion_report = _promotion_report(
        _promoted_candidate("proposal_1", "Real Museum", provider_place_id=None, lat=0.0, lng=0.0)
    )

    ExperiencePlannerService().run(planning_state)

    names = _scheduled_names(planning_state)
    assert names.count("Real Museum") == 1


# 10. Candidate with missing required scheduling fields (coordinates) is
#     skipped, not completed with fake data.
def test_promoted_candidate_missing_coordinates_is_skipped_with_warning() -> None:
    candidates = [_place("p1", "Real Museum", "museum", lat=0.0, lng=0.0)]
    planning_state = _planning_state(candidate_pois=candidates, pace=TripPace.RELAXED)
    planning_state.ai_candidate_promotion_report = _promotion_report(
        _promoted_candidate("proposal_1", "Coordinateless Landmark", lat=None, lng=None)
    )

    ExperiencePlannerService().run(planning_state)

    assert "Coordinateless Landmark" not in _scheduled_names(planning_state)
    assert any(
        "Coordinateless Landmark" in assumption and "missing" in assumption.lower()
        for assumption in planning_state.experience_plan.assumptions
    )


# Promoted candidates must not bypass geographic scheduling rules or bump
# an already-grounded, geographically-closer real candidate out of a slot
# it would otherwise win.
def test_promoted_candidate_does_not_replace_a_naturally_closer_real_candidate() -> None:
    candidates = [
        _place("p1", "Anchor Museum", "museum", lat=0.0, lng=0.0),
        _place("p2", "Nearby Gallery", "museum", lat=0.0, lng=0.001),
    ]
    planning_state = _planning_state(candidate_pois=candidates, pace=TripPace.RELAXED)
    # Pace RELAXED caps this single day at 2 slots -- both real candidates
    # already fill it. The promoted candidate is far away geographically,
    # so it must not bump "Nearby Gallery" out of the remaining slot.
    planning_state.ai_candidate_promotion_report = _promotion_report(
        _promoted_candidate("proposal_1", "Far Away Landmark", lat=10.0, lng=10.0)
    )

    ExperiencePlannerService().run(planning_state)

    names = _scheduled_names(planning_state)
    assert "Anchor Museum" in names
    assert "Nearby Gallery" in names
    assert "Far Away Landmark" not in names


# 13. Existing provider-grounded OSM candidates still schedule as before
#     when a promotion report exists but adds nothing new.
def test_real_candidates_schedule_unaffected_by_unrelated_promotion_report() -> None:
    candidates = [
        _place("p1", "Museum One", "museum", lat=0.0, lng=0.0),
        _place("p2", "Museum Two", "museum", lat=0.0, lng=0.001),
    ]
    baseline_state = _planning_state(candidate_pois=candidates)
    ExperiencePlannerService().run(baseline_state)
    baseline_names = _scheduled_names(baseline_state)

    with_promotion_state = _planning_state(candidate_pois=candidates)
    with_promotion_state.ai_candidate_promotion_report = _promotion_report(
        _promoted_candidate("proposal_1", "Museum One", provider_place_id="p1", lat=0.0, lng=0.0)
    )
    ExperiencePlannerService().run(with_promotion_state)

    assert _scheduled_names(with_promotion_state) == baseline_names


# 12. No new forbidden factual fields (coordinates/rating/etc. are never
#     invented) are introduced by a promoted candidate's ExperienceItem.
def test_promoted_candidate_experience_item_has_no_forbidden_factual_fields() -> None:
    candidates = [_place("p1", "Real Museum", "museum", lat=0.0, lng=0.0)]
    planning_state = _planning_state(candidate_pois=candidates, pace=TripPace.RELAXED)
    planning_state.ai_candidate_promotion_report = _promotion_report(
        _promoted_candidate("proposal_1", "Promoted Landmark", provider_place_id="osm/way/999", lat=0.0, lng=0.01)
    )

    ExperiencePlannerService().run(planning_state)
    plan_dump = planning_state.experience_plan.model_dump(mode="json")

    _assert_no_forbidden_fields(plan_dump)


# ---------------------------------------------------------------------------
# Step 172A: stable itinerary ordering metadata (day_number/stop_order) is
# stamped by initial scheduling, and route_aware_provenance stays honestly
# unset until an actual route-aware reorder happens (never this service's
# job to set it).
# ---------------------------------------------------------------------------


def test_initial_scheduling_stamps_day_number_and_stop_order() -> None:
    candidates = [
        _place("p1", "Museum One", "museum", lat=0.0, lng=0.0),
        _place("p2", "Museum Two", "museum", lat=0.0, lng=0.001),
        _place("p3", "Museum Three", "museum", lat=0.0, lng=0.002),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        pace=TripPace.PACKED,
        start_date="2026-08-10",
        end_date="2026-08-11",
    )

    ExperiencePlannerService().run(planning_state)

    for day_plan in planning_state.experience_plan.daily_plans:
        for stop_index, experience in enumerate(day_plan.experiences, start=1):
            assert experience.day_number == day_plan.day_number
            assert experience.stop_order == stop_index
            # Never this service's job to claim route-aware provenance --
            # only RouteAwareSequencingService.apply_report ever sets this.
            assert experience.route_aware_provenance is None


def test_empty_day_has_no_experiences_to_stamp() -> None:
    """No candidates at all -- every day is empty, so there is nothing to
    stamp, and this never raises."""
    planning_state = _planning_state(candidate_pois=[])

    ExperiencePlannerService().run(planning_state)

    for day_plan in planning_state.experience_plan.daily_plans:
        assert day_plan.experiences == []


# ---------------------------------------------------------------------------
# 9. Stay-area guidance anchor cap (Step 182D: raised from 3 to 5 so the
#    frontend's trip-level "Where to stay" section has enough real
#    candidates to show 4-5 cards without falling back to a shorter list).
# ---------------------------------------------------------------------------


def test_stay_area_guidance_returns_at_most_five_anchors() -> None:
    attraction = _place("anchor", "Anchor Museum", "museum", lat=0.0, lng=0.0)
    # 7 coordinate-backed accommodation POI candidates at increasing
    # distance from the one scheduled attraction -- more than the cap, so
    # this proves the cap is enforced rather than merely "usually small".
    accommodations = [
        _place(f"acc{i}", f"Stay Option {i}", "guest_house", lat=0.0, lng=0.01 * i)
        for i in range(1, 8)
    ]
    planning_state = _planning_state(
        candidate_pois=[attraction],
        candidate_accommodation_pois=accommodations,
    )

    ExperiencePlannerService().run(planning_state)

    guidance = planning_state.experience_plan.stay_area_guidance
    assert len(guidance.suggested_anchor_accommodation_pois) == 5
    # The 5 returned are the 5 closest to the anchor -- Stay Option 6 and 7
    # (the two farthest) must not appear.
    returned_names = {poi.name for poi in guidance.suggested_anchor_accommodation_pois}
    assert "Stay Option 6" not in returned_names
    assert "Stay Option 7" not in returned_names
    # No fabricated lodging fact ever appears on a stay-area suggestion --
    # it is a location candidate only.
    for poi in guidance.suggested_anchor_accommodation_pois:
        assert not hasattr(poi, "price")
        assert not hasattr(poi, "rating")
        assert not hasattr(poi, "booking_url")


def test_stay_area_guidance_returns_fewer_than_five_when_fewer_candidates_exist() -> None:
    attraction = _place("anchor", "Anchor Museum", "museum", lat=0.0, lng=0.0)
    accommodations = [
        _place("acc1", "Stay Option 1", "guest_house", lat=0.0, lng=0.01),
        _place("acc2", "Stay Option 2", "guest_house", lat=0.0, lng=0.02),
    ]
    planning_state = _planning_state(
        candidate_pois=[attraction],
        candidate_accommodation_pois=accommodations,
    )

    ExperiencePlannerService().run(planning_state)

    guidance = planning_state.experience_plan.stay_area_guidance
    assert len(guidance.suggested_anchor_accommodation_pois) == 2
