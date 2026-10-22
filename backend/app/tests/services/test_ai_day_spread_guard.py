from __future__ import annotations

import inspect
import socket
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.models.common import GeoPoint
from app.models.inventory_sufficiency import InventorySufficiencyReport, InventorySufficiencyStatus
from app.models.planning_state import DailyPlan, ExperienceItem, PlanningState, TripPace, UserLock
from app.services import day_order_heuristics, place_taxonomy as taxonomy
from app.services import experience_planner_service as planner_module
from app.services import plan_validator_service as validator_module
from app.services.day_order_heuristics import GEOGRAPHIC_SPREAD_THRESHOLD_KM, day_spread_km
from app.services.entity_collisions import SUSPECT_COLLISION_KEY
from app.services.experience_planner_service import (
    ExperiencePlannerService,
    _CandidateProfile,
    _limit_ai_day_spread,
    _order_day_by_distance,
    _poi_coordinates,
)
from app.tests.services.test_experience_planner_ai_guided import (
    _candidate_id,
    _completed_result,
    _day,
    _place,
    _planning_state,
    _scheduled_names_by_day,
)

# The prospective geographic pass over days the reasoning model chose: an
# ordinary broad-pool stop that would put its day beyond the validator's own
# geographic-spread boundary gives way to a comparable unused local
# candidate, when one exists. Synthetic places on a coordinate grid (0.01
# degree of latitude is about 1.1 km); nothing is city-specific.

_LAT, _LNG = 50.0, 10.0
_REMOTE = 0.5  # degrees of latitude: about 55 km away


def _poi(key: str, north: float = 0.0, east: float = 0.0, **extra: Any) -> dict[str, Any]:
    return {
        "place_id": f"geoapify/{key}", "name": key,
        "coordinates": {"lat": _LAT + north, "lng": _LNG + east}, **extra,
    }


def _profile(
    tier_rank: int = 4,
    score: float = 0.8,
    primary: str = taxonomy.MUSEUM,
    *,
    low_value: bool = False,
    gallery: bool = False,
    interests: tuple[str, ...] = (),
) -> _CandidateProfile:
    return _CandidateProfile(
        score=score, tier_rank=tier_rank, primary=primary, categories=frozenset({primary}),
        low_value=low_value, commercial_gallery=gallery, sub_feature_cluster=None,
        matched_interests=list(interests), tier=None,
    )


class _Scene:
    """A pool, its profiles and the protected stops, for one call of the pass."""

    def __init__(self) -> None:
        self.pool: list[dict[str, Any]] = []
        self.profiles: dict[int, _CandidateProfile] = {}
        self.must_visit_ids: set[int] = set()
        self.anchor_ids: set[int] = set()

    def add(self, key: str, north: float = 0.0, east: float = 0.0, *, anchor: bool = False,
            must_visit: bool = False, poi_extra: dict[str, Any] | None = None, **profile: Any) -> dict[str, Any]:
        poi = _poi(key, north, east, **(poi_extra or {}))
        self.pool.append(poi)
        self.profiles[id(poi)] = _profile(**profile)
        if anchor:
            self.anchor_ids.add(id(poi))
        if must_visit:
            self.must_visit_ids.add(id(poi))
        return poi

    def run(self, days: list[list[dict[str, Any]]], interests: tuple[str, ...] = (), **kwargs: Any) -> list[list[str]]:
        result = _limit_ai_day_spread(
            days, self.pool, self.profiles, self.must_visit_ids, self.anchor_ids, list(interests),
            markets_requested=kwargs.get("markets_requested", False), justified=kwargs.get("justified", frozenset()),
        )
        return [[poi["name"] for poi in day] for day in result]


def _spread(scene_days: list[list[dict[str, Any]]]) -> list[float]:
    return [day_spread_km([_poi_coordinates(poi) for poi in _order_day_by_distance(day)]) or 0.0 for day in scene_days]


@pytest.fixture()
def scene() -> _Scene:
    return _Scene()


# -- the Melbourne-shaped day ------------------------------------------------------------------


def test_remote_broad_stops_beside_an_anchor_give_way_to_local_candidates(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote_a = scene.add("Remote A", north=_REMOTE)
    remote_b = scene.add("Remote B", north=_REMOTE + 0.05)
    scene.add("Local far", north=0.05)  # 5.5 km: acceptable, but not the nearest
    scene.add("Local near", north=0.01)
    scene.add("Local mid", north=0.02)
    day = [anchor, remote_a, remote_b]
    assert _spread([day])[0] > GEOGRAPHIC_SPREAD_THRESHOLD_KM

    result = scene.run([day])

    # the anchor stays where it was; each remote stop is replaced in place by the nearest candidate
    assert result == [["Anchor", "Local near", "Local mid"]]
    assert day == [anchor, remote_a, remote_b]  # the input grouping is not mutated


def test_the_result_is_within_the_validators_boundary(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote", north=_REMOTE)
    scene.add("Local", north=0.02)
    names = scene.run([[anchor, remote]])
    by_name = {poi["name"]: poi for poi in scene.pool}
    assert _spread([[by_name[name] for name in names[0]]])[0] <= GEOGRAPHIC_SPREAD_THRESHOLD_KM


def test_a_candidate_is_only_acceptable_if_the_day_stays_within_the_boundary(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote", north=_REMOTE)
    scene.add("Also too far", north=0.2)  # 22 km: closer than the stop, still beyond the boundary
    assert scene.run([[anchor, remote]]) == [["Anchor", "Remote"]]  # no acceptable alternative: it stays


def test_a_remote_stop_is_kept_when_no_alternative_exists(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote", north=_REMOTE)
    assert scene.run([[anchor, remote]]) == [["Anchor", "Remote"]]


def test_a_local_stop_after_a_kept_remote_one_is_still_kept(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote", north=_REMOTE, score=0.9)
    local = scene.add("Local", north=0.01, score=0.5)
    assert scene.run([[anchor, remote, local]]) == [["Anchor", "Remote", "Local"]]


# -- requested-interest coverage -----------------------------------------------------------------


def test_the_replacement_must_itself_cover_the_interest_the_remote_stop_alone_covered(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote history", north=_REMOTE, interests=("history",))
    scene.add("Nearest, no history", north=0.01)
    scene.add("Local history", north=0.03, interests=("history",))

    # the remote stop is NOT retained for being the sole cover: a local stop that covers it takes its place
    assert scene.run([[anchor, remote]], interests=("history",)) == [["Anchor", "Local history"]]


def test_with_no_local_candidate_covering_the_interest_the_remote_stop_stays(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote history", north=_REMOTE, interests=("history",))
    scene.add("Nearest, no history", north=0.01)
    assert scene.run([[anchor, remote]], interests=("history",)) == [["Anchor", "Remote history"]]


def test_coverage_held_by_another_scheduled_stop_frees_the_replacement(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True, interests=("history",))
    remote = scene.add("Remote history", north=_REMOTE, interests=("history",))
    scene.add("Nearest, no history", north=0.01)
    assert scene.run([[anchor, remote]], interests=("history",)) == [["Anchor", "Nearest, no history"]]

    # another DAY counts too: coverage is the plan's
    other_day = [scene.add("Elsewhere history", north=0.02, east=0.3, interests=("history",))]
    scene.profiles[id(anchor)] = _profile()
    assert scene.run([[anchor, remote], other_day], interests=("history",))[0] == ["Anchor", "Nearest, no history"]


def test_an_interest_the_traveller_did_not_request_is_not_a_constraint(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote", north=_REMOTE, interests=("nightlife",))
    scene.add("Local", north=0.01)
    assert scene.run([[anchor, remote]], interests=("history",)) == [["Anchor", "Local"]]


def test_food_is_covered_by_nearby_food_and_never_keeps_a_remote_market(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote market", north=_REMOTE, primary=taxonomy.FOOD_MARKET, interests=("food",))
    scene.add("Local", north=0.01, primary=taxonomy.PARK_NATURE)  # (a second museum would concentrate the day)
    assert scene.run([[anchor, remote]], interests=("food",)) == [["Anchor", "Local"]]


# -- the other constraints -----------------------------------------------------------------------


def test_the_quality_tier_floor_is_one_tier_below_the_stop(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote", north=_REMOTE, tier_rank=4)
    scene.add("Two tiers below", north=0.01, tier_rank=2)
    assert scene.run([[anchor, remote]]) == [["Anchor", "Remote"]]
    scene.add("One tier below", north=0.02, tier_rank=3)
    assert scene.run([[anchor, remote]]) == [["Anchor", "One tier below"]]


def test_low_value_places_and_commercial_galleries_are_never_used(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote", north=_REMOTE)
    scene.add("Plaque", north=0.01, low_value=True)
    scene.add("Gallery", north=0.012, gallery=True)
    scene.add("No coordinates", poi_extra={"coordinates": None})
    assert scene.run([[anchor, remote]]) == [["Anchor", "Remote"]]
    # a gallery is an ordinary candidate on an art-focused trip
    assert scene.run([[anchor, remote]], interests=("art",)) == [["Anchor", "Gallery"]]


def test_a_suspected_duplicate_of_a_scheduled_stop_is_never_used(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True, poi_extra={SUSPECT_COLLISION_KEY: "pair-1"})
    remote = scene.add("Remote", north=_REMOTE)
    scene.add("Twin of the anchor", north=0.001, poi_extra={SUSPECT_COLLISION_KEY: "pair-1"})
    assert scene.run([[anchor, remote]]) == [["Anchor", "Remote"]]
    scene.add("Distinct", north=0.02)
    assert scene.run([[anchor, remote]]) == [["Anchor", "Distinct"]]


def test_a_replacement_never_makes_the_day_more_concentrated_in_one_class(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    market = scene.add("Local market", north=0.005, primary=taxonomy.FOOD_MARKET, anchor=True)
    remote = scene.add("Remote park", north=_REMOTE, primary=taxonomy.PARK_NATURE)
    scene.add("Second market", north=0.01, primary=taxonomy.FOOD_MARKET)
    # a second market would break the day's market cap: not acceptable, so the stop stays
    assert scene.run([[anchor, market, remote]]) == [["Anchor", "Local market", "Remote park"]]
    scene.add("Local park", north=0.02, primary=taxonomy.PARK_NATURE)
    assert scene.run([[anchor, market, remote]]) == [["Anchor", "Local market", "Local park"]]


# -- protection ----------------------------------------------------------------------------------


@pytest.mark.parametrize("protection", ["anchor", "must_visit"])
def test_a_remote_must_visit_or_grounded_anchor_is_never_replaced(scene: _Scene, protection: str) -> None:
    near = scene.add("Near anchor", anchor=True)
    remote = scene.add("Remote protected", north=_REMOTE, **{protection: True})
    broad = scene.add("Broad", north=0.01)
    scene.add("Local", north=0.02)
    # the protected stops alone are spread out: the day is left exactly as it is
    assert scene.run([[near, remote, broad]]) == [["Near anchor", "Remote protected", "Broad"]]


def test_a_day_whose_protected_stops_already_exceed_the_boundary_is_untouched(scene: _Scene) -> None:
    first = scene.add("Anchor one", anchor=True)
    second = scene.add("Anchor two", north=0.2, anchor=True)
    remote = scene.add("Remote broad", north=_REMOTE)
    scene.add("Local", north=0.01)
    assert scene.run([[first, second, remote]]) == [["Anchor one", "Anchor two", "Remote broad"]]


def test_without_a_protected_stop_the_best_ranked_stop_is_the_reference(scene: _Scene) -> None:
    best = scene.add("Best", score=0.9)
    remote = scene.add("Remote", north=_REMOTE, score=0.6)
    scene.add("Local", north=0.01)
    assert scene.run([[remote, best]]) == [["Local", "Best"]]  # replaced in place: positions are kept


def test_a_day_within_the_boundary_is_returned_unchanged(scene: _Scene) -> None:
    stops = [scene.add("One", anchor=True), scene.add("Two", north=0.02), scene.add("Three", north=0.04)]
    scene.add("Unused", north=0.001)
    assert _spread([stops])[0] <= GEOGRAPHIC_SPREAD_THRESHOLD_KM
    result = _limit_ai_day_spread(
        [stops], scene.pool, scene.profiles, set(), scene.anchor_ids, [], markets_requested=False
    )
    assert [[id(poi) for poi in day] for day in result] == [[id(poi) for poi in stops]]


def test_a_stop_without_coordinates_is_left_alone(scene: _Scene) -> None:
    anchor = scene.add("Anchor", anchor=True)
    unlocated = scene.add("Unlocated", poi_extra={"coordinates": None})
    scene.add("Local", north=0.01)
    assert scene.run([[anchor, unlocated]]) == [["Anchor", "Unlocated"]]


# -- determinism ---------------------------------------------------------------------------------


def test_a_candidate_is_used_once_and_ties_follow_tier_score_then_pool_order(scene: _Scene) -> None:
    first_anchor = scene.add("Anchor 1", anchor=True)
    second_anchor = scene.add("Anchor 2", east=0.5, anchor=True)
    remote_one = scene.add("Remote 1", north=_REMOTE)
    remote_two = scene.add("Remote 2", north=_REMOTE, east=0.5)
    # equally near day 1's anchor: the better tier wins, then the better score, then pool order
    scene.add("Tie, lower tier", north=0.01, tier_rank=3)
    scene.add("Tie, first in pool", north=0.01, score=0.7)
    scene.add("Tie, second in pool", north=0.01, score=0.7)
    scene.add("Near anchor 2", north=0.01, east=0.5)

    days = [[first_anchor, remote_one], [second_anchor, remote_two]]
    result = scene.run(days)
    assert result == [["Anchor 1", "Tie, first in pool"], ["Anchor 2", "Near anchor 2"]]
    assert all(scene.run(days) == result for _ in range(5))
    scheduled = [name for day in result for name in day]
    assert len(scheduled) == len(set(scheduled))


# -- the shared boundary -------------------------------------------------------------------------


def test_the_guard_and_the_validator_read_the_same_boundary_and_measure() -> None:
    assert GEOGRAPHIC_SPREAD_THRESHOLD_KM == 8.0  # unchanged
    assert validator_module._GEOGRAPHIC_SPREAD_WARNING_THRESHOLD_KM is day_order_heuristics.GEOGRAPHIC_SPREAD_THRESHOLD_KM
    assert planner_module.GEOGRAPHIC_SPREAD_THRESHOLD_KM is day_order_heuristics.GEOGRAPHIC_SPREAD_THRESHOLD_KM
    assert planner_module.day_spread_km is validator_module.day_spread_km is day_spread_km

    # the validator's own per-day figure is that shared measure
    stops = [
        ExperienceItem(experience_id=f"e{index}", name=f"S{index}", category="museum", coordinates=point)
        for index, point in enumerate([GeoPoint(lat=50.0, lng=10.0), None, GeoPoint(lat=50.05, lng=10.0)])
    ]
    day = DailyPlan(day_number=1, date="2026-08-10", experiences=stops)
    expected = day_spread_km([stop.coordinates for stop in stops])
    assert validator_module._day_geographic_spread_km(day) == expected == pytest.approx(5.56, abs=0.02)
    assert day_spread_km([GeoPoint(lat=50.0, lng=10.0), None]) is None  # not measurable: never guessed

    # no other distance was introduced by the pass
    source = inspect.getsource(_limit_ai_day_spread)
    assert "GEOGRAPHIC_SPREAD_THRESHOLD_KM" in source and "_DIVERSITY_MAX_TIER_DROP" in source
    assert not [token for token in ("_OUTLIER", "_NEAR_KM", " 8.0", "8 *", "* 1.") if token in source]


# -- no provider, routing or model call ----------------------------------------------------------


@pytest.fixture()
def no_io(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any network or provider-gateway use during the test is recorded (and refused)."""
    used: list[str] = []

    def refuse(label: str) -> Any:
        def _refuse(*args: Any, **kwargs: Any) -> Any:
            used.append(label)
            raise AssertionError(f"unexpected {label}")

        return _refuse

    from app.providers.gateway import ProviderGateway
    from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider

    monkeypatch.setattr(socket, "create_connection", refuse("socket"))
    monkeypatch.setattr(httpx.Client, "send", refuse("http"))
    for name in (
        "get_route", "get_route_sequence", "get_route_sequences", "get_alternate_mode_route",
        "get_alternate_mode_routes", "search_attractions", "search_must_visit_places", "places_for", "routing_for",
    ):
        if hasattr(ProviderGateway, name):
            monkeypatch.setattr(ProviderGateway, name, refuse(f"gateway.{name}"))
    monkeypatch.setattr(GroqItineraryNarratorProvider, "narrate", refuse("model"))
    return used


def test_the_pass_makes_no_provider_routing_or_model_call(scene: _Scene, no_io: list[str]) -> None:
    anchor = scene.add("Anchor", anchor=True)
    remote = scene.add("Remote", north=_REMOTE)
    scene.add("Local", north=0.01)
    assert scene.run([[anchor, remote]]) == [["Anchor", "Local"]]
    assert no_io == []

    # and it has no way to: it is given no gateway, context or client, and names none
    parameters = set(inspect.signature(_limit_ai_day_spread).parameters)
    assert parameters == {
        "day_groups", "pool", "profiles", "must_visit_ids", "anchor_ids", "canonical_interests",
        "markets_requested", "justified",
    }
    source = inspect.getsource(_limit_ai_day_spread).split('"""')[2]
    for forbidden in ("gateway", "provider_context", "httpx", "invoke(", "get_route", "requests", "planning_state"):
        assert forbidden not in source, forbidden


# -- through the planner -------------------------------------------------------------------------


def _sufficiency(viable: int, target: int = 6) -> InventorySufficiencyReport:
    return InventorySufficiencyReport(
        status=InventorySufficiencyStatus.HEALTHY if viable >= target else InventorySufficiencyStatus.THIN_BUT_USABLE,
        trip_days=2, pace="balanced", target_stops=target, minimum_useful=5, healthy_buffer=14,
        viable_candidates=viable, message="fixture",
    )


_ANCHOR = "Anchor Museum"
_PLACES = [
    # day 1 as the model chose it: a must-visit and two remote broad stops
    _place("anchor", _ANCHOR, lat=50.000, lng=10.000),
    _place("remote-a", "Remote Homestead", lat=50.500, lng=10.000),
    _place("remote-b", "Remote Market Hall", lat=50.520, lng=10.000),
    # day 2: a compact cluster a few kilometres east
    _place("east-1", "East One", lat=50.000, lng=10.060),
    _place("east-2", "East Two", lat=50.004, lng=10.062),
    _place("east-3", "East Three", lat=50.008, lng=10.064),
    # unused local candidates beside the must-visit
    _place("local-1", "Local One", lat=50.004, lng=10.000),
    _place("local-2", "Local Two", lat=50.008, lng=10.000),
    _place("local-3", "Local Three", lat=50.012, lng=10.000),
]
_REMOTE_NAMES = {"Remote Homestead", "Remote Market Hall"}


def _ai_plan(*, viable: int | None = 9, lock: bool = False, reasoning: bool = True) -> PlanningState:
    state = _planning_state(_PLACES, pace=TripPace.BALANCED)
    state.trip_request.must_visit = [_ANCHOR]
    if reasoning:
        state.ai_itinerary_reasoning_result = _completed_result(
            [
                _day(1, [_candidate_id("anchor"), _candidate_id("remote-a"), _candidate_id("remote-b")]),
                _day(2, [_candidate_id("east-1"), _candidate_id("east-2"), _candidate_id("east-3")]),
            ]
        )
    if viable is not None:
        state.inventory_sufficiency_report = _sufficiency(viable)
    if lock:
        state.user_locks = [UserLock(locked_item_type="experience", locked_item_id="anything")]
    return ExperiencePlannerService().run(state)


def _scheduled(state: PlanningState) -> set[str]:
    return {name for day in _scheduled_names_by_day(state) for name in day}


def test_an_ai_guided_plan_with_sufficient_inventory_loses_its_remote_fillers(no_io: list[str]) -> None:
    state = _ai_plan()

    scheduled = _scheduled(state)
    assert _ANCHOR in scheduled and not scheduled & _REMOTE_NAMES
    assert {"East One", "East Two", "East Three"} <= scheduled  # the compact day is untouched
    assert len(scheduled) == 6  # nothing added, nothing dropped
    assert len(scheduled & {"Local One", "Local Two", "Local Three"}) == 2
    # every day is now within the validator's boundary
    for day in state.experience_plan.daily_plans:
        assert (day_spread_km([stop.coordinates for stop in day.experiences]) or 0.0) <= GEOGRAPHIC_SPREAD_THRESHOLD_KM
    # replacements are real provider-grounded candidates of the same pool
    by_name = {stop.name: stop for day in state.experience_plan.daily_plans for stop in day.experiences}
    assert all(stop.provider_place_id for stop in by_name.values())
    assert no_io == []  # the whole planner run made no provider, routing or model call


def test_the_pass_only_runs_with_viable_inventory_at_or_above_the_target() -> None:
    assert _REMOTE_NAMES <= _scheduled(_ai_plan(viable=5))  # viable < T: the model's choice stands
    assert _REMOTE_NAMES <= _scheduled(_ai_plan(viable=None))  # no sufficiency report at all
    assert not _scheduled(_ai_plan(viable=6)) & _REMOTE_NAMES  # viable == T is enough


def test_an_active_user_lock_leaves_the_plan_untouched() -> None:
    assert _REMOTE_NAMES <= _scheduled(_ai_plan(lock=True))


def test_the_deterministic_path_never_runs_the_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    # Q3: with day composition on, the shared composition objective stands in for this pass
    # (`test_day_composition_q3`). This test pins the pre-Q3 wiring, which the rollback switch restores.
    monkeypatch.setenv("DAY_COMPOSITION_ENABLED", "false")
    get_settings.cache_clear()
    calls: list[int] = []
    original = planner_module._limit_ai_day_spread
    monkeypatch.setattr(
        planner_module, "_limit_ai_day_spread", lambda *args, **kwargs: calls.append(1) or original(*args, **kwargs)
    )
    deterministic = _scheduled_names_by_day(_ai_plan(reasoning=False))
    assert calls == []
    # ...and the AI-guided path runs it exactly once
    _ai_plan()
    assert calls == [1]
    monkeypatch.setattr(planner_module, "_limit_ai_day_spread", original)
    assert _scheduled_names_by_day(_ai_plan(reasoning=False)) == deterministic


def test_the_planner_result_is_deterministic() -> None:
    first = _scheduled_names_by_day(_ai_plan())
    assert all(_scheduled_names_by_day(_ai_plan()) == first for _ in range(3))
