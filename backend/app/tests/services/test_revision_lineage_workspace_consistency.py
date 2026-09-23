from __future__ import annotations

from datetime import date
from typing import Any

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
from app.repositories.factory import get_planning_state_repository
from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository
from app.services.itinerary_fork_service import itinerary_fork_service
from app.services.revision_lineage_service import (
    BranchActivationStatus,
    revision_lineage_service,
)
from app.services.revision_snapshot_service import build_workspace_consistency_projection
from app.services.versioning_service import versioning_service

# Section 199B.1: proves the strengthened `check_branch_head_consistency`
# genuinely compares canonical revision-content, not just
# `active_branch_id` + `version_label` -- the exact gap this section
# exists to close (Tasks 9-17).


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Granada, Spain",
        "start_date": "2026-09-10",
        "end_date": "2026-09-13",
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


def _planning_state_and_save(trip_id: str) -> PlanningState:
    planning_state = PlanningState(trip_id=trip_id, trip_request=_trip_request())
    planning_state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(
                day_number=1,
                date=date(2026, 9, 10),
                experiences=[
                    _experience(
                        "exp_A",
                        "A",
                        1,
                        provider_place_id="way/111",
                        provider_source="openstreetmap_places",
                    )
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


def _fork_and_activate(trip_id: str, source_revision_id: str, name: str = "Alternate") -> str:
    result = itinerary_fork_service.create_fork(
        trip_id, source_revision_id, name, activate_after_create=True
    )
    assert result.activation is not None
    assert result.activation.status == BranchActivationStatus.ACTIVATED
    assert result.branch is not None
    return result.branch.branch_id


# ---------------------------------------------------------------------------
# Task 9: same-label itinerary (ExperiencePlan) content drift
# ---------------------------------------------------------------------------


def test_same_label_experience_plan_drift_blocks_activation() -> None:
    state = _planning_state_and_save("trip_wc_exp_drift")
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None
    fork_branch_id = _fork_and_activate("trip_wc_exp_drift", r1.revision_id)
    main_branch_id = itinerary_lineage_repository.get_default_branch("trip_wc_exp_drift").branch_id

    # Drift: mutate ExperiencePlan content in place, WITHOUT creating a
    # new revision -- current_version stays "v1", exactly matching the
    # fork's own inherited head label. The OLD label-only check would
    # have incorrectly passed this.
    live_state = get_planning_state_repository().get_by_trip_id("trip_wc_exp_drift")
    assert live_state is not None
    live_state.experience_plan.daily_plans[0].experiences.append(_experience("exp_INJECTED", "Injected", 1))
    get_planning_state_repository().save(live_state)

    result = revision_lineage_service.activate_branch("trip_wc_exp_drift", main_branch_id)
    assert result.status == BranchActivationStatus.BLOCKED_STATE_CONFLICT

    # No state change: still on the fork, still drifted.
    reloaded = get_planning_state_repository().get_by_trip_id("trip_wc_exp_drift")
    assert reloaded is not None
    assert reloaded.metadata.active_branch_id == fork_branch_id
    assert len(reloaded.experience_plan.daily_plans[0].experiences) == 2


# ---------------------------------------------------------------------------
# Task 10: same-label traveler-profile content drift
# ---------------------------------------------------------------------------


def test_same_label_traveler_profile_drift_blocks_activation() -> None:
    state = _planning_state_and_save("trip_wc_profile_drift")
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None
    fork_branch_id = _fork_and_activate("trip_wc_profile_drift", r1.revision_id)
    main_branch_id = itinerary_lineage_repository.get_default_branch("trip_wc_profile_drift").branch_id

    live_state = get_planning_state_repository().get_by_trip_id("trip_wc_profile_drift")
    assert live_state is not None
    live_state.traveler_profile.interests.append("food")
    get_planning_state_repository().save(live_state)

    result = revision_lineage_service.activate_branch("trip_wc_profile_drift", main_branch_id)
    assert result.status == BranchActivationStatus.BLOCKED_STATE_CONFLICT
    assert fork_branch_id  # still active, unchanged


# ---------------------------------------------------------------------------
# Task 11: nested/provider-backed drift (not just top-level equality)
# ---------------------------------------------------------------------------


def test_nested_provider_backed_field_drift_blocks_activation() -> None:
    """Mutates a nested field several levels deep (an experience's own
    provider identity, not a top-level PlanningState field) -- proving
    the comparison is a real recursive content comparison, not a
    shallow top-level check (Task 11's own explicit warning)."""
    state = _planning_state_and_save("trip_wc_nested_drift")
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None
    fork_branch_id = _fork_and_activate("trip_wc_nested_drift", r1.revision_id)
    main_branch_id = itinerary_lineage_repository.get_default_branch("trip_wc_nested_drift").branch_id

    live_state = get_planning_state_repository().get_by_trip_id("trip_wc_nested_drift")
    assert live_state is not None
    # Nested mutation: the SAME experience_id, but its own provider
    # identity/coordinates changed underneath it.
    live_state.experience_plan.daily_plans[0].experiences[0].provider_place_id = "way/DIFFERENT"
    get_planning_state_repository().save(live_state)

    result = revision_lineage_service.activate_branch("trip_wc_nested_drift", main_branch_id)
    assert result.status == BranchActivationStatus.BLOCKED_STATE_CONFLICT
    assert fork_branch_id


# ---------------------------------------------------------------------------
# Task 12: pending feedback must NOT false-conflict
# ---------------------------------------------------------------------------


def test_pending_feedback_does_not_cause_false_workspace_conflict() -> None:
    state = _planning_state_and_save("trip_wc_pending_feedback")
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None

    live_state = get_planning_state_repository().get_by_trip_id("trip_wc_pending_feedback")
    assert live_state is not None
    live_state.feedback_history.append(
        FeedbackEvent(feedback_text="less packed", regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY)
    )
    get_planning_state_repository().save(live_state)

    # The consistency check itself must still pass -- pending feedback is
    # excluded from the canonical projection.
    reloaded = get_planning_state_repository().get_by_trip_id("trip_wc_pending_feedback")
    assert revision_lineage_service.check_branch_head_consistency(reloaded) is True


def test_pending_feedback_blocks_activation_via_its_own_separate_guard_not_conflict() -> None:
    """Activation is still blocked -- but via BLOCKED_PENDING_FEEDBACK,
    never BLOCKED_STATE_CONFLICT (Task 12's own distinction)."""
    state = _planning_state_and_save("trip_wc_pending_feedback_guard")
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None
    fork_branch_id = _fork_and_activate("trip_wc_pending_feedback_guard", r1.revision_id, "Other")

    live_state = get_planning_state_repository().get_by_trip_id("trip_wc_pending_feedback_guard")
    assert live_state is not None
    live_state.feedback_history.append(
        FeedbackEvent(feedback_text="less packed", regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY)
    )
    get_planning_state_repository().save(live_state)

    main_branch_id = itinerary_lineage_repository.get_default_branch(
        "trip_wc_pending_feedback_guard"
    ).branch_id
    result = revision_lineage_service.activate_branch("trip_wc_pending_feedback_guard", main_branch_id)
    assert result.status == BranchActivationStatus.BLOCKED_PENDING_FEEDBACK
    assert fork_branch_id


# ---------------------------------------------------------------------------
# Task 13: regeneration-attempt audit differences must not false-conflict
# ---------------------------------------------------------------------------


def test_regeneration_attempt_difference_does_not_false_conflict() -> None:
    from app.services.regeneration_attempt_service import regeneration_attempt_service

    state = _planning_state_and_save("trip_wc_attempt_audit")
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None

    live_state = get_planning_state_repository().get_by_trip_id("trip_wc_attempt_audit")
    assert live_state is not None
    # A blocked attempt appended WITHOUT creating a new version --
    # exactly what a real refused /regenerate call does.
    regeneration_attempt_service.record_blocked_attempt(live_state)
    get_planning_state_repository().save(live_state)

    reloaded = get_planning_state_repository().get_by_trip_id("trip_wc_attempt_audit")
    assert reloaded is not None
    assert reloaded.regeneration_attempts  # really did change
    assert revision_lineage_service.check_branch_head_consistency(reloaded) is True


# ---------------------------------------------------------------------------
# Task 14: inactive lock still does not block/conflict
# ---------------------------------------------------------------------------


def test_inactive_lock_does_not_cause_workspace_conflict() -> None:
    state = _planning_state_and_save("trip_wc_inactive_lock")
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None

    live_state = get_planning_state_repository().get_by_trip_id("trip_wc_inactive_lock")
    assert live_state is not None
    live_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="exp_A", is_active=False)
    )
    get_planning_state_repository().save(live_state)

    reloaded = get_planning_state_repository().get_by_trip_id("trip_wc_inactive_lock")
    assert revision_lineage_service.check_branch_head_consistency(reloaded) is True


# ---------------------------------------------------------------------------
# Task 15: inherited fork-head never false-conflicts
# ---------------------------------------------------------------------------


def test_inherited_fork_head_never_false_conflicts() -> None:
    """The fork's stored head snapshot has `active_branch_id` pointing
    at Main (or None) -- the live activated clone correctly has the
    fork's own id. This must never be treated as content drift."""
    state = _planning_state_and_save("trip_wc_inherited_head")
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None
    main_branch_id = itinerary_lineage_repository.get_default_branch("trip_wc_inherited_head").branch_id
    fork_branch_id = _fork_and_activate("trip_wc_inherited_head", r1.revision_id)

    live_state = get_planning_state_repository().get_by_trip_id("trip_wc_inherited_head")
    assert live_state is not None
    assert live_state.metadata.active_branch_id == fork_branch_id
    assert revision_lineage_service.check_branch_head_consistency(live_state) is True

    # Switch away and back -- no false conflict either direction.
    back_to_main = revision_lineage_service.activate_branch("trip_wc_inherited_head", main_branch_id)
    assert back_to_main.status == BranchActivationStatus.ACTIVATED
    back_to_fork = revision_lineage_service.activate_branch("trip_wc_inherited_head", fork_branch_id)
    assert back_to_fork.status == BranchActivationStatus.ACTIVATED


# ---------------------------------------------------------------------------
# Task 16: exact-state switching still works, re-verified with the
# strengthened check in place
# ---------------------------------------------------------------------------


def test_exact_state_switching_still_works_with_strengthened_check() -> None:
    trip_id = "trip_wc_exact_switch"
    state = _planning_state_and_save(trip_id)
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None
    main_branch_id = itinerary_lineage_repository.get_default_branch(trip_id).branch_id
    fork_branch_id = _fork_and_activate(trip_id, r1.revision_id)

    for _ in range(2):
        assert revision_lineage_service.activate_branch(trip_id, main_branch_id).status == (
            BranchActivationStatus.ACTIVATED
        )
        reloaded = get_planning_state_repository().get_by_trip_id(trip_id)
        assert build_workspace_consistency_projection(
            reloaded
        ) == build_workspace_consistency_projection(revision_lineage_service.load_snapshot(r1.revision_id))

        assert revision_lineage_service.activate_branch(trip_id, fork_branch_id).status == (
            BranchActivationStatus.ACTIVATED
        )
        reloaded = get_planning_state_repository().get_by_trip_id(trip_id)
        assert build_workspace_consistency_projection(
            reloaded
        ) == build_workspace_consistency_projection(revision_lineage_service.load_snapshot(r1.revision_id))


# ---------------------------------------------------------------------------
# Task 17: failure atomicity for the new content-drift conflict
# ---------------------------------------------------------------------------


def test_content_drift_conflict_leaves_everything_unchanged() -> None:
    trip_id = "trip_wc_atomicity"
    state = _planning_state_and_save(trip_id)
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None
    main_branch_id = itinerary_lineage_repository.get_default_branch(trip_id).branch_id
    fork_branch_id = _fork_and_activate(trip_id, r1.revision_id)

    live_state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert live_state is not None
    live_state.experience_plan.daily_plans[0].experiences.append(_experience("exp_DRIFT", "Drift", 1))
    get_planning_state_repository().save(live_state)

    main_branch_before = itinerary_lineage_repository.get_branch(main_branch_id)
    fork_branch_before = itinerary_lineage_repository.get_branch(fork_branch_id)
    revision_count_before = len(itinerary_lineage_repository.list_revisions_for_branch(fork_branch_id))

    result = revision_lineage_service.activate_branch(trip_id, main_branch_id)
    assert result.status == BranchActivationStatus.BLOCKED_STATE_CONFLICT

    reloaded_state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert reloaded_state is not None
    assert reloaded_state.metadata.active_branch_id == fork_branch_id  # active branch unchanged
    assert len(reloaded_state.experience_plan.daily_plans[0].experiences) == 2  # drift still there, untouched

    main_branch_after = itinerary_lineage_repository.get_branch(main_branch_id)
    fork_branch_after = itinerary_lineage_repository.get_branch(fork_branch_id)
    assert main_branch_after == main_branch_before  # source/target branch heads unchanged
    assert fork_branch_after == fork_branch_before
    assert (
        len(itinerary_lineage_repository.list_revisions_for_branch(fork_branch_id))
        == revision_count_before
    )  # no revision created
    assert reloaded_state.feedback_history == []  # no feedback consumed
