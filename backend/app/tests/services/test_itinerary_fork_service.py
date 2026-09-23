from __future__ import annotations

from datetime import date
from typing import Any

from app.models.planning_state import (
    DailyPlan,
    ExperiencePlan,
    ExperienceItem,
    PlanningState,
    TravelGroupType,
    TravelerProfile,
    TripPace,
    TripRequest,
)
from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository
from app.services.itinerary_fork_service import ForkCreationStatus, itinerary_fork_service
from app.services.revision_lineage_service import (
    BranchActivationStatus,
    revision_lineage_service,
)
from app.services.versioning_service import versioning_service

# Section 199B (Tasks 6-11/32-35): direct service-level tests for
# `ItineraryForkService.create_fork`, against the real
# `itinerary_lineage_repository` singleton (local-JSON backend, reset
# between tests by conftest).


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Valencia, Spain",
        "start_date": "2026-09-10",
        "end_date": "2026-09-13",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
        "interests": ["food"],
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _experience(experience_id: str, name: str, day_number: int) -> ExperienceItem:
    return ExperienceItem(
        experience_id=experience_id, name=name, category="attraction", day_number=day_number, stop_order=1
    )


def _planning_state(trip_id: str) -> PlanningState:
    planning_state = PlanningState(trip_id=trip_id, trip_request=_trip_request())
    planning_state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(day_number=1, date=date(2026, 9, 10), experiences=[_experience("exp_A", "A", 1)]),
            DailyPlan(day_number=2, date=date(2026, 9, 11), experiences=[_experience("exp_B", "B", 2)]),
        ]
    )
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE, travelers_count=2, pace=TripPace.BALANCED, interests=["food"]
    )
    versioning_service.create_initial_version(planning_state)
    return planning_state


# ---------------------------------------------------------------------------
# Task 32: create fork without activation
# ---------------------------------------------------------------------------


def test_create_fork_without_activation_leaves_source_and_main_untouched() -> None:
    planning_state = _planning_state("trip_fork_noact")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None

    result = itinerary_fork_service.create_fork(
        "trip_fork_noact", source.revision_id, "Option B", activate_after_create=False
    )

    assert result.status == ForkCreationStatus.CREATED
    assert result.branch is not None
    assert result.branch.is_default is False
    assert result.branch.base_revision_id == source.revision_id
    assert result.branch.head_revision_id == source.revision_id
    assert result.activation is None

    # Task 1 (core invariant): the source revision itself is byte-for-byte
    # unaffected.
    reloaded_source = itinerary_lineage_repository.get_revision(source.revision_id)
    assert reloaded_source == source

    # The trip's active branch is still Main -- creating a fork never
    # switches the workspace by itself.
    default_branch = itinerary_lineage_repository.get_default_branch("trip_fork_noact")
    assert default_branch is not None
    assert revision_lineage_service.resolve_active_branch_id(planning_state) == default_branch.branch_id
    assert default_branch.head_revision_id == source.revision_id


# ---------------------------------------------------------------------------
# Task 33: create + activate
# ---------------------------------------------------------------------------


def test_create_fork_with_activation_switches_workspace() -> None:
    from app.repositories.factory import get_planning_state_repository

    planning_state = _planning_state("trip_fork_act")
    get_planning_state_repository().save(planning_state)
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None

    result = itinerary_fork_service.create_fork(
        "trip_fork_act", source.revision_id, "Option B", activate_after_create=True
    )

    assert result.status == ForkCreationStatus.CREATED
    assert result.activation is not None
    assert result.activation.status == BranchActivationStatus.ACTIVATED
    assert result.activation.active_branch_id == result.branch.branch_id

    from app.repositories.factory import get_planning_state_repository

    reloaded_state = get_planning_state_repository().get_by_trip_id("trip_fork_act")
    assert reloaded_state is not None
    assert reloaded_state.metadata.active_branch_id == result.branch.branch_id


def test_create_fork_with_activation_still_applies_guards() -> None:
    """Task 33: activation-after-create is never a bypass -- a dirty
    CURRENT workspace (pending feedback here) still blocks it, exactly
    like a standalone activate call would, and the branch remains
    created (never rolled back -- local JSON has no such mechanism)."""
    from app.models.common import RegenerationStrategy
    from app.models.planning_state import FeedbackEvent

    planning_state = _planning_state("trip_fork_act_blocked")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None
    planning_state.feedback_history.append(
        FeedbackEvent(feedback_text="pending", regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY)
    )
    from app.repositories.factory import get_planning_state_repository

    get_planning_state_repository().save(planning_state)

    result = itinerary_fork_service.create_fork(
        "trip_fork_act_blocked", source.revision_id, "Option B", activate_after_create=True
    )

    assert result.status == ForkCreationStatus.CREATED
    assert result.branch is not None
    assert result.activation is not None
    assert result.activation.status == BranchActivationStatus.BLOCKED_PENDING_FEEDBACK

    # Branch exists (not rolled back) but workspace is still on Main.
    assert itinerary_lineage_repository.get_branch(result.branch.branch_id) is not None
    default_branch = itinerary_lineage_repository.get_default_branch("trip_fork_act_blocked")
    assert revision_lineage_service.resolve_active_branch_id(planning_state) == default_branch.branch_id


# ---------------------------------------------------------------------------
# Task 8/34: fork only from a real snapshot
# ---------------------------------------------------------------------------


def test_fork_from_unknown_revision_is_rejected() -> None:
    result = itinerary_fork_service.create_fork(
        "trip_fork_unknown", "revision_does_not_exist", "Option B"
    )
    assert result.status == ForkCreationStatus.SOURCE_NOT_FOUND


def test_fork_from_another_trips_revision_is_rejected() -> None:
    planning_state = _planning_state("trip_fork_other")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None

    result = itinerary_fork_service.create_fork(
        "trip_fork_DIFFERENT", source.revision_id, "Option B"
    )
    assert result.status == ForkCreationStatus.SOURCE_NOT_FOUND


def test_fork_from_metadata_only_revision_is_rejected() -> None:
    """Task 8/34: an old, pre-199A-style revision recorded with
    `snapshot_available=False` can never be forked -- never
    reconstructed."""
    from app.models.itinerary_lineage import ItineraryRevision

    branch = revision_lineage_service.ensure_default_branch("trip_fork_metadata_only")
    metadata_only = itinerary_lineage_repository.create_revision(
        ItineraryRevision(
            trip_id="trip_fork_metadata_only",
            branch_id=branch.branch_id,
            version_label="v2",
            created_by="user_feedback",
            snapshot_available=False,
            snapshot=None,
        )
    )

    result = itinerary_fork_service.create_fork(
        "trip_fork_metadata_only", metadata_only.revision_id, "Option B"
    )
    assert result.status == ForkCreationStatus.SNAPSHOT_UNAVAILABLE


# ---------------------------------------------------------------------------
# Task 6/11: display name validation and uniqueness
# ---------------------------------------------------------------------------


def test_fork_with_empty_display_name_is_rejected() -> None:
    planning_state = _planning_state("trip_fork_empty_name")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None

    result = itinerary_fork_service.create_fork("trip_fork_empty_name", source.revision_id, "   ")
    assert result.status == ForkCreationStatus.INVALID_NAME


def test_fork_with_too_long_display_name_is_rejected() -> None:
    planning_state = _planning_state("trip_fork_long_name")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None

    result = itinerary_fork_service.create_fork(
        "trip_fork_long_name", source.revision_id, "x" * 200
    )
    assert result.status == ForkCreationStatus.INVALID_NAME


def test_fork_with_duplicate_display_name_is_rejected_case_insensitively() -> None:
    planning_state = _planning_state("trip_fork_dup_name")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None

    first = itinerary_fork_service.create_fork("trip_fork_dup_name", source.revision_id, "Option B")
    assert first.status == ForkCreationStatus.CREATED

    second = itinerary_fork_service.create_fork(
        "trip_fork_dup_name", source.revision_id, "  option b  "
    )
    assert second.status == ForkCreationStatus.NAME_CONFLICT


def test_fork_display_name_is_trimmed() -> None:
    planning_state = _planning_state("trip_fork_trim_name")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None

    result = itinerary_fork_service.create_fork(
        "trip_fork_trim_name", source.revision_id, "  Option B  "
    )
    assert result.branch is not None
    assert result.branch.display_name == "Option B"


def test_cannot_reuse_main_display_name() -> None:
    planning_state = _planning_state("trip_fork_main_name")
    source = revision_lineage_service.record_current_revision(planning_state)
    assert source is not None

    result = itinerary_fork_service.create_fork("trip_fork_main_name", source.revision_id, "Main")
    assert result.status == ForkCreationStatus.NAME_CONFLICT
