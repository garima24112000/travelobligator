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
from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository
from app.services.revision_lineage_service import revision_lineage_service
from app.services.versioning_service import versioning_service

# Section 199A (Task 35/17/18): a "pre-199A trip" fixture -- real
# version_history metadata for v1/v2/v3 (built via the same real
# `versioning_service` every generation/regeneration call already uses),
# but with `record_current_revision` never having been called for it, so
# no branch/revision has ever been recorded -- exactly what a trip
# generated/regenerated before this section shipped looks like once this
# section's tables/collections exist alongside it.


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Seville, Spain",
        "start_date": "2026-09-10",
        "end_date": "2026-09-13",
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


def _pre_199a_trip(trip_id: str) -> PlanningState:
    planning_state = PlanningState(trip_id=trip_id, trip_request=_trip_request())
    planning_state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(day_number=1, date=date(2026, 9, 10), experiences=[_experience("exp_A", "A", 1)]),
        ]
    )
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE, travelers_count=2, pace=TripPace.BALANCED, interests=["history"]
    )
    # Real v1/v2/v3 metadata, exactly like a real generation + two real
    # regenerations would have produced, all through the real
    # versioning_service -- `record_current_revision` is deliberately
    # never called for any of these three, simulating a trip that
    # existed entirely before this section shipped.
    versioning_service.create_initial_version(planning_state)
    versioning_service.create_version_after_feedback(
        planning_state, feedback_event_id="feedback_1", changed_sections=["experience_plan"]
    )
    versioning_service.create_version_after_feedback(
        planning_state, feedback_event_id="feedback_2", changed_sections=["experience_plan"]
    )
    return planning_state


def test_old_trip_still_loads_and_current_version_is_v3() -> None:
    planning_state = _pre_199a_trip("trip_old_1")
    assert planning_state.metadata.current_version == "v3"
    assert [item.version_label for item in planning_state.version_history] == ["v1", "v2", "v3"]


def test_default_branch_can_be_initialized_safely_for_an_old_trip() -> None:
    planning_state = _pre_199a_trip("trip_old_2")
    branch = revision_lineage_service.ensure_default_branch(planning_state.trip_id)
    assert branch.is_default is True
    assert branch.head_revision_id is None  # nothing captured yet -- honest


def test_current_v3_may_be_snapshotted_without_fabricating_v1_or_v2() -> None:
    planning_state = _pre_199a_trip("trip_old_3")

    revision = revision_lineage_service.record_current_revision(planning_state)

    assert revision is not None
    assert revision.version_label == "v3"
    # Task 18: honestly has no real recorded parent -- v1/v2 were never
    # captured, so this is NOT fabricated as v2's child.
    assert revision.parent_revision_id is None
    assert revision.snapshot_available is True

    branch = itinerary_lineage_repository.get_default_branch(planning_state.trip_id)
    assert branch is not None
    all_revisions = itinerary_lineage_repository.list_revisions_for_branch(branch.branch_id)
    # Exactly one revision exists -- v1/v2 were never falsely presented
    # as available snapshots.
    assert len(all_revisions) == 1
    assert all_revisions[0].version_label == "v3"

    # There is no revision at all for v1/v2 -- not even a
    # snapshot_available=False placeholder was fabricated for them,
    # since this section never invents historical revision records it
    # didn't itself observe.
    v1_or_v2 = [r for r in all_revisions if r.version_label in ("v1", "v2")]
    assert v1_or_v2 == []

    assert revision_lineage_service.check_branch_head_consistency(planning_state) is True


def test_old_trip_next_real_regeneration_has_honest_parentage() -> None:
    """The trip's FIRST captured revision (whichever version is current
    when 199A first captures it) always has `parent_revision_id=None` --
    even if that's v3, v4, or later -- because nothing before it was
    ever really captured. This is the honest lineage a later real
    regeneration builds on, never a synthesized v1/v2 chain."""
    planning_state = _pre_199a_trip("trip_old_4")
    first_captured = revision_lineage_service.record_current_revision(planning_state)
    assert first_captured is not None
    assert first_captured.version_label == "v3"
    assert first_captured.parent_revision_id is None

    # A subsequent real regeneration (v4) DOES get a real parent -- the
    # first captured revision above, never a guessed v3/v2/v1 chain.
    planning_state.feedback_history.append(
        FeedbackEvent(
            feedback_text="one more change",
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
        )
    )
    versioning_service.create_version_after_feedback(
        planning_state,
        feedback_event_id=planning_state.feedback_history[-1].feedback_event_id,
        changed_sections=["experience_plan"],
    )
    second_captured = revision_lineage_service.record_current_revision(planning_state)
    assert second_captured is not None
    assert second_captured.version_label == "v4"
    assert second_captured.parent_revision_id == first_captured.revision_id
