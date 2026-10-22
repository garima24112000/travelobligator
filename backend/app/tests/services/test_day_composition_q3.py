from __future__ import annotations

import inspect
import math
import random
import time
from pathlib import Path
from typing import Any

import pytest

from app.core.config import get_settings
from app.models import PlanningState, TripPace
from app.models.ai_itinerary_reasoning import (
    AREA_INSTRUCTION,
    DEFAULT_ITINERARY_REASONING_INSTRUCTIONS,
    ItineraryReasoningCategory,
)
from app.models.common import GeoPoint
from app.models.inventory_sufficiency import InventorySufficiencyReport, InventorySufficiencyStatus
from app.models.planning_state import UserLock
from app.providers.ai_itinerary_reasoning import anthropic_adapter, groq_adapter
from app.providers.ai_itinerary_reasoning.candidate_refs import CandidateRefMap
from app.services import day_composition as composition
from app.services import experience_planner_service as planner_module
from app.services import schedule_diversity as diversity
from app.services.ai_itinerary_reasoning_request_builder import AIItineraryReasoningRequestBuilder
from app.services.day_composition import Located, Policy, Stop, build_areas, compose
from app.services.day_order_heuristics import (
    AS_WELL_PLACED_KM,
    GEOGRAPHIC_SPREAD_THRESHOLD_KM,
    NEAR_DAY_STOPS_KM,
    day_extent_km,
    day_spread_km,
)
from app.services.experience_planner_service import ExperiencePlannerService
from app.tests.services.test_batch1_tuning_fixes_3b import _scheduled
from app.tests.services.test_candidate_usefulness_q2 import _quality_metrics, _state
from app.tests.services.test_experience_planner_ai_guided import _completed_result, _day
from app.tests.services.test_final_quality_correction_203c2b import _poi, _restaurant
from app.utils.geo import EARTH_RADIUS_KM

# Phase Q3: geographic + semantic day composition -- the pure module. Every
# fixture is synthetic: points on a kilometre grid around an arbitrary origin,
# classes and interests from the existing taxonomy. No city, landmark or real
# coordinate appears, and no distance here is a route or a duration.

_KM_PER_DEGREE = EARTH_RADIUS_KM * math.pi / 180.0
_LAT, _LNG = 50.0, 10.0
_HISTORY, _MUSEUM, _NATURE = diversity.HISTORY_ARCHITECTURE, diversity.MUSEUM_CULTURE, diversity.NATURE_VIEW


def _at(north_km: float = 0.0, east_km: float = 0.0) -> GeoPoint:
    """A point `north_km` / `east_km` from the origin. Northward offsets are
    exact great-circle distances, so boundary tests use them."""
    return GeoPoint(
        lat=_LAT + north_km / _KM_PER_DEGREE,
        lng=_LNG + east_km / (_KM_PER_DEGREE * math.cos(math.radians(_LAT))),
    )


def _stop(
    key: str,
    north: float = 0.0,
    east: float = 0.0,
    *,
    band: int = 1,
    tier: int = 3,
    score: float = 0.6,
    must_visit: bool = False,
    cls: str = _HISTORY,
    interests: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
    may_enter: bool = True,
    located: bool = True,
) -> Stop:
    return Stop(
        key=key,
        point=_at(north, east) if located else None,
        preference=(0 if must_visit else 1, -band, 1, 1, -tier),
        tail=(-score, key, key),
        evidence_band=band,
        tier_rank=tier,
        must_visit=must_visit,
        coarse_class=cls,
        interests=frozenset(interests),
        conflicts=frozenset(conflicts),
        may_enter=may_enter,
    )


def _policy(per_day: int = 3, **overrides: Any) -> Policy:
    # History is requested unless a test says otherwise, so a day of historic
    # places is a justified theme and class variety does not interfere.
    overrides.setdefault("justified", frozenset({_HISTORY}))
    return Policy(per_day=per_day, **overrides)


def _run(days: list[list[Stop]], unused: list[Stop] | None = None, **policy: Any) -> composition.Composition:
    return compose(days, unused or [], _policy(**policy))


def _sets(result: composition.Composition) -> list[set[str]]:
    return [set(day) for day in result.days]


def _areas(points: dict[str, GeoPoint | None]) -> composition.Areas:
    return build_areas([Located(key, point, (key,)) for key, point in points.items()])


# =====================================================================================
# 1. Areas: an explicit grouping policy with boundary tests
# =====================================================================================


def test_the_area_diameter_is_the_existing_near_proxy_not_the_validator_warning() -> None:
    assert composition.AREA_MAX_DIAMETER_KM == NEAR_DAY_STOPS_KM == 3.0
    assert composition.EXTENT_MATERIALITY_KM == AS_WELL_PLACED_KM == 1.0
    assert composition.AREA_MAX_DIAMETER_KM != GEOGRAPHIC_SPREAD_THRESHOLD_KM


def test_two_places_exactly_at_the_diameter_share_an_area_and_one_metre_more_do_not() -> None:
    at_boundary = _areas({"a": _at(0.0), "b": _at(3.000)})
    assert at_boundary.area_of["a"] == at_boundary.area_of["b"]

    beyond = _areas({"a": _at(0.0), "b": _at(3.001)})
    assert beyond.area_of["a"] != beyond.area_of["b"]


def test_an_area_never_chains_along_a_corridor() -> None:
    # 2 km steps: every neighbour pair is close, the ends are 4 km apart
    areas = _areas({"a": _at(0.0), "b": _at(2.0), "c": _at(4.0)})

    assert areas.area_of["a"] != areas.area_of["c"]
    for members in areas.members.values():
        points = [_at(2.0 * "abc".index(key)) for key in members]
        assert (day_extent_km(points) or 0.0) <= composition.AREA_MAX_DIAMETER_KM


def test_coincident_points_a_single_candidate_and_missing_coordinates() -> None:
    assert _areas({}).area_of == {}
    assert _areas({"only": _at(1.0)}).area_of == {"only": "a1"}

    areas = _areas({"a": _at(1.0), "b": _at(1.0), "nowhere": None})
    assert areas.area_of == {"a": "a1", "b": "a1"}  # an unlocated candidate gets no area


def test_areas_depend_only_on_the_set_of_candidates_never_on_their_order() -> None:
    generator = random.Random(3)
    located = [
        Located(f"p{index:02d}", _at(generator.uniform(0, 12), generator.uniform(0, 12)), (index % 5, f"p{index:02d}"))
        for index in range(60)
    ]
    expected = build_areas(located)
    for seed in range(5):
        shuffled = list(located)
        random.Random(seed).shuffle(shuffled)
        assert build_areas(shuffled) == expected


def test_areas_are_near_when_their_closest_members_are_within_the_near_proxy() -> None:
    # two tight groups 2.5 km apart at their closest members, a third 30 km away
    areas = _areas({"a1": _at(0.0), "a2": _at(0.5), "b1": _at(3.0, 0.2), "b2": _at(3.6), "far": _at(30.0)})

    first, second, far = areas.area_of["a1"], areas.area_of["b2"], areas.area_of["far"]
    assert areas.area_of["a2"] == first and first != second
    assert second in areas.near[first] and first in areas.near[second]
    assert areas.near[far] == ()


def test_candidates_beyond_the_clustering_bound_join_an_area_they_fit_or_stand_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composition, "MAX_AREA_CANDIDATES", 2)
    areas = build_areas(
        [
            Located("a", _at(0.0), (0,)),
            Located("b", _at(1.0), (1,)),
            Located("fits", _at(0.5), (2,)),  # beyond the bound, inside the area's diameter
            Located("apart", _at(20.0), (3,)),  # beyond the bound, fits nothing
        ]
    )

    assert areas.area_of["fits"] == areas.area_of["a"] == areas.area_of["b"]
    assert areas.area_of["apart"] not in {areas.area_of["a"]}
    assert set(areas.area_of) == {"a", "b", "fits", "apart"}  # nobody is dropped


def test_the_objective_reads_distances_so_an_area_edge_is_not_a_cliff() -> None:
    points = {"a": _at(0.0), "b": _at(1.4), "c": _at(2.4), "d": _at(3.8)}
    areas = _areas(points)
    assert areas.area_of["c"] != areas.area_of["d"]  # 1.4 km apart, on either side of an area edge

    day = [points["c"], points["d"]]
    assert composition.geographic_band(day_extent_km(day), day_spread_km(day)) == composition.COMPACT
    # ... and a day of exactly those two is left alone although a same-area candidate is unused
    result = _run([[_stop("c", 2.4), _stop("d", 3.8)]], [_stop("b", 1.4)], per_day=2)
    assert not result.changed


# =====================================================================================
# 2. Bands and materiality
# =====================================================================================


def test_the_band_boundaries_are_the_two_existing_proxies() -> None:
    def band(*norths: float) -> str:
        points = [_at(north) for north in norths]
        return composition.geographic_band(day_extent_km(points), day_spread_km(points))

    assert band(0.0, 3.000) == composition.COMPACT
    assert band(0.0, 3.001) == composition.EXTENDED
    assert band(0.0, 8.000) == composition.EXTENDED
    assert band(0.0, 8.001) == composition.DISPERSED
    # fewer than two located stops cannot be measured, and is never called compact
    assert composition.geographic_band(day_extent_km([_at(0.0), None]), None) == composition.UNMEASURED


def test_a_reduction_of_exactly_the_tolerance_is_material_and_one_metre_less_is_not() -> None:
    def composed(candidate_north: float) -> composition.Composition:
        # the fixed stop is a must-visit, so only the other one can give way
        day = [_stop("kept", must_visit=True), _stop("far", 2.000)]
        return _run([day], [_stop("nearer", candidate_north)], per_day=2)

    # extent 2.000 km -> 1.000 km: a 1.000 km reduction
    material = composed(1.000)
    assert _sets(material) == [{"kept", "nearer"}]
    assert material.moves[0].reason == composition.REASON_GEOGRAPHY

    # extent 2.000 km -> 1.001 km: 0.999 km, below the tolerance
    assert not composed(1.001).changed


def test_merely_crossing_a_band_boundary_is_not_a_geographic_improvement() -> None:
    # extended (3.2 km) -> compact (2.9 km), but only 0.3 km better
    day = [_stop("kept", must_visit=True), _stop("just_outside", 3.2)]
    result = _run([day], [_stop("just_inside", 2.9)], per_day=2)

    assert not result.changed


def test_a_material_improvement_may_cost_one_evidence_band_but_not_two_and_never_a_tier() -> None:
    def composed(**incoming: Any) -> composition.Composition:
        day = [_stop("kept", band=3), _stop("outlying", 4.0, band=3)]  # extended, not dispersed
        return _run([day], [_stop("local", 0.5, **incoming)], per_day=2)

    assert _sets(composed(band=2)) == [{"kept", "local"}]  # one evidence band lower: accepted
    assert not composed(band=1).changed  # two bands lower: usefulness wins
    assert not composed(band=3, tier=2).changed  # a lower quality tier: usefulness wins


def test_repairing_a_dispersed_day_may_cost_one_quality_tier_but_not_two() -> None:
    def composed(tier: int) -> composition.Composition:
        day = [_stop("kept", band=3, tier=4), _stop("remote", 20.0, band=3, tier=4)]
        return _run([day], [_stop("local", 0.5, band=0, tier=tier)], per_day=2)

    assert _sets(composed(3)) == [{"kept", "local"}]
    assert not composed(2).changed


def test_between_two_material_improvements_the_more_useful_candidate_wins_a_small_distance() -> None:
    day = [_stop("kept", band=2), _stop("outlying", 4.5, band=2)]
    result = _run([day], [_stop("closest", 0.4, band=1), _stop("stronger", 0.5, band=2)], per_day=2)

    # 4.1 km against 4.0 km gained: the same number of tolerance steps, so usefulness decides
    assert _sets(result) == [{"kept", "stronger"}]


def test_a_variety_gain_never_buys_a_material_loss_of_compactness() -> None:
    def composed(other_group_north: float) -> composition.Composition:
        historic = [_stop(f"h{index}", 0.1 * index, cls=_HISTORY) for index in range(3)]
        parks = [_stop(f"p{index}", other_group_north + 0.1 * index, cls=_NATURE) for index in range(3)]
        return _run([historic, parks], justified=frozenset())  # nothing requested: both days are concentrated

    assert composed(0.3).changed  # the same compact area: mixing the two days costs nothing
    assert composed(0.3).moves[0].reason == composition.REASON_VARIETY
    assert not composed(1.5).changed  # 1.5 km apart: the mix would cost a material amount of extent
    assert not composed(20.0).changed  # far apart: the mix would make both days dispersed


def test_a_thematic_day_the_traveller_asked_for_is_left_alone() -> None:
    historic = [_stop(f"h{index}", 0.1 * index, cls=_HISTORY) for index in range(3)]
    parks = [_stop(f"p{index}", 0.3 + 0.1 * index, cls=_NATURE) for index in range(3)]

    result = _run([historic, parks], justified=frozenset({_HISTORY, _NATURE}))

    assert not result.changed


# =====================================================================================
# 3. The whole multi-day assignment
# =====================================================================================


def _group(prefix: str, north: float, count: int = 3, **extra: Any) -> list[Stop]:
    return [_stop(f"{prefix}{index}", north + 0.2 * index, **extra) for index in range(count)]


def test_three_separated_groups_become_three_days_from_a_fully_crossed_start() -> None:
    a, b, c = _group("a", 0.0), _group("b", 20.0), _group("c", 40.0)
    crossed = [[a[0], b[0], c[0]], [a[1], b[1], c[1]], [a[2], b[2], c[2]]]

    result = _run(crossed)

    assert sorted(map(sorted, result.days)) == [["a0", "a1", "a2"], ["b0", "b1", "b2"], ["c0", "c1", "c2"]]
    assert {move.kind for move in result.moves} == {"swap"}  # the same places, only regrouped


def test_two_days_each_holding_a_stop_of_the_others_group_are_untangled() -> None:
    a, b = _group("a", 0.0), _group("b", 20.0)

    result = _run([[a[0], a[1], b[2]], [b[0], b[1], a[2]]])

    assert _sets(result) == [{"a0", "a1", "a2"}, {"b0", "b1", "b2"}]


def test_with_fewer_days_than_groups_no_day_bridges_two_groups() -> None:
    a, b, c = _group("a", 0.0, 4), _group("b", 20.0, 4), _group("c", 40.0, 2, band=1)
    # the starting selection spent a slot of each day on the third, distant group
    start = [[a[0], a[1], c[0]], [b[0], b[1], c[1]]]

    result = _run(start, [a[2], a[3], b[2], b[3]])

    assert _sets(result) == [{"a0", "a1", "a2"}, {"b0", "b1", "b2"}]  # the third group is left unscheduled


def test_a_day_spread_over_three_places_is_rebuilt_around_one_of_them() -> None:
    # one stop in each of three groups: replacing a single stop cannot shrink the day
    start = [[_stop("a0", band=2), _stop("b0", 20.0), _stop("c0", 40.0)]]

    result = _run(start, [_stop("a1", 0.3), _stop("a2", 0.6)])

    assert _sets(result) == [{"a0", "a1", "a2"}]
    assert [move.kind for move in result.moves] == ["rebuild"]


def test_stops_only_move_to_a_day_at_least_two_lighter() -> None:
    a = _group("a", 0.0, 4)
    b = _group("b", 20.0, 2)
    # a 4/1 start (a model may leave days uneven): the misplaced stop moves to the light day
    assert _sets(_run([[a[0], a[1], a[2], b[0]], [b[1]]], per_day=4)) == [{"a0", "a1", "a2"}, {"b0", "b1"}]
    # balanced 2/1 sizes are never merely permuted
    assert not _run([[a[0], a[1]], [a[2]]], per_day=2).changed


# =====================================================================================
# 4. Must-visits: mandatory, never pre-placed
# =====================================================================================


def test_two_distant_must_visits_separate_when_a_day_is_free() -> None:
    first, second = _stop("m_a", must_visit=True), _stop("m_b", 20.0, must_visit=True)
    a, b = _group("a", 0.2, 2), _group("b", 20.2, 2)

    result = _run([[first, second, a[0]], [a[1], b[0], b[1]]])

    assert _sets(result) == [{"m_a", "a0", "a1"}, {"m_b", "b0", "b1"}]


def test_two_distant_must_visits_share_the_only_day_and_nothing_is_dropped() -> None:
    first, second = _stop("m_a", must_visit=True), _stop("m_b", 20.0, must_visit=True)

    result = _run([[first, second, _stop("a0", 0.2)]], [_stop("a1", 0.4), _stop("b0", 20.2)])

    assert {"m_a", "m_b"} <= _sets(result)[0] and len(result.days[0]) == 3


def test_neighbouring_must_visits_are_not_forced_onto_one_day() -> None:
    # one compact area; the two must-visits are museums, the rest of the plan parks
    museums = [_stop("m1", 0.0, must_visit=True, cls=_MUSEUM), _stop("m2", 0.1, must_visit=True, cls=_MUSEUM)]
    parks = [_stop(f"p{index}", 0.3 + 0.1 * index, cls=_NATURE) for index in range(2)]

    result = _run([museums, parks], per_day=2, justified=frozenset())

    days = _sets(result)
    assert {"m1", "m2"} <= days[0] | days[1]
    assert not ({"m1", "m2"} <= days[0]) and not ({"m1", "m2"} <= days[1])  # split: the better plan


def test_a_must_visit_is_never_replaced_however_far_it_is() -> None:
    def composed(**flags: Any) -> composition.Composition:
        day = [_stop("kept", band=2), _stop("near", 0.3, band=2), _stop("remote", 20.0, band=3, **flags)]
        return _run([day], [_stop("local", 0.6, band=2)])

    # a strong but non-mandatory stop -- e.g. a grounded semantic anchor -- gives way
    assert _sets(composed()) == [{"kept", "near", "local"}]
    # the same place as a grounded must-visit stays
    assert not composed(must_visit=True).changed


def test_a_must_visit_the_starting_plan_left_out_is_brought_in() -> None:
    missing = _stop("m", 0.4, must_visit=True)
    # a free slot is used before anything is replaced
    with_room = _run([[_stop("a0"), _stop("a1", 0.2)]], [missing])
    assert _sets(with_room) == [{"a0", "a1", "m"}]
    assert with_room.moves[0] == composition.Move("add", composition.REASON_MUST_VISIT, incoming=("m",))

    # with every day full it takes the place of a discretionary stop: the far one
    full = _run([[_stop("a0"), _stop("a1", 0.2), _stop("far", 20.0)]], [missing])
    assert _sets(full) == [{"a0", "a1", "m"}]


def test_more_must_visits_than_the_pace_allows_never_break_the_cap() -> None:
    scheduled = [_stop(f"m{index}", 0.2 * index, must_visit=True) for index in range(3)]
    overflow = _stop("m3", 0.8, must_visit=True)

    result = _run([scheduled], [overflow])

    assert _sets(result) == [{"m0", "m1", "m2"}]  # the pace cap holds; the fourth is reported, not squeezed in
    assert not result.changed


def test_a_replacement_needs_permission_but_regrouping_does_not() -> None:
    a, b = _group("a", 0.0), _group("b", 20.0)
    start = [[a[0], a[1], b[2]], [b[0], b[1], _stop("remote", 60.0)]]

    result = _run(start, [a[2]], allow_replacement=False)

    assert all(move.kind in {"swap", "relocate"} for move in result.moves)
    assert {key for day in result.days for key in day} == {stop.key for day in start for stop in day}
    assert not _run(start, [a[2]], allow_replacement=False, allow_regrouping=False).changed


def test_the_number_of_replacements_can_be_bounded() -> None:
    start = [[_stop("a0"), _stop("x", 20.0)], [_stop("b0", 40.0), _stop("y", 60.0)]]
    unused = [_stop("a1", 0.3), _stop("b1", 40.3)]

    assert len(_run(start, unused, per_day=2).moves) == 2
    assert len(_run(start, unused, per_day=2, max_replacements=1).moves) == 1


# =====================================================================================
# 5. Hard rules
# =====================================================================================


def test_a_move_never_uncovers_a_requested_interest() -> None:
    day = [_stop("kept"), _stop("only_park", 4.0, cls=_NATURE, interests=("outdoors",))]

    result = _run([day], [_stop("local", 0.5)], per_day=2, requested_interests=frozenset({"outdoors"}))

    assert not result.changed  # the nearer place would cost the only outdoors stop


def test_covering_one_interest_never_pays_for_uncovering_another() -> None:
    day = [_stop("kept"), _stop("only_park", 0.5, cls=_NATURE, interests=("outdoors",))]
    museum = _stop("only_museum", 0.3, cls=_MUSEUM, interests=("museum",))

    result = _run([day, [_stop("x", 0.2), _stop("y", 0.4)]], [museum], per_day=2,
                  requested_interests=frozenset({"outdoors", "museum"}))

    scheduled = {key for day_keys in result.days for key in day_keys}
    assert {"only_park", "only_museum"} <= scheduled  # the museum came in for a stop that covered nothing


def test_a_stop_covering_an_exempt_interest_is_kept_but_none_is_brought_in_for_it() -> None:
    policy = {"requested_interests": frozenset({"food"}), "coverage_exempt": frozenset({"food"}), "per_day": 2}
    market = _stop("only_market", 4.0, cls=diversity.MARKETPLACE, interests=("food",))

    # the only stop serving the food interest is never taken out for a nearer place ...
    assert not _run([[_stop("kept"), market]], [_stop("local", 0.5)], **policy).changed
    # ... and an unscheduled market is never brought in just to cover it
    assert not _run([[_stop("kept"), _stop("near", 0.3)]], [market], **policy).changed


def test_an_uncovered_interest_gains_a_stop_unless_that_makes_a_day_dispersed() -> None:
    def composed(park_north: float) -> composition.Composition:
        day = [_stop("h0"), _stop("h1", 0.3), _stop("h2", 0.6)]
        park = _stop("park", park_north, cls=_NATURE, interests=("outdoors",))
        return _run([day], [park], requested_interests=frozenset({"outdoors"}))

    covered = composed(1.0)
    assert "park" in _sets(covered)[0] and covered.moves[0].reason == composition.REASON_COVERAGE
    assert not composed(30.0).changed  # the interest stays honestly uncovered


def test_places_that_may_not_enter_never_enter() -> None:
    day = [_stop("kept"), _stop("remote", 20.0)]

    for blocked in (
        _stop("low_value", 0.5, may_enter=False),
        _stop("nowhere", located=False, band=3),  # unlocated: never the answer to a geographic problem
    ):
        assert not _run([day], [blocked], per_day=2).changed


def test_two_places_of_one_conflict_group_are_never_scheduled_together() -> None:
    day = [_stop("kept", conflicts=("complex:1",)), _stop("remote", 20.0)]

    assert not _run([day], [_stop("sibling", 0.5, conflicts=("complex:1",))], per_day=2).changed
    assert _sets(_run([day], [_stop("other", 0.5, conflicts=("complex:2",))], per_day=2)) == [{"kept", "other"}]


def test_a_plan_level_class_cap_is_never_exceeded_by_a_replacement() -> None:
    day = [_stop("museum", cls=_MUSEUM), _stop("remote_park", 20.0, cls=_NATURE)]
    cap = {_MUSEUM: 1}.get

    assert not _run([day], [_stop("museum_2", 0.5, cls=_MUSEUM)], per_day=2, plan_class_cap=cap).changed
    assert _run([day], [_stop("park_2", 0.5, cls=_NATURE)], per_day=2, plan_class_cap=cap).changed


def test_a_replacement_never_adds_a_market_beyond_the_hard_cap() -> None:
    market = diversity.MARKETPLACE
    day = [_stop("market", cls=market), _stop("remote", 20.0)]

    assert not _run([day], [_stop("market_2", 0.5, cls=market)], per_day=2).changed
    assert _run([day], [_stop("market_2", 0.5, cls=market)], per_day=2, markets_requested=True).changed


# =====================================================================================
# 6. Bounds, determinism and safe fallbacks
# =====================================================================================


def test_composition_declines_what_it_cannot_identify_or_bound() -> None:
    duplicate = _run([[_stop("same"), _stop("other", 0.2)]], [_stop("same", 0.4)], per_day=2)
    assert duplicate.declined == composition.DECLINED_DUPLICATE_IDENTITY
    assert duplicate.days == [["same", "other"]] and not duplicate.changed

    # more scheduled stops -- must-visits among them -- than the working-set bound: nothing is dropped
    too_many = [
        [_stop(f"m{day}_{index}", 0.01 * index, must_visit=True) for index in range(11)]
        for day in range(11)
    ]
    assert sum(map(len, too_many)) > composition.MAX_WORKING_SET
    over = compose(too_many, [], _policy(per_day=11))
    assert over.declined == composition.DECLINED_OVER_BOUND
    assert over.days == [[stop.key for stop in day] for day in too_many]


def _bounded_scene() -> tuple[list[list[Stop]], list[Stop]]:
    """One must-visit day and far more unused candidates than the bound: a
    crowd of strong ones in a distant group, and a few weak ones that matter."""
    scheduled = [[_stop("m", must_visit=True), _stop("s1", 0.2, band=3), _stop("s2", 0.4, band=3)]]
    crowd = [_stop(f"crowd{index:03d}", 50.0 + 0.001 * index, band=3, tier=4) for index in range(200)]
    weak = [
        _stop("companion", 0.6, band=0),  # near the must-visit
        _stop("only_park", 25.0, band=0, cls=_NATURE, interests=("outdoors",)),  # the only outdoors supply
        _stop("third_group", 90.0, band=0),  # a geographic group of its own
    ]
    return scheduled, [*crowd, *weak]


def test_the_working_set_is_not_simply_the_best_ranked_candidates() -> None:
    scheduled, unused = _bounded_scene()
    policy = _policy(requested_interests=frozenset({"outdoors"}))

    working = composition._working_set(scheduled, unused, policy)
    keys = {stop.key for stop in working}

    assert len(working) + 3 == composition.MAX_WORKING_SET  # the bound is hard
    assert "only_park" in keys  # requested-interest coverage
    assert "companion" in keys  # a compatible alternative beside the mandatory place
    assert "third_group" in keys  # an alternative from every geographic group
    assert sum(1 for key in keys if key.startswith("crowd")) >= composition.MAX_WORKING_SET // 2  # usefulness fills the rest


def test_the_working_set_and_the_result_ignore_provider_order() -> None:
    scheduled, unused = _bounded_scene()
    policy = _policy(requested_interests=frozenset({"outdoors"}))
    expected_set = composition._working_set(scheduled, unused, policy)
    expected = compose(scheduled, unused, policy)

    for seed in range(4):
        shuffled = list(unused)
        random.Random(seed).shuffle(shuffled)
        assert composition._working_set(scheduled, shuffled, policy) == expected_set
        assert compose(scheduled, shuffled, policy) == expected


def test_a_must_visit_left_out_is_within_reach_whatever_the_bound() -> None:
    scheduled = [[_stop("s0", band=3), _stop("s1", 0.2, band=3), _stop("s2", 0.4, band=3)]]
    crowd = [_stop(f"crowd{index:03d}", 0.6 + 0.001 * index, band=3, tier=4) for index in range(300)]

    result = compose(scheduled, [*crowd, _stop("m", 0.5, band=0, must_visit=True)], _policy())

    assert "m" in _sets(result)[0]


def _largest_plan() -> tuple[list[list[Stop]], list[Stop]]:
    """The largest plan the composition supports in practice: 14 packed days
    (56 stops) drawn at random from 300 candidates of 12 scattered groups."""
    generator = random.Random(7)
    centres = [(generator.uniform(0, 40), generator.uniform(0, 40)) for _ in range(12)]
    classes = (_HISTORY, _MUSEUM, _NATURE)
    stops = [
        _stop(
            f"s{index:03d}",
            centres[index % 12][0] + generator.uniform(-1.0, 1.0),
            centres[index % 12][1] + generator.uniform(-1.0, 1.0),
            band=generator.randrange(4),
            tier=generator.choice((2, 3, 4)),
            cls=classes[index % 3],
            must_visit=index < 6,
        )
        for index in range(300)
    ]
    generator.shuffle(stops)
    scheduled = sorted(stops, key=lambda stop: not stop.must_visit)[:56]
    scheduled_keys = {stop.key for stop in scheduled}
    generator.shuffle(scheduled)
    return [scheduled[day * 4 : day * 4 + 4] for day in range(14)], [s for s in stops if s.key not in scheduled_keys]


def _assert_valid(result: composition.Composition, days: list[list[Stop]], unused: list[Stop], per_day: int) -> None:
    by_key = {stop.key: stop for stop in (*(stop for day in days for stop in day), *unused)}
    scheduled = [key for day in result.days for key in day]
    assert len(scheduled) == len(set(scheduled)) == sum(map(len, days))  # one place once, no stop lost
    assert all(len(day) <= per_day for day in result.days)
    assert {stop.key for day in days for stop in day if stop.must_visit} <= set(scheduled)
    assert all(key in by_key for key in scheduled)


def test_the_largest_supported_plan_stays_within_the_evaluation_budget() -> None:
    days, unused = _largest_plan()
    policy = _policy(per_day=4, justified=frozenset())

    started = time.perf_counter()
    result = compose(days, unused, policy)
    elapsed = time.perf_counter() - started

    assert result.evaluations <= composition.MAX_EVALUATIONS
    assert result.working_set_size <= composition.MAX_WORKING_SET
    _assert_valid(result, days, unused, 4)
    assert result.changed
    # generous: the budget, not the clock, is the guarantee (about 0.8 s on a laptop)
    assert elapsed < 30.0


def test_running_out_of_budget_returns_the_best_valid_plan_found_so_far() -> None:
    days, unused = _largest_plan()
    full = compose(days, unused, _policy(per_day=4, justified=frozenset()))

    for budget in (0, 1, 50, 1500):
        policy = _policy(per_day=4, justified=frozenset(), max_evaluations=budget)
        result = compose(days, unused, policy)
        assert result.budget_exhausted and result.evaluations <= budget
        _assert_valid(result, days, unused, 4)
        assert compose(days, unused, policy) == result  # early stopping is deterministic
        assert len(result.moves) <= len(full.moves)


def test_the_search_makes_at_most_one_move_per_scheduled_stop() -> None:
    days, unused = _largest_plan()

    result = compose(days, unused, _policy(per_day=4, justified=frozenset()))

    discretionary = [move for move in result.moves if move.reason != composition.REASON_MUST_VISIT]
    assert len(discretionary) <= sum(map(len, days))


# =====================================================================================
# 7. Through the planner, the reasoning request and the prompts
# =====================================================================================

_KINDS = {
    "museum": ("museum", {"tourism": "museum"}),
    "park": ("park", {"leisure": "park"}),
    "castle": ("castle", {"historic": "castle"}),
}


def _place(key: str, north: float, east: float = 0.0, kind: str = "museum", name: str | None = None, **tags: str) -> dict[str, Any]:
    category, base_tags = _KINDS[kind]
    return _poi(
        key, name or f"{kind.title()} {key}", _at(north, east), category=category, provider_tags={**base_tags, **tags}
    )


def _core(count: int = 12, north: float = 0.0, prefix: str = "c", kind: str = "museum") -> list[dict[str, Any]]:
    """`count` places of one compact group (a 200 m grid)."""
    return [_place(f"{prefix}{index}", north + 0.2 * (index // 4), 0.2 * (index % 4), kind) for index in range(count)]


@pytest.fixture
def setting(monkeypatch: pytest.MonkeyPatch) -> Any:
    def set_value(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield set_value
    get_settings.cache_clear()


def _plan(state: PlanningState) -> list[list[str]]:
    ExperiencePlannerService().run(state)
    return _scheduled(state)


def _bands(state: PlanningState) -> list[str]:
    return [
        composition.geographic_band(day_extent_km(points), day_spread_km(points))
        for points in ([stop.coordinates for stop in day.experiences] for day in state.experience_plan.daily_plans)
    ]


def _outlying_pool() -> list[dict[str, Any]]:
    """A compact core and three documented places about 6-9 km out, each alone."""
    return [
        *_core(),
        _place("s1", 6.0, wikipedia="en:S1"),
        _place("s2", -6.0, wikipedia="en:S2"),
        _place("s3", 0.0, 9.0, wikipedia="en:S3"),
    ]


def test_the_deterministic_fallback_composes_compact_days(setting: Any) -> None:
    setting("DAY_COMPOSITION_ENABLED", "false")
    before = _state(_outlying_pool(), interests=["museum"])
    days_before = _plan(before)
    assert before.ai_itinerary_reasoning_result is None  # no model involved at all
    assert _bands(before).count(composition.EXTENDED) == 2  # two days stretch out to a lone documented place

    setting("DAY_COMPOSITION_ENABLED", "true")
    after = _state(_outlying_pool(), interests=["museum"])
    days_after = _plan(after)

    assert _bands(after) == [composition.COMPACT] * 3
    assert [len(day) for day in days_after] == [len(day) for day in days_before] == [3, 3, 3]  # never fewer stops
    assert not {"Museum s1", "Museum s2"} & {name for day in days_after for name in day}


def test_a_grounded_must_visit_stays_where_a_documented_place_gives_way(setting: Any) -> None:
    pois = _outlying_pool()
    must_visit = next(poi for poi in pois if poi["name"] == "Museum s1")

    days = _plan(_state(pois, interests=["museum"], must_visit={"the far museum": must_visit}))

    scheduled = {name for day in days for name in day}
    assert "Museum s1" in scheduled and "Museum s2" not in scheduled


def test_the_only_stop_serving_a_requested_interest_is_never_composed_away(setting: Any) -> None:
    market = _poi(
        "mk", "Far Market", _at(3.5), category="marketplace",
        provider_tags={"amenity": "marketplace", "shop": "greengrocer"},  # a market with the provider's food evidence
    )

    days = _plan(_state([*_core(8), market], days=2, pace=TripPace.RELAXED, interests=["food"]))

    assert "Far Market" in {name for day in days for name in day}


def test_a_grounded_anchor_is_a_preference_and_may_give_way(setting: Any) -> None:
    pois = _outlying_pool()
    anchors = [poi for poi in pois if poi["name"] in {"Museum s1", "Museum s2"}]

    state = _state(pois, interests=["museum"], anchors=anchors)
    days = _plan(state)

    # each is a grounded semantic anchor AND documented (three evidences against the core's one): two
    # evidence bands is more usefulness than a geographic improvement may cost, so both stay ...
    assert {"Museum s1", "Museum s2"} <= {name for day in days for name in day}

    # ... while an anchor with one evidence more than its neighbours is not immovable
    plain = [*_core(), _place("s1", 6.0), _place("s2", -6.0)]
    relaxed = _plan(_state(plain, interests=["museum"], anchors=[poi for poi in plain if poi["name"] in {"Museum s1", "Museum s2"}]))
    assert not {"Museum s1", "Museum s2"} & {name for day in relaxed for name in day}


def test_the_plan_does_not_depend_on_provider_order(setting: Any) -> None:
    expected = _plan(_state(_outlying_pool(), interests=["museum"]))
    for seed in range(4):
        pois = _outlying_pool()
        random.Random(seed).shuffle(pois)
        assert _plan(_state(pois, interests=["museum"])) == expected


def test_two_places_with_one_name_stay_two_places(setting: Any) -> None:
    pois = [*_core(8), *_core(4, north=20.0, prefix="n")]
    pois[0]["name"] = pois[8]["name"] = "Old Gate"  # one in each group, each under its own provider id

    # composition identifies a candidate by its provider id alone: a shared name is no obstacle and
    # conflates nothing (whether BOTH are eligible is the candidate-quality stage's decision, not Q3's)
    assert planner_module._composable(pois, 9)
    assert planner_module._composition_key(pois[0]) != planner_module._composition_key(pois[8])
    areas = build_areas(
        [Located(planner_module._composition_key(poi), planner_module._poi_coordinates(poi)) for poi in pois]
    )
    assert areas.area_of["geoapify/c0"] != areas.area_of["geoapify/n0"]

    state = _state(pois, interests=["museum"])
    _plan(state)
    stops = [stop for day in state.experience_plan.daily_plans for stop in day.experiences]
    assert len({stop.provider_place_id for stop in stops}) == len(stops) == 9  # one identity, one slot


def test_two_stops_that_differ_only_in_identity_are_composed_independently() -> None:
    # the module never sees a name: two same-named places are two keys, in two groups
    start = [[_stop("gate/1"), _stop("a1", 0.2), _stop("gate/2", 20.0)], [_stop("b1", 20.2), _stop("b2", 20.4), _stop("a2", 0.4)]]

    assert _sets(_run(start)) == [{"gate/1", "a1", "a2"}, {"gate/2", "b1", "b2"}]


def test_thin_inventory_is_still_fully_used(setting: Any) -> None:
    def scheduled(flag: str) -> list[str]:
        setting("DAY_COMPOSITION_ENABLED", flag)
        pois = [*_core(4), _place("far1", 15.0), _place("far2", 30.0)]
        return sorted(name for day in _plan(_state(pois, interests=["museum"])) for name in day)

    assert scheduled("true") == scheduled("false")
    assert len(scheduled("true")) == 6  # every viable place, none dropped for being far


def test_a_pool_composition_cannot_identify_is_planned_exactly_as_before(setting: Any) -> None:
    def pool() -> list[dict[str, Any]]:
        pois = _outlying_pool()
        pois[1]["place_id"] = pois[0]["place_id"]  # two records sharing one provider id
        return pois

    assert not planner_module._composable(pool(), 9)
    composed = _plan(_state(pool(), interests=["museum"]))
    setting("DAY_COMPOSITION_ENABLED", "false")
    assert composed == _plan(_state(pool(), interests=["museum"]))


# -- a plan the reasoning model chose ---------------------------------------------------------------


def _ai_state(
    pois: list[dict[str, Any]],
    model_days: list[list[str]],
    *,
    viable: int | None = 12,
    lock: bool = False,
    **state: Any,
) -> PlanningState:
    planning_state = _state(pois, days=len(model_days), interests=["museum"], **state)
    by_key = {poi["place_id"].split("/", 1)[1]: poi for poi in pois}
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [
            _day(index + 1, [f"geoapify_places:{by_key[key]['place_id']}" for key in day])
            for index, day in enumerate(model_days)
        ]
    )
    if viable is not None:
        planning_state.inventory_sufficiency_report = InventorySufficiencyReport(
            status=InventorySufficiencyStatus.HEALTHY, trip_days=len(model_days), pace="balanced",
            target_stops=3 * len(model_days), minimum_useful=5, healthy_buffer=14, viable_candidates=viable,
            message="fixture",
        )
    if lock:
        planning_state.user_locks = [UserLock(locked_item_type="experience", locked_item_id="anything")]
    return planning_state


def _two_groups() -> list[dict[str, Any]]:
    return [*_core(8), *_core(4, north=20.0, prefix="n")]


def test_a_model_plan_keeps_its_places_and_is_only_regrouped(setting: Any) -> None:
    state = _ai_state(_two_groups(), [["c0", "c1", "n0"], ["n1", "n2", "c2"]])

    days = _plan(state)

    assert {name for day in days for name in day} == {f"Museum {key}" for key in ("c0", "c1", "c2", "n0", "n1", "n2")}
    assert _bands(state) == [composition.COMPACT, composition.COMPACT]


def test_a_model_plan_trades_a_discretionary_stop_for_a_material_improvement(setting: Any) -> None:
    def scheduled(model_day: list[str], **state: Any) -> set[str]:
        pois = [*_core(8), _place("out", 5.0), _place("remote", 40.0)]
        return {name for day in _plan(_ai_state(pois, [model_day, ["c4", "c5", "c6"]], **state)) for name in day}

    # extended -> compact: the stop 5 km out gives way to a core place of the same usefulness
    assert "Museum out" not in scheduled(["c0", "c1", "out"])
    # a dispersed day is repaired the same way
    assert "Museum remote" not in scheduled(["c0", "c1", "remote"])
    # ... but not without sufficient verified inventory, and never under an active lock
    assert "Museum out" in scheduled(["c0", "c1", "out"], viable=None)
    assert "Museum out" in scheduled(["c0", "c1", "out"], viable=5)
    assert "Museum remote" in scheduled(["c0", "c1", "remote"], lock=True)


def test_in_a_model_plan_only_a_grounded_must_visit_is_immovable(setting: Any) -> None:
    def scheduled(**state_for: Any) -> set[str]:
        pois = [*_core(8), _place("remote", 40.0)]
        remote = pois[-1]
        state = {name: build(remote) for name, build in state_for.items()}
        return {name for day in _plan(_ai_state(pois, [["c0", "c1", "remote"], ["c4", "c5", "c6"]], **state)) for name in day}

    assert "Museum remote" not in scheduled(anchors=lambda remote: [remote])  # a grounded anchor: a preference
    assert "Museum remote" in scheduled(must_visit=lambda remote: {"the remote museum": remote})

    # before Q3 the grounded anchor was protected by the spread pass
    setting("DAY_COMPOSITION_ENABLED", "false")
    assert "Museum remote" in scheduled(anchors=lambda remote: [remote])


def test_a_must_visit_the_model_left_out_is_scheduled(setting: Any) -> None:
    pois = _core(8)
    state = _ai_state(pois, [["c1", "c2", "c3"], ["c4", "c5", "c6"]], must_visit={"the first museum": pois[0]})

    days = _plan(state)

    assert "Museum c0" in {name for day in days for name in day}
    assert [len(day) for day in days] == [3, 3]  # the pace cap holds: it took a discretionary stop's place


def test_the_regrouping_setting_still_governs_a_model_plan(setting: Any) -> None:
    setting("AI_DAY_SPATIAL_REGROUPING_ENABLED", "false")

    days = _plan(_ai_state(_two_groups(), [["c0", "c1", "n0"], ["n1", "n2", "c2"]], viable=None))

    assert days == [["Museum c0", "Museum c1", "Museum n0"], ["Museum n1", "Museum n2", "Museum c2"]]


# -- the bounded reasoning request -----------------------------------------------------------------


def _request(state: PlanningState) -> Any:
    return AIItineraryReasoningRequestBuilder().build_request(state)


def test_the_request_carries_opaque_areas_derived_from_coordinates(setting: Any) -> None:
    pois = [*_core(6), *_core(4, north=2.0, prefix="m"), *_core(3, north=30.0, prefix="n")]
    state = _state(pois, interests=["museum"], restaurants=[_restaurant("r1", _at(0.1))])

    request = _request(state)

    attractions = [c for c in request.allowed_candidates if c.category == ItineraryReasoningCategory.ATTRACTION]
    restaurants = [c for c in request.allowed_candidates if c.category == ItineraryReasoningCategory.RESTAURANT]
    assert all(candidate.area for candidate in attractions) and all(c.area is None for c in restaurants)
    by_name = {candidate.name: candidate.area for candidate in attractions}
    assert by_name["Museum c0"] == by_name["Museum c5"] != by_name["Museum n0"]

    summaries = {summary.area_id: summary for summary in request.areas}
    assert set(summaries) == set(by_name.values())
    assert sum(summary.candidate_count for summary in request.areas) == len(attractions)
    assert all(area_id.startswith("a") and area_id[1:].isdigit() for area_id in summaries)  # ids, never names
    # the two groups 2 km apart are near each other; the one 30 km away is near nothing
    assert by_name["Museum m0"] in summaries[by_name["Museum c0"]].near_area_ids or by_name["Museum m0"] == by_name["Museum c0"]
    assert summaries[by_name["Museum n0"]].near_area_ids == []
    assert request.reasoning_instructions == [*DEFAULT_ITINERARY_REASONING_INSTRUCTIONS, AREA_INSTRUCTION]


def _bound_scene() -> tuple[PlanningState, dict[str, Any]]:
    """A must-visit in a distant group whose neighbours are weak, a strong
    compact core, and a third group of weak places."""
    beside = _core(4, north=20.0, prefix="x", kind="park")  # beside the must-visit; do not serve the interest
    must_visit = _place("mv", 20.4, 0.4)
    third = _core(3, north=45.0, prefix="t", kind="park")
    state = _state(
        [*_core(20), *beside, must_visit, *third], interests=["museum"], must_visit={"the far museum": must_visit}
    )
    return state, must_visit


def test_step_e_keeps_companions_and_alternatives_without_giving_one_area_the_bound(setting: Any) -> None:
    setting("AI_ITINERARY_REASONING_MAX_CANDIDATES", "12")
    state, _ = _bound_scene()

    names = [candidate.name for candidate in _request(state).allowed_candidates]

    assert len(names) == 12 and "Museum mv" in names  # the cap is hard; A (the must-visit) is kept
    assert sum(1 for name in names if name.startswith("Park x")) == 2  # companions: a day's worth with it
    assert sum(1 for name in names if name.startswith("Park t")) == 1  # an alternative from the third group
    assert sum(1 for name in names if name.startswith("Museum c")) == 8  # usefulness still fills most of it

    setting("DAY_COMPOSITION_ENABLED", "false")
    legacy = [candidate.name for candidate in _request(state).allowed_candidates]
    assert sum(1 for name in legacy if name.startswith("Park")) == 0  # before Q3: the must-visit stood alone
    assert sum(1 for name in legacy if name.startswith("Museum c")) == 11


def test_a_compact_destination_is_bounded_exactly_as_before(setting: Any) -> None:
    setting("AI_ITINERARY_REASONING_MAX_CANDIDATES", "10")
    state = _state([*_core(16), *_core(8, north=0.8, prefix="p", kind="park")], interests=["museum", "outdoors"])
    composed = [candidate.candidate_id for candidate in _request(state).allowed_candidates]
    assert len({candidate.area for candidate in _request(state).allowed_candidates}) == 1

    setting("DAY_COMPOSITION_ENABLED", "false")
    assert [candidate.candidate_id for candidate in _request(state).allowed_candidates] == composed


@pytest.mark.parametrize("adapter", [groq_adapter, anthropic_adapter])
def test_every_prompt_prints_areas_and_no_coordinate_or_distance(setting: Any, adapter: Any) -> None:
    pois = [*_core(6), *_core(3, north=30.0, prefix="n")]
    request = _request(_state(pois, interests=["museum"]))

    prompt = adapter._build_prompt(request)

    assert " area=a1" in prompt and "- area=a1 candidates=" in prompt and "near=[" in prompt
    assert AREA_INSTRUCTION in prompt
    for forbidden in ("50.0", "10.0", "lat", "lng", " km", "kilomet", "minutes", "metres", "meters"):
        assert forbidden not in prompt, forbidden


@pytest.mark.parametrize("adapter", [groq_adapter, anthropic_adapter])
def test_with_composition_off_the_request_and_the_prompt_are_what_they_were(setting: Any, adapter: Any) -> None:
    setting("DAY_COMPOSITION_ENABLED", "false")
    request = _request(_state([*_core(6), *_core(3, north=30.0, prefix="n")], interests=["museum"]))

    assert request.areas == [] and all(candidate.area is None for candidate in request.allowed_candidates)
    assert request.reasoning_instructions == list(DEFAULT_ITINERARY_REASONING_INSTRUCTIONS)
    prompt = adapter._build_prompt(request)
    assert "area" not in prompt.lower().replace("areas of", "")
    # the candidate line is exactly the pre-Q3 line
    candidate = request.allowed_candidates[0]
    assert adapter._format_candidate_line(candidate, "c1") == (
        f"- candidate_id='c1' name={candidate.name!r} category=attraction quality_tier={candidate.quality_tier} "
        f"quality_score={candidate.quality_score:.2f} normalized_category={candidate.normalized_category} "
        f"matched_interests={list(candidate.matched_interests)} must_visit=no semantic_anchor=no provider_evidence=[]"
    )


def test_an_area_id_never_reaches_the_traveller_in_model_prose() -> None:
    refs = CandidateRefMap(["id-1"], names={"id-1": "Museum One"}, areas=["a1", "a2"])

    assert refs.scrub_prose("Day 1 stays in area a1 near the river.") == "Day 1 stays in one area near the river."
    assert refs.scrub_prose("Links areas a1 and a2 on foot.") == "Links nearby areas on foot."
    assert refs.scrub_prose("A compact morning (a2) around c1.") == "A compact morning around Museum One."
    assert refs.scrub_prose("Area A2 is the focus.") == "one area is the focus."
    # an id this request does not have, and ordinary words, are left exactly as written
    assert refs.scrub_prose("The a9 road and a quiet area.") == "The a9 road and a quiet area."
    assert CandidateRefMap(["id-1"]).scrub_prose("Stays in area a1.") == "Stays in area a1."


def test_a_repair_request_explains_the_areas_its_candidate_lines_carry(setting: Any) -> None:
    from app.models.ai_itinerary_repair import DEFAULT_ITINERARY_REPAIR_INSTRUCTIONS, AIItineraryRepairRequest
    from app.models.ai_itinerary_repair import AIItineraryRepairIssue, RepairableIssueType
    from app.models.common import ValidationSeverity

    request = _request(_state([*_core(6), *_core(3, north=30.0, prefix="n")], interests=["museum"]))
    first = request.allowed_candidates[0].candidate_id
    repair = AIItineraryRepairRequest(
        trip_id=request.trip_id, destination_name=request.destination_name, start_date=request.start_date,
        end_date=request.end_date, trip_duration_days=request.trip_duration_days,
        traveler_context=request.traveler_context, allowed_candidates=request.allowed_candidates,
        areas=request.areas, repair_instructions=[*DEFAULT_ITINERARY_REPAIR_INSTRUCTIONS, AREA_INSTRUCTION],
        original_days=[_day(1, [first])], affected_days=[1],
        issues=[
            AIItineraryRepairIssue(
                issue_type=RepairableIssueType.GEOGRAPHIC_SPREAD, day_index=1, severity=ValidationSeverity.WARNING,
                message="fixture", source_category="geographic_spread",
            )
        ],
    )

    for adapter in (groq_adapter, anthropic_adapter):
        prompt = adapter._build_repair_prompt(repair)
        assert AREA_INSTRUCTION in prompt and "- area=a1 candidates=" in prompt


# -- rollback, metrics, purity ----------------------------------------------------------------------


def test_with_composition_off_the_planner_never_reaches_the_module(setting: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    setting("DAY_COMPOSITION_ENABLED", "false")

    def unreachable(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("day composition must not run when it is switched off")

    monkeypatch.setattr(composition, "compose", unreachable)
    monkeypatch.setattr(composition, "build_areas", unreachable)
    regrouped: list[int] = []
    limited: list[int] = []
    regroup, limit = planner_module._spatially_regroup_days, planner_module._limit_ai_day_spread
    monkeypatch.setattr(planner_module, "_spatially_regroup_days", lambda *a, **k: regrouped.append(1) or regroup(*a, **k))
    monkeypatch.setattr(planner_module, "_limit_ai_day_spread", lambda *a, **k: limited.append(1) or limit(*a, **k))

    _plan(_state(_outlying_pool(), interests=["museum"]))
    state = _ai_state(_two_groups(), [["c0", "c1", "n0"], ["n1", "n2", "c2"]])
    _plan(state)
    _request(state)

    assert regrouped == [1] and limited == [1]  # the two pre-Q3 passes run, once, for the model's plan


def test_with_composition_on_it_replaces_the_two_passes(setting: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("replaced by day composition")

    monkeypatch.setattr(planner_module, "_spatially_regroup_days", unreachable)
    monkeypatch.setattr(planner_module, "_limit_ai_day_spread", unreachable)

    _plan(_ai_state(_two_groups(), [["c0", "c1", "n0"], ["n1", "n2", "c2"]]))


def test_an_unexpected_composition_error_never_fails_a_plan(setting: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("fixture")

    monkeypatch.setattr(composition, "compose", broken)

    days = _plan(_state(_outlying_pool(), interests=["museum"]))

    assert [len(day) for day in days] == [3, 3, 3]


def test_metrics_report_day_geography_as_proxies_only(setting: Any) -> None:
    metrics_module = _quality_metrics()
    state = _state(_outlying_pool(), interests=["museum"])
    ExperiencePlannerService().run(state)

    metrics = metrics_module.extract_quality_metrics(state)
    geography = metrics["day_geography"]

    assert metrics["schema_version"] == 5  # additive since 4: dispersion causes, food evidence
    assert geography["days_by_band"] == {composition.COMPACT: 3}
    assert geography["days_within_one_area"] == 3 and geography["area_revisits_in_stop_order"] == 0
    assert geography["days_with_a_compact_alternative_of_equal_usefulness"] == []
    assert geography["near_proxy_km"] == 3.0 and geography["materiality_tolerance_km"] == 1.0
    assert "never a route length" in geography["measure"]
    assert "repeated_crossing_between_distant_clusters" not in metrics["future_metrics_not_computed"]
    assert "recognisable_day_purpose" in metrics["future_metrics_not_computed"]  # no locality is stored
    flat = repr(geography).lower()
    for unsupported in ("minute", "seconds", "walk", "duration_", "drive"):
        assert unsupported not in flat.replace("walking distance", "").replace("travel duration", ""), unsupported

    setting("DAY_COMPOSITION_ENABLED", "false")
    legacy = _state(_outlying_pool(), interests=["museum"])
    ExperiencePlannerService().run(legacy)
    before = metrics_module.extract_quality_metrics(legacy)["day_geography"]
    assert before["days_by_band"] == {composition.COMPACT: 1, composition.EXTENDED: 2}
    assert len(before["days_with_a_compact_alternative_of_equal_usefulness"]) == 0  # the outliers had MORE evidence


def test_the_module_is_pure_and_names_no_destination() -> None:
    source = Path(inspect.getfile(composition)).read_text()
    code = source.split('"""', 2)[2]
    for forbidden in ("gateway", "httpx", "requests", "get_route", "routing_for", "invoke(", "get_settings", "planning_state"):
        assert forbidden not in code, forbidden
    # the Q4 services are not touched by composition
    for module in ("route_feasibility", "route_aware_sequencing", "route_burden", "routability_repair"):
        assert module not in code, module
