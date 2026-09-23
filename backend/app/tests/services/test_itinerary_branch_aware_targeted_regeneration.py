from __future__ import annotations

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
from app.repositories.factory import get_planning_state_repository
from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository
from app.services.itinerary_fork_service import itinerary_fork_service
from app.services.revision_lineage_service import (
    BranchActivationStatus,
    revision_lineage_service,
)
from app.services.targeted_regeneration_application_service import (
    TargetedRegenerationApplicationService,
)
from app.services.versioning_service import versioning_service

# Section 199B Task 28: proves targeted regeneration (Section 197) is
# branch-aware -- fork from Main, activate the fork, submit "Remove B",
# and assert the removal only ever reaches the fork's own snapshot/head,
# never Main's. Mirrors `test_revision_lineage_regeneration_consistency.
# py`'s real-service-with-fake-AI-only pattern (only the interpreter/
# plan-builder/executor are fakes; `revision_lineage_service`/
# `versioning_service`/`feedback_service` are all real).


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Seville, Spain",
        "start_date": "2026-09-10",
        "end_date": "2026-09-13",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
        "interests": ["history"],
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _experience(experience_id: str, name: str, day_number: int) -> ExperienceItem:
    return ExperienceItem(
        experience_id=experience_id, name=name, category="attraction", day_number=day_number, stop_order=1
    )


def _planning_state_and_save(trip_id: str) -> PlanningState:
    planning_state = PlanningState(trip_id=trip_id, trip_request=_trip_request())
    planning_state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(
                day_number=1,
                date=date(2026, 9, 10),
                experiences=[
                    _experience("exp_A", "A", 1),
                    _experience("exp_B", "B", 1),
                    _experience("exp_C", "C", 1),
                ],
            ),
        ]
    )
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE, travelers_count=2, pace=TripPace.BALANCED, interests=["history"]
    )
    versioning_service.create_initial_version(planning_state)
    get_planning_state_repository().save(planning_state)
    return planning_state


class _FakeRepository:
    """Wraps the REAL `planning_state_repository` so
    `TargetedRegenerationApplicationService`'s reload-fresh-state
    semantics stay real, while letting this test control the fake
    AI/plan/executor layer exactly like `test_targeted_regeneration_
    application_service.py`'s own `_FakeRepository` does against an
    in-memory dict -- here backed by the real repository so the branch-
    aware `record_current_revision` hook (which reads from the real
    `get_planning_state_repository()`) sees the same state."""

    def get_by_trip_id(self, trip_id: str) -> PlanningState | None:
        return get_planning_state_repository().get_by_trip_id(trip_id)

    def save(self, planning_state: PlanningState) -> PlanningState:
        return get_planning_state_repository().save(planning_state)


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
    def execute(self, planning_state: PlanningState, plan: TargetedRegenerationPlan) -> TargetedRegenerationExecutionResult:
        working_state = planning_state.model_copy(deep=True)
        working_state.experience_plan.daily_plans[0].experiences = [
            e for e in working_state.experience_plan.daily_plans[0].experiences if e.experience_id != "exp_B"
        ]
        return TargetedRegenerationExecutionResult(
            trip_id=working_state.trip_id,
            status=TargetedRegenerationExecutionStatus.COMPLETED,
            source_version=planning_state.metadata.current_version,
            resulting_planning_state=working_state,
            affected_day_indices=[1],
            preserved_day_indices=[],
            deterministic_edits_applied=["removed exp_B from day 1"],
            provider_lookup_status="not_required",
            reasoning_status="not_required",
            routing_rerun=True,
            validation_status="ready",
            preservation_audit_passed=True,
        )


def test_targeted_regeneration_on_activated_fork_only_affects_that_branch() -> None:
    trip_id = "trip_targeted_branch_aware"
    state = _planning_state_and_save(trip_id)
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None
    main_branch_id = revision_lineage_service.resolve_active_branch_id(state)

    fork_result = itinerary_fork_service.create_fork(
        trip_id, r1.revision_id, "Alternate", activate_after_create=True
    )
    assert fork_result.activation is not None
    assert fork_result.activation.status == BranchActivationStatus.ACTIVATED
    alternate_branch_id = fork_result.branch.branch_id

    event = FeedbackEvent(feedback_text="Remove B", regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY)
    state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert state is not None
    state.feedback_history.append(event)
    get_planning_state_repository().save(state)

    service = TargetedRegenerationApplicationService(
        planning_state_repository=_FakeRepository(),
        interpreter_service=_FakeInterpreter(
            AIFeedbackInterpretationResult(
                status=AIFeedbackInterpretationStatus.COMPLETED,
                actions=[RemoveExperienceAction(experience_id="exp_B")],
                confidence=0.9,
                provider_name="fake_provider",
                model_name="fake_model",
            )
        ),
        plan_builder=_FakePlanBuilder(
            TargetedRegenerationPlan(
                trip_id=trip_id,
                source_version="v1",
                status=TargetedRegenerationPlanStatus.READY,
                affected_day_indices=[1],
                preserved_day_indices=[],
                affected_experience_ids=["exp_B"],
                experience_removals=[
                    TargetedExperienceRemoval(experience_id="exp_B", day_index=1, candidate_id="exp_B")
                ],
            )
        ),
        executor=_FakeExecutor(),
    )
    result = service.regenerate(trip_id)
    assert result.status.value == "completed"

    # Main untouched: branch head, revision, and snapshot all unaffected.
    main_branch = itinerary_lineage_repository.get_branch(main_branch_id)
    assert main_branch is not None
    assert main_branch.head_revision_id == r1.revision_id
    main_snapshot = revision_lineage_service.load_snapshot(r1.revision_id)
    assert main_snapshot is not None
    assert {e.experience_id for e in main_snapshot.experience_plan.daily_plans[0].experiences} == {
        "exp_A",
        "exp_B",
        "exp_C",
    }

    # Alternate got a new revision with B removed, parented on R1 (the
    # fork's own base, even though R1 belongs to Main -- Task 9/10/25).
    alternate_branch = itinerary_lineage_repository.get_branch(alternate_branch_id)
    assert alternate_branch is not None
    assert alternate_branch.head_revision_id != r1.revision_id
    alt_revision = itinerary_lineage_repository.get_revision(alternate_branch.head_revision_id)
    assert alt_revision is not None
    assert alt_revision.parent_revision_id == r1.revision_id
    assert alt_revision.branch_id == alternate_branch_id

    alt_snapshot = revision_lineage_service.load_snapshot(alternate_branch.head_revision_id)
    assert alt_snapshot is not None
    assert {e.experience_id for e in alt_snapshot.experience_plan.daily_plans[0].experiences} == {
        "exp_A",
        "exp_C",
    }

    final_state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert final_state is not None
    assert final_state.metadata.active_branch_id == alternate_branch_id


def test_targeted_regeneration_refused_on_same_label_content_drift() -> None:
    """Section 199B.1 (Task 7/8): the same workspace-consistency guard,
    exercised through the real `TargetedRegenerationApplicationService`
    boundary -- content drifted under the same version label must
    refuse with WORKSPACE_CONFLICT before the interpreter/plan/executor
    are ever invoked."""
    trip_id = "trip_targeted_workspace_conflict"
    state = _planning_state_and_save(trip_id)
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None

    # Drift under the same "v1" label -- no new revision recorded.
    live_state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert live_state is not None
    live_state.experience_plan.daily_plans[0].experiences.append(
        _experience("exp_DRIFT", "Drift", 1)
    )
    get_planning_state_repository().save(live_state)

    event = FeedbackEvent(feedback_text="Remove B", regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY)
    live_state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert live_state is not None
    live_state.feedback_history.append(event)
    get_planning_state_repository().save(live_state)

    interpreter_calls = []

    class _NeverCalledInterpreter:
        def interpret(self, planning_state, feedback_text):
            interpreter_calls.append(feedback_text)
            raise AssertionError("interpreter must never be reached after a workspace conflict")

    service = TargetedRegenerationApplicationService(
        planning_state_repository=_FakeRepository(),
        interpreter_service=_NeverCalledInterpreter(),
        plan_builder=None,
        executor=None,
    )
    result = service.regenerate(trip_id)

    assert result.status.value == "workspace_conflict"
    assert interpreter_calls == []

    # No feedback consumed, no version advanced.
    reloaded = get_planning_state_repository().get_by_trip_id(trip_id)
    assert reloaded is not None
    assert reloaded.feedback_history[-1].applied_at is None
    assert reloaded.metadata.current_version == "v1"
