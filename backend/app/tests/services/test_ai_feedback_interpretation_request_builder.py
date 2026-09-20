from __future__ import annotations

from datetime import date
from typing import Any

from app.models.planning_state import (
    DailyPlan,
    ExperiencePlan,
    ExperienceItem,
    PlanningState,
    TravelGroupType,
    TripRequest,
    UserLock,
    ValidationIssue,
    ValidationReport,
)
from app.models.common import ValidationSeverity
from app.services.ai_feedback_interpretation_request_builder import (
    AIFeedbackInterpretationRequestBuilder,
)

# Tests for the Section 196 request builder (docs/14_backend_architecture.md,
# following section 146). No provider/LLM call anywhere here -- every
# PlanningState fixture is constructed directly, deterministically.


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Lisbon, Portugal",
        "start_date": "2026-09-10",
        "end_date": "2026-09-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
        "interests": ["history"],
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _experience(
    experience_id: str,
    name: str,
    day_number: int,
    *,
    provider_place_id: str | None = None,
    provider_source: str | None = None,
    promoted_from_ai: bool = False,
) -> ExperienceItem:
    return ExperienceItem(
        experience_id=experience_id,
        name=name,
        category="attraction",
        day_number=day_number,
        stop_order=1,
        provider_place_id=provider_place_id,
        provider_source=provider_source,
        promoted_from_ai=promoted_from_ai,
    )


def _planning_state() -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request())
    day1 = DailyPlan(
        day_number=1,
        date=date(2026, 9, 10),
        experiences=[_experience("exp_a", "Castelo de Sao Jorge", 1)],
    )
    day2 = DailyPlan(
        day_number=2,
        date=date(2026, 9, 11),
        experiences=[
            _experience(
                "exp_b",
                "Torre de Belem",
                2,
                provider_place_id="way/24341353",
                provider_source="openstreetmap_places",
                promoted_from_ai=True,
            )
        ],
    )
    planning_state.experience_plan = ExperiencePlan(daily_plans=[day1, day2])
    return planning_state


def test_current_items_reflect_final_experience_plan() -> None:
    planning_state = _planning_state()

    request = AIFeedbackInterpretationRequestBuilder().build_request(planning_state, "Remove Belem Tower.")

    assert request.feedback_text == "Remove Belem Tower."
    assert {item.experience_id for item in request.current_items} == {"exp_a", "exp_b"}
    item_b = next(item for item in request.current_items if item.experience_id == "exp_b")
    assert item_b.day_index == 2
    assert item_b.name == "Torre de Belem"


def test_candidate_id_present_only_for_promoted_candidate() -> None:
    planning_state = _planning_state()

    request = AIFeedbackInterpretationRequestBuilder().build_request(planning_state, "feedback")

    item_a = next(item for item in request.current_items if item.experience_id == "exp_a")
    item_b = next(item for item in request.current_items if item.experience_id == "exp_b")
    assert item_a.candidate_id is None
    assert item_b.candidate_id == "openstreetmap_places:way/24341353"


def test_active_locks_are_reflected() -> None:
    planning_state = _planning_state()
    planning_state.user_locks = [
        UserLock(locked_item_type="experience", locked_item_id="exp_a", is_active=True),
        UserLock(locked_item_type="day_plan", locked_item_id="2", is_active=False),
    ]

    request = AIFeedbackInterpretationRequestBuilder().build_request(planning_state, "feedback")

    assert len(request.active_locks) == 1
    assert request.active_locks[0].locked_item_type == "experience"
    assert request.active_locks[0].locked_item_id == "exp_a"


def test_validation_counts_reflect_current_report() -> None:
    planning_state = _planning_state()
    planning_state.validation_report = ValidationReport(
        readiness_status="needs_review",
        warnings=[
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="geographic_spread",
                message="Day 2 is spread out.",
                affected_section="experience_plan.daily_plans[2]",
            )
        ],
    )

    request = AIFeedbackInterpretationRequestBuilder().build_request(planning_state, "feedback")

    assert request.validation_status == "needs_review"
    assert request.warning_count == 1
    assert request.critical_issue_count == 0


def test_no_experience_plan_yields_empty_current_items() -> None:
    planning_state = PlanningState(trip_request=_trip_request())
    assert planning_state.experience_plan is None

    request = AIFeedbackInterpretationRequestBuilder().build_request(planning_state, "feedback")

    assert request.current_items == []


def test_traveler_context_reflects_trip_request_when_no_profile() -> None:
    planning_state = _planning_state()
    assert planning_state.traveler_profile is None

    request = AIFeedbackInterpretationRequestBuilder().build_request(planning_state, "feedback")

    assert request.traveler_context.pace == "balanced"
    assert request.traveler_context.interests == ["history"]


def test_builder_never_mutates_planning_state() -> None:
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)

    AIFeedbackInterpretationRequestBuilder().build_request(planning_state, "Remove Belem Tower.")

    assert planning_state.model_dump() == before.model_dump()
