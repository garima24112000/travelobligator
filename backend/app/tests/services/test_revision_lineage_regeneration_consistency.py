from __future__ import annotations

import pytest

from datetime import date
from typing import Any

from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
    RemoveExperienceAction,
)
from app.models.common import RegenerationStrategy
from app.models.planning_state import (
    DailyPlan,
    ExperiencePlan,
    ExperienceItem,
    FeedbackEvent,
    PlanningState,
    TravelGroupType,
    TravelerProfile,
    TripPace,
    TripRequest,
)
from app.models.targeted_regeneration_execution import (
    TargetedRegenerationExecutionResult,
    TargetedRegenerationExecutionStatus,
)
from app.models.targeted_regeneration_plan import (
    TargetedExperienceRemoval,
    TargetedRegenerationPlan,
    TargetedRegenerationPlanStatus,
)
from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository
from app.services.regeneration_mutation_service import apply_regeneration_mutation
from app.services.revision_lineage_service import revision_lineage_service
from app.services.targeted_regeneration_application_service import (
    TargetedRegenerationApplicationService,
)
from app.services.versioning_service import versioning_service

# Section 199A (Task 33): proves the real 199A revision-lineage foundation
# stays byte-for-byte in agreement with the real legacy/targeted
# regeneration paths' own version bookkeeping -- exercised through the
# REAL `TargetedRegenerationApplicationService`/`apply_regeneration_
# mutation`/`revision_lineage_service` singletons (only the AI
# interpreter/plan-builder/executor are fakes, exactly matching
# `test_targeted_regeneration_application_service.py`'s own convention),
# never a hand-simulated shortcut.


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Porto, Portugal",
        "start_date": "2026-09-10",
        "end_date": "2026-09-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
        "interests": ["history"],
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _experience(experience_id: str, name: str, day_number: int, **overrides: Any) -> ExperienceItem:
    fields: dict[str, Any] = dict(
        experience_id=experience_id, name=name, category="attraction", day_number=day_number, stop_order=1
    )
    fields.update(overrides)
    return ExperienceItem(**fields)


def _planning_state(trip_id: str) -> PlanningState:
    planning_state = PlanningState(trip_id=trip_id, trip_request=_trip_request())
    day1 = DailyPlan(day_number=1, date=date(2026, 9, 10), experiences=[_experience("exp_A", "Museum A", 1)])
    day2 = DailyPlan(
        day_number=2,
        date=date(2026, 9, 11),
        experiences=[_experience("exp_C", "Tower C", 2)],
    )
    planning_state.experience_plan = ExperiencePlan(daily_plans=[day1, day2])
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE, travelers_count=2, pace=TripPace.BALANCED, interests=["history"]
    )
    versioning_service.create_initial_version(planning_state)
    return planning_state


class _FakeRepository:
    """Mirrors `test_targeted_regeneration_application_service.py`'s own
    `_FakeRepository` exactly -- only `planning_state_repository` is
    faked; `revision_lineage_service` is left at its real default."""

    def __init__(self, initial: PlanningState) -> None:
        self._states: dict[str, PlanningState] = {initial.trip_id: initial.model_copy(deep=True)}

    def get_by_trip_id(self, trip_id: str) -> PlanningState | None:
        state = self._states.get(trip_id)
        return state.model_copy(deep=True) if state else None

    def save(self, planning_state: PlanningState) -> PlanningState:
        self._states[planning_state.trip_id] = planning_state.model_copy(deep=True)
        return planning_state


class _FakeInterpreter:
    def __init__(self, result: AIFeedbackInterpretationResult) -> None:
        self._result = result

    def interpret(self, planning_state: PlanningState, feedback_text: str) -> AIFeedbackInterpretationResult:
        return self._result


class _FakePlanBuilder:
    def __init__(self, plan: TargetedRegenerationPlan) -> None:
        self._plan = plan

    def build_plan(self, planning_state: PlanningState, interpretation: Any) -> TargetedRegenerationPlan:
        return self._plan


class _FakeExecutor:
    def __init__(self, builder: Any) -> None:
        self._builder = builder

    def execute(self, planning_state: PlanningState, plan: TargetedRegenerationPlan) -> TargetedRegenerationExecutionResult:
        return self._builder(planning_state)


def _completed_execution(planning_state: PlanningState) -> TargetedRegenerationExecutionResult:
    working_state = planning_state.model_copy(deep=True)
    working_state.experience_plan.daily_plans[1].experiences = []
    return TargetedRegenerationExecutionResult(
        trip_id=working_state.trip_id,
        status=TargetedRegenerationExecutionStatus.COMPLETED,
        source_version=planning_state.metadata.current_version,
        resulting_planning_state=working_state,
        affected_day_indices=[2],
        preserved_day_indices=[1],
        deterministic_edits_applied=["removed exp_C from day 2"],
        provider_lookup_status="not_required",
        reasoning_status="not_required",
        routing_rerun=True,
        validation_status="ready",
        preservation_audit_passed=True,
    )


def test_targeted_regeneration_revision_agrees_with_every_other_version_record() -> None:
    trip_id = "trip_targeted_consistency"
    planning_state = _planning_state(trip_id)
    event = FeedbackEvent(
        feedback_text="Remove the tower.",
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        interpretation={"method": "deterministic_rule_based", "applied_to_plan": False},
    )
    planning_state.feedback_history = [event]

    # Task 33's own real v1 revision, recorded exactly like the real
    # generate_full_plan call site does.
    r1 = revision_lineage_service.record_current_revision(planning_state)
    assert r1 is not None
    assert r1.version_label == "v1"

    repository = _FakeRepository(planning_state)
    interpreter = _FakeInterpreter(
        AIFeedbackInterpretationResult(
            status=AIFeedbackInterpretationStatus.COMPLETED,
            actions=[RemoveExperienceAction(experience_id="exp_C")],
            confidence=0.9,
            provider_name="fake_provider",
            model_name="fake_model",
        )
    )
    plan_builder = _FakePlanBuilder(
        TargetedRegenerationPlan(
            trip_id=trip_id,
            source_version="v1",
            status=TargetedRegenerationPlanStatus.READY,
            affected_day_indices=[2],
            preserved_day_indices=[1],
            affected_experience_ids=["exp_C"],
            experience_removals=[
                TargetedExperienceRemoval(experience_id="exp_C", day_index=2, candidate_id="exp_C")
            ],
        )
    )
    executor = _FakeExecutor(_completed_execution)

    service = TargetedRegenerationApplicationService(
        planning_state_repository=repository,
        interpreter_service=interpreter,
        plan_builder=plan_builder,
        executor=executor,
    )
    result = service.regenerate(trip_id)

    assert result.new_version == "v2"
    persisted = repository.get_by_trip_id(trip_id)
    applied_event = next(e for e in persisted.feedback_history if e.feedback_event_id == event.feedback_event_id)
    regeneration_attempt = persisted.regeneration_attempts[-1]
    version_history_latest = persisted.version_history[-1]

    branch = itinerary_lineage_repository.get_default_branch(trip_id)
    assert branch is not None
    r2 = itinerary_lineage_repository.get_revision(branch.head_revision_id)
    assert r2 is not None

    # Task 33's exact required agreement, all against the SAME real "v2":
    assert r2.version_label == "v2"
    assert version_history_latest.version_label == "v2"
    assert applied_event.applied_in_version == "v2"
    assert regeneration_attempt.current_version == "v2"
    assert branch.head_revision_id == r2.revision_id
    assert r2.parent_revision_id == r1.revision_id


@pytest.mark.usefixtures("synthetic_legacy_regeneration_support")
def test_legacy_regeneration_revision_agrees_with_every_other_version_record() -> None:
    """Same Task 33 agreement check, for the legacy (non-targeted)
    mutation path -- `apply_regeneration_mutation`, the exact function
    both the sync route and the async job runner call."""
    trip_id = "trip_legacy_consistency"
    planning_state = _planning_state(trip_id)
    event = FeedbackEvent(
        feedback_text="less packed",
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
    )
    planning_state.feedback_history = [event]
    revision_lineage_service.record_current_revision(planning_state)

    from app.models.planning_state import PlanningStage

    result = apply_regeneration_mutation(
        planning_state, [PlanningStage.EXPERIENCE_PLAN], [event]
    )
    from app.services.regeneration_attempt_service import regeneration_attempt_service

    final_state = regeneration_attempt_service.record_applied_attempt(result.planning_state)
    revision = revision_lineage_service.record_current_revision(final_state)

    assert revision is not None
    assert revision.version_label == "v2"
    assert final_state.version_history[-1].version_label == "v2"
    applied_event = next(
        e for e in final_state.feedback_history if e.feedback_event_id == event.feedback_event_id
    )
    assert applied_event.applied_in_version == "v2"
    assert final_state.regeneration_attempts[-1].current_version == "v2"

    branch = itinerary_lineage_repository.get_default_branch(trip_id)
    assert branch is not None
    assert branch.head_revision_id == revision.revision_id
