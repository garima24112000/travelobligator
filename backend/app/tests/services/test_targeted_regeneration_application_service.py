from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.models.ai_feedback_interpretation import (
    AIFeedbackClarification,
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
    UserLock,
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
from app.models.targeted_regeneration_runtime import TargetedRegenerationRuntimeStatus
from app.services.targeted_regeneration_application_service import TargetedRegenerationApplicationService
from app.services.versioning_service import versioning_service

# Tests for the Section 197C application service -- the one orchestration
# boundary wiring Sections 196/197A/197B into persistence/versioning/
# feedback-lifecycle. Every AI/provider dependency is a fake here; the
# real deterministic services (versioning, feedback, diff-preview,
# readiness) are used for real since they're pure/local.


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


def _experience(experience_id: str, name: str, day_number: int, **overrides: Any) -> ExperienceItem:
    fields: dict[str, Any] = dict(
        experience_id=experience_id, name=name, category="attraction", day_number=day_number, stop_order=1
    )
    fields.update(overrides)
    return ExperienceItem(**fields)


def _planning_state(trip_id: str = "trip_x") -> PlanningState:
    planning_state = PlanningState(trip_id=trip_id, trip_request=_trip_request())
    day1 = DailyPlan(day_number=1, date=date(2026, 9, 10), experiences=[_experience("exp_A", "Museum A", 1)])
    day2 = DailyPlan(
        day_number=2,
        date=date(2026, 9, 11),
        experiences=[
            _experience(
                "exp_C",
                "Torre de Belem",
                2,
                provider_place_id="way/24341353",
                provider_source="openstreetmap_places",
            )
        ],
    )
    day3 = DailyPlan(day_number=3, date=date(2026, 9, 12), experiences=[_experience("exp_E", "Viewpoint E", 3)])
    planning_state.experience_plan = ExperiencePlan(daily_plans=[day1, day2, day3])
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE, travelers_count=2, pace=TripPace.BALANCED, interests=["history"]
    )
    versioning_service.create_initial_version(planning_state)
    return planning_state


def _pending_event(text: str = "Remove Belem Tower.") -> FeedbackEvent:
    return FeedbackEvent(
        feedback_text=text,
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        interpretation={"method": "deterministic_rule_based", "applied_to_plan": False},
    )


class _FakeRepository:
    def __init__(self, initial: PlanningState) -> None:
        self._states: dict[str, PlanningState] = {initial.trip_id: initial.model_copy(deep=True)}
        self.save_versions: list[str] = []

    def get_by_trip_id(self, trip_id: str) -> PlanningState | None:
        state = self._states.get(trip_id)
        return state.model_copy(deep=True) if state else None

    def save(self, planning_state: PlanningState) -> PlanningState:
        self._states[planning_state.trip_id] = planning_state.model_copy(deep=True)
        self.save_versions.append(planning_state.metadata.current_version)
        return planning_state

    def mutate_externally(self, trip_id: str, **updates: Any) -> None:
        """Simulates a concurrent write from another request."""
        state = self._states[trip_id]
        for key, value in updates.items():
            setattr(state, key, value)


class _FakeInterpreter:
    def __init__(self, result: AIFeedbackInterpretationResult) -> None:
        self._result = result
        self.calls = 0

    def interpret(self, planning_state: PlanningState, feedback_text: str) -> AIFeedbackInterpretationResult:
        self.calls += 1
        return self._result


class _FakePlanBuilder:
    def __init__(self, plan: TargetedRegenerationPlan) -> None:
        self._plan = plan

    def build_plan(self, planning_state: PlanningState, interpretation: Any) -> TargetedRegenerationPlan:
        return self._plan


class _FakeExecutor:
    """Builds its result lazily from whatever `planning_state` it's
    actually called with -- mirroring the real executor's own
    `model_copy(deep=True)`-at-call-time behavior -- rather than a fixed
    canned result computed at test-setup time, which would miss
    mutations (like the just-persisted AI interpretation) the real
    service applies to `planning_state` before calling `execute`."""

    def __init__(
        self,
        result: TargetedRegenerationExecutionResult | None = None,
        builder: Any = None,
    ) -> None:
        self._result = result
        self._builder = builder

    def execute(self, planning_state: PlanningState, plan: TargetedRegenerationPlan) -> TargetedRegenerationExecutionResult:
        if self._builder is not None:
            return self._builder(planning_state)
        assert self._result is not None
        return self._result


def _completed_interpretation(**overrides: Any) -> AIFeedbackInterpretationResult:
    fields: dict[str, Any] = {
        "status": AIFeedbackInterpretationStatus.COMPLETED,
        "actions": [RemoveExperienceAction(experience_id="exp_C")],
        "confidence": 0.9,
        "provider_name": "fake_provider",
        "model_name": "fake_model",
    }
    fields.update(overrides)
    return AIFeedbackInterpretationResult(**fields)


def _ready_removal_plan(source_version: str = "v1") -> TargetedRegenerationPlan:
    return TargetedRegenerationPlan(
        trip_id="trip_x",
        source_version=source_version,
        status=TargetedRegenerationPlanStatus.READY,
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        affected_experience_ids=["exp_C"],
        experience_removals=[
            TargetedExperienceRemoval(experience_id="exp_C", day_index=2, candidate_id="openstreetmap_places:way/24341353")
        ],
    )


def _completed_execution(planning_state: PlanningState) -> TargetedRegenerationExecutionResult:
    working_state = planning_state.model_copy(deep=True)
    working_state.experience_plan.daily_plans[1].experiences = []  # day 2, exp_C removed
    return TargetedRegenerationExecutionResult(
        trip_id="trip_x",
        status=TargetedRegenerationExecutionStatus.COMPLETED,
        source_version=planning_state.metadata.current_version,
        resulting_planning_state=working_state,
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        deterministic_edits_applied=["removed exp_C from day 2"],
        provider_lookup_status="not_required",
        reasoning_status="not_required",
        routing_rerun=True,
        validation_status="ready",
        preservation_audit_passed=True,
    )


def _service(interpreter: Any, plan_builder: Any, executor: Any, repository: _FakeRepository) -> TargetedRegenerationApplicationService:
    return TargetedRegenerationApplicationService(
        planning_state_repository=repository,
        interpreter_service=interpreter,
        plan_builder=plan_builder,
        executor=executor,
    )


# ---------------------------------------------------------------------------
# Successful end-to-end path.
# ---------------------------------------------------------------------------


def test_completed_regeneration_persists_new_version_and_marks_feedback_applied() -> None:
    planning_state = _planning_state()
    event = _pending_event()
    planning_state.feedback_history = [event]
    repository = _FakeRepository(planning_state)

    interpreter = _FakeInterpreter(_completed_interpretation())
    plan_builder = _FakePlanBuilder(_ready_removal_plan())
    executor = _FakeExecutor(builder=_completed_execution)

    service = _service(interpreter, plan_builder, executor, repository)
    result = service.regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.COMPLETED
    assert result.source_version == "v1"
    assert result.new_version == "v2"
    assert result.diff is not None
    assert result.diff.removed_experience_ids == ["exp_C"]
    assert result.diff.added_experience_ids == []

    persisted = repository.get_by_trip_id("trip_x")
    assert persisted.metadata.current_version == "v2"
    assert len(persisted.version_history) == 2
    applied_event = next(e for e in persisted.feedback_history if e.feedback_event_id == event.feedback_event_id)
    assert applied_event.handling_status == "applied"
    assert applied_event.applied_in_version == "v2"
    assert applied_event.applied_at is not None
    assert applied_event.interpretation["method"] == "ai_interpreted"
    assert applied_event.interpretation["status"] == "completed"

    # Section 197C.1: exactly one RegenerationAttempt, "applied", whose
    # own current_version agrees with the persisted metadata/version
    # history/feedback-applied version -- no three-way disagreement.
    assert len(persisted.regeneration_attempts) == 1
    attempt = persisted.regeneration_attempts[0]
    assert attempt.status == "applied"
    assert attempt.current_version == "v2"
    assert attempt.current_version == persisted.metadata.current_version
    assert attempt.current_version == persisted.version_history[-1].version_label
    assert attempt.current_version == applied_event.applied_in_version


# ---------------------------------------------------------------------------
# Clarification never executes or versions.
# ---------------------------------------------------------------------------


def test_needs_clarification_does_not_execute_or_create_version() -> None:
    planning_state = _planning_state()
    event = _pending_event("Remove the museum.")
    planning_state.feedback_history = [event]
    repository = _FakeRepository(planning_state)

    interpretation = AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION,
        clarification=AIFeedbackClarification(reason="Two museums scheduled.", possible_experience_ids=["exp_A"]),
        confidence=0.4,
    )
    interpreter = _FakeInterpreter(interpretation)
    plan_builder = _FakePlanBuilder(_ready_removal_plan())  # should never be consulted meaningfully
    executor = _FakeExecutor(builder=_completed_execution)

    service = _service(interpreter, plan_builder, executor, repository)
    result = service.regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.NEEDS_CLARIFICATION
    assert result.clarification_reason == "Two museums scheduled."
    persisted = repository.get_by_trip_id("trip_x")
    assert persisted.metadata.current_version == "v1"
    assert len(persisted.version_history) == 1  # only the pre-seeded initial v1, no new one created
    event_after = persisted.feedback_history[0]
    assert event_after.handling_status == "captured"
    assert event_after.applied_at is None
    assert event_after.interpretation["status"] == "needs_clarification"

    # Task 10: no executor call happened, so the attempt is honestly
    # "blocked" (never "failed") -- matching legacy's own vocabulary for
    # a pre-execution refusal.
    assert len(persisted.regeneration_attempts) == 1
    assert persisted.regeneration_attempts[0].status == "blocked"
    assert persisted.regeneration_attempts[0].reason_code == "REGENERATION_NEEDS_CLARIFICATION"


# ---------------------------------------------------------------------------
# Rejected / not_connected interpretation never falls back to legacy.
# ---------------------------------------------------------------------------


def test_rejected_interpretation_blocks_without_execution() -> None:
    planning_state = _planning_state()
    planning_state.feedback_history = [_pending_event()]
    repository = _FakeRepository(planning_state)

    interpretation = AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.REJECTED,
        blocked_reasons=["Could not interpret."],
        confidence=0.0,
    )
    interpreter = _FakeInterpreter(interpretation)
    plan_builder = _FakePlanBuilder(_ready_removal_plan())
    executor = _FakeExecutor(builder=_completed_execution)

    result = _service(interpreter, plan_builder, executor, repository).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.BLOCKED
    persisted = repository.get_by_trip_id("trip_x")
    assert persisted.metadata.current_version == "v1"
    assert len(persisted.regeneration_attempts) == 1
    assert persisted.regeneration_attempts[0].status == "blocked"


def test_not_connected_interpretation_is_provider_unavailable() -> None:
    planning_state = _planning_state()
    planning_state.feedback_history = [_pending_event()]
    repository = _FakeRepository(planning_state)

    interpretation = AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.NOT_CONNECTED,
        blocked_reasons=["AI feedback interpreter is disabled."],
        confidence=0.0,
    )
    interpreter = _FakeInterpreter(interpretation)
    plan_builder = _FakePlanBuilder(_ready_removal_plan())
    executor = _FakeExecutor(builder=_completed_execution)

    result = _service(interpreter, plan_builder, executor, repository).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.PROVIDER_UNAVAILABLE
    persisted = repository.get_by_trip_id("trip_x")
    assert persisted.metadata.current_version == "v1"
    # Task 11: this is the INTERPRETER-level provider-unavailable case --
    # the executor was never reached, so the attempt is honestly
    # "blocked," distinct from a new-place grounding failure reached
    # mid-execution (see test_provider_unavailable_during_executor_is_a_failed_attempt).
    assert len(persisted.regeneration_attempts) == 1
    assert persisted.regeneration_attempts[0].status == "blocked"
    assert persisted.regeneration_attempts[0].reason_code == "REGENERATION_PROVIDER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Plan not ready / execution failure never version-bumps.
# ---------------------------------------------------------------------------


def test_plan_blocked_prevents_execution() -> None:
    planning_state = _planning_state()
    planning_state.feedback_history = [_pending_event()]
    repository = _FakeRepository(planning_state)

    blocked_plan = TargetedRegenerationPlan(
        trip_id="trip_x", source_version="v1", status=TargetedRegenerationPlanStatus.BLOCKED,
        block_reasons=["stale reference"],
    )
    interpreter = _FakeInterpreter(_completed_interpretation())
    plan_builder = _FakePlanBuilder(blocked_plan)
    executor = _FakeExecutor(builder=_completed_execution)

    result = _service(interpreter, plan_builder, executor, repository).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.BLOCKED
    persisted = repository.get_by_trip_id("trip_x")
    assert persisted.metadata.current_version == "v1"
    assert len(persisted.regeneration_attempts) == 1
    assert persisted.regeneration_attempts[0].status == "blocked"


def test_execution_failure_leaves_state_unchanged() -> None:
    planning_state = _planning_state()
    planning_state.feedback_history = [_pending_event()]
    repository = _FakeRepository(planning_state)

    failed_execution = TargetedRegenerationExecutionResult(
        trip_id="trip_x",
        status=TargetedRegenerationExecutionStatus.FAILED,
        source_version="v1",
        failure_reason="Preservation audit failed.",
    )
    interpreter = _FakeInterpreter(_completed_interpretation())
    plan_builder = _FakePlanBuilder(_ready_removal_plan())
    executor = _FakeExecutor(failed_execution)

    result = _service(interpreter, plan_builder, executor, repository).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.FAILED
    persisted = repository.get_by_trip_id("trip_x")
    assert persisted.metadata.current_version == "v1"
    # Task 5: the executor WAS invoked here (unlike every branch above),
    # so this attempt is honestly "failed," not "blocked" -- but content/
    # version/feedback are still completely unchanged.
    assert len(persisted.regeneration_attempts) == 1
    assert persisted.regeneration_attempts[0].status == "failed"
    assert persisted.feedback_history[0].handling_status == "captured"
    assert persisted.feedback_history[0].applied_at is None


def test_provider_unavailable_during_executor_is_a_failed_attempt() -> None:
    """Task 11: distinguishes a new-place grounding failure reached mid-
    execution (a "failed" attempt, since the executor genuinely ran) from
    the interpreter-level provider-unavailable case above (a "blocked"
    attempt, since the executor was never reached)."""
    planning_state = _planning_state()
    planning_state.feedback_history = [_pending_event()]
    repository = _FakeRepository(planning_state)

    provider_unavailable_execution = TargetedRegenerationExecutionResult(
        trip_id="trip_x",
        status=TargetedRegenerationExecutionStatus.PROVIDER_UNAVAILABLE,
        source_version="v1",
        provider_lookup_status="unavailable",
        block_reasons=["Could not ground requested place to a real provider result."],
    )
    interpreter = _FakeInterpreter(_completed_interpretation())
    plan_builder = _FakePlanBuilder(_ready_removal_plan())
    executor = _FakeExecutor(provider_unavailable_execution)

    result = _service(interpreter, plan_builder, executor, repository).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.PROVIDER_UNAVAILABLE
    persisted = repository.get_by_trip_id("trip_x")
    assert persisted.metadata.current_version == "v1"
    assert len(persisted.regeneration_attempts) == 1
    assert persisted.regeneration_attempts[0].status == "failed"


# ---------------------------------------------------------------------------
# Version conflict detected right before commit.
# ---------------------------------------------------------------------------


def test_version_conflict_before_commit_is_detected() -> None:
    planning_state = _planning_state()
    planning_state.feedback_history = [_pending_event()]
    repository = _FakeRepository(planning_state)

    interpreter = _FakeInterpreter(_completed_interpretation())
    plan_builder = _FakePlanBuilder(_ready_removal_plan(source_version="v1"))
    executor = _FakeExecutor(builder=_completed_execution)

    class _ConflictRepository(_FakeRepository):
        def __init__(self, initial: PlanningState) -> None:
            super().__init__(initial)
            self._get_count = 0

        def get_by_trip_id(self, trip_id: str) -> PlanningState | None:
            self._get_count += 1
            state = super().get_by_trip_id(trip_id)
            # Simulate a concurrent regeneration advancing the version
            # right before this call's own commit-time recheck (its
            # second get_by_trip_id call).
            if self._get_count == 2 and state is not None:
                state.metadata.current_version = "v2"
            return state

    conflict_repository = _ConflictRepository(planning_state)
    result = _service(interpreter, plan_builder, executor, conflict_repository).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.CONFLICT
    # Section 197C.1: a failed RegenerationAttempt audit record IS saved
    # (onto the freshest known state), but the version itself is never
    # advanced by targeted regeneration -- only "v2" (the externally
    # injected conflicting version) is ever written, never a new one.
    assert conflict_repository.save_versions == ["v2"]
    persisted = conflict_repository.get_by_trip_id("trip_x")
    assert persisted.regeneration_attempts[-1].status == "failed"
    assert persisted.regeneration_attempts[-1].reason_code == "REGENERATION_CONFLICT"


# ---------------------------------------------------------------------------
# Locks.
# ---------------------------------------------------------------------------


def test_active_lock_blocks_before_interpretation() -> None:
    planning_state = _planning_state()
    planning_state.feedback_history = [_pending_event()]
    planning_state.user_locks = [UserLock(locked_item_type="experience", locked_item_id="exp_A", is_active=True)]
    repository = _FakeRepository(planning_state)

    interpreter = _FakeInterpreter(_completed_interpretation())
    plan_builder = _FakePlanBuilder(_ready_removal_plan())
    executor = _FakeExecutor(builder=_completed_execution)

    result = _service(interpreter, plan_builder, executor, repository).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.BLOCKED
    assert interpreter.calls == 0  # never even reached interpretation


def test_lock_introduced_after_execution_blocks_before_commit() -> None:
    planning_state = _planning_state()
    planning_state.feedback_history = [_pending_event()]
    repository = _FakeRepository(planning_state)

    interpreter = _FakeInterpreter(_completed_interpretation())
    plan_builder = _FakePlanBuilder(_ready_removal_plan())
    executor = _FakeExecutor(builder=_completed_execution)

    class _LockAfterExecutionRepository(_FakeRepository):
        def __init__(self, initial: PlanningState) -> None:
            super().__init__(initial)
            self._get_count = 0

        def get_by_trip_id(self, trip_id: str) -> PlanningState | None:
            self._get_count += 1
            state = super().get_by_trip_id(trip_id)
            if self._get_count == 2 and state is not None:
                state.user_locks = [UserLock(locked_item_type="experience", locked_item_id="exp_A", is_active=True)]
            return state

    repo = _LockAfterExecutionRepository(planning_state)
    result = _service(interpreter, plan_builder, executor, repo).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.BLOCKED
    # A failed attempt is saved, but never a new (bumped) version.
    assert repo.save_versions == ["v1"]
    persisted = repo.get_by_trip_id("trip_x")
    assert persisted.regeneration_attempts[-1].status == "failed"
    assert persisted.regeneration_attempts[-1].reason_code == "REGENERATION_BLOCKED_BY_LOCKS"


# ---------------------------------------------------------------------------
# No pending feedback.
# ---------------------------------------------------------------------------


def test_no_pending_feedback_is_blocked() -> None:
    planning_state = _planning_state()
    repository = _FakeRepository(planning_state)
    interpreter = _FakeInterpreter(_completed_interpretation())
    plan_builder = _FakePlanBuilder(_ready_removal_plan())
    executor = _FakeExecutor(builder=_completed_execution)

    result = _service(interpreter, plan_builder, executor, repository).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.BLOCKED
    assert interpreter.calls == 0


# ---------------------------------------------------------------------------
# Multiple pending events: only one processed per call.
# ---------------------------------------------------------------------------


def test_multiple_pending_events_only_oldest_processed() -> None:
    planning_state = _planning_state()
    older = _pending_event("Remove Belem Tower.")
    newer = _pending_event("Also add Sintra.")
    older.created_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    newer.created_at = datetime(2026, 9, 2, tzinfo=timezone.utc)
    planning_state.feedback_history = [newer, older]
    repository = _FakeRepository(planning_state)

    interpreter = _FakeInterpreter(_completed_interpretation())
    plan_builder = _FakePlanBuilder(_ready_removal_plan())
    executor = _FakeExecutor(builder=_completed_execution)

    result = _service(interpreter, plan_builder, executor, repository).regenerate("trip_x")

    assert result.status == TargetedRegenerationRuntimeStatus.COMPLETED
    assert result.feedback_event_id == older.feedback_event_id
    persisted = repository.get_by_trip_id("trip_x")
    older_after = next(e for e in persisted.feedback_history if e.feedback_event_id == older.feedback_event_id)
    newer_after = next(e for e in persisted.feedback_history if e.feedback_event_id == newer.feedback_event_id)
    assert older_after.handling_status == "applied"
    assert newer_after.handling_status == "captured"
    assert newer_after.applied_at is None


# ---------------------------------------------------------------------------
# Backward compatibility: legacy interpretation shape still loads.
# ---------------------------------------------------------------------------


def test_legacy_deterministic_interpretation_shape_still_loads() -> None:
    planning_state = _planning_state()
    legacy_event = FeedbackEvent(
        feedback_text="Some older feedback.",
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        interpretation={
            "method": "deterministic_rule_based",
            "applied_to_plan": False,
            "summary": "x",
            "matched_labels": [],
            "note": "legacy",
            "change_preview": {"preview_status": "not_applied"},
        },
        applied_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        applied_in_version="v1",
        handling_status="applied",
    )
    planning_state.feedback_history = [legacy_event]
    repository = _FakeRepository(planning_state)
    reloaded = repository.get_by_trip_id("trip_x")
    assert reloaded.feedback_history[0].interpretation["method"] == "deterministic_rule_based"
