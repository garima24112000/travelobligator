from __future__ import annotations

from app.core.errors import REGENERATION_NOT_AVAILABLE_MESSAGE
from app.models.common import RegenerationStrategy
from app.models.planning_state import (
    ExperiencePlan,
    FeedbackEvent,
    PlanningStage,
    PlanningState,
    TravelGroupType,
    TripRequest,
    UserLock,
)
from app.services.plan_diff_preview_service import plan_diff_preview_service
from app.services.regeneration_attempt_service import regeneration_attempt_service
from app.services.regeneration_readiness_service import regeneration_readiness_service
from app.services.versioning_service import VersioningService

_EXPECTED_ATTEMPT_FIELDS = {
    "attempt_id",
    "status",
    "requested_at",
    "current_version",
    "would_create_version",
    "pending_feedback_count",
    "active_lock_count",
    "reason_code",
    "message",
}


def _planning_state() -> PlanningState:
    return PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville, Testland",
            start_date="2026-08-10",
            end_date="2026-08-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        )
    )


def _generated_planning_state() -> PlanningState:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    return VersioningService().create_initial_version(planning_state)


def _feedback_event() -> FeedbackEvent:
    return FeedbackEvent(
        feedback_text="Make this less packed",
        feedback_type="pace_change",
        affected_stages=[PlanningStage.EXPERIENCE_PLAN],
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
    )


def test_ungenerated_trip_records_null_current_and_would_create_version() -> None:
    planning_state = _planning_state()
    # Mirrors the real write-path ordering: readiness is always kept fresh
    # before POST /regenerate is ever called (create_trip/generate/feedback/
    # lock write paths all recompute it).
    planning_state = regeneration_readiness_service.recompute(planning_state)

    result = regeneration_attempt_service.record_blocked_attempt(planning_state)
    attempt = result.regeneration_attempts[0]

    assert attempt.status == "blocked"
    assert attempt.current_version is None
    assert attempt.would_create_version is None
    assert attempt.pending_feedback_count == 0
    assert attempt.active_lock_count == 0
    assert attempt.reason_code == "REGENERATION_NOT_AVAILABLE"
    assert attempt.message == REGENERATION_NOT_AVAILABLE_MESSAGE
    assert attempt.attempt_id.startswith("regen_attempt_")


def test_generated_trip_no_feedback_records_v1_and_no_would_create_version() -> None:
    planning_state = _generated_planning_state()
    planning_state = regeneration_readiness_service.recompute(planning_state)

    result = regeneration_attempt_service.record_blocked_attempt(planning_state)
    attempt = result.regeneration_attempts[0]

    assert attempt.current_version == "v1"
    assert attempt.would_create_version is None
    assert attempt.pending_feedback_count == 0
    assert attempt.active_lock_count == 0


def test_generated_trip_with_feedback_records_v1_and_would_create_v2() -> None:
    planning_state = _generated_planning_state()
    planning_state.feedback_history.append(_feedback_event())
    planning_state = regeneration_readiness_service.recompute(planning_state)

    result = regeneration_attempt_service.record_blocked_attempt(planning_state)
    attempt = result.regeneration_attempts[0]

    assert attempt.current_version == "v1"
    assert attempt.would_create_version == "v2"
    assert attempt.pending_feedback_count == 1


def test_active_lock_count_counts_only_active_locks() -> None:
    planning_state = _generated_planning_state()
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_test_1")
    )
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_test_2")
    )
    planning_state = regeneration_readiness_service.recompute(planning_state)

    result = regeneration_attempt_service.record_blocked_attempt(planning_state)
    assert result.regeneration_attempts[0].active_lock_count == 2


def test_removed_lock_is_not_counted() -> None:
    planning_state = _generated_planning_state()
    lock = UserLock(locked_item_type="experience", locked_item_id="experience_test_1")
    planning_state.user_locks.append(lock)
    lock.is_active = False
    planning_state = regeneration_readiness_service.recompute(planning_state)

    result = regeneration_attempt_service.record_blocked_attempt(planning_state)
    assert result.regeneration_attempts[0].active_lock_count == 0


def test_repeated_calls_append_distinct_attempts() -> None:
    planning_state = _generated_planning_state()
    planning_state = regeneration_readiness_service.recompute(planning_state)

    result = regeneration_attempt_service.record_blocked_attempt(planning_state)
    result = regeneration_attempt_service.record_blocked_attempt(result)
    result = regeneration_attempt_service.record_blocked_attempt(result)

    assert len(result.regeneration_attempts) == 3
    assert len({attempt.attempt_id for attempt in result.regeneration_attempts}) == 3
    assert all(attempt.status == "blocked" for attempt in result.regeneration_attempts)


def test_record_blocked_attempt_only_appends_and_touches_metadata() -> None:
    """The service is only allowed to append to `regeneration_attempts` and
    bump `metadata.updated_at` -- every other section of PlanningState must
    stay untouched, including sections that were already populated
    (feedback, locks, version history, diff preview, readiness).
    """
    planning_state = _generated_planning_state()
    planning_state.feedback_history.append(_feedback_event())
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_test_1")
    )
    planning_state = regeneration_readiness_service.recompute(planning_state)
    planning_state = plan_diff_preview_service.recompute(planning_state)

    before_experience_plan = planning_state.experience_plan.model_copy(deep=True)
    before_provider_coverage = planning_state.provider_coverage.model_copy(deep=True)
    before_route_feasibility = (
        planning_state.experience_plan.route_feasibility_context.model_copy(deep=True)
    )
    before_feedback_history = [
        event.model_copy(deep=True) for event in planning_state.feedback_history
    ]
    before_pending_feedback_summary = (
        planning_state.pending_feedback_summary.model_copy(deep=True)
    )
    before_user_locks = [
        lock.model_copy(deep=True) for lock in planning_state.user_locks
    ]
    before_version_history = [
        item.model_copy(deep=True) for item in planning_state.version_history
    ]
    before_plan_diff_preview = planning_state.plan_diff_preview.model_copy(deep=True)
    before_regeneration_readiness = planning_state.regeneration_readiness.model_copy(
        deep=True
    )
    before_created_at = planning_state.metadata.created_at
    before_current_version = planning_state.metadata.current_version

    result = regeneration_attempt_service.record_blocked_attempt(planning_state)

    assert result.experience_plan == before_experience_plan
    assert result.provider_coverage == before_provider_coverage
    assert result.experience_plan.route_feasibility_context == before_route_feasibility
    assert result.feedback_history == before_feedback_history
    assert result.pending_feedback_summary == before_pending_feedback_summary
    assert result.user_locks == before_user_locks
    assert result.version_history == before_version_history
    assert result.plan_diff_preview == before_plan_diff_preview
    assert result.regeneration_readiness == before_regeneration_readiness
    assert result.metadata.created_at == before_created_at
    assert result.metadata.current_version == before_current_version
    assert len(result.regeneration_attempts) == 1


def test_no_fake_travel_fields_in_attempt() -> None:
    planning_state = _generated_planning_state()
    planning_state = regeneration_readiness_service.recompute(planning_state)

    result = regeneration_attempt_service.record_blocked_attempt(planning_state)
    attempt = result.regeneration_attempts[0]

    assert set(attempt.model_dump().keys()) == _EXPECTED_ATTEMPT_FIELDS
    assert attempt.status == "blocked"
    assert attempt.reason_code == "REGENERATION_NOT_AVAILABLE"
