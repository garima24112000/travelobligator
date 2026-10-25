"""Final Batch 1 (B): remote and thin days.

Synthetic fixtures only. The remote-day rebuild now acts on a plan the
reasoning model chose as well, a rebuilt day is filled to the pace minimum
rather than to the size of the remote day, a thin day is topped up with useful
places within reach (never with filler), and a requested excursion day is left
as it is and not called thin."""

from __future__ import annotations

from typing import Any

import pytest

from app.models.planning_state import TripPace
from app.services import visit_day_feasibility as feas
from app.services.pace_targets import MIN_STOPS_PER_DAY
from app.services.plan_validator_service import PlanValidatorService
from app.tests.services.test_day_composition_q3 import _ai_state, _core, _place, _plan
from app.tests.services.test_venue_reliability_m2p3 import START, _helpers, _ids, _poi


def _remote(days, pool, helpers, *, minimum_per_day: int, max_per_day: int | None = None, healthy: bool = True):
    return feas.resolve_unreasoned_remote_days(
        days, pool, helpers, within_reach=lambda p: p["coordinates"]["lat"] < 0.5,
        requested_interests=frozenset(), start_date=START, holidays=frozenset(),
        supply_healthy=healthy, minimum_stops=1, minimum_per_day=minimum_per_day, max_per_day=max_per_day,
    )


def _thin(days, pool, helpers, *, minimum_per_day: int, healthy: bool = True):
    return feas.fill_thin_days(
        days, pool, helpers, within_reach=lambda p: p["coordinates"]["lat"] < 0.5, start_date=START,
        holidays=frozenset(), supply_healthy=healthy, minimum_per_day=minimum_per_day,
    )


CENTRAL = [_poi(f"c{i}", 0.0, i * 0.001) for i in range(5)]


def test_a_single_isolated_stop_is_rebuilt_to_the_pace_minimum_not_to_one_stop() -> None:
    days, report = _remote([[_poi("a")], [_poi("far", 1.0)]], CENTRAL, _helpers(), minimum_per_day=3, max_per_day=4)
    assert len(_ids(days)[1]) == 3 and all(stop.startswith("c") for stop in _ids(days)[1])
    assert report.actions[0] == {"day": 2, "action": feas.REMOTE_REBUILT, "replaced": 1, "stops": 3}


def test_the_rebuild_never_exceeds_the_pace_cap() -> None:
    days, _ = _remote([[_poi("a")], [_poi("far", 1.0)]], CENTRAL, _helpers(), minimum_per_day=3, max_per_day=2)
    assert len(_ids(days)[1]) == 2


def test_a_remote_must_visit_day_is_kept() -> None:
    days, report = _remote([[_poi("a")], [_poi("far", 1.0)]], CENTRAL, _helpers(must={"far"}), minimum_per_day=3)
    assert _ids(days)[1] == ["far"] and report.actions[0]["action"] == feas.REMOTE_KEPT_REASON


def test_a_thin_day_is_topped_up_with_the_most_useful_places_within_reach() -> None:
    pool = [_poi("best", 0.0, 0.002), _poi("good", 0.0, 0.003), _poi("far", 1.0)]
    helpers = _helpers(tiers={"a": 3, "best": 4, "good": 3, "far": 4})
    days, report = _thin([[_poi("a")], [_poi("b"), _poi("d", 0.0, 0.001)]], pool, helpers, minimum_per_day=2)
    assert _ids(days) == [["a", "best"], ["b", "d"]]
    assert report.actions == [{"day": 1, "action": feas.THIN_FILLED, "added": 1}]


def test_no_filler_a_lighter_day_beats_a_weak_place() -> None:
    helpers = _helpers(tiers={"a": 4, "weak": 2})  # two tiers below the day's weakest stop
    days, report = _thin([[_poi("a")]], [_poi("weak", 0.0, 0.002)], helpers, minimum_per_day=2)
    assert _ids(days) == [["a"]] and report.actions[0]["action"] == feas.THIN_KEPT_NO_CANDIDATE


def test_a_thin_day_never_takes_a_duplicate_a_closed_place_or_a_dispersing_one() -> None:
    closed = _poi("closed", 0.0, 0.001, venue={"opening_hours": "off"})
    dup = _poi("dup", 0.0, 0.002)
    dispersing = _poi("wide", 0.0, 0.15)  # ~16 km east: within reach here, beyond the spread boundary
    helpers = _helpers(dup={frozenset({"dup", "a"})})
    days, report = _thin([[_poi("a")]], [closed, dup, dispersing], helpers, minimum_per_day=2)
    assert _ids(days) == [["a"]] and report.actions[0]["action"] == feas.THIN_KEPT_NO_CANDIDATE


def test_a_requested_excursion_day_and_an_unhealthy_supply_are_left_alone() -> None:
    days, report = _thin([[_poi("far", 1.0)]], CENTRAL, _helpers(), minimum_per_day=3)
    assert _ids(days) == [["far"]] and report.actions[0]["action"] == feas.THIN_KEPT_REMOTE
    days, report = _thin([[_poi("a")]], CENTRAL, _helpers(), minimum_per_day=3, healthy=False)
    assert _ids(days) == [["a"]] and report.actions == []


def test_the_minimum_is_the_validators_thin_day_rule() -> None:
    from app.services import plan_quality_findings

    assert plan_quality_findings._MIN_STOPS_PER_DAY is MIN_STOPS_PER_DAY
    assert MIN_STOPS_PER_DAY == {TripPace.RELAXED: 1, TripPace.BALANCED: 2, TripPace.PACKED: 3}


# -- end to end: a plan the model chose ---------------------------------------------------------


def test_a_model_chosen_isolated_day_is_rebuilt_within_reach(setting: Any) -> None:
    pois = [*_core(8), _place("island", 100.0)]  # one place ~100 km away, alone on a day
    state = _ai_state(pois, [["c0", "c1", "c2"], ["island"]])
    days = _plan(state)
    assert "Museum island" not in {name for day in days for name in day}
    assert len(days[1]) >= MIN_STOPS_PER_DAY[TripPace.BALANCED]
    PlanValidatorService().run(state)
    categories = {issue.category for issue in state.validation_report.warnings}
    assert "remote_day_access" not in categories and "thin_day" not in categories


def test_under_an_active_lock_the_model_plan_is_untouched(setting: Any) -> None:
    pois = [*_core(8), _place("island", 100.0)]
    days = _plan(_ai_state(pois, [["c0", "c1", "c2"], ["island"]], lock=True))
    assert days[1] == ["Museum island"]


def test_a_model_chosen_thin_day_is_topped_up(setting: Any) -> None:
    pois = _core(8)
    days = _plan(_ai_state(pois, [["c0", "c1", "c2"], ["c3"]]))
    assert len(days[1]) >= MIN_STOPS_PER_DAY[TripPace.BALANCED] and "Museum c3" in days[1]


@pytest.fixture
def setting(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.core.config import get_settings

    def set_value(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield set_value
    get_settings.cache_clear()


def test_a_requested_excursion_day_is_not_called_thin() -> None:
    from app.tests.services.test_final_quality_correction_203c2b import _poi as poi203, _point, _state

    pool = [poi203(f"n{i}", f"Near {i}", _point(50.0 + i * 0.001, 10.0)) for i in range(8)]
    excursion = poi203("e", "Far Site", _point(50.6, 10.0), must_visit_terms=["Far Site"], must_visit_term="Far Site")
    state = _state([*pool, excursion], [pool[:3], [excursion]], must_visit=["Far Site"])
    state.destination_context.must_visit_grounding_version = 1
    PlanValidatorService().run(state)
    categories = {issue.category for issue in state.validation_report.warnings}
    assert "remote_day_access" in categories and "thin_day" not in categories
