from __future__ import annotations

from datetime import datetime, timezone

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
from app.services.regeneration_readiness_service import regeneration_readiness_service
from app.services.versioning_service import VersioningService


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


def test_default_readiness_on_new_planning_state_is_blocked() -> None:
    readiness = _planning_state().regeneration_readiness

    assert readiness.status == "blocked"
    assert readiness.can_regenerate is False
    assert readiness.current_version is None
    assert readiness.would_create_version is None
    assert readiness.pending_feedback_count == 0
    assert readiness.active_lock_count == 0
    assert readiness.available_inputs == []
    assert "generated_plan" in readiness.missing_capabilities
    assert "regeneration_engine" in readiness.missing_capabilities
    assert "generate the initial plan" in readiness.next_step.lower()


def test_recompute_before_generation_stays_blocked() -> None:
    planning_state = _planning_state()
    result = regeneration_readiness_service.recompute(planning_state)
    readiness = result.regeneration_readiness

    assert readiness.status == "blocked"
    assert readiness.can_regenerate is False
    assert readiness.current_version is None
    assert readiness.would_create_version is None
    assert readiness.required_inputs == [
        "generated_plan",
        "pending_feedback",
        "regeneration_engine",
    ]


def test_recompute_after_generation_with_no_feedback() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state = VersioningService().create_initial_version(planning_state)

    result = regeneration_readiness_service.recompute(planning_state)
    readiness = result.regeneration_readiness

    assert readiness.status == "blocked"
    assert readiness.can_regenerate is False
    assert readiness.current_version == "v1"
    assert readiness.would_create_version is None
    assert readiness.pending_feedback_count == 0
    assert "generated_plan" in readiness.available_inputs
    assert "version_history" in readiness.available_inputs
    assert "pending_feedback" in readiness.missing_capabilities
    assert "regeneration_engine" in readiness.missing_capabilities


def test_recompute_after_generation_with_feedback_is_ready() -> None:
    """Step 174D: version exists, feedback exists and classifies to a real
    affected stage, zero active locks -- exactly the MVP scope
    `POST /trips/{trip_id}/regenerate` now actually supports.
    """
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state = VersioningService().create_initial_version(planning_state)
    planning_state.feedback_history.append(
        FeedbackEvent(
            feedback_text="Make this less packed",
            feedback_type="pace_change",
            affected_stages=[PlanningStage.EXPERIENCE_PLAN],
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        )
    )

    result = regeneration_readiness_service.recompute(planning_state)
    readiness = result.regeneration_readiness

    assert readiness.status == "ready"
    assert readiness.can_regenerate is True
    assert readiness.current_version == "v1"
    assert readiness.would_create_version == "v2"
    assert readiness.pending_feedback_count == 1
    assert readiness.missing_capabilities == []
    assert readiness.blocked_by == []
    assert "plan_diff_preview" in readiness.available_inputs
    assert "pending_feedback" in readiness.available_inputs
    assert "regeneration_engine" in readiness.available_inputs
    assert "confirm=true" in readiness.next_step


def test_active_lock_count_reflected_in_all_branches() -> None:
    planning_state = _planning_state()
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_test_1")
    )

    result = regeneration_readiness_service.recompute(planning_state)
    assert result.regeneration_readiness.active_lock_count == 1
    assert "user_locks" not in result.regeneration_readiness.available_inputs

    planning_state.experience_plan = ExperiencePlan()
    planning_state = VersioningService().create_initial_version(planning_state)
    result = regeneration_readiness_service.recompute(planning_state)
    assert result.regeneration_readiness.active_lock_count == 1
    assert "user_locks" in result.regeneration_readiness.available_inputs


def test_removed_lock_excluded_from_active_lock_count() -> None:
    planning_state = _planning_state()
    lock = UserLock(locked_item_type="experience", locked_item_id="experience_test_1")
    planning_state.user_locks.append(lock)

    result = regeneration_readiness_service.recompute(planning_state)
    assert result.regeneration_readiness.active_lock_count == 1

    lock.is_active = False
    result = regeneration_readiness_service.recompute(result)
    assert result.regeneration_readiness.active_lock_count == 0


def test_recompute_does_not_mutate_other_sections() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state = VersioningService().create_initial_version(planning_state)
    planning_state.feedback_history.append(
        FeedbackEvent(
            feedback_text="Make this less packed",
            feedback_type="pace_change",
            affected_stages=[PlanningStage.EXPERIENCE_PLAN],
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        )
    )
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_test_1")
    )
    from app.services.plan_diff_preview_service import plan_diff_preview_service

    planning_state = plan_diff_preview_service.recompute(planning_state)

    before_experience_plan = planning_state.experience_plan.model_copy(deep=True)
    before_validation_report = planning_state.validation_report
    before_provider_coverage = planning_state.provider_coverage.model_copy(deep=True)
    before_route_feasibility = (
        planning_state.experience_plan.route_feasibility_context.model_copy(deep=True)
    )
    before_feedback_history = [
        event.model_copy(deep=True) for event in planning_state.feedback_history
    ]
    before_pending_feedback_summary = planning_state.pending_feedback_summary.model_copy(
        deep=True
    )
    before_user_locks = [lock.model_copy(deep=True) for lock in planning_state.user_locks]
    before_version_history = [
        item.model_copy(deep=True) for item in planning_state.version_history
    ]
    before_plan_diff_preview = planning_state.plan_diff_preview.model_copy(deep=True)

    result = regeneration_readiness_service.recompute(planning_state)

    assert result.experience_plan == before_experience_plan
    assert result.validation_report == before_validation_report
    assert result.provider_coverage == before_provider_coverage
    assert result.experience_plan.route_feasibility_context == before_route_feasibility
    assert result.feedback_history == before_feedback_history
    assert result.pending_feedback_summary == before_pending_feedback_summary
    assert result.user_locks == before_user_locks
    assert result.version_history == before_version_history
    assert result.plan_diff_preview == before_plan_diff_preview


def test_recompute_never_invents_travel_facts() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state = VersioningService().create_initial_version(planning_state)
    planning_state.feedback_history.append(
        FeedbackEvent(
            feedback_text="Make this less packed",
            feedback_type="pace_change",
            affected_stages=[PlanningStage.EXPERIENCE_PLAN],
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        )
    )

    result = regeneration_readiness_service.recompute(planning_state)
    readiness = result.regeneration_readiness

    known_inputs = {
        "generated_plan",
        "version_history",
        "pending_feedback",
        "plan_diff_preview",
        "user_locks",
        "regeneration_engine",
    }
    assert set(readiness.required_inputs) <= known_inputs
    assert set(readiness.available_inputs) <= known_inputs
    assert set(readiness.missing_capabilities) <= known_inputs
    assert readiness.can_regenerate is True
    assert readiness.status == "ready"


def test_recompute_blocked_by_active_locks_even_with_derivable_feedback() -> None:
    """Step 174D: version exists, feedback exists and classifies to a real
    stage, but an active lock exists -- must stay blocked, never `ready`,
    since locks are disallowed entirely for this MVP scope.
    """
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state = VersioningService().create_initial_version(planning_state)
    planning_state.feedback_history.append(
        FeedbackEvent(
            feedback_text="Make this less packed",
            feedback_type="pace_change",
            affected_stages=[PlanningStage.EXPERIENCE_PLAN],
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        )
    )
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_test_1")
    )

    result = regeneration_readiness_service.recompute(planning_state)
    readiness = result.regeneration_readiness

    assert readiness.status == "blocked"
    assert readiness.can_regenerate is False
    assert readiness.active_lock_count == 1
    assert readiness.would_create_version == "v2"
    assert any("lock" in reason.lower() for reason in readiness.blocked_by)


def test_recompute_blocked_when_pending_feedback_has_no_derivable_stage() -> None:
    """Step 174D: version exists, feedback exists, but classifies to no
    affected stage at all (`affected_stages=[]`, matching an unclassified
    `general_feedback` event) -- must stay blocked, never `ready`.
    """
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state = VersioningService().create_initial_version(planning_state)
    planning_state.feedback_history.append(
        FeedbackEvent(
            feedback_text="Please make it wonderful",
            feedback_type="general_feedback",
            affected_stages=[],
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        )
    )

    result = regeneration_readiness_service.recompute(planning_state)
    readiness = result.regeneration_readiness

    assert readiness.status == "blocked"
    assert readiness.can_regenerate is False
    assert readiness.pending_feedback_count == 1


def test_recompute_blocked_when_all_feedback_already_applied() -> None:
    """Step 174D: feedback exists, but every event's `applied_at` is
    already set (a prior successful regeneration consumed it) -- this
    must be treated exactly like "no pending feedback", never `ready`.
    """
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state = VersioningService().create_initial_version(planning_state)
    applied_event = FeedbackEvent(
        feedback_text="Make this less packed",
        feedback_type="pace_change",
        affected_stages=[PlanningStage.EXPERIENCE_PLAN],
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
    )
    applied_event.applied_at = datetime.now(timezone.utc)
    applied_event.applied_in_version = "v2"
    planning_state.feedback_history.append(applied_event)

    result = regeneration_readiness_service.recompute(planning_state)
    readiness = result.regeneration_readiness

    assert readiness.status == "blocked"
    assert readiness.can_regenerate is False
    assert readiness.pending_feedback_count == 0
    assert readiness.would_create_version is None
