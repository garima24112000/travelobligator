from __future__ import annotations

from datetime import date
from typing import Any

from app.models.planning_state import (
    DailyPlan,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TravelerProfile,
    TripPace,
    TripRequest,
)
from app.repositories.factory import get_planning_state_repository
from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository
from app.services.itinerary_fork_service import itinerary_fork_service
from app.services.itinerary_revision_comparison_service import (
    RevisionComparisonStatus,
    itinerary_revision_comparison_service,
)
from app.services.revision_lineage_service import revision_lineage_service
from app.services.versioning_service import versioning_service

# Section 199C (Task 52): direct service-level tests for the generic,
# deterministic revision comparison.


def _exp(experience_id: str, name: str, day: int, order: int = 1) -> ExperienceItem:
    return ExperienceItem(
        experience_id=experience_id, name=name, category="attraction", day_number=day, stop_order=order
    )


def _state(trip_id: str, days: list[list[ExperienceItem]], interests: list[str] | None = None) -> PlanningState:
    state = PlanningState(
        trip_id=trip_id,
        trip_request=TripRequest(
            primary_destination="Cadiz, Spain",
            start_date="2026-09-10",
            end_date="2026-09-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
            interests=interests or ["history"],
        ),
    )
    state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(day_number=i + 1, date=date(2026, 9, 10 + i), experiences=exps)
            for i, exps in enumerate(days)
        ]
    )
    state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE,
        travelers_count=2,
        pace=TripPace.BALANCED,
        interests=interests or ["history"],
    )
    versioning_service.create_initial_version(state)
    return state


def _record(state: PlanningState):
    get_planning_state_repository().save(state)
    revision = revision_lineage_service.record_current_revision(state)
    assert revision is not None
    return revision


def _two_revisions(trip_id: str, left_days, right_days, right_interests=None):
    left_state = _state(trip_id, left_days)
    left = _record(left_state)
    right_state = left_state.model_copy(deep=True)
    right_state.experience_plan = _state(trip_id, right_days).experience_plan
    if right_interests is not None:
        right_state.traveler_profile.interests = right_interests
    right_state.metadata.current_version = "v2"
    right_state.metadata.active_branch_id = None
    versioning_service.create_version_after_feedback(
        right_state, feedback_event_id="fb", changed_sections=["experience_plan"]
    )
    right = _record(right_state)
    return left, right


def _compare(trip_id: str, left, right):
    return itinerary_revision_comparison_service.compare(trip_id, left.revision_id, right.revision_id)


def test_same_revision_against_itself_has_no_differences() -> None:
    left, _ = _two_revisions("t_same", [[_exp("a", "A", 1)]], [[_exp("a", "A", 1)]])
    result = _compare("t_same", left, left)
    assert result.status == RevisionComparisonStatus.COMPARED
    assert result.comparison.no_compared_differences is True
    assert result.comparison.left.revision_id == result.comparison.right.revision_id


def test_added_and_removed_resolve_names_from_the_correct_side() -> None:
    left, right = _two_revisions(
        "t_addrem", [[_exp("a", "Alpha", 1), _exp("b", "Bravo", 1, 2)]], [[_exp("a", "Alpha", 1), _exp("c", "Charlie", 1, 2)]]
    )
    comparison = _compare("t_addrem", left, right).comparison
    assert [(e.experience_id, e.name) for e in comparison.added_experiences] == [("c", "Charlie")]
    assert [(e.experience_id, e.name) for e in comparison.removed_experiences] == [("b", "Bravo")]
    assert comparison.no_compared_differences is False


def test_moved_experience_uses_stable_id_not_name() -> None:
    left, right = _two_revisions(
        "t_move",
        [[_exp("a", "Alpha", 1)], [_exp("b", "Bravo", 2)]],
        [[_exp("a", "Alpha", 1), _exp("b", "Bravo", 1, 2)], []],
    )
    comparison = _compare("t_move", left, right).comparison
    assert [(m.experience_id, m.from_day, m.to_day) for m in comparison.moved_experiences] == [("b", 2, 1)]


def test_reordered_day_reported_when_same_id_set() -> None:
    left, right = _two_revisions(
        "t_reorder",
        [[_exp("a", "Alpha", 1, 1), _exp("b", "Bravo", 1, 2)]],
        [[_exp("b", "Bravo", 1, 1), _exp("a", "Alpha", 1, 2)]],
    )
    comparison = _compare("t_reorder", left, right).comparison
    assert len(comparison.reordered_days) == 1
    assert [e.experience_id for e in comparison.reordered_days[0].before_order] == ["a", "b"]
    assert [e.experience_id for e in comparison.reordered_days[0].after_order] == ["b", "a"]


def test_profile_interest_change_is_reported() -> None:
    left, right = _two_revisions(
        "t_profile", [[_exp("a", "A", 1)]], [[_exp("a", "A", 1)]], right_interests=["history", "food"]
    )
    comparison = _compare("t_profile", left, right).comparison
    assert comparison.traveler_profile_diff is not None
    assert comparison.traveler_profile_diff.interests_added == ["food"]


def test_validation_counts_are_factual_only_and_carry_no_ranking_field() -> None:
    left, right = _two_revisions("t_val", [[_exp("a", "A", 1)]], [[_exp("a", "A", 1)]])
    comparison = _compare("t_val", left, right).comparison
    dumped = comparison.model_dump()
    for forbidden in ("winner", "better", "recommended", "score", "rank"):
        assert not any(forbidden in key for key in dumped)
    assert comparison.warning_count_left == comparison.warning_count_right == 0


def test_duplicate_version_labels_across_branches_compare_by_revision_id() -> None:
    trip_id = "t_dupe_labels"
    state = _state(trip_id, [[_exp("a", "A", 1)]])
    r1 = _record(state)
    fork = itinerary_fork_service.create_fork(trip_id, r1.revision_id, "Option B", activate_after_create=True)
    assert fork.branch is not None
    live = get_planning_state_repository().get_by_trip_id(trip_id)
    live.experience_plan.daily_plans[0].experiences.append(_exp("z", "Zulu", 1, 2))
    versioning_service.create_version_after_feedback(live, feedback_event_id="fb", changed_sections=["experience_plan"])
    get_planning_state_repository().save(live)
    r_b = revision_lineage_service.record_current_revision(live)
    assert r_b is not None and r_b.version_label == "v2"

    # Main also advances to a "v2" of its own with different content.
    main = itinerary_lineage_repository.get_default_branch(trip_id)
    main_snapshot = revision_lineage_service.load_snapshot(r1.revision_id)
    main_snapshot.metadata.active_branch_id = main.branch_id
    main_snapshot.experience_plan.daily_plans[0].experiences.append(_exp("y", "Yankee", 1, 2))
    versioning_service.create_version_after_feedback(main_snapshot, feedback_event_id="fb2", changed_sections=["experience_plan"])
    get_planning_state_repository().save(main_snapshot)
    r_m = revision_lineage_service.record_current_revision(main_snapshot)
    assert r_m is not None and r_m.version_label == "v2"
    assert r_m.revision_id != r_b.revision_id

    comparison = _compare(trip_id, r_m, r_b).comparison
    assert comparison.left.version_label == comparison.right.version_label == "v2"
    assert comparison.left.revision_id != comparison.right.revision_id
    assert [e.experience_id for e in comparison.added_experiences] == ["z"]
    assert [e.experience_id for e in comparison.removed_experiences] == ["y"]
    assert comparison.left.branch_display_name == "Main"
    assert comparison.right.branch_display_name == "Option B"


def test_unknown_revision_and_wrong_trip_are_not_found() -> None:
    left, right = _two_revisions("t_nf", [[_exp("a", "A", 1)]], [[_exp("a", "A", 1)]])
    missing = itinerary_revision_comparison_service.compare("t_nf", left.revision_id, "revision_nope")
    assert missing.status == RevisionComparisonStatus.REVISION_NOT_FOUND
    wrong_trip = itinerary_revision_comparison_service.compare("t_other", left.revision_id, right.revision_id)
    assert wrong_trip.status == RevisionComparisonStatus.REVISION_NOT_FOUND


def test_unavailable_snapshot_is_refused_not_reconstructed() -> None:
    from app.models.itinerary_lineage import ItineraryRevision

    left, _ = _two_revisions("t_unavail", [[_exp("a", "A", 1)]], [[_exp("a", "A", 1)]])
    metadata_only = itinerary_lineage_repository.create_revision(
        ItineraryRevision(
            trip_id="t_unavail",
            branch_id=left.branch_id,
            version_label="v9",
            created_by="user_feedback",
            snapshot_available=False,
            snapshot=None,
        )
    )
    result = itinerary_revision_comparison_service.compare("t_unavail", left.revision_id, metadata_only.revision_id)
    assert result.status == RevisionComparisonStatus.SNAPSHOT_UNAVAILABLE


def test_comparison_never_mutates_revisions_or_live_state() -> None:
    left, right = _two_revisions(
        "t_nomut", [[_exp("a", "A", 1)]], [[_exp("a", "A", 1), _exp("b", "B", 1, 2)]]
    )
    live_before = get_planning_state_repository().get_by_trip_id("t_nomut").model_dump(mode="json")
    left_before = itinerary_lineage_repository.get_revision(left.revision_id)
    right_before = itinerary_lineage_repository.get_revision(right.revision_id)
    _compare("t_nomut", left, right)
    assert itinerary_lineage_repository.get_revision(left.revision_id) == left_before
    assert itinerary_lineage_repository.get_revision(right.revision_id) == right_before
    assert get_planning_state_repository().get_by_trip_id("t_nomut").model_dump(mode="json") == live_before
