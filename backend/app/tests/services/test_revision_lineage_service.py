from __future__ import annotations

from datetime import date
from typing import Any

from app.models.common import RegenerationStrategy
from app.models.itinerary_lineage import ItineraryRevision
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
from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository
from app.services.revision_lineage_service import revision_lineage_service
from app.services.revision_snapshot_service import (
    deserialize_planning_state_snapshot,
    serialize_planning_state_snapshot,
)
from app.services.versioning_service import versioning_service

# Section 199A (Tasks 31-38): direct unit tests for `RevisionLineageService`
# and the canonical snapshot serializer, exercised against the real
# `itinerary_lineage_repository` singleton (local-JSON backend, reset
# between tests by conftest's `_reset_in_memory_repositories`) rather
# than a fake -- these are meant to prove the real storage/idempotency/
# immutability guarantees, not just the service's own call sequencing.


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


def _experience(experience_id: str, name: str, day_number: int) -> ExperienceItem:
    return ExperienceItem(
        experience_id=experience_id,
        name=name,
        category="attraction",
        day_number=day_number,
        stop_order=1,
    )


def _planning_state(trip_id: str = "trip_lineage_x") -> PlanningState:
    planning_state = PlanningState(trip_id=trip_id, trip_request=_trip_request())
    day1 = DailyPlan(day_number=1, date=date(2026, 9, 10), experiences=[_experience("exp_A", "Museum A", 1)])
    day2 = DailyPlan(day_number=2, date=date(2026, 9, 11), experiences=[_experience("exp_C", "Tower C", 2)])
    planning_state.experience_plan = ExperiencePlan(daily_plans=[day1, day2])
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE,
        travelers_count=2,
        pace=TripPace.BALANCED,
        interests=["history"],
    )
    versioning_service.create_initial_version(planning_state)
    return planning_state


def _pending_event(text: str = "Remove the tower.") -> FeedbackEvent:
    return FeedbackEvent(
        feedback_text=text,
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        interpretation={"method": "deterministic_rule_based", "applied_to_plan": False},
    )


def _advance_to_next_version(planning_state: PlanningState, feedback_text: str) -> PlanningState:
    """Mirrors what a real regeneration does to `planning_state` just
    before `RevisionLineageService.record_current_revision` is called --
    append a pending feedback event, mark it applied, and append a new
    `VersionHistoryItem` via the real `versioning_service` (never a
    hand-rolled `"v" + str(n)`)."""
    event = _pending_event(feedback_text)
    planning_state.feedback_history.append(event)
    planning_state = versioning_service.create_version_after_feedback(
        planning_state,
        feedback_event_id=event.feedback_event_id,
        changed_sections=["experience_plan"],
        preserved_sections=[],
        summary="test regeneration",
    )
    event.applied_at = planning_state.metadata.updated_at
    event.applied_in_version = planning_state.metadata.current_version
    event.handling_status = "applied"
    return planning_state


# ---------------------------------------------------------------------------
# ensure_default_branch
# ---------------------------------------------------------------------------


def test_ensure_default_branch_creates_stable_main_branch() -> None:
    branch = revision_lineage_service.ensure_default_branch("trip_a")
    assert branch.trip_id == "trip_a"
    assert branch.display_name == "Main"
    assert branch.is_default is True
    assert branch.head_revision_id is None
    assert branch.branch_id.startswith("branch_")


def test_ensure_default_branch_is_idempotent() -> None:
    first = revision_lineage_service.ensure_default_branch("trip_b")
    second = revision_lineage_service.ensure_default_branch("trip_b")
    assert first.branch_id == second.branch_id
    assert len(itinerary_lineage_repository.list_branches_for_trip("trip_b")) == 1


def test_ensure_default_branch_is_independent_per_trip() -> None:
    branch_a = revision_lineage_service.ensure_default_branch("trip_c1")
    branch_b = revision_lineage_service.ensure_default_branch("trip_c2")
    assert branch_a.branch_id != branch_b.branch_id
    assert branch_a.trip_id != branch_b.trip_id


# ---------------------------------------------------------------------------
# record_current_revision -- Task 31 (fresh trip) / Task 5 (parent lineage)
# ---------------------------------------------------------------------------


def test_record_current_revision_for_a_fresh_trip() -> None:
    planning_state = _planning_state("trip_fresh")

    revision = revision_lineage_service.record_current_revision(planning_state)

    assert revision is not None
    assert revision.version_label == "v1"
    assert revision.parent_revision_id is None
    assert revision.snapshot_available is True
    assert revision.snapshot is not None

    branch = itinerary_lineage_repository.get_default_branch("trip_fresh")
    assert branch is not None
    assert branch.head_revision_id == revision.revision_id

    loaded = revision_lineage_service.load_snapshot(revision.revision_id)
    assert loaded is not None
    assert loaded.metadata.current_version == "v1"
    assert loaded.experience_plan is not None
    assert [e.experience_id for e in loaded.experience_plan.daily_plans[1].experiences] == ["exp_C"]


# ---------------------------------------------------------------------------
# Task 32: sequential versions / parent chain / head advancement
# ---------------------------------------------------------------------------


def test_sequential_versions_build_a_real_parent_chain() -> None:
    planning_state = _planning_state("trip_sequential")
    r1 = revision_lineage_service.record_current_revision(planning_state)
    assert r1 is not None

    planning_state = _advance_to_next_version(planning_state, "Remove the tower.")
    r2 = revision_lineage_service.record_current_revision(planning_state)
    assert r2 is not None

    planning_state = _advance_to_next_version(planning_state, "Add a museum.")
    r3 = revision_lineage_service.record_current_revision(planning_state)
    assert r3 is not None

    assert r1.parent_revision_id is None
    assert r2.parent_revision_id == r1.revision_id
    assert r3.parent_revision_id == r2.revision_id

    branch = itinerary_lineage_repository.get_default_branch("trip_sequential")
    assert branch is not None
    assert branch.head_revision_id == r3.revision_id

    # Every earlier snapshot must remain independently loadable and
    # correct -- never overwritten by a later revision (Task 11/37).
    loaded_r1 = revision_lineage_service.load_snapshot(r1.revision_id)
    loaded_r2 = revision_lineage_service.load_snapshot(r2.revision_id)
    loaded_r3 = revision_lineage_service.load_snapshot(r3.revision_id)
    assert loaded_r1.metadata.current_version == "v1"
    assert loaded_r2.metadata.current_version == "v2"
    assert loaded_r3.metadata.current_version == "v3"


def test_immutability_earlier_snapshot_unaffected_by_later_mutation() -> None:
    """Task 11/37: after storing R1, mutate nested structures on the
    CURRENT working planning_state and record R2 -- R1, reloaded, must
    remain byte-for-byte equivalent to what was captured at that time."""
    planning_state = _planning_state("trip_immutable")
    r1 = revision_lineage_service.record_current_revision(planning_state)
    assert r1 is not None
    r1_snapshot_before = revision_lineage_service.load_snapshot(r1.revision_id)
    assert r1_snapshot_before is not None
    original_day2_ids = [
        e.experience_id for e in r1_snapshot_before.experience_plan.daily_plans[1].experiences
    ]
    assert original_day2_ids == ["exp_C"]

    # Mutate nested, mutable structures on the LIVE working state --
    # experience list, traveler interests -- exactly the kind of
    # in-place mutation a real regeneration performs.
    planning_state.experience_plan.daily_plans[1].experiences.append(
        _experience("exp_NEW", "New Place", 2)
    )
    planning_state.traveler_profile.interests.append("food")
    planning_state = _advance_to_next_version(planning_state, "Add a place.")
    r2 = revision_lineage_service.record_current_revision(planning_state)
    assert r2 is not None

    r1_snapshot_after = revision_lineage_service.load_snapshot(r1.revision_id)
    assert r1_snapshot_after is not None
    assert [
        e.experience_id for e in r1_snapshot_after.experience_plan.daily_plans[1].experiences
    ] == ["exp_C"]
    assert "food" not in r1_snapshot_after.traveler_profile.interests

    r2_snapshot = revision_lineage_service.load_snapshot(r2.revision_id)
    assert r2_snapshot is not None
    assert set(
        e.experience_id for e in r2_snapshot.experience_plan.daily_plans[1].experiences
    ) == {"exp_C", "exp_NEW"}
    assert "food" in r2_snapshot.traveler_profile.interests


# ---------------------------------------------------------------------------
# Task 34: failed/blocked regeneration never creates a revision
# ---------------------------------------------------------------------------


def test_no_revision_created_when_record_current_revision_is_never_called() -> None:
    """A failed/blocked regeneration never reaches
    `record_current_revision` at all (see the 4 real call sites in
    `planning_orchestrator`/`trips.py`/`generation_job_service.py`/
    `targeted_regeneration_application_service.py`, all inside their
    respective success paths only) -- this test documents that contract
    at the service level: simply never calling it means no branch/
    revision exists."""
    assert itinerary_lineage_repository.get_default_branch("trip_never_generated") is None
    assert revision_lineage_service.list_branch_lineage("branch_does_not_exist") == []


# ---------------------------------------------------------------------------
# Task 38: idempotency / duplicate record prevention
# ---------------------------------------------------------------------------


def test_duplicate_record_current_revision_call_creates_only_one_revision() -> None:
    planning_state = _planning_state("trip_duplicate")

    first = revision_lineage_service.record_current_revision(planning_state)
    second = revision_lineage_service.record_current_revision(planning_state)

    assert first is not None
    assert second is not None
    assert first.revision_id == second.revision_id

    branch = itinerary_lineage_repository.get_default_branch("trip_duplicate")
    assert branch is not None
    assert len(itinerary_lineage_repository.list_revisions_for_branch(branch.branch_id)) == 1


# ---------------------------------------------------------------------------
# Task 18: historical, metadata-only (snapshot_available=False) revisions
# ---------------------------------------------------------------------------


def test_load_snapshot_returns_none_for_a_metadata_only_revision() -> None:
    """Simulates a historical revision this section found recorded (Task
    17) but never itself captured a snapshot for -- never reconstructed
    by any other means (Task 18)."""
    branch = revision_lineage_service.ensure_default_branch("trip_old")
    metadata_only = itinerary_lineage_repository.create_revision(
        ItineraryRevision(
            trip_id="trip_old",
            branch_id=branch.branch_id,
            parent_revision_id=None,
            version_label="v2",
            created_by="user_feedback",
            snapshot_available=False,
            snapshot=None,
        )
    )
    assert revision_lineage_service.load_snapshot(metadata_only.revision_id) is None


# ---------------------------------------------------------------------------
# Task 28: branch head consistency check
# ---------------------------------------------------------------------------


def test_check_branch_head_consistency_true_when_lineage_not_initialized() -> None:
    planning_state = _planning_state("trip_no_lineage_yet")
    assert revision_lineage_service.check_branch_head_consistency(planning_state) is True


def test_check_branch_head_consistency_true_when_agreeing() -> None:
    planning_state = _planning_state("trip_consistent")
    revision_lineage_service.record_current_revision(planning_state)
    assert revision_lineage_service.check_branch_head_consistency(planning_state) is True


def test_check_branch_head_consistency_false_when_versions_disagree() -> None:
    planning_state = _planning_state("trip_inconsistent")
    revision_lineage_service.record_current_revision(planning_state)

    # Simulate the live state having moved on without a matching
    # revision ever being recorded for the new version (e.g. a
    # best-effort recording failure) -- never silently repaired.
    planning_state.metadata.current_version = "v2"
    assert revision_lineage_service.check_branch_head_consistency(planning_state) is False


# ---------------------------------------------------------------------------
# Task 10/36: canonical snapshot serializer round trip
# ---------------------------------------------------------------------------


def test_snapshot_round_trip_preserves_structure_with_no_shared_references() -> None:
    planning_state = _planning_state("trip_round_trip")
    snapshot = serialize_planning_state_snapshot(planning_state)
    clone = deserialize_planning_state_snapshot(snapshot)

    assert clone.trip_id == planning_state.trip_id
    assert clone.metadata.current_version == planning_state.metadata.current_version
    assert [e.experience_id for e in clone.experience_plan.daily_plans[1].experiences] == [
        "exp_C"
    ]

    # No shared mutable references: mutating the clone must never affect
    # the original.
    clone.experience_plan.daily_plans[1].experiences.append(_experience("exp_ONLY_CLONE", "X", 2))
    assert [e.experience_id for e in planning_state.experience_plan.daily_plans[1].experiences] == [
        "exp_C"
    ]
