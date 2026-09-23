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
)
from app.repositories.factory import get_planning_state_repository
from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository
from app.services.itinerary_fork_service import itinerary_fork_service
from app.services.revision_lineage_service import (
    BranchActivationStatus,
    revision_lineage_service,
)
from app.services.versioning_service import versioning_service

# Section 199B Task 40: THE central acceptance test for this whole
# section --
#
#   Main:      R1 -> R2 -> R3M
#   Alternate:       R2 -> R3A -> R4A
#
# proving two branches evolve completely independently after a fork,
# using nothing but the real `RevisionLineageService`/`ItineraryForkService`
# singletons (no HTTP layer -- `test_itinerary_lineage_api.py` covers
# that separately).


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Cordoba, Spain",
        "start_date": "2026-09-10",
        "end_date": "2026-09-14",
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
            DailyPlan(day_number=1, date=date(2026, 9, 10), experiences=[_experience("exp_A", "A", 1)]),
        ]
    )
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE, travelers_count=2, pace=TripPace.BALANCED, interests=["history"]
    )
    versioning_service.create_initial_version(planning_state)
    get_planning_state_repository().save(planning_state)
    return planning_state


def _regenerate_once(trip_id: str, new_experience_id: str, feedback_text: str) -> PlanningState:
    """A minimal, direct simulation of "submit feedback then
    regenerate" -- appends one experience (so each branch's content is
    observably different), advances the version via the real
    `versioning_service`, saves, and records the revision via the real
    `RevisionLineageService` -- exactly matching what the real legacy/
    targeted regeneration call sites do at the moment they call
    `record_current_revision`."""
    state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert state is not None
    event = FeedbackEvent(feedback_text=feedback_text, regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY)
    state.feedback_history.append(event)
    state.experience_plan.daily_plans[0].experiences.append(
        _experience(new_experience_id, new_experience_id, 1)
    )
    state = versioning_service.create_version_after_feedback(
        state,
        feedback_event_id=event.feedback_event_id,
        changed_sections=["experience_plan"],
        preserved_sections=[],
        summary="test regeneration",
    )
    event.applied_at = state.metadata.updated_at
    event.applied_in_version = state.metadata.current_version
    event.handling_status = "applied"
    get_planning_state_repository().save(state)
    revision = revision_lineage_service.record_current_revision(state)
    assert revision is not None
    return state


def test_branch_independence_end_to_end() -> None:
    trip_id = "trip_branch_independence"

    # R1 -- initial generation.
    state = _planning_state_and_save(trip_id)
    r1 = revision_lineage_service.record_current_revision(state)
    assert r1 is not None
    main_branch_id = revision_lineage_service.resolve_active_branch_id(state)

    # R2 -- one regeneration on Main.
    state = _regenerate_once(trip_id, "exp_R2", "add R2 place")
    r2 = itinerary_lineage_repository.get_revision(
        itinerary_lineage_repository.get_branch(main_branch_id).head_revision_id
    )
    assert r2 is not None
    assert r2.parent_revision_id == r1.revision_id

    # Fork "Alternate" from R2, activate it immediately.
    fork_result = itinerary_fork_service.create_fork(
        trip_id, r2.revision_id, "Alternate", activate_after_create=True
    )
    assert fork_result.branch is not None
    assert fork_result.activation is not None
    assert fork_result.activation.status == BranchActivationStatus.ACTIVATED
    alternate_branch_id = fork_result.branch.branch_id
    assert fork_result.branch.base_revision_id == r2.revision_id
    assert fork_result.branch.head_revision_id == r2.revision_id

    # R3M -- continue Main WITHOUT switching back yet (simulates the
    # "someone keeps working on Main" scenario implicitly by directly
    # recording against Main's own branch -- see the explicit
    # cross-branch parent/head assertions below for the real proof this
    # matters).
    state_for_main = revision_lineage_service.load_snapshot(r2.revision_id)
    assert state_for_main is not None
    state_for_main.metadata.active_branch_id = main_branch_id
    get_planning_state_repository().save(state_for_main)
    state_for_main = _regenerate_once(trip_id, "exp_R3M", "add R3M place (Main only)")
    main_branch_after_r3m = itinerary_lineage_repository.get_branch(main_branch_id)
    assert main_branch_after_r3m is not None
    r3m = itinerary_lineage_repository.get_revision(main_branch_after_r3m.head_revision_id)
    assert r3m is not None
    assert r3m.parent_revision_id == r2.revision_id
    assert r3m.branch_id == main_branch_id

    # Switch to Alternate and advance it twice: R3A -> R4A.
    activate_alt = revision_lineage_service.activate_branch(trip_id, alternate_branch_id)
    assert activate_alt.status == BranchActivationStatus.ACTIVATED
    assert activate_alt.head_revision_id == r2.revision_id  # inherited from Main at fork time

    _regenerate_once(trip_id, "exp_R3A", "add R3A place (Alternate only)")
    alternate_branch_after_r3a = itinerary_lineage_repository.get_branch(alternate_branch_id)
    assert alternate_branch_after_r3a is not None
    r3a = itinerary_lineage_repository.get_revision(alternate_branch_after_r3a.head_revision_id)
    assert r3a is not None
    assert r3a.parent_revision_id == r2.revision_id  # Task 25: parent works even though
    assert r3a.branch_id == alternate_branch_id       # R2 belongs to Main (Task 9/10).

    _regenerate_once(trip_id, "exp_R4A", "add R4A place (Alternate only)")
    alternate_branch_after_r4a = itinerary_lineage_repository.get_branch(alternate_branch_id)
    assert alternate_branch_after_r4a is not None
    r4a = itinerary_lineage_repository.get_revision(alternate_branch_after_r4a.head_revision_id)
    assert r4a is not None
    assert r4a.parent_revision_id == r3a.revision_id
    assert r4a.branch_id == alternate_branch_id

    # --- Central assertions (Task 40) -----------------------------------

    # Source R2 unchanged.
    reloaded_r2 = itinerary_lineage_repository.get_revision(r2.revision_id)
    assert reloaded_r2 == r2

    # Main R3M unchanged by everything that happened on Alternate.
    reloaded_r3m = itinerary_lineage_repository.get_revision(r3m.revision_id)
    assert reloaded_r3m == r3m
    reloaded_main_branch = itinerary_lineage_repository.get_branch(main_branch_id)
    assert reloaded_main_branch is not None
    assert reloaded_main_branch.head_revision_id == r3m.revision_id

    # Alternate R3A unchanged after R4A was created.
    reloaded_r3a = itinerary_lineage_repository.get_revision(r3a.revision_id)
    assert reloaded_r3a == r3a

    # Heads are independent.
    reloaded_alternate_branch = itinerary_lineage_repository.get_branch(alternate_branch_id)
    assert reloaded_alternate_branch is not None
    assert reloaded_alternate_branch.head_revision_id == r4a.revision_id
    assert reloaded_main_branch.head_revision_id != reloaded_alternate_branch.head_revision_id

    # Content actually diverged: Main's day 1 never has exp_R3A/exp_R4A;
    # Alternate's day 1 never has exp_R3M.
    main_snapshot = revision_lineage_service.load_snapshot(r3m.revision_id)
    alt_snapshot = revision_lineage_service.load_snapshot(r4a.revision_id)
    assert main_snapshot is not None and alt_snapshot is not None
    main_ids = {e.experience_id for e in main_snapshot.experience_plan.daily_plans[0].experiences}
    alt_ids = {e.experience_id for e in alt_snapshot.experience_plan.daily_plans[0].experiences}
    assert "exp_R3M" in main_ids and "exp_R3A" not in main_ids and "exp_R4A" not in main_ids
    assert "exp_R3A" in alt_ids and "exp_R4A" in alt_ids and "exp_R3M" not in alt_ids

    # Switching restores each branch's exact respective head.
    switch_to_main = revision_lineage_service.activate_branch(trip_id, main_branch_id)
    assert switch_to_main.status == BranchActivationStatus.ACTIVATED
    reloaded_state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert reloaded_state is not None
    assert reloaded_state.metadata.current_version == r3m.version_label
    assert {e.experience_id for e in reloaded_state.experience_plan.daily_plans[0].experiences} == main_ids

    switch_to_alt = revision_lineage_service.activate_branch(trip_id, alternate_branch_id)
    assert switch_to_alt.status == BranchActivationStatus.ACTIVATED
    reloaded_state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert reloaded_state is not None
    assert reloaded_state.metadata.current_version == r4a.version_label
    assert {e.experience_id for e in reloaded_state.experience_plan.daily_plans[0].experiences} == alt_ids

    # Duplicate version labels across branches are expected and harmless
    # -- Main's R2/R3M and Alternate's R3A/R4A independently produce
    # "v2"/"v3" labels of their own (Task 26).
    assert r3m.version_label == "v3"
    assert r3a.version_label == "v3"
    assert r3m.revision_id != r3a.revision_id
