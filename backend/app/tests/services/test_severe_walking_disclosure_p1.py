from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.config import get_settings
from app.models.common import ProviderStatus
from app.models.planning_state import OverLimitWalkingLeg, PlanningState
from app.repositories.planning_state_repository import PlanningStateRepository
from app.services import day_composition as composition
from app.services.plan_validator_service import PlanValidatorService
from app.services.pace_targets import pace_of
from app.services.route_burden import LONG_TRAVEL_DAY, SEVERITY_SEVERE, assess_days, over_limit_walking_legs
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.route_recomposition_service import RouteRecompositionService
from app.storage.local_json_store import LocalJsonStore
from app.tests.services.test_route_recomposition_q4 import (
    _Provider,
    _assert_final_route_state,
    _context,
    _names,
    _place,
    _recompose,
    _routed,
)

# Phase 1 (travel feasibility): severe walking legs. (1) A walk beyond the
# walking-leg limit that is still a walk on the final plan -- no faster way
# was verified -- is named on its day's long-travel finding and listed, for
# presentation only, so the leg itself is never shown as an ordinary walk.
# Nothing claims a vehicle, transit, ferry or other access. (2) Q4 never
# "resolves" such a leg by leaving one of its places alone on a day, where it
# would have no verified leg at all. Synthetic fixtures: at the scripted rate
# 0.01 degree of longitude is a ten-minute walk; the walking-leg limit is 40.

_A = _place("a", 10.000)
_B = _place("b", 10.050)  # a 50-minute walk from a
_C = _place("c", 10.100)
_D = _place("d", 10.105)
_E = _place("e", 10.110)


def _validated(state: PlanningState) -> PlanningState:
    PlanValidatorService().run(state)
    return state


def _long_travel(state: PlanningState, day: int) -> str:
    (finding,) = [
        issue for issue in state.validation_report.warnings
        if issue.category == "long_travel_day" and issue.affected_section == f"experience_plan.daily_plans[{day}]"
    ]
    return finding.message


# -- the disclosure ----------------------------------------------------------------------------


def test_an_over_limit_walk_is_named_on_its_day_and_listed() -> None:
    state = _validated(_routed([_A, _B, _C, _D, _E], [[_A, _B], [_C, _D, _E]], _Provider()))
    assert get_settings().route_burden_max_leg_seconds == 2400

    message = _long_travel(state, 1)
    assert message.endswith(
        " The walk between Museum a and Museum b (about 50 minutes) is longer than a usual walk between stops, "
        "and no faster way of making it was verified."
    )
    # the existing wording is kept word for word in front of it
    assert "no public-transport or taxi time is available" in message
    assert state.validation_report.over_limit_walking_legs == [
        OverLimitWalkingLeg(day_number=1, from_experience_id="exp-geoapify/a", to_experience_id="exp-geoapify/b")
    ]
    assert LONG_TRAVEL_DAY in state.validation_report.review_codes


def test_only_walks_beyond_the_limit_are_listed() -> None:
    at_limit = _place("limit", 10.040)  # exactly 40 minutes: not beyond the limit
    state = _validated(_routed([_A, at_limit, _C, _D], [[_A, at_limit], [_C, _D]], _Provider()))
    assert state.validation_report.over_limit_walking_legs == []
    assert not any(issue.category == "long_travel_day" for issue in state.validation_report.warnings)


def test_a_leg_that_became_a_vehicle_transfer_is_not_listed() -> None:
    state = _routed([_A, _B, _C, _D], [[_A, _B], [_C, _D]], _Provider())
    leg = state.route_feasibility_report.legs[0]
    leg.mode, leg.walking_duration_seconds = "drive", leg.duration_seconds
    assert over_limit_walking_legs(state) == []
    _validated(state)
    assert state.validation_report.over_limit_walking_legs == []
    assert "no faster way" not in " ".join(issue.message for issue in state.validation_report.warnings)


def test_an_unrouted_leg_is_never_listed_and_its_own_disclosure_stays() -> None:
    provider = _Provider()
    provider.unroutable.add((50.0, 10.050))
    requested = {**_B, "must_visit_term": "The B One"}
    state = _validated(_routed([_A, requested, _C, _D], [[_A, requested], [_C, _D]], provider, must_visit=["The B One"]))
    leg = state.route_feasibility_report.legs[0]
    assert (leg.status, leg.duration_seconds, leg.mode) == (ProviderStatus.FAILED, None, None)
    assert state.validation_report.over_limit_walking_legs == []
    assert any(issue.category == "must_visit_route_unverified" for issue in state.validation_report.warnings)


def test_without_a_route_report_the_list_is_unknown_not_empty() -> None:
    state = _routed([_A, _B], [[_A, _B]], _Provider())
    state.route_feasibility_report = None
    assert _validated(state).validation_report.over_limit_walking_legs is None


def test_no_mode_service_or_access_is_claimed() -> None:
    state = _validated(_routed([_A, _B, _C, _D], [[_A, _B], [_C, _D]], _Provider()))
    added = _long_travel(state, 1).split("allow extra time or use local transport.", 1)[1]
    for word in ("drive", "driving", "vehicle", "taxi", "bus", "train", "transit", "ferry", "cable", "funicular",
                 "hike", "trail", "closed", "inaccessible", "cannot be reached"):
        assert word not in added.lower(), word


def test_the_list_survives_save_and_reload(tmp_path: Path) -> None:
    state = _validated(_routed([_A, _B, _C, _D], [[_A, _B], [_C, _D]], _Provider()))
    path = tmp_path / "state.json"
    PlanningStateRepository(store=LocalJsonStore(path)).save(state)
    reloaded = PlanningStateRepository(store=LocalJsonStore(path)).get_by_trip_id(state.trip_id)
    assert reloaded is not None and reloaded is not state
    assert reloaded.validation_report.over_limit_walking_legs == state.validation_report.over_limit_walking_legs
    assert _long_travel(reloaded, 1) == _long_travel(state, 1)


def test_a_report_stored_before_the_field_loads_unchanged() -> None:
    state = _validated(_routed([_A, _B], [[_A, _B]], _Provider()))
    raw = state.validation_report.model_dump(mode="json")
    raw.pop("over_limit_walking_legs")
    loaded = type(state.validation_report).model_validate(raw)
    assert loaded.over_limit_walking_legs is None


# -- Q4 never hides a long leg by isolating one of its places ---------------------------------


def _q3_moves(state: PlanningState, provider: _Provider) -> list[tuple[str, list[list[str]]]]:
    """The moves Q3's own rules admit for the stops of day 1's long leg, as
    Q4 sees the plan -- before Q4 filters any of them."""
    service = RouteRecompositionService(RouteFeasibilityService(gateway=provider))
    service._state, service._context, service._pace = state, None, pace_of(state)
    service._fresh, service._built = {}, {}
    service._prepare_composition()
    days = [[service._scheduled_stop(stop) for stop in day.experiences] for day in state.experience_plan.daily_plans]
    working = composition.working_set(days, service._unused_stops, service._policy)
    day, assessment = state.experience_plan.daily_plans[0], assess_days(state)[0]
    offending = sorted(
        {stop.provider_place_id for index in assessment.offending_legs for stop in day.experiences[index:index + 2]}
    )
    return [(move.kind, move.days) for move in composition.focused_moves(days, working, service._policy, offending)]


def test_a_long_leg_is_not_resolved_by_leaving_a_place_alone_on_a_day() -> None:
    near_b = _place("near_b", 10.051)
    provider = _Provider()
    state = _routed([_A, _B, near_b], [[_A, _B], [near_b]], provider)
    assert assess_days(state)[0].severity == SEVERITY_SEVERE
    # Q3 admits moves that leave `a` or `b` alone on a day; one of them
    # (`b` beside `near_b`, a one-minute leg) would read as "resolved"
    moves = _q3_moves(state, provider)
    assert ("relocate", [["geoapify/a"], ["geoapify/near_b", "geoapify/b"]]) in moves
    assert all(any(len(day) == 1 for day in days) for _, days in moves)
    before = [_names(state, 0), _names(state, 1)]

    (attempt,) = _recompose(state, provider, _context(days=2)).attempts

    assert not attempt.accepted and attempt.operation is None
    assert [_names(state, 0), _names(state, 1)] == before
    # the long walk stays on the plan, and stays disclosed
    _validated(state)
    assert LONG_TRAVEL_DAY in state.validation_report.review_codes
    assert [leg.from_experience_id for leg in state.validation_report.over_limit_walking_legs] == ["exp-geoapify/a"]
    _assert_final_route_state(state)


def test_a_move_that_leaves_both_days_with_legs_is_still_made() -> None:
    """The guard takes nothing else away: two crossed days are still fixed
    by one swap, because every place keeps a verified leg."""
    a1, a2, a3 = _place("a1", 10.000), _place("a2", 10.002), _place("a3", 10.004)
    b1, b2, b3 = _place("b1", 10.100), _place("b2", 10.102), _place("b3", 10.104)
    provider = _Provider()
    state = _routed([a1, a2, a3, b1, b2, b3], [[a1, a2, b1], [b2, b3, a3]], provider)

    (attempt,) = _recompose(state, provider, _context(days=2)).attempts

    assert attempt.accepted and attempt.operation == "swap"
    assert all(len(day.experiences) == 3 for day in state.experience_plan.daily_plans)
    _validated(state)
    assert state.validation_report.over_limit_walking_legs == []
    _assert_final_route_state(state)


def test_a_replacement_is_unaffected_by_the_guard() -> None:
    good = _place("good", 10.004)
    provider = _Provider()
    state = _routed([_A, _B, good], [[_A, _B]], provider)

    (attempt,) = _recompose(state, provider, _context()).attempts

    assert attempt.accepted and attempt.operation == "replace"
    assert _names(state) == ["Museum a", "Museum good"]


@pytest.mark.parametrize("repeat", range(2))
def test_the_guarded_stage_is_deterministic(repeat: int) -> None:
    near_b = _place("near_b", 10.051)
    runs = []
    for _ in range(2):
        provider = _Provider()
        state = _routed([_A, _B, near_b], [[_A, _B], [near_b]], provider)
        attempt = _recompose(state, provider, _context(days=2)).attempts[0]
        runs.append((attempt.reason, attempt.routing_requests, provider.calls, _names(state, 0), _names(state, 1)))
    assert runs[0] == runs[1]
