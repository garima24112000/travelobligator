from __future__ import annotations

import random
from typing import Any

import pytest

from app.core.config import get_settings
from app.core.provider_usage import GenerationProviderContext
from app.models.common import ProviderStatus
from app.models.planning_state import PlanningState, UserLock
from app.models.routing import RouteResult
from app.providers.routing.geoapify_adapter import _includes_ferry
from app.services import route_recomposition_service as recomposition_module
from app.services.plan_validator_service import PlanValidatorService
from app.services.route_burden import (
    LONG_TRAVEL_DAY,
    SEVERITY_ELEVATED,
    SEVERITY_NORMAL,
    SEVERITY_SEVERE,
    SEVERITY_UNVERIFIED,
    assess_days,
    day_route_burdens,
)
from app.services.route_burden_repair_service import RouteBurdenRepairService, apply_route_burden_repair_safely
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.route_recomposition_service import RouteRecompositionService
from app.tests.services.test_batch1_tuning_fixes_3b import _promoted, _promotion
from app.tests.services.test_candidate_usefulness_q2 import _quality_metrics
from app.tests.services.test_experience_planner_ai_guided import _completed_result, _day
from app.tests.services.test_final_quality_correction_203c2b import _poi, _point, _state

# Phase Q4: route-aware recomposition. Every fixture is synthetic: places on a
# small coordinate grid and a scripted routing provider. No city, landmark,
# real coordinate or real route appears; a "river" is just a line of longitude
# the scripted provider charges extra to cross.

_RATE = 60_000.0  # scripted walking seconds per degree: 0.01 degree = 10 minutes
_TRANSIENT = "timeout"
_UNROUTABLE = "unroutable_endpoint"


class _Routing:
    provider_name = "fixture_routing"


class _Provider:
    """A scripted stand-in for the routing provider, behind the gateway's
    `get_route_sequence`. It keeps the real adapter's contract: a request
    whose legs are all already known is answered from memory with no call,
    no allowance and no credit; any other request takes one unit of the
    generation's route-request allowance and one credit per leg, or is
    refused locally when the allowance is used up."""

    routing = _Routing()

    def __init__(self, river: float | None = None, crossing_seconds: float = 3000.0) -> None:
        self.calls: list[list[tuple[float, float]]] = []
        self.river = river
        self.crossing_seconds = crossing_seconds
        self.unroutable: set[tuple[float, float]] = set()  # the provider's definitive "no"
        self.unavailable: set[tuple[float, float]] = set()  # transient: says nothing about the stop
        self.ferry: set[tuple[float, float]] = set()
        self.down = False
        self._known: dict[tuple[tuple[float, float], tuple[float, float]], RouteResult] = {}

    def _result(self, status: ProviderStatus, reason: str | None = None, **fields: Any) -> RouteResult:
        return RouteResult(provider="fixture_routing", status=status, source="fixture_routing", failure_reason=reason, **fields)

    def _leg(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        seconds = (abs(origin[0] - destination[0]) + abs(origin[1] - destination[1])) * _RATE
        if self.river is not None and (origin[1] < self.river) != (destination[1] < self.river):
            seconds += self.crossing_seconds
        return self._result(
            ProviderStatus.SUCCESS, distance_meters=seconds * 1.2, duration_seconds=seconds, confidence=0.9,
            mode="walk", includes_ferry=bool({origin, destination} & self.ferry),
        )

    def get_route_sequence(self, points: list[tuple[float, float]], provider_context: Any = None) -> list[RouteResult]:
        legs = list(zip(points, points[1:]))
        if all(leg in self._known for leg in legs):
            return [self._known[leg] for leg in legs]
        if provider_context is not None:
            if provider_context.route_requests_left <= 0:
                return [self._result(ProviderStatus.UNAVAILABLE, "budget_exhausted")] * len(legs)
            provider_context.route_requests_left -= 1
            provider_context.usage_tracker.reserve("routing", len(legs)).settle()
        self.calls.append(list(points))
        if self.down or set(points) & self.unavailable:
            return [self._result(ProviderStatus.FAILED, _TRANSIENT)] * len(legs)
        if set(points) & self.unroutable:
            # one multi-waypoint request: the provider rejects it as a whole
            return [self._result(ProviderStatus.FAILED, _UNROUTABLE)] * len(legs)
        results = [self._leg(origin, destination) for origin, destination in legs]
        self._known.update(zip(legs, results))
        return results

    def know(self, *places: dict[str, Any]) -> None:
        """Warm the provider's memory with the legs of an order, as an
        earlier stage of the same generation would have."""
        points = [_xy(place) for place in places]
        self._known.update((leg, self._leg(*leg)) for leg in zip(points, points[1:]))

    def forget(self, origin: dict[str, Any], destination: dict[str, Any]) -> None:
        self._known.pop((_xy(origin), _xy(destination)), None)


def _xy(place: dict[str, Any]) -> tuple[float, float]:
    return (place["coordinates"]["lat"], place["coordinates"]["lng"])


def _place(key: str, lng: float, lat: float = 50.0, **extra: Any) -> dict[str, Any]:
    return _poi(key, f"Museum {key}", _point(lat, lng), **extra)


def _park(key: str, lng: float, lat: float = 50.0) -> dict[str, Any]:
    return _poi(key, f"Park {key}", _point(lat, lng), category="park", provider_tags={"leisure": "park"})


def _routed(
    pois: list[dict[str, Any]],
    days: list[list[dict[str, Any]]],
    provider: _Provider,
    *,
    unverified: list[tuple[dict[str, Any], dict[str, Any]]] | None = None,
    **trip: Any,
) -> PlanningState:
    """A plan with its stored route report, as the routing stage leaves it.
    `unverified` legs are marked as a transient provider failure: the report
    holds no route for them and the provider cannot answer them now."""
    state = _state(pois, days, **trip)
    for origin, destination in unverified or []:
        provider.unavailable.update({_xy(origin)})
    state.route_feasibility_report = RouteFeasibilityService(gateway=provider).build_report(state)
    if unverified:
        # the legs of that day were asked for one by one (as the routability repair does)
        report = state.route_feasibility_report
        service = RouteFeasibilityService(gateway=provider)
        blocked = set(provider.unavailable)
        provider.unavailable.clear()
        for day in state.experience_plan.daily_plans:
            for a, b in zip(day.experiences, day.experiences[1:]):
                pair = (a.coordinates.lat, a.coordinates.lng), (b.coordinates.lat, b.coordinates.lng)
                still_failed = any(pair == (_xy(origin), _xy(destination)) for origin, destination in unverified)
                if still_failed:
                    provider.unavailable.update(blocked)
                fresh = service.route_day_legs([a, b])
                provider.unavailable.clear()
                service.replace_day_legs(report, [a, b], fresh)
    provider.calls.clear()
    return state


def _recompose(state: PlanningState, provider: _Provider, context: GenerationProviderContext | None = None):
    return RouteRecompositionService(RouteFeasibilityService(gateway=provider)).recompose(state, context)


def _names(state: PlanningState, day: int = 0) -> list[str]:
    return [stop.name for stop in state.experience_plan.daily_plans[day].experiences]


def _context(days: int = 1, **overrides: int) -> GenerationProviderContext:
    context = GenerationProviderContext.new(trip_days=days)
    for name, value in overrides.items():
        setattr(context, name, value)
    return context


def _assert_final_route_state(state: PlanningState) -> None:
    """The stored legs are exactly the legs of the days as they now stand:
    one per consecutive pair, in day order, and nothing left over."""
    expected = [
        (a.experience_id, b.experience_id)
        for day in state.experience_plan.daily_plans
        for a, b in zip(day.experiences, day.experiences[1:])
    ]
    stored = [(leg.from_experience_id, leg.to_experience_id) for leg in state.route_feasibility_report.legs]
    assert sorted(stored) == sorted(expected)
    for day in state.experience_plan.daily_plans:
        if any(stop.stop_order is not None for stop in day.experiences):  # a day the stage rebuilt
            assert [stop.stop_order for stop in day.experiences] == list(range(1, len(day.experiences) + 1))
            assert all(stop.day_number == day.day_number for stop in day.experiences)


@pytest.fixture
def setting(monkeypatch: pytest.MonkeyPatch) -> Any:
    def set_value(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield set_value
    get_settings.cache_clear()


# Two stops a few minutes apart, one an hour and a half east, and unused candidates.
_A = _place("a", 10.000)
_B = _place("b", 10.002)
_FAR = _place("far", 10.100)
_GOOD = _place("good", 10.004)
_REMOTE = _place("remote", 10.300)


# =====================================================================================
# 1. Severity is judged from verified legs, one leg at a time
# =====================================================================================


def test_the_four_severity_classes_come_from_the_existing_limits() -> None:
    provider = _Provider()
    calm = _routed([_A, _B, _GOOD], [[_A, _B, _GOOD]], provider)
    assert [a.severity for a in assess_days(calm)] == [SEVERITY_NORMAL]

    # 70 minutes of walking in legs of 35: within the balanced limits, beyond the relaxed day limit
    spread = [_place("s0", 10.000), _place("s1", 10.035), _place("s2", 10.070)]
    assert [a.severity for a in assess_days(_routed(spread, [spread], provider))] == [SEVERITY_ELEVATED]

    severe = _routed([_A, _B, _FAR], [[_A, _B, _FAR]], provider)
    (assessment,) = assess_days(severe)
    assert assessment.severity == SEVERITY_SEVERE and assessment.offending_legs == (1,) and assessment.fully_verified

    unverified = _routed([_A, _B, _GOOD], [[_A, _B, _GOOD]], _Provider(), unverified=[(_B, _GOOD)])
    (assessment,) = assess_days(unverified)
    assert assessment.severity == SEVERITY_UNVERIFIED and assessment.unverified_legs == 1


def test_an_unavailable_leg_does_not_erase_a_verified_severe_leg() -> None:
    lone = _place("u", 9.998)
    state = _routed([lone, _A, _B, _FAR], [[lone, _A, _B, _FAR]], _Provider(), unverified=[(lone, _A)])

    (assessment,) = assess_days(state)

    assert assessment.severity == SEVERITY_SEVERE  # the verified legs alone are over the limit
    assert assessment.unverified_legs == 1 and not assessment.fully_verified
    assert assessment.offending_legs == (2,) and assessment.definitive_failures == 0


def test_a_definitive_refusal_is_told_apart_from_an_unavailable_provider() -> None:
    provider = _Provider()
    provider.unroutable.add(_xy(_GOOD))
    state = _routed([_A, _B, _GOOD], [[_A, _B, _GOOD]], provider)

    (assessment,) = assess_days(state)

    assert assessment.severity == SEVERITY_UNVERIFIED  # not severe, not feasible: unknown
    assert assessment.definitive_failures == 2 and assessment.unverified_legs == 2


# =====================================================================================
# 2. Recomposition from provider evidence
# =====================================================================================


def test_a_long_walking_leg_is_repaired_when_a_replacement_verifies() -> None:
    provider = _Provider()
    state = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], provider)

    report = _recompose(state, provider, _context())

    (attempt,) = report.attempts
    assert attempt.accepted and attempt.operation == "replace"
    assert (attempt.replaced_place, attempt.replacement_place) == ("Museum far", "Museum good")
    assert (attempt.severity_before, attempt.severity_after) == (SEVERITY_SEVERE, SEVERITY_NORMAL)
    assert attempt.after_duration_seconds < attempt.before_duration_seconds
    assert _names(state) == ["Museum a", "Museum b", "Museum good"]
    assert not day_route_burdens(state)[0].long_route
    _assert_final_route_state(state)


def test_without_a_feasible_replacement_the_day_and_its_warning_stay() -> None:
    provider = _Provider()
    state = _routed([_A, _B, _FAR, _REMOTE], [[_A, _B, _FAR]], provider)
    before = _names(state)

    report = _recompose(state, provider, _context())

    (attempt,) = report.attempts
    assert not attempt.accepted and attempt.severity_after == SEVERITY_SEVERE
    assert _names(state) == before and day_route_burdens(state)[0].long_route
    validation = PlanValidatorService().run(state).validation_report
    assert LONG_TRAVEL_DAY in validation.review_codes and validation.readiness_status.value != "ready"


def test_a_compact_day_split_by_a_barrier_is_repaired_from_the_providers_route_not_the_map() -> None:
    # the "river" runs along lng 10.005; b -> c is 650 m as the crow flies and almost an hour on foot
    provider = _Provider(river=10.005)
    c = _place("c", 10.008)
    across = _place("across", 10.0055)  # the nearest unused place by straight line: on the far bank
    same_bank = _place("same_bank", 9.997)
    state = _routed([_A, _B, c, across, same_bank], [[_A, _B, c]], provider)
    assert assess_days(state)[0].severity == SEVERITY_SEVERE

    (attempt,) = _recompose(state, provider, _context()).attempts

    # the straight-line favourite was routed, found just as bad, and not used
    assert attempt.accepted and attempt.replacement_place == "Museum same_bank"
    assert attempt.candidates_verified >= 2 and attempt.severity_after == SEVERITY_NORMAL
    assert _names(state) == ["Museum a", "Museum b", "Museum same_bank"]
    assert any(_xy(across) in call for call in provider.calls)
    _assert_final_route_state(state)


def test_crossed_days_are_fixed_by_one_swap_verified_on_both_days() -> None:
    a1, a2, a3 = _place("a1", 10.000), _place("a2", 10.002), _place("a3", 10.004)
    b1, b2, b3 = _place("b1", 10.100), _place("b2", 10.102), _place("b3", 10.104)
    provider = _Provider()
    state = _routed([a1, a2, a3, b1, b2, b3], [[a1, a2, b1], [b2, b3, a3]], provider)
    assert [a.severity for a in assess_days(state)] == [SEVERITY_SEVERE, SEVERITY_SEVERE]

    report = _recompose(state, provider, _context(days=2))

    (attempt,) = report.attempts  # the second day needed no attempt of its own
    assert attempt.accepted and attempt.operation == "swap" and attempt.other_day_number is not None
    assert [sorted(_names(state, day)) for day in (0, 1)] == [
        ["Museum a1", "Museum a2", "Museum a3"], ["Museum b1", "Museum b2", "Museum b3"],
    ]
    assert [a.severity for a in assess_days(state)] == [SEVERITY_NORMAL, SEVERITY_NORMAL]
    # every changed leg of BOTH days was routed by the provider, and the stored legs are the final ones
    routed_points = {point for call in provider.calls for point in call}
    assert {_xy(a3), _xy(b1)} <= routed_points and len(provider.calls) == 2
    _assert_final_route_state(state)


def test_a_material_improvement_that_leaves_the_day_severe_keeps_its_warning() -> None:
    # thin inventory: no unused candidate at all. Reordering halves the walking; one long leg remains.
    provider = _Provider()
    state = _routed([_A, _FAR, _B], [[_A, _FAR, _B]], provider)

    (attempt,) = _recompose(state, provider, _context()).attempts

    assert attempt.accepted and attempt.operation == "reorder"
    assert attempt.after_duration_seconds <= 0.85 * attempt.before_duration_seconds
    assert attempt.severity_after == SEVERITY_SEVERE and attempt.hard_walking_violation_remains
    assert sorted(_names(state)) == ["Museum a", "Museum b", "Museum far"]  # nobody dropped, nobody invented
    assert day_route_burdens(state)[0].long_route
    validation = PlanValidatorService().run(state).validation_report
    assert LONG_TRAVEL_DAY in validation.review_codes  # improved is not resolved
    _assert_final_route_state(state)


def test_a_change_that_is_not_materially_better_is_not_made() -> None:
    # the only other place is nearly as far: a few per cent better, below the materiality ratio
    nearly = _place("nearly", 10.095)
    provider = _Provider()
    state = _routed([_A, _B, _FAR, nearly], [[_A, _B, _FAR]], provider)

    (attempt,) = _recompose(state, provider, _context()).attempts

    assert not attempt.accepted and attempt.reason == "no_material_improvement"
    assert _names(state) == ["Museum a", "Museum b", "Museum far"]


def test_equal_alternatives_are_decided_deterministically_whatever_the_pool_order() -> None:
    twins = [_place("twin_n", 10.004, lat=50.001), _place("twin_s", 10.004, lat=49.999)]

    def chosen(seed: int) -> str:
        pois = [_A, _B, _FAR, *twins]
        random.Random(seed).shuffle(pois)
        provider = _Provider()
        state = _routed(pois, [[_A, _B, _FAR]], provider)
        return _recompose(state, provider, _context()).attempts[0].replacement_place

    assert len({chosen(seed) for seed in range(6)}) == 1


def test_a_proposal_the_provider_cannot_route_is_never_introduced() -> None:
    off_network = _place("off_network", 10.003)  # nearer than the good one; the provider cannot route to it
    provider = _Provider()
    provider.unroutable.add(_xy(off_network))
    state = _routed([_A, _B, _FAR, off_network, _GOOD], [[_A, _B, _FAR]], provider)

    (attempt,) = _recompose(state, provider, _context()).attempts

    assert attempt.accepted and attempt.replacement_place == "Museum good"
    assert assess_days(state)[0].definitive_failures == 0


# =====================================================================================
# 3. Partial route coverage
# =====================================================================================


def test_a_verified_severe_leg_on_a_partly_routed_day_is_repaired_and_the_rest_stays_unverified(setting: Any) -> None:
    lone = _place("u", 9.998)
    provider = _Provider()
    state = _routed([lone, _A, _B, _FAR, _GOOD], [[lone, _A, _B, _FAR]], provider, unverified=[(lone, _A)])
    provider.unavailable.add(_xy(lone))  # the provider still cannot answer for that leg

    (attempt,) = _recompose(state, provider, _context()).attempts

    assert attempt.accepted and attempt.replacement_place == "Museum good"
    # the verified burden is gone; the day is NOT thereby fully verified
    assert attempt.severity_after == SEVERITY_UNVERIFIED and attempt.unverified_legs_after == 1
    (assessment,) = assess_days(state)
    assert not assessment.verified_severe and not assessment.fully_verified
    # the leg nobody could route is still in the report, still without a route
    leg = next(leg for leg in state.route_feasibility_report.legs if leg.from_experience_name == "Museum u")
    assert leg.duration_seconds is None and leg.status != ProviderStatus.SUCCESS
    _assert_final_route_state(state)
    validation = PlanValidatorService().run(state).validation_report
    assert validation.readiness_status.value != "ready"  # never presented as fully feasible

    # the single-attempt repair it replaces would not look at such a day at all
    setting("ROUTE_RECOMPOSITION_ENABLED", "false")
    legacy = _routed([lone, _A, _B, _FAR, _GOOD], [[lone, _A, _B, _FAR]], _Provider(), unverified=[(lone, _A)])
    assert RouteBurdenRepairService(RouteFeasibilityService(gateway=_Provider())).repair(legacy).attempts[0].reason == (
        "incomplete_route_data"
    )


def test_a_move_may_not_add_an_unverified_leg() -> None:
    # the only candidate cannot be reached by the provider right now (transient): no evidence, no change
    provider = _Provider()
    state = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], provider)
    provider.unavailable.add(_xy(_GOOD))

    (attempt,) = _recompose(state, provider, _context()).attempts

    assert not attempt.accepted and _names(state) == ["Museum a", "Museum b", "Museum far"]
    assert assess_days(state)[0].unverified_legs == 0


# =====================================================================================
# 4. Protection: must-visits and locks, not anchors
# =====================================================================================


def _must_visit(place: dict[str, Any], term: str) -> dict[str, Any]:
    return {**place, "must_visit_term": term}


def test_a_must_visit_behind_a_hard_transfer_is_kept_and_reported() -> None:
    far = _must_visit(_FAR, "the far museum")
    provider = _Provider()
    state = _routed([_A, _B, far, _GOOD], [[_A, _B, far]], provider, must_visit=["the far museum"])

    (attempt,) = _recompose(state, provider, _context()).attempts

    assert "Museum far" in _names(state) and attempt.replaced_place != "Museum far"
    assert attempt.stop_protections[2] == "must_visit"
    assert day_route_burdens(state)[0].long_route  # the hard transfer is real and is still reported

    # when every stop on the long leg is one the traveller asked for, the report and the validator say so
    near = _must_visit(_A, "the near museum")
    both = _routed([near, far, _GOOD], [[near, far]], _Provider(), must_visit=["the near museum", "the far museum"])
    both.route_burden_repair_report = _recompose(both, _Provider(), _context())
    (attempt,) = both.route_burden_repair_report.attempts
    assert attempt.reason == "protected_stops_only" and not attempt.accepted
    warning = next(
        issue for issue in PlanValidatorService().run(both).validation_report.warnings if issue.category == "long_travel_day"
    )
    assert "places you asked for or locked" in warning.suggested_fix


def test_a_locked_stop_is_neither_replaced_nor_moved() -> None:
    a1, a2 = _place("a1", 10.000), _place("a2", 10.002)
    b1, b2, b3 = _place("b1", 10.100), _place("b2", 10.102), _place("b3", 10.104)
    provider = _Provider()
    state = _routed([a1, a2, b1, b2, b3, _GOOD], [[a1, a2, b1], [b2, b3]], provider)
    state.user_locks = [UserLock(locked_item_type="experience", locked_item_id="exp-geoapify/b1")]

    report = _recompose(state, provider, _context(days=2))

    assert "Museum b1" in _names(state, 0)  # still on its day
    assert report.attempts[0].stop_protections[2] == "user_lock"


def test_a_grounded_anchor_is_replaceable_here_as_in_q3(setting: Any) -> None:
    def state_with_anchor(provider: _Provider) -> PlanningState:
        state = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], provider)
        state.ai_candidate_promotion_report = _promotion(
            [_promoted("far", "Museum far", _point(50.0, 10.100), category="museum", provider_tags={"tourism": "museum"})]
        )
        return state

    provider = _Provider()
    state = state_with_anchor(provider)
    (attempt,) = _recompose(state, provider, _context()).attempts
    assert attempt.accepted and attempt.replaced_place == "Museum far"

    # the single-attempt repair protected it
    legacy = state_with_anchor(_Provider())
    (attempt,) = RouteBurdenRepairService(RouteFeasibilityService(gateway=_Provider())).repair(legacy).attempts
    assert attempt.stop_protections[2] == "grounded_anchor" and "Museum far" in _names(legacy)


def test_the_only_stop_serving_a_requested_interest_is_not_traded_for_a_shorter_route() -> None:
    park = _park("park", 10.100)

    def repaired(*unused: dict[str, Any]) -> list[str]:
        provider = _Provider()
        state = _routed([_A, _B, park, *unused], [[_A, _B, park]], provider, interests=["outdoors"])
        # as the planner records it on every scheduled stop
        scheduled_park = state.experience_plan.daily_plans[0].experiences[2]
        scheduled_park.matched_interests, scheduled_park.normalized_category = ["outdoors"], "park_nature"
        _recompose(state, provider, _context())
        return _names(state)

    assert "Park park" in repaired(_GOOD)  # a museum next door does not buy out the only park
    assert repaired(_GOOD, _park("near_park", 10.004)) == ["Museum a", "Museum b", "Park near_park"]


# =====================================================================================
# 5. Budgets: shared allowances, whole-move reservation, cached legs
# =====================================================================================


def _five_severe_days() -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    pois: list[dict[str, Any]] = []
    days: list[list[dict[str, Any]]] = []
    for index in range(5):
        lat = 50.0 + index  # each day in its own distant group
        near = [_place(f"d{index}a", 10.000, lat), _place(f"d{index}b", 10.002, lat)]
        far = _place(f"d{index}far", 10.100, lat)
        spare = [_place(f"d{index}s{n}", 10.004 + 0.002 * n, lat) for n in range(3)]
        pois += [*near, far, *spare]
        days.append([*near, far])
    return pois, days


def test_the_stage_never_makes_more_than_its_eight_requests_and_stops_deterministically() -> None:
    def run() -> tuple[int, list[list[str]], Any]:
        pois, days = _five_severe_days()
        provider = _Provider()
        state = _routed(pois, days, provider)
        context = _context(days=5, route_requests_left=100)
        report = _recompose(state, provider, context)
        return len(provider.calls), [_names(state, day) for day in range(5)], (report, context)

    calls, plan, (report, context) = run()

    assert calls <= 8 and report.recomposition_requests_used == calls and report.recomposition_requests_cap == 8
    assert context.recomposition_requests_left == 8 - calls and context.route_requests_left == 100 - calls
    assert sum(attempt.accepted for attempt in report.attempts) == 5  # one request per day was enough here
    assert run()[1] == plan  # deterministic


def test_the_shared_route_allowance_binds_before_the_stage_cap() -> None:
    pois, days = _five_severe_days()
    provider = _Provider()
    state = _routed(pois, days, provider)
    context = _context(days=5, route_requests_left=2)

    report = _recompose(state, provider, context)

    assert len(provider.calls) == 2 and context.route_requests_left == 0
    assert sum(attempt.accepted for attempt in report.attempts) == 2
    unrepaired = [attempt for attempt in report.attempts if not attempt.accepted]
    assert len(unrepaired) == 3 and {attempt.reason for attempt in unrepaired} == {"route_budget_exhausted"}
    assert all(attempt.candidates_skipped_for_budget > 0 and attempt.routing_requests == 0 for attempt in unrepaired)


def test_a_move_that_cannot_be_verified_in_full_is_skipped_never_verified_in_part() -> None:
    a1, a2, a3 = _place("a1", 10.000), _place("a2", 10.002), _place("a3", 10.004)
    b1, b2, b3 = _place("b1", 10.100), _place("b2", 10.102), _place("b3", 10.104)
    provider = _Provider()
    state = _routed([a1, a2, a3, b1, b2, b3], [[a1, a2, b1], [b2, b3, a3]], provider)
    before = [_names(state, day) for day in (0, 1)]
    # the swap that fixes both days needs two requests (one per day); one is left
    context = _context(days=2, route_requests_left=1)

    report = _recompose(state, provider, context)

    assert [_names(state, day) for day in (0, 1)] == before
    assert all(len(call) <= 3 for call in provider.calls)
    assert sum(attempt.candidates_skipped_for_budget for attempt in report.attempts) >= 1
    # no day was left half-verified: the stored legs are still those of the stored days
    _assert_final_route_state(state)
    assert [a.unverified_legs for a in assess_days(state)] == [0, 0]


def test_the_credit_cap_is_respected_before_dispatch() -> None:
    provider = _Provider()
    state = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], provider)
    context = GenerationProviderContext.new(trip_days=1, budget=0)  # no credit left at all

    (attempt,) = _recompose(state, provider, context).attempts

    assert provider.calls == [] and not attempt.accepted and attempt.reason == "route_budget_exhausted"


def test_legs_the_provider_already_knows_cost_no_request_and_no_credit() -> None:
    provider = _Provider()
    state = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], provider)
    provider.know(_B, _GOOD)  # e.g. routed earlier in this generation, or in the provider cache
    context = _context(route_requests_left=1)
    spent = context.usage_tracker.credits_used()

    report = _recompose(state, provider, context)

    assert report.attempts[0].accepted and _names(state) == ["Museum a", "Museum b", "Museum good"]
    assert provider.calls == [] and report.recomposition_requests_used == 0
    assert context.route_requests_left == 1 and context.recomposition_requests_left == 8
    assert context.usage_tracker.credits_used() == spent


def test_a_leg_verified_for_one_proposal_is_reused_by_the_next() -> None:
    provider = _Provider(river=10.005)
    c = _place("c", 10.008)
    state = _routed(
        [_A, _B, c, _place("across", 10.0055), _place("same_bank", 9.997)], [[_A, _B, c]], provider
    )

    report = _recompose(state, provider, _context())

    # each proposal changed one leg; no leg was asked for twice
    assert len(provider.calls) == len({tuple(call) for call in provider.calls}) == report.recomposition_requests_used


# =====================================================================================
# 6. Provider failure and fallback
# =====================================================================================


def test_a_failing_provider_changes_nothing_and_ends_the_stage_early() -> None:
    pois, days = _five_severe_days()
    provider = _Provider()
    state = _routed(pois, days, provider)
    before = [_names(state, day) for day in range(5)]
    provider.down = True

    report = _recompose(state, provider, _context(days=5, route_requests_left=100))

    assert [_names(state, day) for day in range(5)] == before
    assert len(provider.calls) == 2  # two transient failures in a row: nothing more is asked
    assert {attempt.reason for attempt in report.attempts} == {"replacement_route_unavailable"}
    assert all(day.long_route for day in day_route_burdens(state))  # every finding is kept
    _assert_final_route_state(state)


def test_without_a_severe_day_nothing_is_asked_and_nothing_is_reported() -> None:
    provider = _Provider()
    state = _routed([_A, _B, _GOOD], [[_A, _B, _GOOD]], provider)

    assert _recompose(state, provider, _context()) is None and provider.calls == []

    # a day that is only unverified is not evidence of a burden: nothing is recomposed
    unknown = _routed([_A, _B, _GOOD, _FAR], [[_A, _B, _GOOD]], _Provider(), unverified=[(_B, _GOOD)])
    assert _recompose(unknown, _Provider(), _context()) is None


def test_an_unexpected_error_never_fails_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _Provider()
    state = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], provider)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("fixture")

    monkeypatch.setattr(RouteRecompositionService, "recompose", broken)
    apply_route_burden_repair_safely(state, RouteFeasibilityService(gateway=provider))

    assert state.route_burden_repair_report is None and _names(state) == ["Museum a", "Museum b", "Museum far"]


# =====================================================================================
# 7. The shortlist
# =====================================================================================


def _shortlisted(state: PlanningState, provider: _Provider, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    kinds: list[str] = []
    original = RouteRecompositionService._shortlist

    def spy(self: RouteRecompositionService, candidates: list[Any]) -> list[Any]:
        chosen = original(self, candidates)
        kinds.extend(candidate.kind for candidate in chosen)
        return chosen

    monkeypatch.setattr(RouteRecompositionService, "_shortlist", spy)
    _recompose(state, provider, _context(days=len(state.experience_plan.daily_plans)))
    return kinds[: recomposition_module.SHORTLIST_SIZE]


def test_the_shortlist_is_bounded_and_represents_more_than_one_kind_of_move(monkeypatch: pytest.MonkeyPatch) -> None:
    a1, a2, a3 = _place("a1", 10.000), _place("a2", 10.002), _place("a3", 10.004)
    b1, b2, b3 = _place("b1", 10.100), _place("b2", 10.102), _place("b3", 10.104)
    spares = [_place(f"spare{n}", 10.006 + 0.001 * n) for n in range(4)]
    provider = _Provider()
    state = _routed([a1, a2, a3, b1, b2, b3, *spares], [[a1, a2, b1], [b2, b3, a3]], provider)

    kinds = _shortlisted(state, provider, monkeypatch)

    groups = {recomposition_module._KIND_GROUP[kind] for kind in kinds}
    assert len(kinds) == recomposition_module.SHORTLIST_SIZE == 3
    assert len(groups) >= 2 and recomposition_module.REPLACE in groups and "cross_day" in groups


def test_a_kind_of_move_is_not_shortlisted_just_to_be_represented(monkeypatch: pytest.MonkeyPatch) -> None:
    # one day, so no cross-day move exists; every reorder is materially LONGER by the estimate
    # (the middle stop is 1.4 km along the way, so any other order adds more than a kilometre)
    middle = _place("middle", 10.020)
    spares = [_place(f"spare{n}", 10.022 + 0.001 * n) for n in range(4)]
    provider = _Provider()
    state = _routed([_A, middle, _FAR, *spares], [[_A, middle, _FAR]], provider)

    kinds = _shortlisted(state, provider, monkeypatch)

    assert kinds and set(kinds) == {recomposition_module.REPLACE}


def test_the_straight_line_estimate_only_orders_proposals() -> None:
    source = open(recomposition_module.__file__).read()
    judge = source.split("def _judge(", 1)[1].split("    # -- applying", 1)[0]
    for proxy in ("proxy", "path_length", "haversine", "_path_m", "extent"):
        assert proxy not in judge, proxy  # acceptance reads provider legs only


# =====================================================================================
# 8. Final state, both planning paths, rollback
# =====================================================================================


def test_the_validator_sees_only_the_final_days_and_their_current_legs() -> None:
    provider = _Provider()
    state = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], provider)
    _recompose(state, provider, _context())

    legs = state.route_feasibility_report.legs
    assert all("far" not in leg.from_experience_id and "far" not in leg.to_experience_id for leg in legs)
    _assert_final_route_state(state)
    validation = PlanValidatorService().run(state).validation_report
    assert not any(issue.category == "long_travel_day" for issue in validation.warnings)
    assert LONG_TRAVEL_DAY not in validation.review_codes


def test_a_model_chosen_plan_and_a_deterministic_one_are_recomposed_alike() -> None:
    def repaired(with_model_result: bool) -> list[str]:
        provider = _Provider()
        state = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], provider)
        if with_model_result:
            state.ai_itinerary_reasoning_result = _completed_result(
                [_day(1, [f"geoapify_places:{poi['place_id']}" for poi in (_A, _B, _FAR)])]
            )
        apply_route_burden_repair_safely(state, RouteFeasibilityService(gateway=provider), _context())
        assert state.route_burden_repair_report.attempts[0].accepted
        return _names(state)

    assert repaired(True) == repaired(False) == ["Museum a", "Museum b", "Museum good"]


def test_switched_off_the_single_attempt_repair_runs_exactly_as_before(
    setting: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    setting("ROUTE_RECOMPOSITION_ENABLED", "false")

    def unreachable(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("route recomposition must not run when it is switched off")

    monkeypatch.setattr(RouteRecompositionService, "recompose", unreachable)
    provider = _Provider()
    state = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], provider)
    expected = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], _Provider())
    RouteBurdenRepairService(RouteFeasibilityService(gateway=_Provider())).repair(expected)

    apply_route_burden_repair_safely(state, RouteFeasibilityService(gateway=provider))

    assert _names(state) == _names(expected)
    report = state.route_burden_repair_report
    assert report.recomposition_requests_cap is None and report.attempts[0].operation is None

    # the master switch still turns both off
    setting("ROUTE_BURDEN_REPAIR_ENABLED", "false")
    setting("ROUTE_RECOMPOSITION_ENABLED", "true")
    untouched = _routed([_A, _B, _FAR, _GOOD], [[_A, _B, _FAR]], _Provider())
    apply_route_burden_repair_safely(untouched, RouteFeasibilityService(gateway=_Provider()))
    assert untouched.route_burden_repair_report is None


def test_metrics_keep_verified_route_figures_apart_from_straight_line_proxies() -> None:
    metrics_module = _quality_metrics()
    provider = _Provider(river=10.005)
    c = _place("c", 10.008)
    state = _routed([_A, _B, c, _place("across", 10.0055), _place("same_bank", 9.997)], [[_A, _B, c]], provider)
    before = metrics_module.extract_quality_metrics(state)["route_severity"]
    assert before["days_by_severity"] == {SEVERITY_SEVERE: 1}
    # the barrier shows as provider distance many times the straight line -- a ratio, not a duration
    assert before["max_route_distance_over_straight_line_ratio"] > 3

    state.route_burden_repair_report = _recompose(state, provider, _context())
    metrics = metrics_module.extract_quality_metrics(state)
    severity, geography = metrics["route_severity"], metrics["day_geography"]

    assert metrics["schema_version"] == 5  # additive since 4: dispersion causes, food evidence
    assert severity["days_by_severity"] == {SEVERITY_NORMAL: 1}
    assert severity["days"][0]["fully_verified"] and severity["days"][0]["verified_transfer_seconds"] > 0
    recomposition = severity["recomposition"]
    assert recomposition["accepted_changes"] == 1 and recomposition["accepted_but_still_severe"] == 0
    assert 0 < recomposition["requests_used"] <= recomposition["requests_cap"] == 8
    assert "VERIFIED legs only" in severity["meaning"]
    # the Q3 block is still labelled as a proxy and carries no duration
    assert "never a route length" in geography["measure"]
    assert not any("seconds" in key for key in geography["days"][0])


# =====================================================================================
# 9. Ferries: the provider's own statement, nothing more
# =====================================================================================


def test_the_ferry_flag_is_read_from_the_documented_step_field_only() -> None:
    assert _includes_ferry({"steps": [{"distance": 10}, {"distance": 900, "ferry": True}]}) is True
    assert _includes_ferry({"steps": [{"distance": 10}, {"distance": 900, "ferry": False}]}) is False
    assert _includes_ferry({"steps": [{"distance": 10}]}) is False
    # no step data is no statement: never read as "no ferry"
    for silent in ({}, {"steps": []}, {"steps": None}, {"steps": ["x"]}, None):
        assert _includes_ferry(silent) is None


def test_a_ferry_in_the_providers_route_is_carried_to_the_leg_and_reported_without_invention() -> None:
    island = _place("island", 10.006)
    provider = _Provider()
    provider.ferry.add(_xy(island))
    state = _routed([_A, _B, island], [[_A, _B, island]], provider)

    leg = state.route_feasibility_report.legs[-1]
    assert leg.includes_ferry is True and assess_days(state)[0].ferry_legs == 1
    validation = PlanValidatorService().run(state).validation_report
    warning = next(issue for issue in validation.warnings if issue.category == "route_includes_ferry")
    assert "Museum b and Museum island" in warning.message and "ROUTE_INCLUDES_FERRY" in validation.review_codes
    assert validation.readiness_status.value != "ready"
    # only the fact is stated: no schedule, price or booking is offered
    assert "No ferry timetable, fare, ticket or availability is known" in warning.message
    for invented in ("departs", "every ", "minutes by ferry", "book", "€", "$"):
        assert invented not in warning.message

    # a provider that says nothing about ferries produces no such finding either way
    silent = _routed([_A, _B, _GOOD], [[_A, _B, _GOOD]], _Provider())
    for stored in silent.route_feasibility_report.legs:
        stored.includes_ferry = None
    assert not any(
        issue.category == "route_includes_ferry" for issue in PlanValidatorService().run(silent).validation_report.warnings
    )
