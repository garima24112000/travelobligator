from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.config import get_settings
from app.core.provider_usage import GenerationProviderContext, route_request_allowance
from app.models.common import ProviderStatus
from app.models.planning_state import PlanningState, UserLock
from app.models.routing import RouteResult
from app.providers import errors
from app.repositories.planning_state_repository import PlanningStateRepository
from app.services.plan_validator_service import PlanValidatorService
from app.services.routability_repair_service import RoutabilityRepairService, apply_routability_repair_safely
from app.storage.local_json_store import LocalJsonStore
from app.tests.services.test_final_quality_correction_203c2b import _poi, _point
from app.tests.services.test_routability_repair_3c2 import (
    _A,
    _B,
    _C,
    _D,
    _E,
    _NEAR,
    _NEAR_TWO,
    _X,
    _RejectingGateway,
    _routed_state,
)

# Phase 1 (travel feasibility): repeated DEFINITIVE routing refusals around a
# protected stop. A two-stop day whose one leg the provider refuses does not
# say which stop is at fault, so the routability repair replaces the
# unprotected one and verifies the replacement against the protected one.
# When that has already failed -- the protected stop has been refused with
# two different places in this generation and never routed at all -- doing
# it again (in a later planner pass, or with a new neighbour) is shown not to
# help: the neighbour is kept, no request is made, and the leg keeps its
# provider code and its traveller-facing disclosure. Synthetic fixtures; no
# city or landmark name.

_REQUESTED = {**_X, "must_visit_term": "The Requested One"}
_REQUESTED_KEY = "geoapify/x"
_TWO_STOP = [[_A, _B, _C], [_D, _REQUESTED]]
_REASON = "protected_stop_repeated_failure"


def _context() -> GenerationProviderContext:
    return GenerationProviderContext.new(trip_days=2)


def _state(gateway: _RejectingGateway, days: list[list[dict[str, Any]]] = _TWO_STOP, **kwargs: Any):
    return _routed_state(gateway, days, unused=kwargs.pop("unused", (_NEAR, _NEAR_TWO)), must_visit=["The Requested One"], **kwargs)


def _repair(state: PlanningState, service: Any, context: GenerationProviderContext | None):
    return RoutabilityRepairService(service).repair(state, context)


def _day_two(state: PlanningState) -> list[str]:
    return [stop.name for stop in state.experience_plan.daily_plans[1].experiences]


def _allowances(context: GenerationProviderContext) -> tuple[int, int, int, int]:
    return (
        context.route_requests_left,
        context.alternate_mode_requests_left,
        context.recomposition_requests_left,
        context.usage_tracker.credits_used(),
    )


def _first_pass(context: GenerationProviderContext) -> tuple[PlanningState, Any, _RejectingGateway]:
    """The repair's ordinary first attempt: the neighbour is replaced and the
    replacement, verified against the requested stop, is refused too."""
    gateway = _RejectingGateway(reason=errors.REASON_NO_ROUTE)
    state, service = _state(gateway)
    attempt = _repair(state, service, context).attempts[0]
    assert (attempt.reason, attempt.verification_attempts, attempt.replaced_place) == (
        "replacement_unroutable", 1, "Museum Delta",
    )
    assert len(gateway.calls) == 1
    return state, service, gateway


# -- the evidence and the first attempt --------------------------------------------------------


def test_the_first_refusal_still_gets_its_one_replacement_attempt() -> None:
    context = _context()
    state, _, _ = _first_pass(context)

    # the requested stop was refused with two different places (its scheduled
    # neighbour, then the replacement); nothing of it was ever routed
    assert context.definitive_failure_partners[_REQUESTED_KEY] == {"geoapify/d", "geoapify/near"}
    assert _REQUESTED_KEY not in context.routed_place_keys
    assert {"geoapify/a", "geoapify/b", "geoapify/c"} <= context.routed_place_keys
    assert _day_two(state) == ["Museum Delta", "Museum Unroutable"]  # nothing accepted


def test_one_refusal_is_never_enough() -> None:
    """A single refused leg proves nothing about either of its stops: with
    one partner on record the replacement is still tried."""
    context = _context()
    gateway = _RejectingGateway(reason=errors.REASON_NO_ROUTE)
    state, service = _state(gateway)
    context.route_requests_left = 0
    first = _repair(state, service, context).attempts[0]
    assert first.reason == "route_budget_exhausted" and gateway.calls == []
    assert context.definitive_failure_partners[_REQUESTED_KEY] == {"geoapify/d"}

    context.route_requests_left = 4
    second = _repair(state, service, context).attempts[0]
    assert (second.reason, second.verification_attempts) == ("replacement_unroutable", 1)
    assert len(gateway.calls) == 1


# -- what a later pass does -----------------------------------------------------------------


def test_a_later_pass_keeps_the_neighbour_and_makes_no_request() -> None:
    context = _context()
    state, service, gateway = _first_pass(context)
    gateway.calls.clear()
    before_plan = state.experience_plan.model_dump(mode="json")
    before_legs = state.route_feasibility_report.model_dump(mode="json")
    before_allowances = _allowances(context)

    report = _repair(state, service, context)

    (attempt,) = report.attempts
    assert (attempt.reason, attempt.accepted, attempt.verification_attempts) == (_REASON, False, 0)
    assert (attempt.suspect_place, attempt.suspect_protection) == ("Museum Unroutable", "must_visit")
    assert attempt.replaced_place is None and attempt.replacement_place is None
    assert (attempt.failed_legs_before, attempt.failed_legs_after) == (1, 1)
    assert gateway.calls == [] and _allowances(context) == before_allowances
    # the plan and every stored leg are exactly as they were
    assert state.experience_plan.model_dump(mode="json") == before_plan
    assert state.route_feasibility_report.model_dump(mode="json") == before_legs


def test_a_new_neighbour_in_a_later_pass_is_kept_without_a_request() -> None:
    context = _context()
    _first_pass(context)

    # a later planner pass put the requested stop beside a different place
    gateway = _RejectingGateway(reason=errors.REASON_NO_ROUTE)
    state, service = _state(gateway, [[_A, _B, _C], [_E, _REQUESTED]])
    before = _allowances(context)

    (attempt,) = _repair(state, service, context).attempts
    assert attempt.reason == _REASON and gateway.calls == [] and _allowances(context) == before
    assert _day_two(state) == ["Museum Epsilon", "Museum Unroutable"]  # the neighbour stays
    # the new refusal is on record too
    assert "geoapify/e" in context.definitive_failure_partners[_REQUESTED_KEY]


def test_a_locked_stop_is_treated_like_a_must_visit() -> None:
    context = _context()
    gateway = _RejectingGateway(reason=errors.REASON_NO_ROUTE)
    state, service = _routed_state(gateway, [[_A, _B, _C], [_D, _X]], unused=(_NEAR, _NEAR_TWO))
    locked = state.experience_plan.daily_plans[1].experiences[1]
    state.user_locks = [UserLock(locked_item_type="experience", locked_item_id=locked.experience_id)]
    assert _repair(state, service, context).attempts[0].reason == "replacement_unroutable"
    gateway.calls.clear()

    (attempt,) = _repair(state, service, context).attempts
    assert (attempt.reason, attempt.suspect_protection) == (_REASON, "user_lock") and gateway.calls == []


def test_an_ordinary_stop_never_gets_the_new_outcome() -> None:
    """Without a protected stop on the day the ambiguous repair runs exactly
    as before, whatever is on record: both end stops may be tried."""
    context = _context()
    # both end stops refused: nothing is ever accepted, so every pass repairs again
    gateway = _RejectingGateway(unroutable=((50.050, 10.050), (50.052, 10.052)), reason=errors.REASON_NO_ROUTE)
    state, service = _routed_state(gateway, [[_A, _B, _C], [_D, _X]], unused=(_NEAR, _NEAR_TWO))
    for _ in range(3):
        gateway.calls.clear()
        attempt = _repair(state, service, context).attempts[0]
        assert (attempt.reason, attempt.verification_attempts) == ("replacement_unroutable", 2)
        assert len(gateway.calls) == 2
    assert len(context.definitive_failure_partners["geoapify/x"]) >= 2  # on record, but not protected


# -- what is never evidence -----------------------------------------------------------------


@pytest.mark.parametrize("reason", ["timeout", "rate_limited", "server", "network"])
def test_transient_failures_are_never_evidence(reason: str) -> None:
    context = _context()
    gateway = _RejectingGateway(reason=reason)
    state, service = _state(gateway)
    for _ in range(3):
        assert _repair(state, service, context).attempts[0].reason == "transient_failure"
    assert context.definitive_failure_partners == {} and gateway.calls == []


def test_a_rejected_multi_waypoint_request_is_not_evidence_about_one_leg() -> None:
    """A three-stop day rejected as one request fails every leg together:
    nothing is recorded until a leg is asked for on its own."""
    context = _context()
    context.route_requests_left = 0  # nothing can be asked for again
    gateway = _RejectingGateway(reason=errors.REASON_NO_ROUTE)
    state, service = _state(gateway, [[_A, _B, _C], [_D, _REQUESTED, _E]])
    assert _repair(state, service, context).attempts[0].reason == "route_budget_exhausted"
    assert context.definitive_failure_partners == {}


def test_a_place_with_any_routed_leg_is_never_treated_as_repeatedly_refused() -> None:
    context = _context()
    # an earlier pass routed the requested stop with its neighbours (full coverage: no repair runs)
    routable = _RejectingGateway(unroutable=())
    state, service = _state(routable, [[_A, _B, _C], [_D, _REQUESTED, _E]])
    assert _repair(state, service, context) is None
    assert _REQUESTED_KEY in context.routed_place_keys

    for _ in range(2):
        gateway = _RejectingGateway(reason=errors.REASON_NO_ROUTE)
        state, service = _state(gateway)
        attempt = _repair(state, service, context).attempts[0]
        assert attempt.reason == "replacement_unroutable" and len(gateway.calls) == 1
    assert len(context.definitive_failure_partners[_REQUESTED_KEY]) >= 2  # on record, but proven routable


def test_a_new_generation_starts_from_no_evidence() -> None:
    """Evidence lives on ONE generation's provider context: the next
    generation's first pass tries the replacement again."""
    _first_pass(_context())
    fresh = _context()
    assert fresh.definitive_failure_partners == {} and fresh.routed_place_keys == set()
    _first_pass(fresh)  # asserts the ordinary attempt and its one request


def test_without_a_generation_context_each_repair_starts_from_nothing() -> None:
    gateway = _RejectingGateway(reason=errors.REASON_NO_ROUTE)
    state, service = _state(gateway)
    for _ in range(2):
        gateway.calls.clear()
        attempt = _repair(state, service, None).attempts[0]
        assert attempt.reason == "replacement_unroutable" and len(gateway.calls) == 1


# -- what is kept: identity, provenance, disclosure -------------------------------------------


def _validated(state: PlanningState) -> list[Any]:
    PlanValidatorService().run(state)
    return [issue for issue in state.validation_report.warnings if issue.category == "must_visit_route_unverified"]


def test_the_must_visit_its_failed_leg_and_its_disclosure_are_kept() -> None:
    context = _context()
    state, service, _ = _first_pass(context)
    findings_before = [(issue.message, issue.affected_section) for issue in _validated(state)]
    assert len(findings_before) == 1

    _repair(state, service, context)

    requested = state.experience_plan.daily_plans[1].experiences[1]
    assert (requested.name, requested.provider_place_id) == ("Museum Unroutable", _REQUESTED_KEY)
    leg = state.route_feasibility_report.legs[-1]
    assert (leg.status, leg.failure_reason, leg.distance_meters, leg.duration_seconds, leg.mode) == (
        ProviderStatus.FAILED, "no_route", None, None, None,
    )
    assert leg.provider == "fixture_routing"
    findings_after = [(issue.message, issue.affected_section) for issue in _validated(state)]
    assert findings_after == findings_before
    message = findings_after[0][0]
    # nothing is said about whether the place itself can be reached
    for word in ("inaccessible", "closed", "cannot be reached", "impossible"):
        assert word not in message
    assert "MUST_VISIT_ROUTE_UNVERIFIED" in state.validation_report.review_codes


def test_the_evidence_never_enters_the_planning_state_and_reload_is_identical(tmp_path: Path) -> None:
    context = _context()
    state, service, _ = _first_pass(context)
    apply_routability_repair_safely(state, service, context)
    PlanValidatorService().run(state)
    dumped = state.model_dump_json()
    assert "definitive_failure_partners" not in dumped and "routed_place_keys" not in dumped

    path = tmp_path / "state.json"
    PlanningStateRepository(store=LocalJsonStore(path)).save(state)
    reloaded = PlanningStateRepository(store=LocalJsonStore(path)).get_by_trip_id(state.trip_id)
    assert reloaded is not None and reloaded is not state
    assert reloaded.routability_repair_report.attempts[0].reason == _REASON
    assert reloaded.experience_plan.model_dump(mode="json") == state.experience_plan.model_dump(mode="json")
    assert reloaded.route_feasibility_report.model_dump(mode="json") == state.route_feasibility_report.model_dump(
        mode="json"
    )
    assert [issue.message for issue in reloaded.validation_report.warnings] == [
        issue.message for issue in state.validation_report.warnings
    ]


def test_the_safe_entry_point_stores_the_new_outcome() -> None:
    context = _context()
    state, service, gateway = _first_pass(context)
    gateway.calls.clear()
    apply_routability_repair_safely(state, service, context)
    assert state.routability_repair_report.attempts[0].reason == _REASON and gateway.calls == []


# -- determinism and unchanged limits ---------------------------------------------------------


def _layout(state: PlanningState) -> list[Any]:
    """The plan and its legs without generated ids."""
    return [
        [(stop.experience_id, stop.provider_place_id, stop.stop_order) for stop in day.experiences]
        for day in state.experience_plan.daily_plans
    ] + [
        (leg.from_experience_id, leg.to_experience_id, leg.status.value, leg.failure_reason, leg.duration_seconds)
        for leg in state.route_feasibility_report.legs
    ]


def _scenario(monkeypatch: pytest.MonkeyPatch, concurrent: bool) -> tuple[Any, ...]:
    monkeypatch.setenv("PROVIDER_IO_CONCURRENCY_ENABLED", "true" if concurrent else "false")
    get_settings.cache_clear()
    try:
        context = _context()
        state, service, gateway = _first_pass(context)
        second = _repair(state, service, context)
        other_gateway = _RejectingGateway(reason=errors.REASON_NO_ROUTE)
        other, other_service = _state(other_gateway, [[_A, _B, _C], [_E, _REQUESTED]])
        third = _repair(other, other_service, context)
        return (
            [attempt.model_dump(mode="json") for attempt in second.attempts],
            [attempt.model_dump(mode="json") for attempt in third.attempts],
            _layout(state),
            _layout(other),
            {key: sorted(value) for key, value in sorted(context.definitive_failure_partners.items())},
            sorted(context.routed_place_keys),
            _allowances(context),
            gateway.calls,
            other_gateway.calls,
        )
    finally:
        get_settings.cache_clear()


def test_serial_and_concurrent_runs_give_the_same_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    serial = _scenario(monkeypatch, concurrent=False)
    assert serial == _scenario(monkeypatch, concurrent=True)
    assert serial == _scenario(monkeypatch, concurrent=False)  # and identical inputs give identical results


def test_request_limits_are_unchanged() -> None:
    settings = get_settings()
    context = GenerationProviderContext.new(trip_days=3)
    assert route_request_allowance(3) == 10 and context.route_requests_left == 10
    assert settings.route_alternate_mode_max_requests_per_generation == 6
    assert settings.route_recomposition_max_requests_per_generation == 8
    assert context.alternate_mode_requests_left == 6 and context.recomposition_requests_left == 8
    assert settings.geoapify_max_concurrent_requests == 4


def test_a_definitive_answer_the_provider_gave_is_kept_on_the_leg() -> None:
    """Both definitive codes are evidence; the repair does not rewrite them."""
    for reason in (errors.REASON_NO_ROUTE, errors.REASON_UNROUTABLE_ENDPOINT):
        context = _context()
        gateway = _RejectingGateway(reason=reason)
        state, service = _state(gateway)
        _repair(state, service, context)
        gateway.calls.clear()
        assert _repair(state, service, context).attempts[0].reason == _REASON and gateway.calls == []
        assert state.route_feasibility_report.legs[-1].failure_reason == reason


def test_far_spare_candidates_change_nothing() -> None:
    """More unused candidates do not buy more attempts."""
    spare = tuple(_poi(f"s{i}", f"Museum Spare {i}", _point(50.051 + 0.0001 * i, 10.051)) for i in range(4))
    context = _context()
    gateway = _RejectingGateway(reason=errors.REASON_NO_ROUTE)
    state, service = _state(gateway, unused=(_NEAR, _NEAR_TWO, *spare))
    _repair(state, service, context)
    gateway.calls.clear()
    assert _repair(state, service, context).attempts[0].reason == _REASON and gateway.calls == []


def test_the_route_result_fixture_is_provider_shaped() -> None:
    """Guard on the fixture itself: a refusal carries no figures and no mode."""
    gateway = _RejectingGateway(reason=errors.REASON_NO_ROUTE)
    (result,) = gateway.get_route_sequence([(50.0, 10.0), (50.052, 10.052)])
    assert isinstance(result, RouteResult)
    assert (result.status, result.distance_meters, result.duration_seconds) == (ProviderStatus.FAILED, None, None)
