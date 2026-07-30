from __future__ import annotations

from typing import Any

from app.models.planning_state import (
    DestinationContext,
    PlanningState,
    TravelGroupType,
    TripRequest,
)
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.plan_validator_service import PlanValidatorService

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
    *,
    must_visit: list[str] | None = None,
    destination_name: str = "Lisbon, Portugal",
    start_date: str = "2026-08-10",
    end_date: str = "2026-08-12",
) -> PlanningState:
    trip_request = TripRequest(
        primary_destination=destination_name,
        start_date=start_date,
        end_date=end_date,
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
        must_visit=must_visit or [],
    )
    planning_state = PlanningState(trip_request=trip_request)
    planning_state.destination_context = DestinationContext(
        destination_name=destination_name,
        candidate_pois=candidate_pois or [],
    )
    planning_state.candidate_quality_report = CandidateQualityService().build_report(
        planning_state
    )
    return planning_state


def _run_planner_then_validator(planning_state: PlanningState) -> PlanningState:
    ExperiencePlannerService().run(planning_state)
    PlanValidatorService().run(planning_state)
    return planning_state


def _scheduled_names(planning_state: PlanningState) -> list[str]:
    return [
        experience.name
        for day_plan in planning_state.experience_plan.daily_plans
        for experience in day_plan.experiences
    ]


def _must_visit_warnings(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "must_visit"
    ]


# ---------------------------------------------------------------------------
# Lisbon-style mismatched must-visits: destination_context has Lisbon
# candidates only, but must_visit names Paris landmarks that were never
# grounded to this destination.
# ---------------------------------------------------------------------------


def test_lisbon_plan_surfaces_ungrounded_must_visits_without_substitution() -> None:
    candidates = [
        _place("p1", "Belem Tower", "attraction", lat=38.6916, lng=-9.2160),
        _place("p2", "Lisbon Cathedral", "attraction", lat=38.7095, lng=-9.1332),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Eiffel Tower", "Louvre Museum"],
        destination_name="Lisbon, Portugal",
    )

    _run_planner_then_validator(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    # Neither ungrounded must-visit is scheduled, and no unrelated
    # attraction is substituted in their place -- only the real Lisbon
    # candidates ever appear.
    assert "Eiffel Tower" not in scheduled_names
    assert "Louvre Museum" not in scheduled_names
    assert set(scheduled_names) == {"Belem Tower", "Lisbon Cathedral"}

    must_visit_warnings = _must_visit_warnings(planning_state)
    assert len(must_visit_warnings) == 1
    message = must_visit_warnings[0].message
    assert "Eiffel Tower" in message
    assert "Louvre Museum" in message
    assert "not grounded/scheduled" in message
    assert "not replaced with unrelated attractions" in message

    # A failed must-visit never claims the plan is ready.
    assert planning_state.validation_report.readiness_status.value in {
        "needs_review",
        "blocked",
    }
    # Real candidates were scheduled here, so this specific plan is
    # needs_review, not blocked -- the failed must-visit is a warning only.
    assert planning_state.validation_report.readiness_status.value == "needs_review"


# ---------------------------------------------------------------------------
# Matched must-visit: no failed-must-visit warning is produced.
# ---------------------------------------------------------------------------


def test_matched_must_visit_produces_no_warning() -> None:
    candidates = [
        _place("p1", "Empire State Building", "attraction", lat=40.7484, lng=-73.9857),
        _place("p2", "Central Park", "park", lat=40.7851, lng=-73.9683),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Empire State Building"],
        destination_name="New York",
    )

    _run_planner_then_validator(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    assert "Empire State Building" in scheduled_names
    assert _must_visit_warnings(planning_state) == []


# ---------------------------------------------------------------------------
# Severe rejected must-visit: candidate exists but is excluded from
# scheduling by candidate quality (missing coordinates), so it still needs
# to surface a warning even though it *is* a real, grounded candidate.
# ---------------------------------------------------------------------------


def test_severe_rejected_must_visit_still_surfaces_warning() -> None:
    candidates = [
        _place("p1", "Ungrounded Fort", "attraction", lat=None, lng=None),
        _place("p2", "City Museum", "museum", lat=38.7, lng=-9.15),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Ungrounded Fort"],
    )

    _run_planner_then_validator(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    # The candidate exists in candidate_pois (it was "found" in the older
    # sense) but is rejected by CandidateQualityService for missing
    # coordinates, so ExperiencePlannerService never schedules it.
    reject_score = next(
        score
        for score in planning_state.candidate_quality_report.attraction_scores
        if score.candidate_name == "Ungrounded Fort"
    )
    assert reject_score.quality_tier.value == "rejected"
    assert "Ungrounded Fort" not in scheduled_names
    assert "City Museum" in scheduled_names

    must_visit_warnings = _must_visit_warnings(planning_state)
    assert len(must_visit_warnings) == 1
    assert "Ungrounded Fort" in must_visit_warnings[0].message
    assert planning_state.validation_report.readiness_status.value != "ready"


# ---------------------------------------------------------------------------
# No forbidden factual fields appear anywhere in the validation report.
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


def test_validation_report_has_no_forbidden_factual_fields() -> None:
    candidates = [
        _place("p1", "Belem Tower", "attraction", lat=38.6916, lng=-9.2160),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Eiffel Tower"],
    )

    _run_planner_then_validator(planning_state)
    report_dump = planning_state.validation_report.model_dump(mode="json")

    _assert_no_forbidden_fields(report_dump)
