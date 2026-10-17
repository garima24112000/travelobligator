from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.models.common import ReadinessStatus, ValidationSeverity
from app.models.planning_state import PlanningState
from app.repositories.planning_state_repository import PlanningStateRepository
from app.services import experience_planner_service as planner_module
from app.services import review_classification as classification
from app.services.must_visit_matching import must_visit_place_ids
from app.services.plan_validator_service import PlanValidatorService
from app.services.remote_days import remote_days
from app.storage.local_json_store import LocalJsonStore
from app.tests.services.test_final_quality_correction_203c2b import _poi, _point, _state

# Phase 1 (travel feasibility): a day whose stops ALL lie away from where the
# trip's places are. Its legs cover only the moves between its own stops, so
# its travel figures can read as a short compact day while the journey to the
# area and back is unchecked. The boundary is the planner's own pool reach;
# the plan is never changed; nothing is estimated. Synthetic fixtures on a
# small grid (at latitude 50, 0.01 degree of longitude is about 0.72 km).

_CATEGORY = "remote_day_access"
_CODE = _CATEGORY.upper()

# Twelve places within about 1.5 km of each other, and three about 25 km east.
_CENTRAL = [_poi(f"c{i}", f"Museum Central {i}", _point(50.0 + 0.003 * (i % 4), 10.0 + 0.004 * (i // 4))) for i in range(12)]
_FAR = [_poi(f"f{i}", f"Museum Far {i}", _point(50.0 + 0.002 * i, 10.35)) for i in range(3)]


def _planned(days: list[list[dict[str, Any]]], pois: list[dict[str, Any]] | None = None, **trip: Any) -> PlanningState:
    state = _state(pois if pois is not None else [*_CENTRAL, *_FAR], days, **trip)
    PlanValidatorService().run(state)
    return state


def _findings(state: PlanningState) -> list[Any]:
    return [issue for issue in state.validation_report.warnings if issue.category == _CATEGORY]


_THREE_DAYS = [_CENTRAL[0:3], _FAR[0:2], _CENTRAL[3:6]]


# -- what raises the finding ---------------------------------------------------------------


def test_a_day_whose_stops_all_lie_beyond_the_pool_reach_is_disclosed_on_that_day() -> None:
    state = _planned(_THREE_DAYS)

    (finding,) = _findings(state)
    assert finding.severity == ValidationSeverity.WARNING
    assert finding.affected_section == "experience_plan.daily_plans[2]"
    assert finding.message == (
        "Day 2: All of this day's stops lie away from the area where most of this trip's places are. The travel "
        "times shown cover only the moves between this day's stops: getting to this area and back is not included "
        "and has not been checked."
    )
    assert finding.suggested_fix == (
        "Check how you will get to this area and back, and allow time for it, before following this day."
    )
    report = state.validation_report
    assert _CODE in report.review_codes and report.readiness_status == ReadinessStatus.NEEDS_REVIEW
    assert report.review_code_classification[_CODE] == classification.MATERIAL
    assert classification.classify(_CODE) == classification.MATERIAL


def test_a_single_stop_remote_day_says_the_journey_there_and_back_is_unchecked() -> None:
    state = _planned([_CENTRAL[0:3], _FAR[0:1], _CENTRAL[3:6]])
    (finding,) = _findings(state)
    assert finding.message == (
        "Day 2: This day's stop lies away from the area where most of this trip's places are. Getting there and "
        "back is not included in this plan and has not been checked."
    )


def test_a_requested_place_keeps_its_day_and_is_named() -> None:
    requested = {**_FAR[0], "must_visit_term": "The Far One"}
    pois = [*_CENTRAL, requested, *_FAR[1:]]
    days = [_CENTRAL[0:3], [requested, _FAR[1]], _CENTRAL[3:6]]
    state = _planned(days, pois, must_visit=["The Far One"])
    assert must_visit_place_ids(state) == {"geoapify/f0"}
    layout = [[stop.provider_place_id for stop in day.experiences] for day in state.experience_plan.daily_plans]

    (finding,) = _findings(state)
    assert finding.message.endswith(" Museum Far 0 is on this day because you asked for it.")
    assert finding.severity == ValidationSeverity.WARNING  # the journey is unchecked whatever the reason
    # the excursion is preserved: nothing is moved or removed by validation
    assert [[stop.provider_place_id for stop in day.experiences] for day in state.experience_plan.daily_plans] == layout


def test_two_requested_places_are_both_named() -> None:
    first, second = ({**place, "must_visit_term": f"Far {i}"} for i, place in enumerate(_FAR[0:2]))
    state = _planned([_CENTRAL[0:3], [first, second], _CENTRAL[3:6]], [*_CENTRAL, first, second, _FAR[2]],
                     must_visit=["Far 0", "Far 1"])
    (finding,) = _findings(state)
    assert finding.message.endswith(" Museum Far 0 and Museum Far 1 are on this day because you asked for them.")


# -- what does not raise it ------------------------------------------------------------------


def test_a_compact_central_plan_has_no_finding() -> None:
    state = _planned([_CENTRAL[0:3], _CENTRAL[3:6], _CENTRAL[6:9]])
    assert _findings(state) == [] and _CODE not in state.validation_report.review_codes


def test_a_day_with_one_stop_where_the_places_are_is_not_remote() -> None:
    """Any located stop within reach anchors the day; that day's spread is
    the existing geography findings' business, not this one's."""
    state = _planned([_CENTRAL[0:3], [_CENTRAL[3], _FAR[0]], _CENTRAL[4:7]])
    assert _findings(state) == []


def test_the_boundary_is_the_planners_own_pool_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    """No threshold of its own: widen the planner's isolated-outlier
    minimum and the same day is within reach."""
    assert [day.day_number for day in remote_days(_state([*_CENTRAL, *_FAR], _THREE_DAYS))] == [2]
    monkeypatch.setattr(planner_module, "_OUTLIER_MIN_KM", 30.0)
    assert remote_days(_state([*_CENTRAL, *_FAR], _THREE_DAYS)) == []


def test_the_boundary_adapts_to_a_spread_out_destination() -> None:
    """A destination whose own places are spread widely has a wider reach:
    a day 25 km out is ordinary there. No per-city radius."""
    ring = [
        _poi(f"r{i}", f"Museum Ring {i}", _point(50.0 + 0.12 * ((i % 3) - 1), 10.0 + 0.18 * ((i // 3) - 1)))
        for i in range(9)
    ]
    state = _planned([ring[0:3], _FAR[0:2], ring[3:6]], [*ring, *_FAR])
    assert _findings(state) == []


def test_a_day_without_a_located_stop_and_a_pool_without_coordinates_say_nothing() -> None:
    unlocated = {**_FAR[0], "coordinates": None}
    state = _state([*_CENTRAL, unlocated], [_CENTRAL[0:3], _CENTRAL[3:6]])
    state.experience_plan.daily_plans[1].experiences[0].coordinates = None
    state.experience_plan.daily_plans[1].experiences = state.experience_plan.daily_plans[1].experiences[:1]
    assert remote_days(state) == []

    bare = [{**poi, "coordinates": None} for poi in _CENTRAL]
    assert remote_days(_state(bare, [_FAR[0:2]])) == []


def test_no_plan_no_finding() -> None:
    state = _state([*_CENTRAL, *_FAR], [])
    state.experience_plan = None
    assert remote_days(state) == []


# -- nothing invented -------------------------------------------------------------------------


def test_no_time_distance_mode_or_place_to_stay_is_stated() -> None:
    for state in (_planned(_THREE_DAYS), _planned([_CENTRAL[0:3], _FAR[0:1], _CENTRAL[3:6]])):
        (finding,) = _findings(state)
        text = f"{finding.message} {finding.suggested_fix}"
        assert not re.search(r"\d", text.removeprefix("Day 2: ")), text  # the day number is the only figure
        for word in ("minute", "hour", " km", "drive", "taxi", "bus", "train", "transit", "ferry", "hotel",
                     "accommodation", "provider", "inventory", "closed", "inaccessible"):
            assert word not in text.lower(), word


def test_validation_makes_no_change_to_the_plan_or_its_legs() -> None:
    state = _state([*_CENTRAL, *_FAR], _THREE_DAYS)
    before = state.experience_plan.model_dump(mode="json")
    PlanValidatorService().run(state)
    assert state.experience_plan.model_dump(mode="json") == before
    assert state.route_feasibility_report is None  # no route was asked for or made up


def test_every_other_finding_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import plan_validator_service as validator_module

    with_it = _planned(_THREE_DAYS)
    monkeypatch.setattr(validator_module, "_remote_day_issues", lambda _state: [])
    without_it = _planned(_THREE_DAYS)
    strip = lambda state: [  # noqa: E731
        (issue.category, issue.severity, issue.message) for issue in state.validation_report.warnings
        if issue.category != _CATEGORY
    ]
    assert strip(with_it) == strip(without_it)
    assert set(with_it.validation_report.review_codes) - set(without_it.validation_report.review_codes) == {_CODE}


def test_the_finding_survives_save_and_reload(tmp_path: Path) -> None:
    state = _planned(_THREE_DAYS)
    path = tmp_path / "state.json"
    PlanningStateRepository(store=LocalJsonStore(path)).save(state)
    reloaded = PlanningStateRepository(store=LocalJsonStore(path)).get_by_trip_id(state.trip_id)
    assert reloaded is not None and reloaded is not state
    assert [issue.model_dump() for issue in _findings(reloaded)] == [issue.model_dump() for issue in _findings(state)]
    assert reloaded.validation_report.review_code_classification[_CODE] == classification.MATERIAL


def test_an_unreadable_state_never_fails_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import remote_days as remote_module

    def broken(_state: PlanningState) -> list[Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(remote_module, "remote_days", broken)
    state = _planned(_THREE_DAYS)
    assert _findings(state) == [] and state.validation_report is not None


def test_identical_inputs_give_identical_findings() -> None:
    first, second = _planned(_THREE_DAYS), _planned(_THREE_DAYS)
    assert [issue.message for issue in first.validation_report.warnings] == [
        issue.message for issue in second.validation_report.warnings
    ]
