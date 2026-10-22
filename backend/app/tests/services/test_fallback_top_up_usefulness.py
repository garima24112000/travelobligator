from __future__ import annotations

import itertools
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import get_settings
from app.services import experience_planner_service as planner_module
from app.services import geographic_dispersion as dispersion
from app.services.candidate_usefulness import usefulness_by_place_id
from app.services.entity_collisions import SUSPECT_COLLISION_KEY
from app.tests.services.test_day_composition_q3 import _ai_state, _core, _place
from app.tests.services.test_quality_tuning_corrections import _introduced_by, _traced

# The deterministic fallback top-up takes the MOST USEFUL candidate that keeps a day within the
# geographic boundary (the one Q2 ordering); distance to the day only breaks that ordering's
# ties. It used to take the nearest compatible candidate, whatever its evidence. Synthetic
# fixtures only -- no city, no real place, no provider call.

_REMOTE_KM = 30.0
_top_up = planner_module._top_up_underfilled_days


class _Profile:
    """The facts the top-up reads, with the shape of the real usefulness record."""

    low_value = False

    def __init__(self, key: str, *, band: int = 0, score: float = 0.5, must_visit: bool = False, low_value: bool = False) -> None:
        self.low_value = low_value
        preference = (0 if must_visit else 1, -band, 1, 1, 0)
        tail = (-score, key, key)
        self.usefulness = SimpleNamespace(
            must_visit=must_visit, evidence_band=band, preference=preference, tail=tail, sort_key=(*preference, *tail)
        )


def _key(poi: dict[str, Any]) -> str:
    return poi["place_id"].split("/", 1)[1]


def _run(days: list[list[dict[str, Any]]], unused: list[dict[str, Any]], facts: dict[str, dict[str, Any]],
         *, cap: int = 3, minimum: int | None = None) -> list[list[str]]:
    pool = [*(poi for day in days for poi in day), *unused]
    profiles = {id(poi): _Profile(_key(poi), **facts.get(_key(poi), {})) for poi in pool}
    return [[_key(poi) for poi in day] for day in _top_up(days, pool, profiles, cap, minimum_stops=minimum)]


def _full_and_open() -> list[list[dict[str, Any]]]:
    """Day 1 is full; day 2 has ONE open slot, so the choice is visible."""
    return [[_place("a", 0.0), _place("b", 0.2), _place("c", 0.4)], [_place("d", 0.0, 0.3), _place("e", 0.2, 0.3)]]


# =====================================================================================
# The choice among compatible candidates
# =====================================================================================


def test_a_farther_high_evidence_place_beats_the_filler_next_door() -> None:
    filler, documented = _place("filler", 0.15, 0.3), _place("documented", 2.5, 0.3)
    # nearest-first took `filler` (150 m away); both keep the day within the boundary
    assert _run(_full_and_open(), [filler, documented], {"documented": {"band": 3}}) == [
        ["a", "b", "c"], ["d", "e", "documented"],
    ]
    # ... and with room for both, the more useful one is placed first (the emptiest day takes it)
    assert _run(_full_and_open(), [filler, documented], {"documented": {"band": 3}}, cap=4) == [
        ["a", "b", "c", "filler"], ["d", "e", "documented"],
    ]


def test_a_high_evidence_place_beyond_the_boundary_stays_out() -> None:
    filler, remote = _place("filler", 0.15, 0.3), _place("remote", _REMOTE_KM)
    facts = {"remote": {"band": 3, "score": 0.99}}
    # a compatible candidate exists: it is used, however much evidence the remote one carries
    assert _run(_full_and_open(), [remote, filler], facts) == [["a", "b", "c"], ["d", "e", "filler"]]
    # nothing compatible, plan at or above the minimum: the slot stays open (T is a target)
    assert _run(_full_and_open(), [remote], facts, minimum=5) == [["a", "b", "c"], ["d", "e"]]
    assert _run(_full_and_open(), [remote], facts) == [["a", "b", "c"], ["d", "e"]]


def test_distance_only_separates_candidates_the_ordering_holds_equal() -> None:
    near, far = _place("zz_near", 0.3, 0.3), _place("aa_far", 3.0, 0.3)
    # same preference and score: the nearer one, although the neutral name tie-break says otherwise
    assert _run(_full_and_open(), [far, near], {})[1][-1] == "zz_near"
    # a better quality score is part of the canonical ordering and comes before distance
    assert _run(_full_and_open(), [far, near], {"aa_far": {"score": 0.9}})[1][-1] == "aa_far"
    # ... and a higher evidence band before the score
    assert _run(_full_and_open(), [far, near], {"aa_far": {"score": 0.1, "band": 1}})[1][-1] == "aa_far"


def test_the_choice_does_not_depend_on_the_order_of_the_pool() -> None:
    unused = [
        _place("u_band2", 2.0, 0.3), _place("u_band2_far", 3.0, 0.3), _place("u_score", 1.0, 0.3),
        _place("u_plain", 0.1, 0.3), _place("u_plain_far", 4.0, 0.3), _place("u_remote", _REMOTE_KM),
    ]
    facts = {"u_band2": {"band": 2}, "u_band2_far": {"band": 2}, "u_score": {"score": 0.8}, "u_remote": {"band": 3}}
    results = set()
    for order in itertools.permutations(unused):
        days = [[_place("a", 0.0), _place("b", 0.2)], [_place("d", 0.0, 0.3)]]
        results.add(str(_run(days, list(order), facts, cap=4)))
    assert len(results) == 1
    plan = _run([[_place("a", 0.0), _place("b", 0.2)], [_place("d", 0.0, 0.3)]], unused, facts, cap=4)
    # emptiest day first, the most useful compatible candidate each time; the remote one never
    assert plan == [["a", "b", "u_band2_far", "u_plain"], ["d", "u_band2", "u_score", "u_plain_far"]]
    assert "u_remote" not in [key for day in plan for key in day]


# =====================================================================================
# What the top-up still never does
# =====================================================================================


def test_nothing_scheduled_is_removed_so_a_sole_cover_of_an_interest_stays() -> None:
    # Day 1 is full and holds the only place serving another requested interest; a far better
    # candidate cannot take its slot -- the top-up only ever ADDS to an open day.
    sole_cover = _place("only_park", 0.4, kind="park")
    days = [[_place("a", 0.0), _place("b", 0.2), sole_cover], [_place("d", 0.0, 0.3), _place("e", 0.2, 0.3)]]
    documented = _place("documented", 0.5)
    plan = _run(days, [documented, _place("second", 0.6)], {"documented": {"band": 3}, "second": {"band": 3}})
    assert plan == [["a", "b", "only_park"], ["d", "e", "documented"]]


def test_ineligible_and_already_scheduled_places_are_never_added() -> None:
    low_value = _place("statue", 0.1, 0.3)
    unlocated = {**_place("nowhere", 0.0), "coordinates": None}
    scheduled_again = _full_and_open()
    protected = scheduled_again[0][0]  # the same record offered again by the pool
    pool_extra = [low_value, unlocated]
    profiles_facts = {"statue": {"band": 3, "low_value": True}, "nowhere": {"band": 3}, "a": {"must_visit": True}}
    pool = [*(poi for day in scheduled_again for poi in day), protected, *pool_extra]
    profiles = {id(poi): _Profile(_key(poi), **profiles_facts.get(_key(poi), {})) for poi in pool}
    plan = [[_key(poi) for poi in day] for day in _top_up(scheduled_again, pool, profiles, 3)]
    assert plan == [["a", "b", "c"], ["d", "e"]]


def test_a_thin_plan_still_takes_a_distant_place_to_reach_the_minimum() -> None:
    nearer_remote, farther_remote = _place("r_near", _REMOTE_KM), _place("r_far", 2 * _REMOTE_KM)
    facts = {"r_far": {"band": 3}}
    # below R with nothing compatible: ONE place at a time, the least dispersing -- not the best-evidenced
    assert _run(_full_and_open(), [farther_remote, nearer_remote], facts, minimum=6) == [
        ["a", "b", "c"], ["d", "e", "r_near"],
    ]
    # below R, but a compatible candidate exists: it is used and no remote place is
    filler = _place("filler", 0.15, 0.3)
    assert _run(_full_and_open(), [farther_remote, filler], facts, minimum=6) == [["a", "b", "c"], ["d", "e", "filler"]]


def test_a_grounded_must_visit_is_first_when_compatible_and_still_added_when_distant() -> None:
    requested, documented = _place("requested", 1.0, 0.3), _place("documented", 0.3, 0.3)
    facts = {"requested": {"must_visit": True}, "documented": {"band": 3, "score": 0.99}}
    assert _run(_full_and_open(), [documented, requested], facts)[1][-1] == "requested"
    distant = _place("requested", _REMOTE_KM)
    assert _run(_full_and_open(), [distant], {"requested": {"must_visit": True}}, minimum=5) == [
        ["a", "b", "c"], ["d", "e", "requested"],
    ]


# =====================================================================================
# Through the planner: the real Q2 ordering, the real stages around the top-up
# =====================================================================================


@pytest.fixture
def setting(monkeypatch: pytest.MonkeyPatch) -> Any:
    def set_value(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield set_value
    get_settings.cache_clear()


def _underfilled(extra: list[dict[str, Any]], **state: Any) -> Any:
    """Two days the model left one stop short, the usefulness fallback applied."""
    pool = [*_core(5), *extra]
    planning_state = _ai_state(pool, [["c0", "c1", "c2"], ["c3", "c4"]], viable=len(pool), **state)
    planning_state.usefulness_fallback_applied = True
    return planning_state


@pytest.mark.parametrize("composition", ["true", "false"], ids=["day composition on", "day composition off"])
def test_the_planner_tops_up_with_the_better_evidenced_compatible_place(setting: Any, composition: str) -> None:
    setting("DAY_COMPOSITION_ENABLED", composition)
    next_door = _place("p0", 0.25, 0.1, kind="park", name="Pocket Park Next Door")
    documented = _place("d0", 2.0, name="Documented Museum", wikipedia="en:D0", heritage="yes")
    state = _underfilled([next_door, documented])
    assessed = usefulness_by_place_id(state)
    # the ordering the top-up reads is the canonical one, not a figure of its own
    assert assessed[documented["place_id"]].preference < assessed[next_door["place_id"]].preference

    plan, _, history = _traced(state)
    names = [name for day in plan for name in day]
    assert "Documented Museum" in names and "Pocket Park Next Door" not in names
    assert _introduced_by(state, history)["Documented Museum"] == "fallback_top_up"
    assert sorted(len(day) for day in plan) == [3, 3] and dispersion.assess_days(state) == []


def test_the_planner_still_leaves_a_remote_documented_place_out(setting: Any) -> None:
    next_door = _place("p0", 0.25, 0.1, kind="park", name="Pocket Park Next Door")
    remote = _place("r0", _REMOTE_KM, name="Remote Documented Museum", wikipedia="en:R0", heritage="yes")
    state = _underfilled([next_door, remote])
    plan, _, history = _traced(state)
    names = [name for day in plan for name in day]
    assert "Remote Documented Museum" not in names
    assert _introduced_by(state, history)["Pocket Park Next Door"] == "fallback_top_up"
    assert dispersion.assess_days(state) == []


def test_a_suspected_duplicate_of_a_must_visit_is_never_scheduled_beside_it() -> None:
    # The best-evidenced unused place may be the same real place as the must-visit (an unresolved
    # suspected pair). Whatever the top-up reaches for, the existing separation keeps the
    # must-visit and the pair is never scheduled together.
    pool = _core(5)
    must_visit = pool[0]
    twin = _place("t0", 0.3, 0.3, name="Twin Record", wikipedia="en:T0", heritage="yes")
    must_visit[SUSPECT_COLLISION_KEY] = twin[SUSPECT_COLLISION_KEY] = "pair-1"
    other = _place("o0", 0.5, 0.3, name="Other Museum")
    state = _ai_state([*pool, twin, other], [["c0", "c1", "c2"], ["c3", "c4"]], viable=7, must_visit={must_visit["name"]: must_visit})
    state.usefulness_fallback_applied = True
    plan, _, _ = _traced(state)
    names = [name for day in plan for name in day]
    assert must_visit["name"] in names and "Twin Record" not in names
    assert len(names) == len(set(names))
