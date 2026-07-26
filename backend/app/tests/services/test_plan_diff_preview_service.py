from __future__ import annotations

from app.models.common import RegenerationStrategy
from app.models.planning_state import (
    ExperiencePlan,
    FeedbackEvent,
    PlanningStage,
    PlanningState,
    TravelGroupType,
    TripRequest,
    UserLock,
    ValidationReport,
)
from app.services.plan_diff_preview_service import plan_diff_preview_service
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


def test_default_preview_on_new_planning_state_is_not_available() -> None:
    planning_state = _planning_state()
    preview = planning_state.plan_diff_preview

    assert preview.preview_status == "not_available"
    assert preview.from_version is None
    assert preview.to_version is None
    assert preview.regeneration_available is False
    assert preview.would_create_version is None
    assert preview.triggered_by_feedback_event_ids == []
    assert preview.pending_feedback_count == 0
    assert preview.active_lock_count == 0
    assert preview.would_consider_sections == []
    assert preview.would_preserve_locked_items == []
    assert "no plan has been generated" in preview.note.lower()


def test_recompute_before_generation_stays_not_available() -> None:
    planning_state = _planning_state()
    result = plan_diff_preview_service.recompute(planning_state)
    preview = result.plan_diff_preview

    assert preview.preview_status == "not_available"
    assert preview.from_version is None
    assert preview.would_create_version is None


def test_recompute_after_generation_with_no_feedback() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state.validation_report = ValidationReport()
    planning_state = VersioningService().create_initial_version(planning_state)

    result = plan_diff_preview_service.recompute(planning_state)
    preview = result.plan_diff_preview

    assert preview.preview_status == "not_available"
    assert preview.from_version == "v1"
    assert preview.would_create_version is None
    assert preview.pending_feedback_count == 0
    assert "no pending feedback" in preview.note.lower()


def test_recompute_after_generation_with_feedback_is_ready_for_preview() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state.validation_report = ValidationReport()
    planning_state = VersioningService().create_initial_version(planning_state)

    feedback_event = FeedbackEvent(
        feedback_text="Make this less packed",
        feedback_type="pace_change",
        affected_stages=[PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION],
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
    )
    planning_state.feedback_history.append(feedback_event)

    result = plan_diff_preview_service.recompute(planning_state)
    preview = result.plan_diff_preview

    assert preview.preview_status == "ready_for_future_regeneration_preview"
    assert preview.from_version == "v1"
    assert preview.would_create_version == "v2"
    assert preview.triggered_by_feedback_event_ids == [feedback_event.feedback_event_id]
    assert preview.pending_feedback_count == 1
    assert preview.would_consider_sections == [
        PlanningStage.EXPERIENCE_PLAN,
        PlanningStage.VALIDATION,
    ]
    assert preview.regeneration_available is False
    assert preview.to_version is None
    assert "no plan diff has been generated or applied" in preview.note.lower()


def test_would_consider_sections_is_union_ordered_by_stage_order() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state = VersioningService().create_initial_version(planning_state)

    # Second event contributes an earlier-order stage than the first, so the
    # union must come back in PlanningStage declaration order, not insertion
    # order.
    planning_state.feedback_history.append(
        FeedbackEvent(
            feedback_text="less packed please",
            feedback_type="pace_change",
            affected_stages=[PlanningStage.VALIDATION],
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        )
    )
    planning_state.feedback_history.append(
        FeedbackEvent(
            feedback_text="more museums",
            feedback_type="interest_change",
            affected_stages=[
                PlanningStage.TRAVELER_PROFILE,
                PlanningStage.DESTINATION_CONTEXT,
                PlanningStage.EXPERIENCE_PLAN,
                PlanningStage.VALIDATION,
            ],
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        )
    )

    result = plan_diff_preview_service.recompute(planning_state)
    preview = result.plan_diff_preview

    assert preview.would_consider_sections == [
        PlanningStage.TRAVELER_PROFILE,
        PlanningStage.DESTINATION_CONTEXT,
        PlanningStage.EXPERIENCE_PLAN,
        PlanningStage.VALIDATION,
    ]
    assert preview.pending_feedback_count == 2
    assert len(preview.triggered_by_feedback_event_ids) == 2


def test_active_locks_are_reflected_regardless_of_feedback() -> None:
    planning_state = _planning_state()
    planning_state.user_locks.append(
        UserLock(
            locked_item_type="experience",
            locked_item_id="experience_test_1",
            reason="user_requested_keep",
        )
    )

    result = plan_diff_preview_service.recompute(planning_state)
    preview = result.plan_diff_preview

    assert preview.active_lock_count == 1
    assert len(preview.would_preserve_locked_items) == 1
    preserved = preview.would_preserve_locked_items[0]
    assert preserved.locked_item_type == "experience"
    assert preserved.locked_item_id == "experience_test_1"
    assert preserved.reason == "user_requested_keep"


def test_removed_lock_is_excluded_from_active_count_and_preserved_items() -> None:
    planning_state = _planning_state()
    lock = UserLock(
        locked_item_type="experience",
        locked_item_id="experience_test_1",
        reason="user_requested_keep",
    )
    planning_state.user_locks.append(lock)

    result = plan_diff_preview_service.recompute(planning_state)
    assert result.plan_diff_preview.active_lock_count == 1

    lock.is_active = False
    result = plan_diff_preview_service.recompute(result)

    assert result.plan_diff_preview.active_lock_count == 0
    assert result.plan_diff_preview.would_preserve_locked_items == []


def test_recompute_does_not_mutate_other_sections() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state.validation_report = ValidationReport()
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

    before_experience_plan = planning_state.experience_plan.model_copy(deep=True)
    before_validation_report = planning_state.validation_report.model_copy(deep=True)
    before_provider_coverage = planning_state.provider_coverage.model_copy(deep=True)
    before_route_feasibility = (
        planning_state.experience_plan.route_feasibility_context.model_copy(deep=True)
    )
    before_feedback_history = [event.model_copy(deep=True) for event in planning_state.feedback_history]
    before_pending_feedback_summary = planning_state.pending_feedback_summary.model_copy(
        deep=True
    )
    before_user_locks = [lock.model_copy(deep=True) for lock in planning_state.user_locks]
    before_version_history = [
        item.model_copy(deep=True) for item in planning_state.version_history
    ]

    result = plan_diff_preview_service.recompute(planning_state)

    assert result.experience_plan == before_experience_plan
    assert result.validation_report == before_validation_report
    assert result.provider_coverage == before_provider_coverage
    assert result.experience_plan.route_feasibility_context == before_route_feasibility
    assert result.feedback_history == before_feedback_history
    assert result.pending_feedback_summary == before_pending_feedback_summary
    assert result.user_locks == before_user_locks
    assert result.version_history == before_version_history


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
    planning_state.user_locks.append(
        UserLock(
            locked_item_type="experience",
            locked_item_id="experience_test_1",
            reason="user_requested_keep",
        )
    )

    result = plan_diff_preview_service.recompute(planning_state)
    preview = result.plan_diff_preview

    known_sections = {
        "traveler_profile",
        "destination_context",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation_report",
        "provider_coverage",
        "route_feasibility_context",
    }
    assert {stage.value for stage in preview.would_consider_sections} <= known_sections
    assert preview.regeneration_available is False
    assert preview.to_version is None
    for preserved in preview.would_preserve_locked_items:
        # Only the fields already on UserLock are carried over -- no place
        # name, coordinates, price, rating, or opening-hours field exists.
        assert set(preserved.model_dump().keys()) == {
            "locked_item_type",
            "locked_item_id",
            "reason",
        }
