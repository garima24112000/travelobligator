from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core import generation_diagnostics
from app.core.config import get_settings
from app.models import PlanningState, TripPace
from app.models.planning_state import ValidationSeverity
from app.models.providers import ProviderStatus
from app.providers.base import CurrencyProvider, HolidayProvider, WeatherProvider
from app.providers.gateway import provider_gateway
from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
from app.providers.places import geoapify_categories as categories
from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter, places_request_credits
from app.providers.routing.geoapify_adapter import GeoapifyRoutingAdapter
from app.services import experience_planner_service as planner_module
from app.services import geographic_dispersion as dispersion
from app.services import place_taxonomy as taxonomy
from app.services import route_burden_repair_service as legacy_repair_module
from app.services.day_order_heuristics import (
    GEOGRAPHIC_SPREAD_THRESHOLD_KM,
    day_extent_km,
    day_spread_km,
    keeps_day_within_spread,
    prospective_day_spread_km,
)
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.interest_coverage import food_coverage_evidence, interest_coverage
from app.services.must_visit_matching import record_grounded_term
from app.services.plan_validator_service import PlanValidatorService
from app.services.route_burden import LONG_TRAVEL_DAY, day_route_burdens
from app.services.route_burden_repair_service import apply_route_burden_repair_safely
from app.services.route_feasibility_service import RouteFeasibilityService
from app.storage.provider_cache_store import ProviderCacheStore
from app.tests.services import test_final_state_consistency_203c2b as final_state_fixtures
from app.tests.services import test_generalization_correction_203c2b as routed
from app.tests.services.test_batch1_tuning_fixes_3b import _scheduled
from app.tests.services.test_day_composition_q3 import _ai_state, _at, _core, _place
from app.tests.services.test_experience_planner_ai_guided import _completed_result, _day
from app.tests.services.test_final_v1_contracts_203c2b import _production_like_network

# Quality tuning corrections (docs/24_itinerary_quality_contract.md): evidence-backed, generic
# corrections after the two Q0 tuning arms. Synthetic fixtures only -- no city, no place of any
# benchmark, no live provider call.

_REMOTE_KM = 30.0
_KEY = "SENTINEL_TUNING_CORRECTIONS_KEY_0001"


@pytest.fixture
def setting(monkeypatch: pytest.MonkeyPatch) -> Any:
    def set_value(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield set_value
    get_settings.cache_clear()


def _traced(state: PlanningState) -> tuple[list[list[str]], dict[str, Any], dict[str, Any]]:
    """The plan, the raw trace and the per-stop history of one planner run under a recorder."""
    recorder = generation_diagnostics.GenerationDiagnostics()
    with generation_diagnostics.activate(recorder):
        ExperiencePlannerService().run(state)
    trace = recorder.snapshot()
    return _scheduled(state), trace, generation_diagnostics.stop_history(trace)


def _introduced_by(state: PlanningState, history: dict[str, Any]) -> dict[str, str]:
    stops = {stop.provider_place_id: stop.name for day in state.experience_plan.daily_plans for stop in day.experiences}
    return {stops[place_id]: entry["introduced_by"] for place_id, entry in history["stops"].items()}


# =====================================================================================
# A. Diagnostics
# =====================================================================================


def test_diagnostics_are_inactive_by_default_and_never_change_the_plan() -> None:
    pool = [*_core(8), _place("r0", _REMOTE_KM)]
    model_days = [["c0", "c1"], ["c2", "c3"]]

    assert generation_diagnostics.current() is None
    plain = _ai_state(pool, model_days)
    plain.usefulness_fallback_applied = True
    ExperiencePlannerService().run(plain)  # every diagnostic call is a no-op here

    recorded = _ai_state(pool, model_days)
    recorded.usefulness_fallback_applied = True
    plan, trace, _ = _traced(recorded)
    assert plan == _scheduled(plain)
    assert generation_diagnostics.current() is None  # the recorder was bound to that run only
    # ids, fixed labels and numbers only: never a place name
    assert "Museum" not in json.dumps(trace)
    # and nothing of it is stored with the plan or shown to the traveller
    assert "fallback_top_up" not in recorded.model_dump_json()


def test_the_trace_names_the_stage_that_introduced_a_distant_discretionary_stop() -> None:
    # Thin inventory: three core places and one remote one. The model used the core; the
    # usefulness fallback has nothing else to reach the minimum with.
    pool = [*_core(3), _place("r0", _REMOTE_KM, name="Remote Museum")]
    state = _ai_state(pool, [["c0", "c1"], ["c2"]], viable=4)
    state.usefulness_fallback_applied = True
    plan, trace, history = _traced(state)

    assert "Remote Museum" in [name for day in plan for name in day]
    introduced = _introduced_by(state, history)
    assert introduced["Remote Museum"] == "fallback_top_up"
    assert introduced["Museum c0"] == "ai_reasoning_selection"
    assert trace["planner_passes"] == [{"pass": 1, "trigger": generation_diagnostics.PASS_USEFULNESS_FALLBACK}]
    stages = [item["stage"] for item in trace["schedule_snapshots"]]
    assert stages[:3] == ["ai_reasoning_selection", "empty_day_fill", "fallback_top_up"] and stages[-1] == "planner_final"

    composition = trace["composition"][0]
    assert composition["ran"] is True and composition["used_ai_reasoning"] is True
    assert composition["status"] in {"changed", "unchanged_no_admissible_improvement", "unchanged_budget_exhausted"}
    assert composition["accepted_moves"] == sum(composition["moves_by_kind"].values())
    assert composition["evaluations"] >= 0 and composition["budget_exhausted"] is False
    assert composition["dispersed_days_before"] >= 1  # the remote stop made its day dispersed


def test_composition_status_is_reported_when_it_does_not_run(setting: Any) -> None:
    setting("DAY_COMPOSITION_ENABLED", "false")
    _, trace, _ = _traced(_ai_state([*_core(8)], [["c0", "c1"], ["c2", "c3"]]))
    assert trace["composition"][0]["status"] == "not_run_disabled" and trace["composition"][0]["ran"] is False


def test_stop_history_attributes_later_stages_and_removals() -> None:
    def snapshot(number: int, stage: str, days: list[list[str]]) -> dict[str, Any]:
        return {"pass": number, "stage": stage, "days": days}

    trace = {
        "planner_passes": [{"pass": 1, "trigger": "initial"}, {"pass": 2, "trigger": "after_ai_repair"}],
        "schedule_snapshots": [
            snapshot(1, "ai_reasoning_selection", [["old"], ["a"]]),  # a superseded pass explains nothing
            snapshot(2, "ai_reasoning_selection", [["a", "b"], ["c"]]),
            snapshot(2, "schedule_diversity", [["a", "d"], ["c"]]),  # b replaced by d
            snapshot(2, "interest_coverage", [["a", "d"], ["c", "e"]]),
            snapshot(2, "planner_final", [["a", "d"], ["c", "e"]]),
            snapshot(2, "routability_repair", [["a", "d"], ["c", "f"]]),  # e replaced by f after routing
            snapshot(2, "route_burden_repair", [["a"], ["c", "f", "d"]]),  # d moved to day 2
        ],
    }
    history = generation_diagnostics.stop_history(trace)
    assert history["final_pass"] == 2 and history["final_pass_trigger"] == "after_ai_repair"
    assert {key: value["introduced_by"] for key, value in history["stops"].items()} == {
        "a": "ai_reasoning_selection", "c": "ai_reasoning_selection", "d": "schedule_diversity", "f": "routability_repair",
    }
    assert history["stops"]["d"]["last_moved_by"] == "route_burden_repair" and history["stops"]["a"]["last_moved_by"] is None
    assert {(item["place_id"], item["removed_by"]) for item in history["removed"]} == {
        ("b", "schedule_diversity"), ("e", "routability_repair"),
    }
    assert "old" not in history["stops"]
    assert generation_diagnostics.stop_history({}) == {"final_pass": None, "stops": {}, "removed": []}


def test_the_recorder_is_bounded_and_drops_what_it_cannot_keep() -> None:
    recorder = generation_diagnostics.GenerationDiagnostics()
    with generation_diagnostics.activate(recorder):
        generation_diagnostics.begin_planner_pass("initial")
        generation_diagnostics.begin_planner_pass("a label with spaces is not a fixed label")
        for index in range(200):
            generation_diagnostics.schedule("stage", [[f"id{index}"] * 50])
        generation_diagnostics.schedule("not a label!", [["x"]])
        generation_diagnostics.composition(status="changed", accepted_moves=2, moves_by_kind={"swap": 2}, note="free text")
        generation_diagnostics.allowances("stage", None)
    trace = recorder.snapshot()
    assert trace["planner_passes"] == [{"pass": 1, "trigger": "initial"}]
    assert len(trace["schedule_snapshots"]) == 96 and trace["dropped_entries"] == 104
    assert all(len(day) <= 16 for item in trace["schedule_snapshots"] for day in item["days"])
    assert "note" not in trace["composition"][0] and trace["allowances"] == []


# =====================================================================================
# B. Geographic sanity
# =====================================================================================


def test_the_boundary_is_a_path_measure_and_is_not_applied_to_extent() -> None:
    # Three stops on a line, 3.5 km apart: the path is 7 km (within the 8 km boundary) and so is
    # the extent. A fourth makes the path 10.5 km while the extent is 10.5 km too; a triangle
    # shows the two measures apart.
    line = [_at(0.0), _at(3.5), _at(7.0)]
    assert day_spread_km(line) == pytest.approx(7.0, abs=0.01) and day_extent_km(line) == pytest.approx(7.0, abs=0.01)
    triangle = [_at(0.0), _at(4.0), _at(0.0, 4.0)]
    assert day_extent_km(triangle) < GEOGRAPHIC_SPREAD_THRESHOLD_KM < day_spread_km(triangle)
    # the safeguard uses the validator's own measure (a nearest-next path), not the extent
    assert prospective_day_spread_km(triangle) == pytest.approx(day_spread_km([_at(0.0), _at(4.0), _at(0.0, 4.0)]), abs=0.01)
    assert keeps_day_within_spread(line[:2], line[2]) is True
    assert keeps_day_within_spread(line, _at(10.5)) is False
    assert keeps_day_within_spread(line, None) is True  # an unlocated place cannot be judged here


def test_nearby_candidates_take_priority_over_a_better_ranked_remote_one() -> None:
    # The remote place carries more stored evidence than any core place, and the days still
    # fill from the core.
    remote = _place("r0", _REMOTE_KM, name="Remote Documented Museum", wikipedia="en:R0", heritage="yes")
    state = _ai_state([*_core(8), remote], [["c0", "c1"], ["c2", "c3"]])
    state.usefulness_fallback_applied = True
    plan, _, history = _traced(state)
    names = [name for day in plan for name in day]
    assert len(names) == 6 and "Remote Documented Museum" not in names
    assert all(day_spread_km([s.coordinates for s in day.experiences]) <= GEOGRAPHIC_SPREAD_THRESHOLD_KM
               for day in state.experience_plan.daily_plans)
    assert "fallback_top_up" in set(_introduced_by(state, history).values())


def test_the_pace_target_is_not_reached_with_remote_filler() -> None:
    # Five core places (the minimum R for two balanced days) and two remote ones without
    # supporting evidence: the plan stays at five rather than send a day across the region.
    pool = [*_core(5), _place("r0", _REMOTE_KM), _place("r1", _REMOTE_KM + 0.2)]
    state = _ai_state(pool, [["c0", "c1", "c2"], ["c3", "c4"]], viable=7)
    state.usefulness_fallback_applied = True
    plan, _, _ = _traced(state)
    assert sorted(len(day) for day in plan) == [2, 3]
    assert not [name for day in plan for name in day if name.endswith(("r0", "r1"))]


def test_high_usefulness_evidence_is_not_a_reason_to_disperse_a_day() -> None:
    # Same plan at the minimum R; the remote place now carries the highest stored usefulness
    # evidence in the pool (documented by the provider, a heritage designation, a grounded
    # anchor, and it serves the requested interest). Evidence says a place is worth a slot, not
    # that the traveller meant a regional excursion: the open slot stays open.
    documented = _place("r0", _REMOTE_KM, name="Remote Documented Museum", wikipedia="en:R0", heritage="yes")
    pool = [*_core(5), documented]
    state = _ai_state(pool, [["c0", "c1", "c2"], ["c3", "c4"]], viable=6, anchors=[documented])
    from app.services.candidate_usefulness import usefulness_by_place_id

    assessed = usefulness_by_place_id(state)
    assert assessed[documented["place_id"]].evidence_band == max(item.evidence_band for item in assessed.values()) >= 2
    state.usefulness_fallback_applied = True
    plan, _, _ = _traced(state)
    assert "Remote Documented Museum" not in [name for day in plan for name in day]
    assert sorted(len(day) for day in plan) == [2, 3]  # at R, below T, and compact
    assert dispersion.assess_days(state) == []


def test_a_distant_grounded_must_visit_is_still_added_and_kept() -> None:
    # The traveller asked for the remote place; the model left it out. The plan is already at
    # R, yet the must-visit is scheduled -- an explicit request is the one stored input that
    # establishes a deliberately distant stop. The day it shares with optional city stops is
    # still a review finding: the request explains the place, not the grouping.
    requested = _place("r0", _REMOTE_KM, name="Requested Remote Museum")
    pool = [*_core(5), requested]
    state = _ai_state(
        pool, [["c0", "c1", "c2"], ["c3", "c4"]], viable=6, must_visit={"Requested Remote Museum": requested}
    )
    state.usefulness_fallback_applied = True
    plan, _, _ = _traced(state)
    assert "Requested Remote Museum" in [name for day in plan for name in day]
    causes = [entry.cause for entry in dispersion.assess_days(state)]
    assert causes and dispersion.CAUSE_MANDATORY_DESTINATION not in causes
    assert set(causes) <= {dispersion.CAUSE_LIMITED_COMPATIBLE_INVENTORY, dispersion.CAUSE_DISCRETIONARY_DISPERSION}


def test_top_up_adds_a_dispersing_candidate_only_for_a_must_visit_or_the_minimum() -> None:
    top_up = planner_module._top_up_underfilled_days

    class _Profile:
        low_value = False

        def __init__(self, must_visit: bool = False, band: int = 0) -> None:
            preference = (0 if must_visit else 1, -band)
            self.usefulness = type(
                "U",
                (),
                {"must_visit": must_visit, "evidence_band": band, "preference": preference, "tail": (0.0, "", ""), "sort_key": preference},
            )()

    a, b, c = _place("a", 0.0), _place("b", 0.3), _place("c", 0.6)
    near, remote = _place("n", 0.9), _place("r", _REMOTE_KM)

    def run(unused_profile: _Profile, minimum: int | None, extra: list[dict[str, Any]] = ()) -> list[list[str]]:
        pool = [a, b, c, *extra, remote]
        profiles = {id(poi): _Profile() for poi in pool}
        profiles[id(remote)] = unused_profile
        days = top_up([[a, b], [c]], pool, profiles, 3, minimum_stops=minimum)
        return [[poi["place_id"].split("/", 1)[1] for poi in day] for day in days]

    # at or above the minimum: the remote place is left out, whatever its evidence band
    assert run(_Profile(band=3), minimum=3) == [["a", "b"], ["c"]]
    assert run(_Profile(band=3), minimum=None) == [["a", "b"], ["c"]]
    # below the minimum with nothing compatible left: it is used (thin inventory, reported later)
    assert run(_Profile(), minimum=4) == [["a", "b"], ["c", "r"]]
    # below the minimum, but a compatible candidate exists: that one is used and the remote is not
    assert run(_Profile(band=3), minimum=4, extra=[near]) == [["a", "b"], ["c", "n"]]
    # a grounded must-visit is added whatever the count
    assert run(_Profile(must_visit=True), minimum=3) == [["a", "b"], ["c", "r"]]


def test_an_empty_day_starts_from_a_place_it_can_be_built_around() -> None:
    # The best-ranked unused place stands alone 30 km out; the empty day is seeded from the
    # best place that has a companion inside the boundary.
    lone = _place("r0", _REMOTE_KM, name="Lone Documented Museum", wikipedia="en:R0", heritage="yes")
    pool = [*_core(8), lone]
    state = _ai_state(pool, [["c0", "c1", "c2"], ["c3"]])
    # the model planned only the first of the two days
    state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, [f"geoapify_places:{poi['place_id']}" for poi in pool[:3]])]
    )
    plan, _, history = _traced(state)
    assert plan[1] and "Lone Documented Museum" not in plan[1]
    assert "empty_day_fill" in set(_introduced_by(state, history).values())


def test_empty_day_seed_rules() -> None:
    seed = planner_module._empty_day_seed

    class _Profile:
        low_value = False

        def __init__(self, must_visit: bool) -> None:
            self.usefulness = type("U", (), {"must_visit": must_visit, "evidence_band": 0})()

    lone, near_a, near_b = _place("lone", _REMOTE_KM), _place("a", 0.0), _place("b", 0.5)
    profiles = {id(poi): _Profile(False) for poi in (lone, near_a, near_b)}
    assert seed([lone, near_a, near_b], profiles) == 1  # the first place with a companion
    assert seed([lone], profiles) == 0  # nothing qualifies: an empty day is still never left empty
    profiles[id(lone)] = _Profile(True)
    assert seed([lone, near_a, near_b], profiles) == 0  # a grounded must-visit is never passed over


# -- final validation --------------------------------------------------------------------------


def _dispersion_issues(state: PlanningState) -> list[Any]:
    report = PlanValidatorService().run(state).validation_report
    return [issue for issue in report.warnings if issue.category == dispersion.CATEGORY]


def _routed_day(pois: list[dict[str, Any]], day: list[dict[str, Any]], gateway: Any = None, **trip: Any) -> PlanningState:
    return routed._routed(routed._state(pois, [day], **trip), gateway or routed._Gateway())


def test_a_fully_routed_drive_no_longer_hides_a_dispersed_day() -> None:
    day = [routed._NEAR_A, routed._NEAR_B, routed._DISTANT]
    state = _routed_day(day, day)
    burden = day_route_burdens(state)[0]
    assert burden.routed_legs == burden.required_legs and not burden.long_route  # verified, within every limit

    issues = _dispersion_issues(state)
    assert len(issues) == 1 and issues[0].severity == ValidationSeverity.WARNING
    report = state.validation_report
    assert "GEOGRAPHIC_DISPERSION" in report.review_codes and report.readiness_status.value == "needs_review"
    # three findings stay apart: this is not a route-burden finding and not an unverified one
    categories = {issue.category for issue in report.warnings}
    assert "geographic_spread" not in categories and "long_travel_day" not in categories
    assert LONG_TRAVEL_DAY not in report.review_codes
    assert "km in a straight line" in issues[0].message and "compact" not in issues[0].message.lower()


def test_a_routed_forty_kilometre_transfer_is_never_called_compact() -> None:
    far = routed._castle("forty", "Forty Kilometre Fort", routed._point(50.000, 10.560))  # about 40 km east
    day = [routed._NEAR_A, routed._NEAR_B, far]
    state = _routed_day(day, day, routed._Gateway(drive_rate=4000.0))  # a 37-minute drive: within the limit
    leg = state.route_feasibility_report.legs[1]
    assert leg.mode == "drive" and leg.duration_seconds < get_settings().route_burden_max_drive_leg_seconds
    assert leg.distance_meters is not None and not day_route_burdens(state)[0].long_route

    issues = _dispersion_issues(state)
    assert len(issues) == 1 and issues[0].severity == ValidationSeverity.WARNING
    points = [stop.coordinates for stop in state.experience_plan.daily_plans[0].experiences]
    spread = day_spread_km(points)
    # the figure reported is the ordered PATH spread (the measure the boundary is defined for)
    assert spread > 35 and f"{spread:.1f} km" in issues[0].message
    assert [round(entry.spread_km, 3) for entry in dispersion.assess_days(state)] == [round(spread, 3)]
    assert "GEOGRAPHIC_DISPERSION" in state.validation_report.review_codes
    # ... and the finding does not claim the provider-verified drive cannot be made
    message = issues[0].message.lower()
    assert "checked route within the usual limits" in message
    assert not any(word in message for word in ("impossible", "cannot be", "infeasible", "not feasible", "unreachable"))


def test_dispersion_is_judged_on_path_spread_never_on_pairwise_extent() -> None:
    # Three places 4 km apart in an L: no two are 8 km apart (extent about 5.7 km), yet the path
    # through them is 8 km and more. It is the path the boundary is defined for.
    a = routed._poi("pa", "Museum Path A", _at(0.0))
    b = routed._poi("pb", "Museum Path B", _at(4.1))
    c = routed._poi("pc", "Museum Path C", _at(4.1, 4.1))
    day = [a, b, c]
    state = _routed_day(day, day, routed._Gateway(drive_rate=4000.0))
    points = [stop.coordinates for stop in state.experience_plan.daily_plans[0].experiences]
    assert day_extent_km(points) < GEOGRAPHIC_SPREAD_THRESHOLD_KM < day_spread_km(points)
    assert len(dispersion.assess_days(state)) == 1 and len(_dispersion_issues(state)) == 1
    # the insertion safeguard agrees with the validator on the same three places
    assert keeps_day_within_spread(points[:2], points[2]) is False

    # the reverse: two places 7.5 km apart are within the boundary on both measures
    near_pair = [a, routed._poi("pd", "Museum Path D", _at(7.5))]
    within = _routed_day(near_pair, near_pair, routed._Gateway(drive_rate=4000.0))
    assert dispersion.assess_days(within) == [] and _dispersion_issues(within) == []


def test_the_cause_of_dispersion_is_distinguished_from_stored_evidence() -> None:
    day = [routed._NEAR_A, routed._NEAR_B, routed._DISTANT]

    # limited inventory: nothing else exists, and without the far stop the day is below the minimum
    thin = _routed_day(day, day)
    assert [entry.cause for entry in dispersion.assess_days(thin)] == [dispersion.CAUSE_LIMITED_COMPATIBLE_INVENTORY]
    assert _dispersion_issues(thin)[0].severity == ValidationSeverity.WARNING  # thin inventory is still reported

    # discretionary: a compatible viable candidate was left unused
    spare = _routed_day([*day, routed._GOOD_NEARBY], day)
    assert [entry.cause for entry in dispersion.assess_days(spare)] == [dispersion.CAUSE_DISCRETIONARY_DISPERSION]

    # unverified: the stored state cannot say what else was available
    unknown = _routed_day(day, day)
    unknown.candidate_quality_report = None
    assert [entry.cause for entry in dispersion.assess_days(unknown)] == [dispersion.CAUSE_UNVERIFIED]


def _requested(poi: dict[str, Any], term: str) -> dict[str, Any]:
    copy = dict(poi)
    record_grounded_term(copy, term)
    return copy


def test_a_must_visit_on_a_dispersed_day_does_not_make_the_day_mandatory() -> None:
    # The shape of the tuning failure: ONE central requested place, grouped with two optional
    # places that sit together far away. The request explains nothing about the distance.
    central = _requested(routed._NEAR_A, "Museum Alpha")
    remote_a = routed._castle("ra", "Remote Fort A", routed._point(50.000, 10.560))
    remote_b = routed._castle("rb", "Remote Fort B", routed._point(50.002, 10.562))
    day = [central, remote_a, remote_b]
    pool = [*day, routed._NEAR_B, routed._GOOD_NEARBY]  # compatible places near the requested one exist
    state = _routed_day(pool, day, routed._Gateway(drive_rate=4000.0), must_visit=["Museum Alpha"])

    assert [entry.cause for entry in dispersion.assess_days(state)] == [dispersion.CAUSE_DISCRETIONARY_DISPERSION]
    issue = _dispersion_issues(state)[0]
    assert issue.severity == ValidationSeverity.WARNING and "GEOGRAPHIC_DISPERSION" in state.validation_report.review_codes
    assert "optional" in issue.message and "you asked for" not in issue.message
    # the requested place itself is kept, as ever
    assert state.experience_plan.daily_plans[0].experiences[0].name == "Museum Alpha"

    # the same holds for one REMOTE requested place grouped with optional city stops, with or
    # without a nearer alternative: a warning either way, never a note
    remote = _requested(routed._DISTANT, "Ridge Fort")
    city_day = [routed._NEAR_A, routed._NEAR_B, remote]
    for extra, cause in (
        ([routed._GOOD_NEARBY], dispersion.CAUSE_LIMITED_COMPATIBLE_INVENTORY),  # nothing lies near the requested place
        ([routed._castle("near_remote", "Ridge Tower", routed._point(50.001, 10.151))], dispersion.CAUSE_DISCRETIONARY_DISPERSION),
    ):
        grouped = _routed_day([*city_day, *extra], city_day, must_visit=["Ridge Fort"])
        assert [entry.cause for entry in dispersion.assess_days(grouped)] == [cause]
        assert _dispersion_issues(grouped)[0].severity == ValidationSeverity.WARNING


def test_two_distant_requested_places_are_mandatory_only_when_they_cannot_be_separated() -> None:
    first = _requested(routed._NEAR_A, "Museum Alpha")
    second = _requested(routed._DISTANT, "Ridge Fort")
    pair = [first, second]

    # a one-day trip: both requests must share the day -- the request itself requires the distance
    one_day = _routed_day(pair, pair, must_visit=["Museum Alpha", "Ridge Fort"])
    assert [entry.cause for entry in dispersion.assess_days(one_day)] == [dispersion.CAUSE_MANDATORY_DESTINATION]
    issue = _dispersion_issues(one_day)[0]
    assert issue.severity == ValidationSeverity.SUGGESTION and "places you asked for" in issue.message
    assert "GEOGRAPHIC_DISPERSION" not in one_day.validation_report.review_codes
    assert [stop.name for stop in one_day.experience_plan.daily_plans[0].experiences] == ["Museum Alpha", "Ridge Fort"]

    # a two-day trip: each could have had its own day, so sharing one is a grouping choice
    two_days = routed._routed(
        routed._state([*pair, routed._NEAR_B], [pair, [routed._NEAR_B]], must_visit=["Museum Alpha", "Ridge Fort"]),
        routed._Gateway(),
    )
    assert [entry.cause for entry in dispersion.assess_days(two_days)] == [dispersion.CAUSE_DISCRETIONARY_DISPERSION]
    assert _dispersion_issues(two_days)[0].severity == ValidationSeverity.WARNING

    # one day again, but an optional stop adds materially to the distance: no longer the request alone
    detour = routed._castle("detour", "Far Detour Fort", routed._point(50.150, 10.150))
    with_detour = [first, second, detour]
    widened = _routed_day(with_detour, with_detour, routed._Gateway(drive_rate=4000.0), must_visit=["Museum Alpha", "Ridge Fort"])
    assert [entry.cause for entry in dispersion.assess_days(widened)] == [dispersion.CAUSE_DISCRETIONARY_DISPERSION]


def test_requested_places_with_local_companions_or_a_day_of_their_own_raise_no_dispersion_finding() -> None:
    remote = _requested(routed._DISTANT, "Ridge Fort")
    companion = routed._castle("near_remote", "Ridge Tower", routed._point(50.001, 10.151))
    # a remote requested place with a companion next to it: a compact (if remote) day
    local = routed._routed(
        routed._state([remote, companion, routed._NEAR_A, routed._NEAR_B], [[remote, companion], [routed._NEAR_A, routed._NEAR_B]],
                      must_visit=["Ridge Fort"]),
        routed._Gateway(),
    )
    assert dispersion.assess_days(local) == [] and _dispersion_issues(local) == []
    # a remote requested place alone on its day: nothing to measure, nothing to report
    alone = routed._routed(
        routed._state([remote, routed._NEAR_A, routed._NEAR_B], [[remote], [routed._NEAR_A, routed._NEAR_B]], must_visit=["Ridge Fort"]),
        routed._Gateway(),
    )
    assert dispersion.assess_days(alone) == [] and _dispersion_issues(alone) == []
    assert [stop.name for stop in alone.experience_plan.daily_plans[0].experiences] == ["Ridge Fort"]


def test_dispersion_is_never_downgraded_without_proof_that_the_requests_alone_explain_it() -> None:
    unavoidable = dispersion.mandatory_dispersion_unavoidable
    near, far = routed._point(50.000, 10.000), routed._point(50.000, 10.150)
    assert unavoidable([near, far], 1, 3) is True  # one day: they must share it
    assert unavoidable([near, far], 2, 3) is False  # two days: each can have its own
    assert unavoidable([near, routed._point(50.001, 10.001), far], 2, 3) is False  # the near pair shares a day
    assert unavoidable([near, far, routed._point(50.150, 10.000)], 2, 3) is True  # three mutually distant, two days
    assert unavoidable([near, far], 1, 1) is True and unavoidable([near], 1, 3) is False
    # what cannot be established is not assumed: an unlocated request, or too many to work out
    assert unavoidable([near, None], 1, 3) is None
    assert unavoidable([routed._point(50.0 + 0.2 * i, 10.0) for i in range(9)], 2, 3) is None

    first, second = _requested(routed._NEAR_A, "Museum Alpha"), _requested(routed._DISTANT, "Ridge Fort")
    state = _routed_day([first, second], [first, second], must_visit=["Museum Alpha", "Ridge Fort"])
    day = state.experience_plan.daily_plans[0]
    facts = dict(
        must_visit_ids={first["place_id"], second["place_id"]}, unused_points=[], meaningful_stops=2, minimum_stops=2
    )
    assert dispersion.dispersion_cause(day, mandatory_unavoidable=True, **facts) == dispersion.CAUSE_MANDATORY_DESTINATION
    assert dispersion.dispersion_cause(day, mandatory_unavoidable=None, **facts) == dispersion.CAUSE_UNVERIFIED
    assert dispersion.dispersion_cause(day, **facts) == dispersion.CAUSE_UNVERIFIED  # nothing established: not downgraded
    assert dispersion.dispersion_cause(day, mandatory_unavoidable=False, **facts) == dispersion.CAUSE_DISCRETIONARY_DISPERSION

    from app.services.plan_validator_service import _geographic_dispersion_issue

    severities = {
        cause: _geographic_dispersion_issue(1, 12.0, cause).severity
        for cause in (
            dispersion.CAUSE_MANDATORY_DESTINATION, dispersion.CAUSE_LIMITED_COMPATIBLE_INVENTORY,
            dispersion.CAUSE_DISCRETIONARY_DISPERSION, dispersion.CAUSE_UNVERIFIED, "anything unknown",
        )
    }
    assert severities.pop(dispersion.CAUSE_MANDATORY_DESTINATION) == ValidationSeverity.SUGGESTION
    assert set(severities.values()) == {ValidationSeverity.WARNING}


def test_a_grounded_anchor_far_from_its_day_is_a_warning_not_an_excursion() -> None:
    # An anchor is a preference: its distance is discretionary dispersion, reported as a warning.
    from app.tests.services.test_candidate_usefulness_q2 import _anchor_for, _promotion

    day = [routed._NEAR_A, routed._NEAR_B, routed._DISTANT]
    state = _routed_day([*day, routed._GOOD_NEARBY], day)
    state.ai_candidate_promotion_report = _promotion([_anchor_for(routed._DISTANT)])
    assert [entry.cause for entry in dispersion.assess_days(state)] == [dispersion.CAUSE_DISCRETIONARY_DISPERSION]
    assert _dispersion_issues(state)[0].severity == ValidationSeverity.WARNING


def test_unverified_and_over_limit_days_keep_their_existing_findings() -> None:
    day = [routed._NEAR_A, routed._NEAR_B, routed._DISTANT]
    unrouted = _routed_day(day, day, routed._Gateway(drive_status=ProviderStatus.FAILED))
    report = PlanValidatorService().run(unrouted).validation_report
    categories = {issue.category for issue in report.warnings}
    assert "geographic_spread" in categories and dispersion.CATEGORY not in categories

    compact = _routed_day([routed._NEAR_A, routed._NEAR_B, routed._GOOD_NEARBY], [routed._NEAR_A, routed._NEAR_B, routed._GOOD_NEARBY])
    assert _dispersion_issues(compact) == [] and dispersion.assess_days(compact) == []


def test_the_legacy_rollback_still_runs_the_legacy_repair_and_validation_still_reports_dispersion(
    setting: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    setting("ROUTE_RECOMPOSITION_ENABLED", "false")
    calls: list[str] = []
    original = legacy_repair_module.RouteBurdenRepairService.repair

    def _spy(self: Any, planning_state: PlanningState, provider_context: Any = None) -> Any:
        calls.append("legacy")
        return original(self, planning_state, provider_context)

    monkeypatch.setattr(legacy_repair_module.RouteBurdenRepairService, "repair", _spy)
    day = [routed._NEAR_A, routed._NEAR_B, routed._DISTANT]
    gateway = routed._Gateway()
    state = _routed_day(day, day, gateway)
    before = _scheduled(state)
    apply_route_burden_repair_safely(state, RouteFeasibilityService(gateway=gateway))

    assert calls == ["legacy"]  # the rollback switch still selects the single-attempt repair
    assert _scheduled(state) == before  # no long-travel day: the legacy repair changes nothing, as before
    assert len(_dispersion_issues(state)) == 1  # ... and the final validation reports the day


# =====================================================================================
# C. Waterfront
# =====================================================================================


def _classify(tags: dict[str, str], name: str | None = None) -> taxonomy.PlaceClassification:
    return taxonomy.classify_place(tags, None, name) if name is not None else taxonomy.classify_place(tags)


def test_waterfront_is_classified_only_from_provider_evidence() -> None:
    serves = lambda tags: taxonomy.matched_interests(taxonomy.classify_place(tags), ["waterfront", "outdoors"])  # noqa: E731

    assert serves({"man_made": "pier"}) == ["waterfront", "outdoors"]
    assert serves({"leisure": "marina"}) == ["waterfront", "outdoors"]  # raw-tag evidence, when the provider returns it
    assert serves({"natural": "beach"}) == ["waterfront", "outdoors"]
    # the provider's own categories, through the adapter's tag vocabulary
    assert serves(categories.taxonomy_tags_from_categories(["man_made", "man_made.pier"])) == ["waterfront", "outdoors"]
    assert serves(categories.taxonomy_tags_from_categories(["beach", "beach.beach_resort"])) == ["waterfront", "outdoors"]
    # an ordinary park, a viewpoint and a general attraction are not waterfront
    assert serves({"leisure": "park"}) == ["outdoors"]
    assert serves({"tourism": "viewpoint"}) == ["outdoors"]
    assert serves({"tourism": "attraction"}) == []
    # a promenade or coastal path has no category in the verified snapshot: nothing is invented for it
    assert serves({"highway": "pedestrian"}) == []
    assert serves(categories.taxonomy_tags_from_categories(["natural", "natural.coastal"])) == []


def test_waterfront_discovery_uses_only_verified_categories_within_the_same_pool() -> None:
    group = categories.INTEREST_GROUPS["waterfront"]
    assert set(group.categories) <= categories.VERIFIED_TAXONOMY
    # piers, beaches and managed beaches; a marina is mapped for classification but never asked for
    assert set(group.categories) == {"man_made.pier", "beach", "beach.beach_resort"}
    assert "maritime.marina" in categories.VERIFIED_TAXONOMY and "maritime.marina" not in categories.configured_categories()
    assert set(group.categories) <= categories.configured_categories()

    plan = GeoapifyPlacesAdapter._attraction_plan
    broad = plan({"pool_size": 60})
    asked = plan({"pool_size": 60, "interest_groups": ["waterfront"]})
    assert [g.key for g, _ in broad] == [g.key for g in categories.ATTRACTION_GROUPS]
    assert [g.key for g, _ in asked] == [*(g.key for g in categories.ATTRACTION_GROUPS), "waterfront"]
    # the pool is no larger, the places come out of the largest broad group, the others are untouched
    assert sum(limit for _, limit in asked) == sum(limit for _, limit in broad) == 60
    assert dict((g.key, limit) for g, limit in asked) == {
        "sights": 15, "attractions": 9, "culture": 18, "outdoors_heritage_markets": 12, "waterfront": 6,
    }
    # credit-neutral: never more reserved credits than the broad plan
    reserve = lambda rows: sum(places_request_credits(limit) for _, limit in rows)  # noqa: E731
    assert reserve(asked) <= reserve(broad)
    # an expansion page, an unknown interest and no interest ask for the broad groups only
    assert plan({"pool_size": 60, "interest_groups": ["waterfront"], "page": 1}) == broad
    assert plan({"pool_size": 60, "interest_groups": ["quantum chess"]}) == broad
    assert plan({"pool_size": 60, "interest_groups": []}) == broad
    assert taxonomy.discovery_interests(["museum", "waterfront"]) == ["waterfront"]
    assert taxonomy.discovery_interests(["museum", "outdoors"]) == []


def _load_canary() -> Any:
    path = Path(__file__).resolve().parents[3] / "scripts" / "canary_city.py"
    spec = importlib.util.spec_from_file_location("canary_city_under_tuning_corrections", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _waterfront_network(with_supply: bool) -> tuple[Any, list[str]]:
    """The production-like network, plus the provider's answer to the waterfront categories."""
    base, _seen = _production_like_network()
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/places":
            requested = request.url.params["categories"]
            asked.append(requested)
            if "man_made.pier" in requested:
                features = [
                    {"properties": {"place_id": f"pier{i}", "name": f"Landing Stage {i}",
                                    "categories": ["man_made", "man_made.pier"],
                                    "lat": 50.02 + i * 0.001, "lon": 10.02,
                                    "wiki_and_media": {"wikipedia": f"en:Landing Stage {i}"}}}
                    for i in range(3)
                ] if with_supply and int(request.url.params["offset"]) == 0 else []
                return httpx.Response(200, json={"features": features})
        return base(request)

    return handler, asked


def _run_canary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, handler: Any, interests: list[str]) -> dict[str, Any]:
    monkeypatch.setenv("GEOAPIFY_API_KEY", _KEY)
    monkeypatch.setenv("GEOCODING_PROVIDER", "geoapify")
    get_settings.cache_clear()
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    monkeypatch.setattr(provider_gateway, "places", GeoapifyPlacesAdapter(cache_store=store, geocoder=GeoapifyGeocoder()))
    monkeypatch.setattr(provider_gateway, "routing", GeoapifyRoutingAdapter(cache_store=store))
    for slot, stub in (("weather", WeatherProvider()), ("holiday", HolidayProvider()), ("currency", CurrencyProvider())):
        monkeypatch.setattr(provider_gateway, slot, stub)
    return _load_canary()._run(
        argparse.Namespace(city="Testville, Testland", days=3, pace="balanced", interests=interests, must_visit=[]), {}
    )


def test_a_waterfront_request_finds_supply_schedules_it_and_reports_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inventory_sufficiency_gate_enabled: None
) -> None:
    handler, asked = _waterfront_network(with_supply=True)
    report = _run_canary(monkeypatch, tmp_path, handler, ["history", "piers"])

    # discovery: exactly one request for the waterfront categories, inside the usual budget
    assert sum("man_made.pier" in requested for requested in asked) == 1
    usage = report["provider_usage"]
    assert usage["geoapify_calls_refused_by_budget"] == 0 and usage["total_geoapify_credits"] <= usage["geoapify_credit_budget"]
    # supply, eligibility and the final schedule -- not merely the canonical interest
    interests = report["itinerary_quality"]["interests"]
    assert interests["canonical"] == ["history", "waterfront"]
    assert interests["viable_supply_by_interest"]["waterfront"] >= 1
    assert "waterfront" in interests["covered"] and report["quality"]["interest_coverage"]["waterfront"] is True
    stops = report["planning_diagnostics"]["final_stops"]
    waterfront = [stop for stop in stops if "waterfront" in stop["matched_interests"]]
    assert waterfront and all(stop["name"].startswith("Landing Stage") for stop in waterfront)
    assert waterfront[0]["provider_categories"]["man_made"] == "pier"  # classified from the provider's category
    assert waterfront[0]["in_broad_pool"] is True and waterfront[0]["introduced_by"]
    assert report["acceptance"]["outcome"].startswith("PASS")
    # the discovery report: every request by source, and what became of the records
    found = report["planning_diagnostics"]["discovery"]
    attraction_requests = [item for item in found["requests"] if item["kind"] == "attractions"]
    assert [item["source"] for item in attraction_requests] == ["broad"] * 4 + ["destination_local"] * 4 + ["interest_local"]
    assert found["attraction_records_requested"] == sum(item["requested"] for item in attraction_requests)
    assert found["unique_attraction_identities_returned"] <= found["attraction_records_returned"] <= found["attraction_records_requested"]
    assert sum(found["pool_candidates_by_source"].values()) == found["attraction_candidates_in_pool"]
    assert sum(found["scheduled_stops_by_source"].values()) == len(stops)
    assert found["pool_candidates_by_source"].get("interest_local", 0) >= 1  # the waterfront record reached the pool
    # routing by planner pass: one pass here, and its allowance arithmetic adds up
    routing = report["planning_diagnostics"]["routing_by_pass"]
    assert [item["pass"] for item in routing["passes"]] == [1] and routing["route_requests_used_before_the_final_pass"] == 0
    only = routing["passes"][0]
    assert only["route_requests_left_before"] - only["route_requests_left_after"] == only["route_requests_used"] >= 1
    assert only["legs_after_repairs"]["required_legs"] >= only["legs_after_repairs"]["verified_legs"]
    # the diagnostics are reported only: acceptance does not read them
    canary = _load_canary()
    assert canary._acceptance({k: v for k, v in report.items() if k != "planning_diagnostics"}) == report["acceptance"]
    assert "PLANNING DIAGNOSTICS (diagnostic only" in canary._render(report)
    assert "- routing, pass 1 (initial): day-route allowance" in canary._render(report)
    assert _KEY not in json.dumps(report)
    # the benchmark runner stores the canary report through its scrubber: the diagnostics survive it whole
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    spec = importlib.util.spec_from_file_location("benchmark_tuning_cities_under_corrections", scripts / "benchmark_tuning_cities.py")
    tuning = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(tuning)
    plain = json.loads(json.dumps(report, default=str))
    stored = tuning.scrub(plain)
    assert stored["planning_diagnostics"] == plain["planning_diagnostics"]
    assert stored["planning_diagnostics"]["final_pass_stages"] and stored["planning_diagnostics"]["composition"]


def test_without_verified_waterfront_supply_the_interest_stays_honestly_uncovered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inventory_sufficiency_gate_enabled: None
) -> None:
    handler, asked = _waterfront_network(with_supply=False)
    report = _run_canary(monkeypatch, tmp_path, handler, ["history", "piers"])
    assert sum("man_made.pier" in requested for requested in asked) == 1  # it was looked for
    interests = report["itinerary_quality"]["interests"]
    assert interests["uncovered_without_supply"] == ["waterfront"] and "waterfront" not in interests["covered"]
    # a park (the fixture's gardens) never stands in for it
    assert not [s for s in report["planning_diagnostics"]["final_stops"] if "waterfront" in s["matched_interests"]]


def test_no_waterfront_request_means_no_waterfront_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inventory_sufficiency_gate_enabled: None
) -> None:
    handler, asked = _waterfront_network(with_supply=True)
    report = _run_canary(monkeypatch, tmp_path, handler, ["history"])
    assert not any("man_made.pier" in requested for requested in asked)
    assert not [s for s in report["planning_diagnostics"]["final_stops"] if s["name"].startswith("Landing Stage")]


# =====================================================================================
# D. Food coverage
# =====================================================================================


def test_food_is_served_only_by_provider_verified_food_evidence() -> None:
    serves = lambda tags: taxonomy.matched_interests(taxonomy.classify_place(tags), ["food", "shopping"])  # noqa: E731

    assert serves({"amenity": "marketplace"}) == ["shopping"]  # a market: the provider says nothing about food
    assert serves({"amenity": "marketplace", "shop": "florist"}) == ["shopping"]  # a flower market
    assert serves({"amenity": "marketplace", "shop": "greengrocer"}) == ["food", "shopping"]  # a recorded food trade
    assert serves({"amenity": "food_court"}) == ["food", "shopping"]
    assert serves({"amenity": "restaurant"}) == ["food"]
    # a market is still a market for every other purpose
    plain, food = taxonomy.classify_place({"amenity": "marketplace"}), taxonomy.classify_place({"amenity": "marketplace", "shop": "greengrocer"})
    assert plain.primary_category == food.primary_category == taxonomy.FOOD_MARKET
    assert taxonomy.CULINARY in food.categories and taxonomy.CULINARY not in plain.categories


def _food_state(stops: list[Any], food: list[Any]) -> PlanningState:
    fixtures = final_state_fixtures
    return fixtures._with_day(fixtures._state(fixtures._POOL, interests=["food"]), stops, food)


def test_food_coverage_reports_which_evidence_satisfied_it() -> None:
    fixtures = final_state_fixtures
    castle = fixtures._stop(fixtures._CASTLE, matched_interests=["history"])
    culinary = fixtures._stop(fixtures._MARKET, matched_interests=["food", "shopping"])
    generic_market = fixtures._stop(fixtures._MARKET, matched_interests=["shopping"])
    cafe = fixtures._food("Corner Cafe", fixtures._point(50.0005, 10.0005))

    cases = {
        "market_only": ([castle, culinary], [], True, [fixtures._MARKET["name"]], []),
        "restaurant_only": ([castle], [cafe], True, [], ["Corner Cafe"]),
        "mixed": ([castle, culinary], [cafe], True, [fixtures._MARKET["name"]], ["Corner Cafe"]),
        "unsupported_market": ([castle, generic_market], [], False, [], []),
    }
    for label, (stops, food, covered, scheduled, nearby) in cases.items():
        state = _food_state(stops, food)
        evidence = food_coverage_evidence(state)
        assert interest_coverage(state)["food"] is covered, label
        assert evidence == {"scheduled_culinary_stops": scheduled, "nearby_restaurant_suggestions": nearby}, label
        # restaurants that are not on the plan are never reported
        assert evidence["nearby_restaurant_suggestions"] == [s.name for s in state.experience_plan.daily_plans[0].restaurant_suggestions], label


# =====================================================================================
# E. Bounded rejection diagnostics (day composition, interest coverage)
# =====================================================================================


def _compose(days: list[list[Any]], unused: list[Any], **policy: Any) -> Any:
    from app.services.day_composition import compose
    from app.tests.services.test_day_composition_q3 import _policy

    return compose(days, unused, _policy(**policy))


def _dispersed_day() -> list[Any]:
    from app.tests.services.test_day_composition_q3 import _stop

    # one central stop and two that sit together 30 km away: no single replacement can fix the day
    return [_stop("a", 0.0), _stop("far1", _REMOTE_KM), _stop("far2", _REMOTE_KM + 0.2)]


def test_composition_counts_tell_no_candidate_from_inadmissible_from_not_improving() -> None:
    from app.tests.services.test_day_composition_q3 import _stop

    # 1. no candidate available at all
    nothing = _compose([_dispersed_day()], [])
    counts = nothing.diagnostics
    assert not nothing.changed and counts["unused_candidates"] == 0 and counts["unused_in_working_set"] == 0
    assert counts["replace_no_candidate_near_day"] >= 1 and counts["rebuild_no_candidate_near_kept_stops"] >= 1
    assert not any(key.startswith("improving_") for key in counts)

    # 2a. candidates exist near the central stop but may not enter a plan (eligibility / geographic pruning)
    barred = _compose([_dispersed_day()], [_stop(f"n{i}", 0.2 * (i + 1), may_enter=False) for i in range(3)])
    counts = barred.diagnostics
    assert not barred.changed and counts["unused_candidates"] == 3 and counts["unused_not_eligible_to_enter"] == 3
    assert counts["unused_in_working_set"] == 0 and not any(key.startswith("improving_") for key in counts)

    # 2b. candidates may enter, but sit too far below the stops they would replace (usefulness guard)
    weak = _compose([_dispersed_day()], [_stop(f"n{i}", 0.2 * (i + 1), tier=1) for i in range(3)])
    counts = weak.diagnostics
    assert not weak.changed and counts["unused_in_working_set"] == 3
    assert counts.get("rebuild_candidates_failed_guard_or_entry", 0) >= 1
    assert not counts.get("improving_rebuild") and not counts.get("improving_replace")

    # 3. admissible candidates that do not improve an already compact plan
    compact = [_stop("a", 0.0), _stop("b", 0.2), _stop("c", 0.4)]
    settled = _compose([compact], [_stop(f"n{i}", 0.1 * (i + 1)) for i in range(3)])
    counts = settled.diagnostics
    assert not settled.changed and counts["judged_replace"] >= 1 and counts["admissible_not_improving"] >= 1
    assert not any(key.startswith("improving_") for key in counts)

    # 4. an improving admissible replacement exists and is taken
    fixed = _compose([_dispersed_day()], [_stop(f"n{i}", 0.2 * (i + 1)) for i in range(3)])
    counts = fixed.diagnostics
    assert fixed.changed and counts["improving_rebuild"] >= 1 and counts["moves_applied"] == len(fixed.moves) == 1
    assert sorted(fixed.days[0]) == ["a", "n0", "n1"]
    assert counts["evaluation_budget_exhausted"] == 0 and counts["move_limit"] == 3

    # mandatory identities and a spent allowance are counted, not hidden
    pinned = _compose(
        [[_stop("a", 0.0, must_visit=True), _stop("far1", _REMOTE_KM, must_visit=True), _stop("far2", _REMOTE_KM + 0.2)]],
        [_stop("n0", 0.2)],
    )
    assert pinned.diagnostics["replace_blocked_must_visit_stop"] >= 2
    locked = _compose([_dispersed_day()], [_stop("n0", 0.2), _stop("n1", 0.4)], allow_replacement=False)
    assert locked.diagnostics["replacement_pass_skipped_not_allowed"] >= 1 and not locked.changed
    budget = _compose([_dispersed_day()], [_stop("n0", 0.2), _stop("n1", 0.4)], max_evaluations=1)
    assert budget.budget_exhausted and budget.diagnostics["evaluation_budget_exhausted"] == 1


def test_composition_counters_change_neither_the_result_nor_the_move_order() -> None:
    from app.services import day_composition
    from app.tests.services.test_day_composition_q3 import _stop

    def scene() -> tuple[list[list[Any]], list[Any]]:
        days = [
            [_stop("a", 0.0), _stop("far1", _REMOTE_KM), _stop("far2", _REMOTE_KM + 0.2)],
            [_stop("b", 0.3), _stop("c", 4.0), _stop("d", 0.5)],
        ]
        return days, [_stop(f"n{i}", 0.15 * (i + 1), band=i % 3) for i in range(8)]

    counted = _compose(*scene())

    class _Silent(day_composition._Search):  # the same search with its counters switched off
        def _note(self, label: str, amount: int = 1) -> None:
            return None

    original = day_composition._Search
    day_composition._Search = _Silent
    try:
        silent = _compose(*scene())
    finally:
        day_composition._Search = original
    assert silent.days == counted.days and silent.evaluations == counted.evaluations
    assert [(m.kind, m.reason, m.outgoing, m.incoming) for m in silent.moves] == [
        (m.kind, m.reason, m.outgoing, m.incoming) for m in counted.moves
    ]
    assert counted.diagnostics and all(isinstance(value, int) for value in counted.diagnostics.values())
    assert len(counted.diagnostics) <= 64  # bounded: aggregate labels, never one record per candidate


def _pier(key: str, name: str, north: float) -> dict[str, Any]:
    from app.tests.services.test_final_quality_correction_203c2b import _poi

    return _poi(key, name, _at(north), category="pier", provider_tags={"man_made": "pier"})


def _model_plan(pois: list[dict[str, Any]], model_days: list[list[str]], interests: list[str]) -> PlanningState:
    """A plan the reasoning model chose (so the planner's own selection does not cover the
    interest first), with sufficient verified inventory."""
    from app.models.inventory_sufficiency import InventorySufficiencyReport, InventorySufficiencyStatus
    from app.tests.services.test_candidate_usefulness_q2 import _state

    state = _state(pois, days=len(model_days), interests=interests)
    by_key = {poi["place_id"].split("/", 1)[1]: poi for poi in pois}
    state.ai_itinerary_reasoning_result = _completed_result(
        [
            _day(index + 1, [f"geoapify_places:{by_key[key]['place_id']}" for key in day])
            for index, day in enumerate(model_days)
        ]
    )
    state.inventory_sufficiency_report = InventorySufficiencyReport(
        status=InventorySufficiencyStatus.HEALTHY, trip_days=len(model_days), pace="balanced",
        target_stops=3 * len(model_days), minimum_useful=5, healthy_buffer=14, viable_candidates=len(pois),
        message="fixture",
    )
    return state


def test_coverage_diagnostics_separate_discovery_from_schedulability() -> None:
    from app.tests.services.test_candidate_usefulness_q2 import _state

    # Discovery succeeded (a provider-classified pier is in the pool) but it lies 30 km from
    # every day: the coverage pass finds no slot, and says why.
    far_pier = _pier("p0", "Far Landing Stage", _REMOTE_KM)
    state = _model_plan([*_core(8), far_pier], [["c0", "c1", "c2"], ["c3", "c4", "c5"]], ["museum", "piers"])
    _, trace, _ = _traced(state)
    assert interest_coverage(state)["waterfront"] is False
    record = next(item for item in trace["interest_coverage"] if item["interest"] == "waterfront")
    assert record["outcome"] == "no_admissible_slot"
    counts = record["counts"]
    assert counts["unused_candidates_serving_interest"] == counts["eligible_candidates"] == counts["candidates_tried"] == 1
    assert counts["pairs_candidate_too_far_from_day"] >= 1 and "pairs_admissible_target" not in counts
    rejected = record["strongest_rejected"]
    assert [item["place_id"] for item in rejected] == [far_pier["place_id"]]
    assert rejected[0]["nearest_day_km"] == pytest.approx(_REMOTE_KM, abs=1.5)
    assert rejected[0]["reasons"]["candidate_too_far_from_day"] >= 1
    assert "Far Landing Stage" not in json.dumps(trace)  # an id, a tier rank, a distance and counts -- no name

    # Protected stops: a pier next door, but every scheduled stop is a place the traveller asked for.
    from app.models import TripPace

    a, b = _place("a", 0.0, name="Requested A"), _place("b", 0.3, name="Requested B")
    near_pier = _pier("p1", "Near Landing Stage", 0.5)
    protected = _state(
        [a, b, near_pier], days=1, pace=TripPace.RELAXED, interests=["piers"],
        must_visit={"Requested A": a, "Requested B": b},
    )
    _, trace, _ = _traced(protected)
    if interest_coverage(protected)["waterfront"] is False:
        record = next(item for item in trace["interest_coverage"] if item["interest"] == "waterfront")
        assert record["outcome"] == "no_admissible_slot" and record["counts"]["pairs_stop_is_must_visit"] == 2
        assert set(record["strongest_rejected"][0]["reasons"]) == {"stop_is_must_visit"}

    # A near, schedulable pier is scheduled: there is nothing to diagnose.
    covered = _state([*_core(8), _pier("p2", "Near Landing Stage", 0.6)], days=2, interests=["museum", "piers"])
    _, trace, _ = _traced(covered)
    assert interest_coverage(covered)["waterfront"] is True
    assert not [item for item in trace["interest_coverage"] if item["outcome"] != "replaced_a_stop"]


def test_diagnostics_reach_no_provider_and_no_model() -> None:
    # The recorder and the dispersion module import nothing that can make a call ...
    root = Path(__file__).resolve().parents[2]
    for module in ("core/generation_diagnostics.py", "services/geographic_dispersion.py"):
        source = (root / module).read_text()
        assert "providers" not in source and "httpx" not in source and "gateway" not in source, module
    # ... and a traced planner + validator run works with every provider slot removed.
    from app.tests.services.test_candidate_usefulness_q2 import _state

    saved = {slot: getattr(provider_gateway, slot) for slot in ("places", "routing", "weather", "holiday", "currency")}
    try:
        for slot in saved:
            setattr(provider_gateway, slot, None)
        state = _model_plan(
            [*_core(8), _pier("p0", "Far Landing Stage", _REMOTE_KM)], [["c0", "c1", "c2"], ["c3", "c4", "c5"]],
            ["museum", "piers"],
        )
        plan, trace, _ = _traced(state)
        PlanValidatorService().run(state)
        assert plan and trace["composition"] and trace["interest_coverage"]
    finally:
        for slot, value in saved.items():
            setattr(provider_gateway, slot, value)


# =====================================================================================
# F. Ferry warnings: acceptance policy 2
# =====================================================================================


def _ferry_network(with_steps: bool) -> Any:
    """The production-like network; the routing provider flags a ferry step on the first leg of
    each day route when `with_steps`, and says nothing about steps otherwise."""
    base, _seen = _production_like_network()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/routing":
            legs = [{"distance": 400.0, "time": 300.0} for _ in request.url.params["waypoints"].split("|")[1:]]
            if with_steps:
                for index, leg in enumerate(legs):
                    leg["steps"] = [{"ferry": index == 0}]
            return httpx.Response(200, json={"features": [{"properties": {"legs": legs}}]})
        return base(request)

    return handler


def test_a_provider_confirmed_disclosed_ferry_is_informational_under_policy_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inventory_sufficiency_gate_enabled: None
) -> None:
    report = _run_canary(monkeypatch, tmp_path, _ferry_network(with_steps=True), ["history"])
    canary = _load_canary()
    ferry = report["routing"]["ferry_disclosure"]
    assert ferry["provider_confirmed_ferry_legs"] >= 1 and ferry["legs_with_unknown_ferry_status"] == 0
    # disclosed to the traveller on every day that has one, without claiming anything about the service
    assert ferry["days_with_traveller_facing_ferry_warning"] == ferry["provider_confirmed_ferry_days"] != []
    assert ferry["warnings_state_nothing_about_the_service_is_verified"] is True
    assert "ROUTE_INCLUDES_FERRY" in report["quality"]["review_codes"]

    acceptance = report["acceptance"]
    assert acceptance["policy_version"] == 2 and acceptance["informational_codes_accepted"] == ["ROUTE_INCLUDES_FERRY"]
    assert acceptance["outcome"].startswith("PASS")
    # the Q0 list itself is untouched
    assert "ROUTE_INCLUDES_FERRY" not in canary._ACCEPTED_REVIEW_CODES

    def judged(**changes: Any) -> dict[str, Any]:
        altered = json.loads(json.dumps(report, default=str))
        for path, value in changes.items():
            section, key = path.split("__")
            if section == "ferry":
                altered["routing"]["ferry_disclosure"][key] = value
            else:
                altered[section][key] = value
        return canary._acceptance(altered)

    # each condition is necessary: without it the code is an ordinary unaccepted review code again
    for change in (
        {"ferry__provider_confirmed_ferry_legs": 0},
        {"ferry__days_with_traveller_facing_ferry_warning": []},
        {"ferry__warnings_state_nothing_about_the_service_is_verified": False},
        {"ferry__ferry_warnings_without_a_provider_confirmed_leg": 1},
    ):
        verdict = judged(**change)
        assert verdict["outcome"] == "FAIL" and verdict["informational_codes_accepted"] == [], change
    # and it overrides nothing else: any other unaccepted code still fails the plan
    for other in ("LONG_TRAVEL_DAY", "GEOGRAPHIC_DISPERSION", "INTEREST_UNDERCOVERAGE", "MOVEMENT_DATA", "FEASIBILITY"):
        verdict = judged(quality__review_codes=[*report["quality"]["review_codes"], other])
        assert verdict["outcome"] == "FAIL" and verdict["informational_codes_accepted"] == ["ROUTE_INCLUDES_FERRY"], other
        assert other in next(c["check"] for c in verdict["checks"] if not c["passed"])


def test_unknown_ferry_status_is_neither_a_ferry_nor_the_absence_of_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inventory_sufficiency_gate_enabled: None
) -> None:
    report = _run_canary(monkeypatch, tmp_path, _ferry_network(with_steps=False), ["history"])
    ferry = report["routing"]["ferry_disclosure"]
    assert ferry["provider_confirmed_ferry_legs"] == 0 and ferry["provider_confirmed_ferry_days"] == []
    assert ferry["legs_with_unknown_ferry_status"] == report["routing"]["factual_routed_legs"] > 0
    assert "ROUTE_INCLUDES_FERRY" not in report["quality"]["review_codes"]
    assert report["acceptance"]["informational_codes_accepted"] == []
    # a ferry code with no provider-confirmed leg behind it would not be accepted
    canary = _load_canary()
    altered = json.loads(json.dumps(report, default=str))
    altered["quality"]["review_codes"] = [*altered["quality"]["review_codes"], "ROUTE_INCLUDES_FERRY"]
    altered["quality"]["readiness"] = "needs_review"
    verdict = canary._acceptance(altered)
    assert verdict["outcome"] == "FAIL" and verdict["informational_codes_accepted"] == []
