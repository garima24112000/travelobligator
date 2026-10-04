from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from app.models.common import GeoPoint
from app.models.planning_state import PlanningState, UserLock
from app.services import experience_planner_service as planner_module
from app.services import grounded_anchors
from app.services import place_taxonomy as taxonomy
from app.services.experience_planner_service import ExperiencePlannerService
from app.tests.services.test_batch1_tuning_fixes_3b import (
    _broad_museums,
    _hall,
    _plan_state,
    _promoted,
    _scheduled,
)
from app.tests.services.test_experience_planner_ai_guided import _completed_result, _day
from app.tests.services.test_final_quality_correction_203c2b import _poi, _point

# Section 3C.1: grounded-anchor utilization when the reasoning model's plan
# is used. The model chooses the plan; a bounded deterministic pass then lets
# a few ordinary broad-pool stops give way to compatible grounded anchors
# when -- and only when -- the plan clearly under-uses them. Every fixture is
# synthetic.

_SOURCE = "geoapify_places"


def _candidate_id(key: str) -> str:
    return planner_module.build_candidate_id(_SOURCE, f"geoapify/{key}")


def _castles(count: int, lat: float = 50.001, lng: float = 10.001, step: float = 0.002) -> list[Any]:
    return [_promoted(f"anchor{index}", f"Castle {index}", _point(lat + step * index, lng)) for index in range(count)]


def _reasoned_state(
    anchors: list[Any], days: list[list[str]], *, interests: list[str] | None = None, **kwargs: Any
) -> PlanningState:
    """A 3-day plan whose days the reasoning model chose (by candidate id)."""
    state = _plan_state(_broad_museums(), anchors, interests=interests, **kwargs)
    state.ai_itinerary_reasoning_result = _completed_result(
        [_day(index, [_candidate_id(key) for key in keys]) for index, keys in enumerate(days, start=1)]
    )
    return state


def _names(state: PlanningState) -> list[str]:
    return [name for day in _scheduled(state) for name in day]


def _castle_count(names: list[str]) -> int:
    return sum(name.startswith("Castle") for name in names)


# the model scheduled ONE of six grounded anchors
_ONE_OF_SIX = [["m0", "m1", "anchor0"], ["m4", "m5", "m6"], ["m8", "m9", "m10"]]


# 1. reasoning succeeds but 1/6 anchors used -> bounded substitution occurs
def test_a_reasoned_plan_using_one_of_six_anchors_gets_a_bounded_substitution() -> None:
    state = _reasoned_state(_castles(6), _ONE_OF_SIX)
    ExperiencePlannerService().run(state)

    days = _scheduled(state)
    names = [name for day in days for name in day]
    assert [len(day) for day in days] == [3, 3, 3]  # T and the day sizes are untouched
    assert len(set(names)) == 9  # an exchange: nothing added, dropped or repeated
    # one anchor per day -- several, never all six, never a quota
    assert planner_module.anchor_seed_count(3, 9) == 3
    assert _castle_count(names) == 3 and all(_castle_count(day) == 1 for day in days)
    # the model's own anchor is kept, and only two of its nine choices gave way
    chosen = {"Museum 00", "Museum 01", "Castle 0", "Museum 04", "Museum 05", "Museum 06", "Museum 08", "Museum 09", "Museum 10"}
    assert "Castle 0" in names and len(chosen - set(names)) == 2
    # the replacements are provider-grounded places from the same pool
    for day in state.experience_plan.daily_plans:
        for stop in day.experiences:
            assert stop.provider_place_id and stop.provider_source and stop.coordinates
    assert {s.provider_place_id for d in state.experience_plan.daily_plans for s in d.experiences if s.name.startswith("Castle")} <= (
        grounded_anchors.grounded_anchor_place_ids(state)
    )


# 2. reasoning succeeds and anchors already well represented -> unchanged
def test_a_reasoned_plan_that_already_uses_its_anchors_is_left_alone() -> None:
    two_anchors = [["m0", "m1", "anchor0"], ["m4", "m5", "anchor1"], ["m8", "m9", "m10"]]
    state = _reasoned_state(_castles(6), two_anchors)
    ExperiencePlannerService().run(state)
    assert sorted(_names(state)) == sorted(
        ["Museum 00", "Museum 01", "Castle 0", "Museum 04", "Museum 05", "Castle 1", "Museum 08", "Museum 09", "Museum 10"]
    )

    # too few grounded anchors to call it low utilization: unchanged as well
    few = _reasoned_state(_castles(3), _ONE_OF_SIX)
    ExperiencePlannerService().run(few)
    assert _castle_count(_names(few)) == 1

    # without the reasoning model's plan this pass does not run at all (the fallback seeds instead)
    assert grounded_anchors.low_anchor_utilization(6, 1, 3) is True
    assert grounded_anchors.low_anchor_utilization(6, 2, 3) is False
    assert grounded_anchors.low_anchor_utilization(3, 0, 3) is False
    assert grounded_anchors.low_anchor_utilization(6, 0, 1) is False


def test_the_planner_trigger_and_the_benchmark_flag_use_the_same_threshold() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "benchmark_tuning_cities.py"
    spec = importlib.util.spec_from_file_location("benchmark_tuning_cities_for_3c1", script)
    bench = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(bench)
    assert (bench.LOW_ANCHOR_UTILIZATION_MIN_GROUNDED, bench.LOW_ANCHOR_UTILIZATION_MAX_SCHEDULED) == (
        grounded_anchors.LOW_ANCHOR_UTILIZATION_MIN_GROUNDED, grounded_anchors.LOW_ANCHOR_UTILIZATION_MAX_SCHEDULED,
    )


# 3. anchor would worsen geographic burden materially -> no substitution
def test_an_anchor_that_would_spread_a_day_out_is_not_substituted() -> None:
    # five more anchors ~5 km from the compact days: inside the destination, but each would
    # be far farther from a day's other stops than any stop it could replace
    distant = [_promoted("anchor0", "Castle 0", _point(50.001, 10.001))] + [
        _promoted(f"anchor{index}", f"Castle {index}", _point(50.045 + 0.001 * index, 10.001)) for index in range(1, 6)
    ]
    state = _reasoned_state(distant, _ONE_OF_SIX)
    ExperiencePlannerService().run(state)
    assert sorted(_names(state)) == sorted(
        ["Museum 00", "Museum 01", "Castle 0", "Museum 04", "Museum 05", "Museum 06", "Museum 08", "Museum 09", "Museum 10"]
    )

    # an anchor far outside the destination's own spread is never considered either
    remote = [_promoted("anchor0", "Castle 0", _point(50.001, 10.001))] + [
        _promoted(f"anchor{index}", f"Castle {index}", _point(51.5 + 0.001 * index, 11.5)) for index in range(1, 6)
    ]
    state = _reasoned_state(remote, _ONE_OF_SIX)
    ExperiencePlannerService().run(state)
    assert _castle_count(_names(state)) == 1


# -- the pass itself, on hand-built days ---------------------------------------------------------------


def _top_tier(poi: dict[str, Any]) -> dict[str, Any]:
    return {**poi, "quality_tier": "primary_anchor"}


def _raise(
    days: list[list[dict[str, Any]]],
    pool: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    interests: list[str],
    *,
    must_visit: tuple[dict[str, Any], ...] = (),
    max_per_day: int = 3,
) -> list[list[str]]:
    canonical = taxonomy.canonical_interests(interests)
    profiles = {id(poi): planner_module._candidate_profile(poi, None, canonical) for poi in pool}
    result = planner_module._raise_anchor_utilization(
        days, pool, profiles, {id(poi) for poi in must_visit}, {id(poi) for poi in anchors}, canonical, max_per_day,
        markets_requested=False,
    )
    return [[poi["name"] for poi in group] for group in result]


def _museum_anchor(key: str, point: GeoPoint) -> dict[str, Any]:
    """A grounded anchor that serves history only."""
    return _top_tier(_poi(key, f"Anchor Museum {key}", point))


# 4. anchor would remove sole interest coverage -> no substitution
def test_the_only_stop_serving_another_interest_is_never_replaced() -> None:
    hall = _top_tier(_hall("only", _point(50.000, 10.000)))  # the plan's only architecture stop
    museums = [_top_tier(poi) for poi in _broad_museums(6)]
    # every anchor sits exactly on the hall, so replacing the hall would be the tightest exchange
    anchors = [_museum_anchor(f"a{index}", _point(50.000, 10.000 + 0.0001 * index)) for index in range(5)]
    days = [[hall, museums[1], museums[2]], [museums[3], museums[4], museums[5]]]

    result = _raise(days, [hall, *museums, *anchors], anchors, ["architecture", "history"])

    assert "Hall only" in result[0]  # kept: no anchor serves architecture
    flat = [name for day in result for name in day]
    assert sum(name.startswith("Anchor Museum") for name in flat) == 2 and len(flat) == 6

    # an anchor that itself serves the interest may take the stop's place
    castle_anchors = [
        _top_tier({**_poi(f"c{index}", f"Castle {index}", _point(50.000, 10.000 + 0.0001 * index)),
                   "category": "castle", "provider_tags": {"historic": "castle"}})
        for index in range(5)
    ]
    result = _raise(days, [hall, *museums, *castle_anchors], castle_anchors, ["architecture", "history"])
    assert sum(name.startswith("Castle") for day in result for name in day) == 2


# 5. must-visit / lock never replaced
def test_a_must_visit_is_never_replaced_and_an_active_lock_leaves_the_plan_untouched() -> None:
    museums = [_top_tier(poi) for poi in _broad_museums(6)]
    anchors = [_museum_anchor(f"a{index}", _point(50.0005, 10.0005 + 0.0001 * index)) for index in range(5)]
    days = [museums[:3], museums[3:]]
    unchanged = [[poi["name"] for poi in group] for group in days]

    assert _raise(days, [*museums, *anchors], anchors, ["history"], must_visit=tuple(museums)) == unchanged
    # only the stops that are NOT must-visits may give way
    protected = tuple(museums[:5])
    result = _raise(days, [*museums, *anchors], anchors, ["history"], must_visit=protected)
    assert [name for day in result for name in day if not name.startswith("Anchor")] == [m["name"] for m in protected]
    assert [[poi["name"] for poi in group] for group in days] == unchanged  # the input is not mutated

    # through the planner: any active user lock leaves the reasoning model's plan exactly as it is
    locked = _reasoned_state(_castles(6), _ONE_OF_SIX)
    locked.user_locks = [UserLock(locked_item_type="experience", locked_item_id="exp-anything")]
    ExperiencePlannerService().run(locked)
    assert _castle_count(_names(locked)) == 1

    # must-visits chosen by the model stay, whatever else gives way
    state = _reasoned_state(_castles(6), _ONE_OF_SIX, must_visit=["Museum 04", "Museum 05", "Museum 06"])
    ExperiencePlannerService().run(state)
    assert {"Museum 04", "Museum 05", "Museum 06"} <= set(_names(state))


def test_quality_diversity_and_low_value_guards_hold() -> None:
    museums = [_top_tier(poi) for poi in _broad_museums(6)]
    days = [museums[:3], museums[3:]]
    unchanged = [[poi["name"] for poi in group] for group in days]
    near = _point(50.0005, 10.0005)

    # an anchor in a lower quality tier than every stop replaces nothing
    weak = [{**_museum_anchor(f"w{index}", near), "quality_tier": "good_candidate"} for index in range(5)]
    assert _raise(days, [*museums, *weak], weak, ["history"]) == unchanged
    # an anchor below the top two tiers is not eligible at all
    low = [{**_museum_anchor(f"l{index}", near), "quality_tier": "secondary_candidate"} for index in range(5)]
    lower_days = [[{**m, "quality_tier": "secondary_candidate"} for m in group] for group in days]
    pool = [poi for group in lower_days for poi in group] + low
    assert _raise(lower_days, pool, low, ["history"]) == unchanged
    # an anchor that serves none of the requested interests is not eligible
    parks = [
        _top_tier({**_poi(f"p{index}", f"Park {index}", near), "category": "park", "provider_tags": {"leisure": "park"}})
        for index in range(5)
    ]
    assert _raise(days, [*museums, *parks], parks, ["history"]) == unchanged
    # a one-day plan is never "low utilization"
    anchors = [_museum_anchor(f"a{index}", near) for index in range(5)]
    assert _raise([museums[:3]], [*museums, *anchors], anchors, ["history"]) == [unchanged[0]]


# 6. deterministic identical mocked input -> identical output
def test_identical_input_gives_identical_output() -> None:
    results = []
    for _ in range(3):
        state = _reasoned_state(_castles(6), _ONE_OF_SIX, interests=["history", "architecture"])
        ExperiencePlannerService().run(state)
        results.append(_scheduled(state))
    assert results[0] == results[1] == results[2]
    assert _castle_count([name for day in results[0] for name in day]) == 3
