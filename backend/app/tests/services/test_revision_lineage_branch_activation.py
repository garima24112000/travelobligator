from __future__ import annotations

from datetime import date
from typing import Any

from app.models.common import RegenerationStrategy
from app.models.generation_job import GenerationJobType, create_queued_job, mark_job_running
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
from app.repositories.factory import get_planning_state_repository
from app.repositories.job_repository import job_repository
from app.services.itinerary_fork_service import itinerary_fork_service
from app.services.revision_lineage_service import (
    BranchActivationStatus,
    revision_lineage_service,
)
from app.services.versioning_service import versioning_service

# Section 199B (Tasks 14-18/36-39): direct service-level tests for
# `RevisionLineageService.activate_branch` -- every workspace-cleanliness
# guard, checked independently, against the real `planning_state_repository`/
# `itinerary_lineage_repository`/`job_repository` singletons (all reset
# between tests by conftest).


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Bilbao, Spain",
        "start_date": "2026-09-10",
        "end_date": "2026-09-13",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
        "interests": ["art"],
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
            DailyPlan(day_number=1, date=date(2026, 9, 10), experiences=[_experience("exp_A", "A", 1)]),
        ]
    )
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE, travelers_count=2, pace=TripPace.BALANCED, interests=["art"]
    )
    versioning_service.create_initial_version(planning_state)
    get_planning_state_repository().save(planning_state)
    return planning_state


def _fork(trip_id: str, source_revision_id: str, name: str = "Option B") -> str:
    result = itinerary_fork_service.create_fork(trip_id, source_revision_id, name)
    assert result.branch is not None
    return result.branch.branch_id


def test_activate_unknown_branch_returns_branch_not_found() -> None:
    planning_state = _planning_state_and_save("trip_act_unknown_branch")
    revision_lineage_service.record_current_revision(planning_state)

    result = revision_lineage_service.activate_branch(
        "trip_act_unknown_branch", "branch_does_not_exist"
    )
    assert result.status == BranchActivationStatus.BRANCH_NOT_FOUND


def test_activate_branch_from_another_trip_returns_branch_not_found() -> None:
    """Task 39/49: never reveal a branch belonging to a different trip."""
    state_a = _planning_state_and_save("trip_act_owner_a")
    revision_a = revision_lineage_service.record_current_revision(state_a)
    assert revision_a is not None
    branch_a = _fork("trip_act_owner_a", revision_a.revision_id)

    _planning_state_and_save("trip_act_owner_b")

    result = revision_lineage_service.activate_branch("trip_act_owner_b", branch_a)
    assert result.status == BranchActivationStatus.BRANCH_NOT_FOUND


def test_activate_branch_with_no_head_revision_is_snapshot_unavailable() -> None:
    """A branch that was somehow created with no head at all (defensive
    -- a real fork always starts with head=base=source) is refused
    honestly rather than activated onto nothing."""
    from app.models.itinerary_lineage import ItineraryBranch

    _planning_state_and_save("trip_act_no_head")
    from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository

    empty_branch = itinerary_lineage_repository.create_branch(
        ItineraryBranch(trip_id="trip_act_no_head", display_name="Empty")
    )

    result = revision_lineage_service.activate_branch("trip_act_no_head", empty_branch.branch_id)
    assert result.status == BranchActivationStatus.SNAPSHOT_UNAVAILABLE


# ---------------------------------------------------------------------------
# Task 36: pending-feedback guard
# ---------------------------------------------------------------------------


def test_activation_blocked_by_pending_feedback_on_current_branch() -> None:
    planning_state = _planning_state_and_save("trip_act_pending_feedback")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None
    branch_id = _fork("trip_act_pending_feedback", source.revision_id)

    planning_state.feedback_history.append(
        FeedbackEvent(feedback_text="pending", regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY)
    )
    get_planning_state_repository().save(planning_state)

    result = revision_lineage_service.activate_branch("trip_act_pending_feedback", branch_id)
    assert result.status == BranchActivationStatus.BLOCKED_PENDING_FEEDBACK

    # Main remains active, state unchanged, feedback still pending.
    reloaded = get_planning_state_repository().get_by_trip_id("trip_act_pending_feedback")
    assert reloaded is not None
    assert revision_lineage_service.resolve_active_branch_id(reloaded) != branch_id
    assert reloaded.feedback_history[-1].applied_at is None


# ---------------------------------------------------------------------------
# Task 37: running-job guard
# ---------------------------------------------------------------------------


def test_activation_blocked_by_running_job() -> None:
    planning_state = _planning_state_and_save("trip_act_running_job")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None
    branch_id = _fork("trip_act_running_job", source.revision_id)

    job = create_queued_job(
        trip_id="trip_act_running_job", owner_id="user_x", job_type=GenerationJobType.REGENERATE
    )
    mark_job_running(job)
    job_repository.create(job)

    result = revision_lineage_service.activate_branch("trip_act_running_job", branch_id)
    assert result.status == BranchActivationStatus.BLOCKED_RUNNING_JOB

    reloaded = get_planning_state_repository().get_by_trip_id("trip_act_running_job")
    assert revision_lineage_service.resolve_active_branch_id(reloaded) != branch_id


# ---------------------------------------------------------------------------
# Task 38: active-lock guard
# ---------------------------------------------------------------------------


def test_activation_blocked_by_active_lock() -> None:
    planning_state = _planning_state_and_save("trip_act_active_lock")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None
    branch_id = _fork("trip_act_active_lock", source.revision_id)

    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="exp_A", is_active=True)
    )
    get_planning_state_repository().save(planning_state)

    result = revision_lineage_service.activate_branch("trip_act_active_lock", branch_id)
    assert result.status == BranchActivationStatus.BLOCKED_ACTIVE_LOCK

    # No hidden unlock -- the lock is still active afterward.
    reloaded = get_planning_state_repository().get_by_trip_id("trip_act_active_lock")
    assert reloaded is not None
    assert reloaded.user_locks[-1].is_active is True


def test_removed_lock_does_not_block_activation() -> None:
    planning_state = _planning_state_and_save("trip_act_removed_lock")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None
    branch_id = _fork("trip_act_removed_lock", source.revision_id)

    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="exp_A", is_active=False)
    )
    get_planning_state_repository().save(planning_state)

    result = revision_lineage_service.activate_branch("trip_act_removed_lock", branch_id)
    assert result.status == BranchActivationStatus.ACTIVATED


# ---------------------------------------------------------------------------
# Task 39: lineage-mismatch guard
# ---------------------------------------------------------------------------


def test_activation_blocked_by_current_branch_state_conflict() -> None:
    planning_state = _planning_state_and_save("trip_act_conflict")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None
    branch_id = _fork("trip_act_conflict", source.revision_id)

    # Corrupt the current state so it disagrees with Main's own recorded
    # head (Task 39: "active branch head R3 but current PlanningState
    # corresponds to R2").
    planning_state.metadata.current_version = "v99_does_not_exist"
    get_planning_state_repository().save(planning_state)

    result = revision_lineage_service.activate_branch("trip_act_conflict", branch_id)
    assert result.status == BranchActivationStatus.BLOCKED_STATE_CONFLICT


# ---------------------------------------------------------------------------
# Successful activation + restoring Main
# ---------------------------------------------------------------------------


def test_successful_activation_restores_target_branch_snapshot_exactly() -> None:
    planning_state = _planning_state_and_save("trip_act_success")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None
    branch_id = _fork("trip_act_success", source.revision_id)

    result = revision_lineage_service.activate_branch("trip_act_success", branch_id)

    assert result.status == BranchActivationStatus.ACTIVATED
    assert result.active_branch_id == branch_id
    assert result.head_revision_id == source.revision_id
    assert result.current_version == "v1"
    assert result.previous_branch_id != branch_id

    reloaded = get_planning_state_repository().get_by_trip_id("trip_act_success")
    assert reloaded is not None
    assert reloaded.metadata.active_branch_id == branch_id
    assert reloaded.metadata.current_version == "v1"
    assert [e.experience_id for e in reloaded.experience_plan.daily_plans[0].experiences] == ["exp_A"]

    # Task 4/28: consistency check now passes for the newly active branch.
    assert revision_lineage_service.check_branch_head_consistency(reloaded) is True


def test_activating_back_to_main_restores_mains_snapshot() -> None:
    planning_state = _planning_state_and_save("trip_act_back_to_main")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None
    default_branch_id = revision_lineage_service.resolve_active_branch_id(planning_state)
    branch_id = _fork("trip_act_back_to_main", source.revision_id)

    activated_to_b = revision_lineage_service.activate_branch("trip_act_back_to_main", branch_id)
    assert activated_to_b.status == BranchActivationStatus.ACTIVATED

    back_to_main = revision_lineage_service.activate_branch(
        "trip_act_back_to_main", default_branch_id
    )
    assert back_to_main.status == BranchActivationStatus.ACTIVATED
    assert back_to_main.active_branch_id == default_branch_id
    assert back_to_main.previous_branch_id == branch_id

    reloaded = get_planning_state_repository().get_by_trip_id("trip_act_back_to_main")
    assert reloaded is not None
    assert reloaded.metadata.active_branch_id == default_branch_id
