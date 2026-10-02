from __future__ import annotations

import dataclasses
import importlib.util
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.core.provider_usage import GenerationProviderContext
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.planning_state import (
    DailyPlan,
    DestinationContext,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.models.providers import NormalizedPlace
from app.models.routing import RouteRequest, RouteResult, leg_mode
from app.providers.places.entity_identity import (
    alternate_names,
    dedupe_places,
    merge_rule,
    source_entity_id,
)
from app.services import experience_planner_service as planner_module
from app.services import place_taxonomy as taxonomy
from app.services import schedule_diversity as diversity
from app.services.candidate_quality_service import CandidateQualityService
from app.services.entity_collisions import SUSPECT_COLLISION_KEY, scheduled_unresolved_collisions
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.plan_validator_service import PlanValidatorService
from app.services.route_burden import LONG_TRAVEL_DAY, day_route_burdens
from app.services.route_burden_repair_service import apply_route_burden_repair_safely
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.travel_time_buffer_service import TravelTimeBufferService
from app.utils.names import comparable_name, same_name

# Section 203C.2B arbitrary-city generalization correction: mixed-mode
# transfers, the schedule-diversity contract and real-entity de-duplication.
# Every fixture is synthetic (invented names on a small coordinate grid);
# nothing here is city-specific.

_START = date(2026, 11, 10)


def _point(lat: float, lng: float) -> GeoPoint:
    return GeoPoint(lat=lat, lng=lng)


def _poi(key: str, name: str, point: GeoPoint, **extra: Any) -> dict[str, Any]:
    return {
        "place_id": f"geoapify/{key}", "name": name, "category": "museum",
        "coordinates": {"lat": point.lat, "lng": point.lng}, "source": "geoapify_places",
        "data_status": "live", "confidence": 0.6, "provider_tags": {"tourism": "museum"}, **extra,
    }


def _market(key: str, name: str, point: GeoPoint, **extra: Any) -> dict[str, Any]:
    return _poi(key, name, point, category="marketplace", provider_tags={"amenity": "marketplace"}, **extra)


def _castle(key: str, name: str, point: GeoPoint, **extra: Any) -> dict[str, Any]:
    return _poi(key, name, point, category="castle", provider_tags={"historic": "castle"}, **extra)


def _stop(poi: dict[str, Any]) -> ExperienceItem:
    return ExperienceItem(
        experience_id=f"exp-{poi['place_id']}", name=poi["name"], category=poi["category"],
        coordinates=GeoPoint(**poi["coordinates"]), provider_place_id=poi["place_id"],
        provider_source="geoapify_places",
    )


def _state(pois: list[dict[str, Any]], days: list[list[dict[str, Any]]], **trip: Any) -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Fixtureville, Fixtureland", start_date=_START,
            end_date=_START + timedelta(days=max(1, len(days)) - 1), travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE, pace=TripPace.BALANCED, **trip,
        )
    )
    state.destination_context = DestinationContext(
        destination_name="Fixtureville, Fixtureland", candidate_pois=pois, candidate_restaurants=[]
    )
    state.candidate_quality_report = CandidateQualityService().build_report(state)
    state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(
                day_number=index, date=_START + timedelta(days=index - 1), experiences=[_stop(poi) for poi in day]
            )
            for index, day in enumerate(days, start=1)
        ]
    )
    return state


# =====================================================================================
# 1. Mixed-mode transfers
# =====================================================================================

_WALK_SECONDS_PER_DEGREE = 60000.0  # 0.1 degrees apart = a 6000 s walk
_DRIVE_SECONDS_PER_DEGREE = 9000.0  # ... and a 900 s drive


class _Routing:
    provider_name = "fixture_routing"


class _Gateway:
    """A routing provider stand-in with a walking and a driving mode. The
    driving mode honours the generation's alternate-mode cap, as the real
    adapter does."""

    routing = _Routing()

    def __init__(self, drive_status: ProviderStatus = ProviderStatus.SUCCESS, drive_rate: float = _DRIVE_SECONDS_PER_DEGREE) -> None:
        self.walk_calls: list[list[tuple[float, float]]] = []
        self.drive_calls: list[tuple[tuple[float, float], tuple[float, float]]] = []
        self._drive_status = drive_status
        self._drive_rate = drive_rate

    @staticmethod
    def _degrees(a: tuple[float, float], b: tuple[float, float]) -> float:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def _result(self, seconds: float | None, mode: str, status: ProviderStatus = ProviderStatus.SUCCESS) -> RouteResult:
        seconds = round(seconds, 3) if seconds else seconds
        return RouteResult(
            provider="fixture_routing", status=status, source="fixture_routing", mode=mode if seconds else None,
            distance_meters=seconds * 1.2 if seconds else None, duration_seconds=seconds, confidence=0.9,
        )

    def get_route_sequence(self, points: list[tuple[float, float]], provider_context: Any = None) -> list[RouteResult]:
        self.walk_calls.append(points)
        return [
            self._result(self._degrees(a, b) * _WALK_SECONDS_PER_DEGREE, "walk") for a, b in zip(points, points[1:])
        ]

    def get_route(self, request: RouteRequest, provider_context: Any = None) -> RouteResult:
        a, b = (request.origin_lat, request.origin_lon), (request.destination_lat, request.destination_lon)
        return self._result(self._degrees(a, b) * _WALK_SECONDS_PER_DEGREE, "walk")

    def get_alternate_mode_route(
        self, origin: tuple[float, float], destination: tuple[float, float], provider_context: Any = None
    ) -> RouteResult:
        if provider_context is not None:
            if provider_context.alternate_mode_requests_left <= 0:
                return self._result(None, "drive", ProviderStatus.UNAVAILABLE)
            provider_context.alternate_mode_requests_left -= 1
        self.drive_calls.append((origin, destination))
        if self._drive_status != ProviderStatus.SUCCESS:
            return self._result(None, "drive", self._drive_status)
        return self._result(self._degrees(origin, destination) * self._drive_rate, "drive")


_NEAR_A = _poi("a", "Museum Alpha", _point(50.000, 10.000))
_NEAR_B = _poi("b", "Museum Beta", _point(50.002, 10.002))
_FAR = _castle("far", "Hilltop Castle", _point(50.000, 10.100))
_GOOD_NEARBY = _poi("good", "Museum Gamma", _point(50.003, 10.003))
_OTHER_FAR = _castle("far2", "Valley Castle", _point(50.100, 10.000))


def _routed(state: PlanningState, gateway: _Gateway, context: GenerationProviderContext | None = None) -> PlanningState:
    state.route_feasibility_report = RouteFeasibilityService(gateway=gateway).build_report(state, context)
    return state


def _review_codes(state: PlanningState) -> list[str]:
    return list(PlanValidatorService().run(state).validation_report.review_codes)


def test_a_short_leg_stays_a_walking_leg_and_no_driving_route_is_requested() -> None:
    gateway = _Gateway()
    state = _routed(_state([_NEAR_A, _NEAR_B, _GOOD_NEARBY], [[_NEAR_A, _NEAR_B, _GOOD_NEARBY]]), gateway)

    legs = state.route_feasibility_report.legs
    assert [leg.mode for leg in legs] == ["walk", "walk"]
    assert not any(leg.mode_adaptation_attempted or leg.walking_duration_seconds for leg in legs)
    assert gateway.drive_calls == [] and len(gateway.walk_calls) == 1
    assert LONG_TRAVEL_DAY not in _review_codes(state)


def test_a_long_walking_leg_gets_exactly_one_driving_request_and_the_day_becomes_mixed_mode() -> None:
    gateway = _Gateway()
    state = _routed(_state([_NEAR_A, _NEAR_B, _FAR], [[_NEAR_A, _NEAR_B, _FAR]]), gateway)

    short, long = state.route_feasibility_report.legs
    assert (short.mode, short.mode_adaptation_attempted) == ("walk", False)
    assert (long.mode, long.mode_adaptation_attempted) == ("drive", True)
    # the leg carries the provider's DRIVING figures; its walking figures are kept alongside
    assert long.duration_seconds == 900.0 and long.distance_meters == 900.0 * 1.2
    assert long.walking_duration_seconds == 6000.0
    assert long.status == ProviderStatus.SUCCESS and long.provider == "fixture_routing"
    assert len(gateway.walk_calls) == 1 and len(gateway.drive_calls) == 1  # one request, for that one leg only
    assert gateway.drive_calls[0] == ((50.002, 10.002), (50.000, 10.100))

    # factual wording only: a vehicle transfer described by a driving route estimate
    message = long.message.lower()
    assert "vehicle transfer" in message and "driving route estimate" in message
    assert not any(word in message for word in ("taxi", "rideshare", "ride-hail", "uber", "bus", "your car", "price"))


def test_a_successful_vehicle_transfer_clears_long_travel_day_and_no_attraction_is_replaced() -> None:
    gateway = _Gateway()
    # an unused, closer, equally good candidate exists: the old behaviour replaced the far stop with it
    state = _routed(_state([_NEAR_A, _NEAR_B, _FAR, _GOOD_NEARBY], [[_NEAR_A, _NEAR_B, _FAR]]), gateway)

    burden = day_route_burdens(state)[0]
    assert burden.drive_legs == 1 and not burden.excessive_walking and not burden.long_route
    assert burden.walking_duration_seconds == 240.0  # walk legs only
    assert burden.total_duration_seconds == 240.0 + 900.0  # every mode, for information

    apply_route_burden_repair_safely(state, RouteFeasibilityService(gateway=gateway))
    assert state.route_burden_repair_report is None  # nothing left to repair
    assert [stop.name for stop in state.experience_plan.daily_plans[0].experiences] == [
        "Museum Alpha", "Museum Beta", "Hilltop Castle",
    ]
    assert LONG_TRAVEL_DAY not in _review_codes(state)

    # buffers describe the same vehicle transfer, not the walking route
    buffers = TravelTimeBufferService(gateway=gateway).build_report(state).buffers
    assert [buffer.route_duration_seconds for buffer in buffers] == [240.0, 900.0]


def test_a_failed_driving_request_keeps_the_factual_walking_leg_and_the_warning() -> None:
    gateway = _Gateway(drive_status=ProviderStatus.FAILED)
    context = GenerationProviderContext.new(trip_days=1)
    state = _routed(_state([_NEAR_A, _NEAR_B, _FAR], [[_NEAR_A, _NEAR_B, _FAR]]), gateway, context)

    long = state.route_feasibility_report.legs[1]
    assert (long.mode, long.mode_adaptation_attempted) == ("walk", True)
    assert long.duration_seconds == 6000.0 and long.walking_duration_seconds is None  # nothing invented
    assert day_route_burdens(state)[0].excessive_walking
    assert LONG_TRAVEL_DAY in _review_codes(state)

    # rebuilding the report never asks for the same failed leg a second time
    _routed(state, gateway, context)
    assert len(gateway.drive_calls) == 1


def test_a_driving_route_that_is_not_faster_or_is_itself_too_long_does_not_hide_the_problem() -> None:
    slower = _Gateway(drive_rate=_WALK_SECONDS_PER_DEGREE * 2)
    state = _routed(_state([_NEAR_A, _NEAR_B, _FAR], [[_NEAR_A, _NEAR_B, _FAR]]), slower)
    assert state.route_feasibility_report.legs[1].mode == "walk" and day_route_burdens(state)[0].excessive_walking

    # a real but unreasonably long vehicle transfer: no walking problem, still a long-travel day
    long_drive = _Gateway(drive_rate=30000.0)  # a 3000 s drive, beyond the drive-leg limit
    state = _routed(_state([_NEAR_A, _NEAR_B, _FAR], [[_NEAR_A, _NEAR_B, _FAR]]), long_drive)
    burden = day_route_burdens(state)[0]
    assert state.route_feasibility_report.legs[1].mode == "drive"
    assert not burden.excessive_walking and burden.over_transfer_limit and burden.long_route
    report = PlanValidatorService().run(state).validation_report
    assert LONG_TRAVEL_DAY in report.review_codes
    warning = next(issue.message for issue in report.warnings if issue.category == "long_travel_day")
    assert "vehicle transfer" in warning and "minutes of walking" not in warning


def test_the_alternate_mode_cap_is_enforced_and_usage_stays_bounded() -> None:
    gateway = _Gateway()
    context = GenerationProviderContext.new(trip_days=2)
    assert context.alternate_mode_requests_left == 6  # the configured per-generation cap
    context.alternate_mode_requests_left = 1
    days = [[_NEAR_A, _NEAR_B, _FAR], [_GOOD_NEARBY, _OTHER_FAR]]
    state = _routed(_state([_NEAR_A, _NEAR_B, _FAR, _GOOD_NEARBY, _OTHER_FAR], days), gateway, context)

    modes = [leg.mode for leg in state.route_feasibility_report.legs]
    assert modes == ["walk", "drive", "walk"]  # the second long leg stayed a walking leg
    assert len(gateway.drive_calls) == 1 and context.alternate_mode_requests_left == 0
    assert len(gateway.walk_calls) == 2  # still exactly one day-route request per day; no matrix
    burdens = day_route_burdens(state)
    assert [burden.long_route for burden in burdens] == [False, True]
    assert LONG_TRAVEL_DAY in _review_codes(state)


def test_mixed_mode_state_survives_persist_and_reload_and_an_old_state_reads_as_walking() -> None:
    gateway = _Gateway()
    state = _routed(_state([_NEAR_A, _NEAR_B, _FAR], [[_NEAR_A, _NEAR_B, _FAR]]), gateway)

    reloaded = PlanningState.model_validate_json(state.model_dump_json())
    assert [(leg.mode, leg.duration_seconds, leg.walking_duration_seconds) for leg in reloaded.route_feasibility_report.legs] == [
        ("walk", 240.0, None), ("drive", 900.0, 6000.0),
    ]
    assert day_route_burdens(reloaded) == day_route_burdens(state)

    # a state stored before modes existed: no mode fields, no diversity report, no merge counts
    old = state.model_dump(mode="json")
    for leg in old["route_feasibility_report"]["legs"]:
        for key in ("mode", "mode_adaptation_attempted", "walking_distance_meters", "walking_duration_seconds"):
            leg.pop(key)
        leg["duration_seconds"], leg["distance_meters"] = 6000.0, 7200.0
    old["experience_plan"].pop("schedule_diversity")
    loaded = PlanningState.model_validate(old)
    legs = loaded.route_feasibility_report.legs
    assert [leg.mode for leg in legs] == [None, None] and [leg_mode(leg.mode) for leg in legs] == ["walk", "walk"]
    burden = day_route_burdens(loaded)[0]  # read exactly as before: every leg is a walk
    assert burden.walking_duration_seconds == burden.total_duration_seconds == 12000.0 and burden.excessive_walking
    assert loaded.experience_plan.schedule_diversity == []


# =====================================================================================
# 2. Schedule diversity
# =====================================================================================

_CENTRE = (50.000, 10.000)


def _near(offset: int) -> GeoPoint:
    return _point(_CENTRE[0] + 0.001 * offset, _CENTRE[1] + 0.001 * offset)


def _profiles(pois: list[dict[str, Any]], interests: list[str]) -> dict[int, Any]:
    state = _state(pois, [])
    lookup = planner_module._build_quality_lookup(pois, state.candidate_quality_report.attraction_scores)
    canonical = taxonomy.canonical_interests(interests)
    return {id(poi): planner_module._candidate_profile(poi, lookup.get(id(poi)), canonical) for poi in pois}


def _enforce(
    days: list[list[dict[str, Any]]],
    pool: list[dict[str, Any]],
    interests: list[str],
    must_visit: list[dict[str, Any]] | None = None,
    profiles: dict[int, Any] | None = None,
):
    return planner_module._enforce_day_diversity(
        days, pool, profiles or _profiles(pool, interests), {id(poi) for poi in (must_visit or [])},
        markets_requested=diversity.markets_explicitly_requested(interests),
        justified=diversity.justified_classes(interests),
    )


def _names(day: list[dict[str, Any]]) -> list[str]:
    return [poi["name"] for poi in day]


_MARKETS = [_market(f"m{i}", f"Market {i}", _near(i)) for i in range(3)]
_MUSEUM = _poi("mu", "Local Museum", _near(4))
_CASTLE = _castle("ca", "Old Castle", _near(5))
_INTERESTS = ["architecture", "history", "food"]


def test_three_markets_with_a_diverse_unused_pool_are_repaired_to_one_market() -> None:
    pool = [*_MARKETS, _MUSEUM, _CASTLE]
    days, reports = _enforce([list(_MARKETS)], pool, _INTERESTS)

    classes = [diversity.coarse_class(_profiles(pool, _INTERESTS)[id(poi)].primary) for poi in days[0]]
    assert classes.count(diversity.MARKETPLACE) == 1 and len(days[0]) == 3  # nothing dropped
    assert {"Local Museum", "Old Castle"} <= set(_names(days[0]))
    report = reports[0]
    assert report.repair_attempted and not report.concentration_violation and report.alternatives_available
    assert len(report.replacements) == 2
    assert {r.replacement_place for r in report.replacements} == {"Local Museum", "Old Castle"}
    assert report.class_counts[diversity.MARKETPLACE] == 1


def test_a_marketplace_takes_at_most_one_attraction_slot_per_day() -> None:
    pool = [_MARKETS[0], _MARKETS[1], _MUSEUM, _CASTLE]
    days, reports = _enforce([[_MARKETS[0], _MARKETS[1], _MUSEUM]], pool, _INTERESTS)
    assert sorted(_names(days[0])) == ["Local Museum", "Market 1", "Old Castle"] or sorted(_names(days[0])) == [
        "Local Museum", "Market 0", "Old Castle",
    ]
    assert len(reports[0].replacements) == 1 and not reports[0].concentration_violation
    # the pure HARD rule: two markets is one too many -- unless markets were explicitly requested
    two_markets = [diversity.MARKETPLACE] * 2 + [diversity.MUSEUM_CULTURE]
    assert diversity.hard_excess(two_markets, False) == {diversity.MARKETPLACE: 1}
    assert diversity.hard_excess(two_markets, True) == {}
    assert diversity.concentration_kind(two_markets, False) == diversity.HARD
    assert diversity.hard_excess([diversity.MARKETPLACE, diversity.MUSEUM_CULTURE], False) == {}


def test_an_explicit_markets_or_shopping_interest_keeps_the_markets() -> None:
    for interests in (["shopping"], ["local markets", "history"], ["bazaars"]):
        assert diversity.markets_explicitly_requested(interests)
        pool = [*_MARKETS, _MUSEUM, _CASTLE]
        days, reports = _enforce([list(_MARKETS)], pool, interests)
        assert _names(days[0]) == ["Market 0", "Market 1", "Market 2"]
        assert not reports[0].repair_attempted and not reports[0].concentration_violation


def test_a_generic_food_interest_does_not_permit_a_day_of_markets() -> None:
    assert not diversity.markets_explicitly_requested(["food"])
    assert not diversity.markets_explicitly_requested(["food", "architecture", "history"])
    pool = [*_MARKETS, _MUSEUM, _CASTLE]
    days, _ = _enforce([list(_MARKETS)], pool, ["food"])
    assert sum(1 for name in _names(days[0]) if name.startswith("Market")) == 1

    # end to end: a food trip over a market-heavy pool never schedules two markets on one day
    markets = [_market(f"e{i}", f"Market E{i}", _near(i)) for i in range(6)]
    others = [_poi(f"o{i}", f"Museum O{i}", _near(i + 6)) for i in range(3)] + [
        _castle(f"c{i}", f"Castle C{i}", _near(i + 9)) for i in range(3)
    ]
    state = _state([*markets, *others], [], interests=["food"])
    state.trip_request.end_date = _START + timedelta(days=2)
    ExperiencePlannerService().run(state)
    for day in state.experience_plan.daily_plans:
        classes = [diversity.coarse_class(stop.normalized_category) for stop in day.experiences]
        assert classes.count(diversity.MARKETPLACE) <= 1 and not diversity.hard_excess(classes, False)
    assert len(state.experience_plan.schedule_diversity) == 3


def test_no_worse_or_invalid_candidate_is_used_merely_for_diversity() -> None:
    statue = _poi("st", "Bronze Figure", _near(4), category="artwork",
                  provider_tags={"tourism": "artwork", "artwork_type": "statue"})
    pool = [*_MARKETS, _MUSEUM, statue]
    profiles = _profiles(pool, _INTERESTS)
    assert profiles[id(statue)].low_value
    # the only real alternative is dramatically worse (two quality tiers below the markets)
    market_rank = profiles[id(_MARKETS[0])].tier_rank
    profiles[id(_MUSEUM)] = dataclasses.replace(profiles[id(_MUSEUM)], tier_rank=market_rank - 2)

    days, reports = _enforce([list(_MARKETS)], pool, _INTERESTS, profiles=profiles)

    assert _names(days[0]) == ["Market 0", "Market 1", "Market 2"]  # left exactly as it was
    report = reports[0]
    assert report.repair_attempted and report.replacements == [] and report.concentration_violation

    # with nothing of another class available at all, there is no alternative to report
    days, reports = _enforce([list(_MARKETS)], list(_MARKETS), _INTERESTS)
    assert _names(days[0]) == ["Market 0", "Market 1", "Market 2"] and not reports[0].alternatives_available


def test_a_must_visit_is_never_removed_for_diversity() -> None:
    pool = [*_MARKETS, _MUSEUM, _CASTLE]
    days, reports = _enforce([list(_MARKETS)], pool, _INTERESTS, must_visit=list(_MARKETS))
    assert _names(days[0]) == ["Market 0", "Market 1", "Market 2"]
    assert not reports[0].replacements

    days, _ = _enforce([list(_MARKETS)], pool, _INTERESTS, must_visit=[_MARKETS[2]])
    assert "Market 2" in _names(days[0]) and sum(1 for n in _names(days[0]) if n.startswith("Market")) == 1


def test_a_diversity_replacement_stays_near_the_rest_of_its_day() -> None:
    far_museum = _poi("far-mu", "Distant Museum", _point(50.200, 10.200))  # ~26 km away
    far_castle = _castle("far-ca", "Distant Castle", _point(50.300, 10.300))
    pool = [*_MARKETS, far_museum, far_castle]
    days, reports = _enforce([list(_MARKETS)], pool, _INTERESTS)
    assert _names(days[0]) == ["Market 0", "Market 1", "Market 2"]  # geography is not traded for diversity
    assert reports[0].concentration_violation and reports[0].alternatives_available

    # given a near and a far alternative of the same quality, the near one is used
    pool = [*_MARKETS, far_museum, _MUSEUM]
    days, _ = _enforce([list(_MARKETS)], pool, _INTERESTS)
    assert "Local Museum" in _names(days[0]) and "Distant Museum" not in _names(days[0])


def test_with_no_unused_alternative_a_stop_trades_places_with_another_day_and_no_place_is_lost() -> None:
    second_museum = _poi("mu2", "Second Museum", _near(6))
    pool = [*_MARKETS, _MUSEUM, _CASTLE, second_museum]  # every candidate is already scheduled
    days, reports = _enforce([list(_MARKETS), [_MUSEUM, _CASTLE, second_museum]], pool, _INTERESTS)

    assert sorted(name for day in days for name in _names(day)) == sorted(_names(pool))  # same places, none added
    profiles = _profiles(pool, _INTERESTS)
    for day in days:
        classes = [diversity.coarse_class(profiles[id(poi)].primary) for poi in day]
        assert classes.count(diversity.MARKETPLACE) <= 1 or len(set(classes)) > 1
    assert sum(1 for name in _names(days[0]) if name.startswith("Market")) < 3
    assert reports[0].repair_attempted and reports[0].replacements
    # the reports describe the days as they finally stand
    assert sum(reports[1].class_counts.values()) == 3 and reports[1].class_counts.get(diversity.MARKETPLACE, 0) >= 1


# =====================================================================================
# 3. Real-entity de-duplication
# =====================================================================================

_LOCAL_NAME = "खगोल वेधशाला"
_OTHER_LOCAL_NAME = "पुराना द्वार"


def _place(key: str, name: str, lat: float, lng: float, **extra: Any) -> NormalizedPlace:
    return NormalizedPlace(
        place_id=f"geoapify/{key}", name=name, category="attraction", coordinates=_point(lat, lng),
        source="geoapify_places", data_status=DataStatus.LIVE, confidence=0.6, **extra,
    )


def test_the_same_source_object_under_an_english_and_a_local_script_name_is_merged() -> None:
    english = _place("en", "Royal Observatory", 50.0100, 10.0100, source_entity_id="osm/way/4711")
    local = _place("local", _LOCAL_NAME, 50.0103, 10.0103, source_entity_id="osm/way/4711")
    merges: dict[str, int] = {}
    kept = dedupe_places([english, local], 100.0, merges)

    assert [place.place_id for place in kept] == ["geoapify/en"]  # the canonical Geoapify identity survives
    assert merges == {"source_identity": 1}
    assert _LOCAL_NAME in kept[0].alt_names  # so a later record under either name still matches
    assert merge_rule(kept[0], _place("again", _LOCAL_NAME, 50.0101, 10.0101), 100.0) == "name_proximity"

    # the same Wikidata entity close by is the same real place too
    a = _place("a", "Royal Observatory", 50.01, 10.01, provider_tags={"wikidata": "Q42"})
    b = _place("b", _LOCAL_NAME, 50.0102, 10.0102, provider_tags={"wikidata": "Q42"})
    assert merge_rule(a, b, 100.0) == "source_identity"
    assert merge_rule(a, _place("c", _LOCAL_NAME, 50.2, 10.2, provider_tags={"wikidata": "Q42"}), 100.0) is None


def test_different_nearby_source_objects_are_not_merged() -> None:
    observatory = _place("obs", "Royal Observatory", 50.0100, 10.0100, source_entity_id="osm/way/4711")
    gate = _place("gate", "Observatory Gate", 50.0101, 10.0101, source_entity_id="osm/node/9001")
    assert merge_rule(observatory, gate, 100.0) is None

    # two DIFFERENT local-script names next to each other are two places: a name that
    # cannot be compared in Latin letters is never treated as an equal (empty) name
    first = _place("l1", _LOCAL_NAME, 50.0100, 10.0100)
    second = _place("l2", _OTHER_LOCAL_NAME, 50.0100, 10.0100)
    assert comparable_name(_LOCAL_NAME) and comparable_name(_LOCAL_NAME) != comparable_name(_OTHER_LOCAL_NAME)
    assert not same_name(_LOCAL_NAME, _OTHER_LOCAL_NAME) and not same_name("---", "!!!")
    assert merge_rule(first, second, 100.0) is None
    assert len(dedupe_places([observatory, gate, first, second], 100.0)) == 4

    # an equal name far away is a different place of the same name
    assert merge_rule(observatory, _place("far", "Royal Observatory", 50.2, 10.2), 100.0) is None
    assert merge_rule(observatory, _place("near", "royal  observatory!", 50.0102, 10.0102), 100.0) == "name_proximity"
    assert merge_rule(observatory, _place("obs", "Anything", 60.0, 20.0), 100.0) == "place_id"


def test_the_source_identity_is_sanitised_and_never_serialised() -> None:
    raw = {"osm_type": "w", "osm_id": 4711, "name": _LOCAL_NAME, "name:en": "Royal Observatory", "phone": "1"}
    assert source_entity_id(raw) == "osm/way/4711"
    assert source_entity_id({"osm_type": "relation", "osm_id": "12"}) == "osm/relation/12"
    for bad in (None, {}, {"osm_type": "w"}, {"osm_type": "x", "osm_id": 1}, {"osm_type": "w", "osm_id": "1; drop"}, {"osm_type": "w", "osm_id": True}):
        assert source_entity_id(bad) is None
    assert alternate_names(raw, "Royal Observatory") == [_LOCAL_NAME]  # only names; the primary is not repeated

    place = _place("obs", "Royal Observatory", 50.01, 10.01, source_entity_id="osm/way/4711", alt_names=[_LOCAL_NAME])
    dumped = place.model_dump(mode="json")
    assert dumped["place_id"] == "geoapify/obs"
    assert "source_entity_id" not in dumped and "alt_names" not in dumped and "4711" not in str(dumped)
    # and therefore never reaches a planning state's candidate pool
    state = _state([dumped], [])
    assert "4711" not in state.model_dump_json()


# =====================================================================================
# 4. Live cleanup: unresolved suspected duplicates are never scheduled together
# =====================================================================================


def _twins() -> tuple[dict[str, Any], dict[str, Any]]:
    """An unresolved suspected pair: same spot, compatible class, identity unproven."""
    english = _poi("hall-en", "Grand Hall Museum", _near(1), **{SUSPECT_COLLISION_KEY: "collision-1"})
    local = _poi("hall-local", _LOCAL_NAME, _near(1), **{SUSPECT_COLLISION_KEY: "collision-1"})
    return english, local


def _scheduled_names(state: PlanningState) -> list[str]:
    return [stop.name for day in state.experience_plan.daily_plans for stop in day.experiences]


def test_an_unresolved_suspected_pair_is_never_scheduled_together_by_the_planner() -> None:
    english, local = _twins()
    others = [_poi(f"o{i}", f"Museum O{i}", _near(i + 2)) for i in range(2)] + [
        _castle(f"c{i}", f"Castle C{i}", _near(i + 4)) for i in range(2)
    ]
    state = _state([english, local, *others], [], interests=["history"])
    ExperiencePlannerService().run(state)  # one balanced day: three stops

    names = _scheduled_names(state)
    assert len(names) == 3 and len({"Grand Hall Museum", _LOCAL_NAME} & set(names)) <= 1
    assert scheduled_unresolved_collisions(state) == []


def test_a_day_holding_both_of_a_suspected_pair_has_one_replaced_and_no_place_is_invented() -> None:
    english, local = _twins()
    castle = _castle("c", "Old Castle", _near(3))
    spare = _poi("spare", "Spare Museum", _near(4))
    pool = [english, local, castle, spare]
    profiles = _profiles(pool, ["history"])

    days, separations = planner_module._separate_suspected_duplicates([[english, local, castle]], pool, profiles, set())
    assert _names(days[0]) == ["Grand Hall Museum", "Spare Museum", "Old Castle"]
    assert [(s.replaced_place, s.replacement_place) for s in separations] == [(_LOCAL_NAME, "Spare Museum")]

    # nothing suitable to put in its place: the stop is removed and the day is simply lighter
    days, separations = planner_module._separate_suspected_duplicates(
        [[english, local, castle]], [english, local, castle], profiles, set()
    )
    assert _names(days[0]) == ["Grand Hall Museum", "Old Castle"] and separations[0].replacement_place == ""

    # a must-visit is the one that is kept
    days, _ = planner_module._separate_suspected_duplicates([[english, local, castle]], pool, profiles, {id(local)})
    assert _LOCAL_NAME in _names(days[0]) and "Grand Hall Museum" not in _names(days[0])

    # the diversity pass can never bring the twin back in as a replacement
    markets = [_market(f"k{i}", f"Market K{i}", _near(i)) for i in range(2)]
    pool = [english, local, *markets]
    days, reports = _enforce([[english, markets[0], markets[1]]], pool, ["history"])
    assert _LOCAL_NAME not in _names(days[0]) and reports[0].concentration_violation


def test_a_scheduled_unresolved_pair_is_reported_by_the_validator_and_carries_no_provider_payload() -> None:
    english, local = _twins()
    state = _state([english, local], [[english, local]])
    state.destination_context.suspect_entity_collisions = [
        {
            "place_ids": ["geoapify/hall-en", "geoapify/hall-local"],
            "name_variants": [["Grand Hall Museum"], [_LOCAL_NAME]],
            "coarse_classes": ["museum_culture", "museum_culture"], "separation_meters": 0.0,
            "source_identity_present": [False, False], "wikidata_identity_present": [False, False],
            "enrichment_attempted": True, "resolution": "unresolved", "merged_by": None,
        }
    ]
    assert len(scheduled_unresolved_collisions(state)) == 1
    report = PlanValidatorService().run(state).validation_report
    assert "SUSPECTED_DUPLICATE_STOP" in report.review_codes
    assert report.readiness_status.value != "ready"

    # one of the two scheduled, or the pair resolved: nothing to report
    state.experience_plan.daily_plans[0].experiences.pop()
    assert scheduled_unresolved_collisions(state) == []
    reloaded = PlanningState.model_validate_json(state.model_dump_json())
    assert reloaded.destination_context.suspect_entity_collisions[0]["resolution"] == "unresolved"
    assert "datasource" not in state.model_dump_json() and "osm/" not in state.model_dump_json()


# =====================================================================================
# 5. Live cleanup: diversity is hard for markets, a preference for everything else
# =====================================================================================

_CASTLES = [_castle(f"h{i}", f"Castle H{i}", _near(i)) for i in range(3)]
_MUSEUMS = [_poi(f"u{i}", f"Museum U{i}", _near(i)) for i in range(3)]
_PARK = _poi("park", "Riverside Garden", _near(4), category="park", provider_tags={"leisure": "park"})


def test_three_history_stops_are_allowed_when_the_traveller_asked_for_history_and_architecture() -> None:
    assert diversity.justified_classes(_INTERESTS) >= {diversity.HISTORY_ARCHITECTURE}
    assert diversity.MARKETPLACE not in diversity.justified_classes(["food", "shopping"])  # markets: explicit rule only
    pool = [*_CASTLES, _MUSEUM, _PARK]
    days, reports = _enforce([list(_CASTLES)], pool, _INTERESTS)

    assert _names(days[0]) == ["Castle H0", "Castle H1", "Castle H2"]  # untouched
    report = reports[0]
    assert not report.repair_attempted and report.replacements == []
    assert report.concentration_kind == diversity.JUSTIFIED and not report.concentration_violation


def test_three_museums_nobody_asked_for_may_be_relieved_by_an_equally_good_nearby_alternative() -> None:
    interests = ["outdoors"]  # nothing that justifies a day of museums
    assert diversity.MUSEUM_CULTURE not in diversity.justified_classes(interests)
    pool = [*_MUSEUMS, _PARK]
    profiles = _profiles(pool, interests)
    museum = profiles[id(_MUSEUMS[0])]
    # an alternative of equal quality, as near as the stop it replaces
    profiles[id(_PARK)] = dataclasses.replace(profiles[id(_PARK)], tier_rank=museum.tier_rank, score=museum.score + 0.01)

    days, reports = _enforce([list(_MUSEUMS)], pool, interests, profiles=profiles)
    assert "Riverside Garden" in _names(days[0]) and len(days[0]) == 3
    assert len(reports[0].replacements) == 1 and reports[0].concentration_kind is None

    # left alone, the same day is only a SOFT concentration -- reported, never a violation
    days, reports = _enforce([list(_MUSEUMS)], list(_MUSEUMS), interests)
    assert reports[0].concentration_kind == diversity.SOFT and not reports[0].concentration_violation


def test_diversity_never_replaces_a_stronger_anchor_merely_for_category_count() -> None:
    interests = ["outdoors"]
    pool = [*_MUSEUMS, _PARK]
    profiles = _profiles(pool, interests)
    museum = profiles[id(_MUSEUMS[0])]

    # a lower-tier alternative: never used for a soft concentration
    profiles[id(_PARK)] = dataclasses.replace(profiles[id(_PARK)], tier_rank=museum.tier_rank - 1)
    days, reports = _enforce([list(_MUSEUMS)], pool, interests, profiles=profiles)
    assert _names(days[0]) == ["Museum U0", "Museum U1", "Museum U2"] and reports[0].replacements == []

    # primary-anchor stops: an alternative that is not clearly better does not displace one
    primary = planner_module._QUALITY_TIER_RANK[planner_module.CandidateQualityTier.PRIMARY_ANCHOR]
    for poi in _MUSEUMS:
        profiles[id(poi)] = dataclasses.replace(profiles[id(poi)], tier_rank=primary, score=0.83)
    profiles[id(_PARK)] = dataclasses.replace(profiles[id(_PARK)], tier_rank=primary, score=0.80)
    days, reports = _enforce([list(_MUSEUMS)], pool, interests, profiles=profiles)
    assert _names(days[0]) == ["Museum U0", "Museum U1", "Museum U2"]
    assert reports[0].concentration_kind == diversity.SOFT

    # an equally good alternative that is much farther away is not used either
    far_park = _poi("far-park", "Distant Garden", _point(50.05, 10.05), category="park", provider_tags={"leisure": "park"})
    pool = [*_MUSEUMS, far_park]
    profiles = _profiles(pool, interests)
    profiles[id(far_park)] = dataclasses.replace(
        profiles[id(far_park)], tier_rank=profiles[id(_MUSEUMS[0])].tier_rank, score=0.99
    )
    days, _ = _enforce([list(_MUSEUMS)], pool, interests, profiles=profiles)
    assert _names(days[0]) == ["Museum U0", "Museum U1", "Museum U2"]


def test_must_visits_stay_protected_under_the_soft_rule_too() -> None:
    interests = ["outdoors"]
    pool = [*_MUSEUMS, _PARK]
    profiles = _profiles(pool, interests)
    profiles[id(_PARK)] = dataclasses.replace(profiles[id(_PARK)], tier_rank=9, score=0.99)
    days, reports = _enforce([list(_MUSEUMS)], pool, interests, must_visit=list(_MUSEUMS), profiles=profiles)
    assert _names(days[0]) == ["Museum U0", "Museum U1", "Museum U2"] and reports[0].replacements == []


# =====================================================================================
# 6. Live cleanup: geographic spread is judged on the final mode-aware route
# =====================================================================================

# ~10.7 km from the two near stops: a 9000 s walk, a 1350 s drive
_DISTANT = _castle("distant", "Ridge Fort", _point(50.000, 10.150))


def _categories(state: PlanningState) -> set[str]:
    return {issue.category for issue in PlanValidatorService().run(state).validation_report.warnings}


def test_a_long_separation_with_a_reasonable_factual_drive_is_not_geographic_spread() -> None:
    gateway = _Gateway()
    state = _routed(_state([_NEAR_A, _NEAR_B, _DISTANT], [[_NEAR_A, _NEAR_B, _DISTANT]]), gateway)

    legs = state.route_feasibility_report.legs
    assert [leg.mode for leg in legs] == ["walk", "drive"]  # a mixed walk/drive day within every limit
    burden = day_route_burdens(state)[0]
    assert burden.routed_legs == burden.required_legs and not burden.long_route
    categories = _categories(state)
    assert "geographic_spread" not in categories and "long_travel_day" not in categories


def test_the_same_separation_without_a_usable_alternate_mode_is_still_flagged() -> None:
    failed_drive = _Gateway(drive_status=ProviderStatus.FAILED)
    state = _routed(_state([_NEAR_A, _NEAR_B, _DISTANT], [[_NEAR_A, _NEAR_B, _DISTANT]]), failed_drive)
    assert state.route_feasibility_report.legs[1].mode == "walk"
    assert {"geographic_spread", "long_travel_day"} <= _categories(state)

    # no route report at all (routing not connected): the straight-line warning, worded as before
    unrouted = _state([_NEAR_A, _NEAR_B, _DISTANT], [[_NEAR_A, _NEAR_B, _DISTANT]])
    report = PlanValidatorService().run(unrouted).validation_report
    warning = next(issue for issue in report.warnings if issue.category == "geographic_spread")
    assert "straight-line" in warning.message and "GEOGRAPHIC_SPREAD" in report.review_codes


def test_an_unreasonable_drive_burden_still_produces_the_geographic_warning() -> None:
    long_drive = _Gateway(drive_rate=30000.0)  # a 4500 s drive, beyond the drive-leg limit
    state = _routed(_state([_NEAR_A, _NEAR_B, _DISTANT], [[_NEAR_A, _NEAR_B, _DISTANT]]), long_drive)
    assert state.route_feasibility_report.legs[1].mode == "drive" and day_route_burdens(state)[0].over_transfer_limit
    report = PlanValidatorService().run(state).validation_report
    warning = next(issue for issue in report.warnings if issue.category == "geographic_spread")
    assert "exceed the configured walking or transfer limits" in warning.message
    assert {"GEOGRAPHIC_SPREAD", LONG_TRAVEL_DAY} <= set(report.review_codes)


def test_an_old_walking_only_route_report_is_judged_exactly_as_walking() -> None:
    gateway = _Gateway()
    state = _routed(_state([_NEAR_A, _NEAR_B, _DISTANT], [[_NEAR_A, _NEAR_B, _DISTANT]]), gateway)
    old = state.model_dump(mode="json")
    for leg in old["route_feasibility_report"]["legs"]:
        for key in ("mode", "mode_adaptation_attempted", "walking_distance_meters", "walking_duration_seconds"):
            leg.pop(key)
    old["route_feasibility_report"]["legs"][1].update(duration_seconds=9000.0, distance_meters=10800.0)

    loaded = PlanningState.model_validate(old)
    assert day_route_burdens(loaded)[0].excessive_walking  # a 9000 s leg read as a walk
    assert {"geographic_spread", "long_travel_day"} <= _categories(loaded)

    # and an old walking-only day that is within the walking limits carries no geographic warning
    old["route_feasibility_report"]["legs"][1].update(duration_seconds=1800.0, distance_meters=2000.0)
    calm = PlanningState.model_validate(old)
    assert "geographic_spread" not in _categories(calm)


# =====================================================================================
# 7. Live cleanup: canary acceptance
# =====================================================================================


def _canary() -> Any:
    path = Path(__file__).resolve().parents[3] / "scripts" / "canary_city.py"
    spec = importlib.util.spec_from_file_location("canary_city_cleanup_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _canary_report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "destination": {"geocode_success": True},
        "inventory": {"viable_meaningful_candidate_count": 32, "R": 8, "T": 9},
        "anchors": {"proposal_status": "completed", "failure_kind": None, "proposed": 17, "grounded": 4},
        "quality": {
            "duplicates": [], "meaningful_scheduled_stops": 9, "empty_days": [], "blocking_codes": [],
            "review_codes": [], "readiness": "ready",
        },
        "routing": {"coverage_percentage": 100.0},
        "must_visits": {"grounded": [], "scheduled": []},
        "daily_travel_burden": [
            {"day": 1, "long_route": False, "excessive_walking": False, "unreasonable_transfers": False}
        ],
        "movement_modes": {"legs_with_unverified_movement_data": 0},
        "diversity": {"days": [{"day": 1, "hard_violation": False, "soft_concentration": False,
                                "explicit_interest_justified_concentration": False,
                                "eligible_alternatives_existed": True}]},
        "suspect_entity_collisions": {"scheduled_unresolved_collision_count": 0, "zero_distance_scheduled_pairs": []},
        "food_locality": {"repeated_suggestion_count": 0, "suggestions_beyond_radius": 0},
        "rationale_consistency": {"stale_rationale_detected": False},
        "factual_safety": {"fabricated_or_unverified_scheduled_identities": [], "unsupported_factual_claims_in_stored_narrative": 0},
        "persistence": {"save_succeeded": True, "reload_succeeded": True},
        "provider_usage": {"geoapify_credit_budget": 100, "total_geoapify_credits": 42},
    }
    report.update(overrides)
    return report


def test_the_canary_fails_a_scheduled_suspected_duplicate_and_only_hard_diversity_violations() -> None:
    canary = _canary()
    assert canary._acceptance(_canary_report())["outcome"] == "PASS"

    def day(**flags: bool) -> dict[str, Any]:
        base = {"day": 1, "hard_violation": False, "soft_concentration": False,
                "explicit_interest_justified_concentration": False, "eligible_alternatives_existed": True}
        return {"days": [{**base, **flags}]}

    # a soft or an interest-justified concentration is reported, never a failure
    assert canary._acceptance(_canary_report(diversity=day(soft_concentration=True)))["outcome"] == "PASS"
    assert canary._acceptance(
        _canary_report(diversity=day(explicit_interest_justified_concentration=True))
    )["outcome"] == "PASS"
    hard = canary._acceptance(_canary_report(diversity=day(hard_violation=True)))
    assert hard["outcome"] == "FAIL" and "schedule diversity" in hard["failed_stages"]

    # "zero duplicates" is not claimed while a suspected collision is on the itinerary
    for suspects in (
        {"scheduled_unresolved_collision_count": 1, "zero_distance_scheduled_pairs": []},
        {"scheduled_unresolved_collision_count": 0,
         "zero_distance_scheduled_pairs": [{"day": 2, "stops": ["Grand Hall Museum", _LOCAL_NAME]}]},
    ):
        result = canary._acceptance(_canary_report(suspect_entity_collisions=suspects))
        assert result["outcome"] == "FAIL" and "entity identity / experience planning" in result["failed_stages"]
