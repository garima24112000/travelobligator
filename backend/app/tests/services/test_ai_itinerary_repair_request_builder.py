from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
)
from app.models.ai_itinerary_repair import RepairableIssueType
from app.models.candidate_quality import CandidateQualityReport, CandidateQualityScore, CandidateQualityTier, CandidateUseCase
from app.models.common import ProviderStatus, ValidationSeverity
from app.models.planning_state import (
    DailyPlan,
    DestinationContext,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripRequest,
    ValidationIssue,
    ValidationReport,
)
from app.models.routing import (
    BufferSufficiencyStatus,
    RouteFeasibilityReport,
    RouteFeasibilityStatus,
    RouteLegFeasibility,
    TravelTimeBuffer,
    TravelTimeBufferReport,
    TravelTimeBufferStatus,
)
from app.services.ai_itinerary_repair_request_builder import (
    AIItineraryRepairRequestBuilder,
    classify_repairable_issues,
)

# Tests for the Section 194A repair request builder (docs/14_backend_
# architecture.md, following section 143). No provider/LLM call is made
# anywhere here -- every PlanningState fixture is constructed directly,
# deterministically, exactly like Section 193A's own
# test_ai_itinerary_reasoning_request_builder.py.


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Lisbon, Portugal",
        "start_date": "2026-09-10",
        "end_date": "2026-09-11",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _poi(place_id: str, name: str, *, lat: float = 38.7, lng: float = -9.1) -> dict[str, Any]:
    return {
        "place_id": place_id,
        "name": name,
        "category": "attraction",
        "coordinates": {"lat": lat, "lng": lng},
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": 0.8,
    }


def _quality_score(candidate_id: str, name: str) -> CandidateQualityScore:
    return CandidateQualityScore(
        candidate_id=candidate_id,
        candidate_name=name,
        use_case=CandidateUseCase.ATTRACTION,
        quality_tier=CandidateQualityTier.GOOD_CANDIDATE,
        total_score=0.6,
    )


def _experience(experience_id: str, name: str, day_number: int, stop_order: int) -> ExperienceItem:
    return ExperienceItem(
        experience_id=experience_id,
        name=name,
        category="attraction",
        day_number=day_number,
        stop_order=stop_order,
    )


def _base_planning_state(*, with_experience_plan: bool = True) -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request())
    candidate_ids = ["way/A", "way/B", "way/C", "way/D", "way/E"]
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_pois=[_poi(cid, cid.split("/")[-1]) for cid in candidate_ids],
    )
    planning_state.candidate_quality_report = CandidateQualityReport(
        destination_name="Lisbon, Portugal",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[_quality_score(cid, cid.split("/")[-1]) for cid in candidate_ids],
        restaurant_scores=[],
        accommodation_poi_scores=[],
    )
    planning_state.ai_itinerary_reasoning_result = AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.COMPLETED,
        strategy=ItineraryReasoningStrategy(summary="s", pace="balanced", reason="r"),
        days=[
            ItineraryReasoningDayPlan(
                day_index=1,
                candidate_ids=["openstreetmap_places:way/A", "openstreetmap_places:way/B"],
                rationale="Day one.",
            ),
            ItineraryReasoningDayPlan(
                day_index=2,
                candidate_ids=[
                    "openstreetmap_places:way/C",
                    "openstreetmap_places:way/D",
                    "openstreetmap_places:way/E",
                ],
                rationale="Day two, overloaded.",
            ),
        ],
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        confidence=0.8,
    )
    if with_experience_plan:
        day1 = DailyPlan(
            day_number=1,
            date=date(2026, 9, 10),
            experiences=[_experience("exp_a", "A", 1, 1), _experience("exp_b", "B", 1, 2)],
        )
        day2 = DailyPlan(
            day_number=2,
            date=date(2026, 9, 11),
            experiences=[
                _experience("exp_c", "C", 2, 1),
                _experience("exp_d", "D", 2, 2),
                _experience("exp_e", "E", 2, 3),
            ],
        )
        planning_state.experience_plan = ExperiencePlan(daily_plans=[day1, day2])
    return planning_state


def _with_geographic_spread_issue(planning_state: PlanningState) -> PlanningState:
    planning_state.validation_report = ValidationReport(
        warnings=[
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="geographic_spread",
                message="Day 2's scheduled experiences are geographically spread out.",
                affected_section="experience_plan.daily_plans[2]",
            )
        ],
    )
    return planning_state


def _with_route_needs_review(planning_state: PlanningState) -> PlanningState:
    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.PARTIAL,
        provider="osrm",
        route_data_source="osrm",
        legs=[
            RouteLegFeasibility(
                from_experience_id="exp_c",
                from_experience_name="C",
                to_experience_id="exp_d",
                to_experience_name="D",
                provider="osrm",
                status=ProviderStatus.FAILED,
                feasibility_status=RouteFeasibilityStatus.NEEDS_REVIEW,
                message="No usable route was found between C and D.",
            )
        ],
    )
    return planning_state


def _with_insufficient_buffer(planning_state: PlanningState) -> PlanningState:
    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.PARTIAL,
        buffers=[
            TravelTimeBuffer(
                from_experience_id="exp_d",
                from_experience_name="D",
                to_experience_id="exp_e",
                to_experience_name="E",
                provider="osrm",
                status=TravelTimeBufferStatus.SUCCESS,
                route_duration_seconds=3600.0,
                available_gap_seconds=600.0,
                recommended_buffer_seconds=3600.0,
                buffer_status=BufferSufficiencyStatus.INSUFFICIENT,
                message="Travel time from D to E exceeds the available schedule gap.",
            )
        ],
    )
    return planning_state


# ---------------------------------------------------------------------------
# Task 19: repairable issue -> included; non-repairable -> excluded.
# ---------------------------------------------------------------------------


def test_geographic_spread_issue_is_classified_as_repairable() -> None:
    planning_state = _with_geographic_spread_issue(_base_planning_state())

    issues = classify_repairable_issues(planning_state)

    assert len(issues) == 1
    assert issues[0].issue_type == RepairableIssueType.GEOGRAPHIC_SPREAD
    assert issues[0].day_index == 2
    assert issues[0].source_category == "geographic_spread"


def test_route_needs_review_leg_is_classified_as_repairable() -> None:
    planning_state = _with_route_needs_review(_base_planning_state())

    issues = classify_repairable_issues(planning_state)

    assert len(issues) == 1
    assert issues[0].issue_type == RepairableIssueType.ROUTE_NEEDS_REVIEW
    assert issues[0].day_index == 2


def test_insufficient_travel_buffer_is_classified_as_repairable() -> None:
    planning_state = _with_insufficient_buffer(_base_planning_state())

    issues = classify_repairable_issues(planning_state)

    assert len(issues) == 1
    assert issues[0].issue_type == RepairableIssueType.INSUFFICIENT_TRAVEL_BUFFER
    assert issues[0].day_index == 2


def test_non_repairable_validator_categories_are_excluded() -> None:
    planning_state = _base_planning_state()
    planning_state.validation_report = ValidationReport(
        warnings=[
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="budget",
                message="A budget was captured but not validated.",
                affected_section="experience_plan",
            ),
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="must_visit",
                message="A must-visit place was not scheduled.",
                affected_section="experience_plan",
            ),
        ],
    )

    issues = classify_repairable_issues(planning_state)

    assert issues == []


def test_not_connected_route_leg_is_not_classified_as_repairable() -> None:
    """Section 194B correction: `feasibility_status == NEEDS_REVIEW`
    alone must not trigger repair when the underlying `status` is
    `not_connected` -- that means "no routing provider is configured"
    (this app's own documented default), not a detected problem."""
    planning_state = _base_planning_state()
    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.NOT_CONNECTED,
        provider="not_connected",
        route_data_source="not_connected",
        legs=[
            RouteLegFeasibility(
                from_experience_id="exp_c",
                from_experience_name="C",
                to_experience_id="exp_d",
                to_experience_name="D",
                provider="not_connected",
                status=ProviderStatus.NOT_CONNECTED,
                feasibility_status=RouteFeasibilityStatus.NEEDS_REVIEW,
                message="No routing provider is connected.",
            )
        ],
    )

    assert classify_repairable_issues(planning_state) == []


def test_feasible_route_leg_is_not_classified_as_repairable() -> None:
    planning_state = _base_planning_state()
    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.SUCCESS,
        provider="osrm",
        route_data_source="osrm",
        legs=[
            RouteLegFeasibility(
                from_experience_id="exp_c",
                from_experience_name="C",
                to_experience_id="exp_d",
                to_experience_name="D",
                provider="osrm",
                status=ProviderStatus.SUCCESS,
                feasibility_status=RouteFeasibilityStatus.FEASIBLE,
            )
        ],
    )

    assert classify_repairable_issues(planning_state) == []


def test_sufficient_travel_buffer_is_not_classified_as_repairable() -> None:
    planning_state = _base_planning_state()
    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.SUCCESS,
        buffers=[
            TravelTimeBuffer(
                from_experience_id="exp_d",
                from_experience_name="D",
                to_experience_id="exp_e",
                to_experience_name="E",
                provider="osrm",
                status=TravelTimeBufferStatus.SUCCESS,
                route_duration_seconds=300.0,
                available_gap_seconds=3600.0,
                recommended_buffer_seconds=300.0,
                buffer_status=BufferSufficiencyStatus.SUFFICIENT,
            )
        ],
    )

    assert classify_repairable_issues(planning_state) == []


def test_mixed_issues_only_supported_ones_become_repair_instructions() -> None:
    planning_state = _base_planning_state()
    planning_state.validation_report = ValidationReport(
        warnings=[
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="geographic_spread",
                message="Day 2 is spread out.",
                affected_section="experience_plan.daily_plans[2]",
            ),
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="weather",
                message="Weather data is unavailable.",
                affected_section="experience_plan",
            ),
        ],
    )
    planning_state = _with_insufficient_buffer(planning_state)

    issues = classify_repairable_issues(planning_state)

    assert {issue.issue_type for issue in issues} == {
        RepairableIssueType.GEOGRAPHIC_SPREAD,
        RepairableIssueType.INSUFFICIENT_TRAVEL_BUFFER,
    }
    assert all(issue.day_index == 2 for issue in issues)


# ---------------------------------------------------------------------------
# Task 20: affected-day targeting.
# ---------------------------------------------------------------------------


def test_build_request_scopes_affected_days_to_only_the_days_with_issues() -> None:
    planning_state = _with_geographic_spread_issue(_base_planning_state())

    request = AIItineraryRepairRequestBuilder().build_request(planning_state)

    assert request is not None
    assert request.affected_days == [2]
    assert all(issue.day_index == 2 for issue in request.issues)


def test_build_request_preserves_original_days_and_candidate_universe() -> None:
    planning_state = _with_geographic_spread_issue(_base_planning_state())

    request = AIItineraryRepairRequestBuilder().build_request(planning_state)

    assert request is not None
    assert {day.day_index for day in request.original_days} == {1, 2}
    assert request.allowed_candidate_ids() == {
        "openstreetmap_places:way/A",
        "openstreetmap_places:way/B",
        "openstreetmap_places:way/C",
        "openstreetmap_places:way/D",
        "openstreetmap_places:way/E",
    }


# ---------------------------------------------------------------------------
# Task 13/18: no repairable issues / no reasoning result / no candidates
# -> builder returns None (do not call the LLM).
# ---------------------------------------------------------------------------


def test_build_request_returns_none_when_no_reasoning_result_exists() -> None:
    planning_state = _base_planning_state()
    planning_state.ai_itinerary_reasoning_result = None

    assert AIItineraryRepairRequestBuilder().build_request(planning_state) is None


def test_build_request_returns_none_when_reasoning_result_is_not_completed() -> None:
    planning_state = _base_planning_state()
    planning_state.ai_itinerary_reasoning_result = AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.NOT_CONNECTED,
        days=[],
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=["disabled"]),
        confidence=0.0,
    )

    assert AIItineraryRepairRequestBuilder().build_request(planning_state) is None


def test_build_request_returns_none_when_no_repairable_issues_exist() -> None:
    planning_state = _base_planning_state()  # no validation/route/buffer reports set

    assert AIItineraryRepairRequestBuilder().build_request(planning_state) is None


def test_build_request_returns_none_when_no_candidates_are_available() -> None:
    planning_state = _with_geographic_spread_issue(_base_planning_state())
    planning_state.destination_context = None
    planning_state.candidate_quality_report = None

    assert AIItineraryRepairRequestBuilder().build_request(planning_state) is None
