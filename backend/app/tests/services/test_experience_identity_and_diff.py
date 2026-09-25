from __future__ import annotations

from datetime import date
from typing import Any

from app.models.planning_state import (
    DailyPlan,
    DestinationContext,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripRequest,
    ValidationSeverity,
)
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_identity import (
    deterministic_experience_id,
    experience_stable_key,
    stable_place_key,
)
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.plan_validator_service import PlanValidatorService
from app.services.targeted_regeneration_diff_builder import (
    compute_itinerary_content_diff,
    partition_days_by_final_equality,
)
from app.services.targeted_regeneration_executor import TargetedRegenerationExecutor

# Section 202B.1 (Tasks 5-11, 21): stable place identity, truthful
# comparison/preservation, and duplicate prevention/detection.


def _exp(place: str | None, name: str, day: int, *, experience_id: str | None = None, order: int = 1) -> ExperienceItem:
    fields: dict[str, Any] = dict(name=name, category="attraction", day_number=day, stop_order=order)
    if experience_id is not None:
        fields["experience_id"] = experience_id
    if place is not None:
        fields["provider_source"] = "openstreetmap_places"
        fields["provider_place_id"] = place
    return ExperienceItem(**fields)


def _state(days: list[list[ExperienceItem]], trip_id: str = "trip_t") -> PlanningState:
    state = PlanningState(
        trip_id=trip_id,
        trip_request=TripRequest(
            primary_destination="Testville",
            start_date="2026-08-10",
            end_date="2026-08-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        ),
    )
    state.experience_plan = ExperiencePlan(
        daily_plans=[DailyPlan(day_number=i + 1, date=date(2026, 8, 10 + i), experiences=e) for i, e in enumerate(days)]
    )
    return state


# -- identity rule ------------------------------------------------------------


def test_deterministic_id_is_stable_per_trip_and_place_and_never_name_based() -> None:
    a = deterministic_experience_id("trip_1", "openstreetmap_places", "node/1")
    assert a == deterministic_experience_id("trip_1", "openstreetmap_places", "node/1")
    assert a != deterministic_experience_id("trip_2", "openstreetmap_places", "node/1")
    assert a != deterministic_experience_id("trip_1", "openstreetmap_places", "node/2")
    assert deterministic_experience_id("trip_1", None, "node/1") is None
    assert deterministic_experience_id("trip_1", "openstreetmap_places", None) is None
    assert stable_place_key("openstreetmap_places", "node/1") == "openstreetmap_places:node/1"
    assert experience_stable_key(_exp(None, "Same Name", 1)) is None


def _planner_state() -> PlanningState:
    def place(pid: str, name: str, lat: float) -> dict[str, Any]:
        return {
            "place_id": pid,
            "name": name,
            "category": "museum",
            "coordinates": {"lat": lat, "lng": 0.0},
            "source": "openstreetmap_places",
            "data_status": "live",
            "confidence": 0.7,
        }

    state = PlanningState(
        trip_id="trip_planner",
        trip_request=TripRequest(
            primary_destination="Testville",
            start_date="2026-08-10",
            end_date="2026-08-11",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        ),
    )
    state.destination_context = DestinationContext(
        destination_name="Testville",
        candidate_pois=[place("n1", "Museum One", 0.0), place("n2", "Museum Two", 0.001), place("n3", "Museum Three", 0.5)],
    )
    state.candidate_quality_report = CandidateQualityService().build_report(state)
    return state


def test_planner_gives_every_provider_place_its_provider_identity_and_stable_id() -> None:
    first = _planner_state()
    ExperiencePlannerService().run(first)
    second = _planner_state()
    ExperiencePlannerService().run(second)

    first_items = [e for d in first.experience_plan.daily_plans for e in d.experiences]
    second_items = [e for d in second.experience_plan.daily_plans for e in d.experiences]
    assert first_items
    assert all(e.provider_source == "openstreetmap_places" and e.provider_place_id for e in first_items)
    # Rerunning the planner (what every regeneration does) never re-ids an
    # unchanged place.
    assert {e.provider_place_id: e.experience_id for e in first_items} == {
        e.provider_place_id: e.experience_id for e in second_items
    }
    assert len({e.experience_id for e in first_items}) == len(first_items)


# -- comparison identity (Task 7 regression) -----------------------------------


def test_unchanged_places_with_different_ids_are_not_fake_removed_and_added() -> None:
    """The exact 202A regression: X,Y,Z before; the same X,Y,Z after, but
    every `experience_id` regenerated (older persisted data)."""
    before = _state([[_exp("n1", "X", 1, experience_id="old_x"), _exp("n2", "Y", 1, experience_id="old_y", order=2)],
                     [_exp("n3", "Z", 2, experience_id="old_z")], []])
    after = _state([[_exp("n1", "X", 1, experience_id="new_x"), _exp("n2", "Y", 1, experience_id="new_y", order=2)],
                    [_exp("n3", "Z", 2, experience_id="new_z")], []])

    diff = compute_itinerary_content_diff(before, after)

    assert diff.added_experience_ids == []
    assert diff.removed_experience_ids == []
    assert diff.moved_experiences == []
    assert diff.reordered_days == []


def test_only_the_real_difference_is_reported() -> None:
    before = _state([[_exp("n1", "X", 1, experience_id="old_x")], [_exp("n3", "Z", 2, experience_id="old_z")], []])
    after = _state([[_exp("n1", "X", 1, experience_id="new_x")], [_exp("n9", "W", 2, experience_id="new_w")], []])

    diff = compute_itinerary_content_diff(before, after)

    assert diff.added_experience_ids == ["new_w"]
    assert diff.removed_experience_ids == ["old_z"]
    assert diff.moved_experiences == []


def test_same_place_on_a_different_day_is_a_move_reported_under_its_after_id() -> None:
    before = _state([[_exp("n1", "X", 1, experience_id="old_x")], [], []])
    after = _state([[], [_exp("n1", "X", 2, experience_id="new_x")], []])

    diff = compute_itinerary_content_diff(before, after)

    assert [(m.experience_id, m.from_day, m.to_day) for m in diff.moved_experiences] == [("new_x", 1, 2)]
    assert diff.added_experience_ids == [] and diff.removed_experience_ids == []


def test_items_without_provider_identity_still_compare_by_experience_id_only() -> None:
    before = _state([[_exp(None, "Same Name", 1, experience_id="a")], [], []])
    after = _state([[_exp(None, "Same Name", 1, experience_id="b")], [], []])

    diff = compute_itinerary_content_diff(before, after)

    # No provider identity -> no fuzzy name matching.
    assert diff.added_experience_ids == ["b"] and diff.removed_experience_ids == ["a"]


# -- final-state preservation truth (Tasks 8/9) ---------------------------------


def test_partition_reports_only_byte_identical_days_as_preserved() -> None:
    before = _state([[_exp("n1", "X", 1, experience_id="x")], [_exp("n2", "Y", 2, experience_id="y")], [_exp("n3", "Z", 3, experience_id="z")]])
    after = before.model_copy(deep=True)

    affected, preserved = partition_days_by_final_equality(before, after)
    assert affected == [] and preserved == [1, 2, 3]


def test_additive_insertion_day_is_affected_not_preserved() -> None:
    before = _state([[_exp("n1", "X", 1, experience_id="x")], [_exp("n2", "Y", 2, experience_id="y")], []])
    after = before.model_copy(deep=True)
    after.experience_plan.daily_plans[1].experiences.append(_exp("n7", "New", 2, experience_id="new", order=2))

    affected, preserved = partition_days_by_final_equality(before, after)

    assert affected == [2]
    assert preserved == [1, 3]


def test_downstream_reorder_marks_the_day_affected_even_if_plan_said_preserved() -> None:
    before = _state([[_exp("n1", "X", 1, experience_id="x"), _exp("n2", "Y", 1, experience_id="y", order=2)], [], []])
    after = before.model_copy(deep=True)
    after.experience_plan.daily_plans[0].experiences.reverse()  # e.g. route-aware sequencing

    affected, preserved = partition_days_by_final_equality(before, after)

    assert affected == [1] and 1 not in preserved


def test_downstream_field_mutation_marks_the_day_affected() -> None:
    before = _state([[_exp("n1", "X", 1, experience_id="x")], [], []])
    after = before.model_copy(deep=True)
    after.experience_plan.daily_plans[0].warnings.append("changed downstream")

    assert partition_days_by_final_equality(before, after)[0] == [1]


# -- duplicate prevention (Task 10) and detection (Task 11) ----------------------


def test_final_state_uniqueness_drops_repeat_from_non_preserved_days_and_says_so() -> None:
    state = _state(
        [
            [_exp("n1", "Pelourinho", 1, experience_id="p1")],
            [_exp("n1", "Pelourinho", 2, experience_id="p1_again"), _exp("n5", "Other", 2, experience_id="o", order=2)],
            [_exp("n1", "Pelourinho", 3, experience_id="p1_third")],
        ]
    )
    preserved = {1: state.experience_plan.daily_plans[0].model_copy(deep=True)}

    TargetedRegenerationExecutor._enforce_unique_places(state, preserved)

    days = state.experience_plan.daily_plans
    assert [e.experience_id for e in days[0].experiences] == ["p1"]  # preserved day untouched
    assert [e.experience_id for e in days[1].experiences] == ["o"]
    assert days[1].experiences[0].stop_order == 1
    assert any("not repeated" in w for w in days[1].warnings)
    assert days[2].experiences == []


def test_uniqueness_never_dedupes_by_display_name() -> None:
    state = _state(
        [[_exp("n1", "Old Town", 1, experience_id="a")], [_exp("n2", "Old Town", 2, experience_id="b")], []]
    )
    TargetedRegenerationExecutor._enforce_unique_places(state, {})
    assert [len(d.experiences) for d in state.experience_plan.daily_plans] == [1, 1, 0]


def test_validator_reports_same_provider_place_on_multiple_days() -> None:
    state = _state(
        [[_exp("n1", "Pelourinho", 1, experience_id="a")], [_exp("n1", "Pelourinho", 2, experience_id="b")], []]
    )
    PlanValidatorService().run(state)

    duplicate_issues = [i for i in state.validation_report.critical_issues if i.category == "duplicate_experience"]
    assert len(duplicate_issues) == 1
    assert duplicate_issues[0].severity == ValidationSeverity.CRITICAL
    assert "day(s) 1, 2" in duplicate_issues[0].message
    # Reported, never silently dropped.
    assert sum(len(d.experiences) for d in state.experience_plan.daily_plans) == 2


def test_validator_does_not_flag_distinct_places_or_unidentified_items() -> None:
    state = _state(
        [[_exp("n1", "A", 1, experience_id="a")], [_exp("n2", "A", 2, experience_id="b")], [_exp(None, "A", 3, experience_id="c")]]
    )
    PlanValidatorService().run(state)
    assert not [i for i in state.validation_report.critical_issues if i.category == "duplicate_experience"]
