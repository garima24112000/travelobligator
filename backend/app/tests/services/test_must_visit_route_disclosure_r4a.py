from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models.common import ProviderStatus, ReadinessStatus, ValidationSeverity
from app.models.planning_state import PlanningState
from app.models.routing import RouteResult
from app.repositories.planning_state_repository import PlanningStateRepository
from app.services import plan_validator_service as validator_module
from app.services import review_classification as classification
from app.services.plan_validator_service import PlanValidatorService
from app.services.route_feasibility_service import RouteFeasibilityService
from app.storage.local_json_store import LocalJsonStore
from app.tests.services.test_candidate_usefulness_q2 import _museum, _planned, _state
from app.tests.services.test_must_visit_access_disclosure_r2a import _clustered, _day_of

# Remediation R4a: a scheduled grounded must-visit on a leg the routing
# provider DEFINITIVELY failed to route (`no_route` / `unroutable_endpoint`)
# is kept and the traveller is told, on that day, which leg is not verified.
# Disclosure only. Synthetic, provider-shaped fixtures; no city or landmark name.

_CATEGORY = "must_visit_route_unverified"
_CODE = _CATEGORY.upper()


class _Routing:
    provider_name = "fixture_routing"


class _Gateway:
    """Routes each day as one sequence. A leg named in `failures` comes back
    failed with that status and reason; every other leg gets a real route.
    No alternate (driving) mode is offered."""

    routing = _Routing()

    def __init__(self, state: PlanningState, failures: dict[tuple[str, str], tuple[ProviderStatus, str | None]]) -> None:
        self._names = {
            (stop.coordinates.lat, stop.coordinates.lng): stop.name
            for day in state.experience_plan.daily_plans
            for stop in day.experiences
        }
        self._failures = failures

    def get_route_sequence(self, points: list[tuple[float, float]], provider_context: Any = None) -> list[RouteResult]:
        results = []
        for origin, destination in zip(points, points[1:]):
            failure = self._failures.get((self._names[origin], self._names[destination]))
            if failure is None:
                results.append(
                    RouteResult(
                        provider="fixture_routing", status=ProviderStatus.SUCCESS, distance_meters=500.0,
                        duration_seconds=400.0, source="fixture_routing", mode="walk",
                    )
                )
            else:
                status, reason = failure
                results.append(
                    RouteResult(
                        provider="fixture_routing", status=status, source="fixture_routing", failure_reason=reason
                    )
                )
        return results


def _routed(state: PlanningState, failures: dict[tuple[str, str], tuple[ProviderStatus, str | None]]) -> PlanningState:
    state.route_feasibility_report = RouteFeasibilityService(gateway=_Gateway(state, failures)).build_report(state)
    PlanValidatorService().run(state)
    return state


def _single() -> tuple[PlanningState, dict[str, Any]]:
    """Twelve ordinary museums and one requested museum, planned."""
    pois = [_museum(f"m{index}", index) for index in range(12)]
    requested = _museum("req", 12)
    return _planned(_state([*pois, requested], must_visit={"the requested museum": requested})), requested


def _leg_touching(state: PlanningState, name: str) -> tuple[int, str, str]:
    for day in state.experience_plan.daily_plans:
        for a, b in zip(day.experiences, day.experiences[1:]):
            if name in (a.name, b.name):
                return day.day_number, a.name, b.name
    raise AssertionError(f"{name} has no leg")


def _findings(state: PlanningState) -> list[Any]:
    return [issue for issue in state.validation_report.warnings if issue.category == _CATEGORY]


def _layout(state: PlanningState) -> list[list[str | None]]:
    return [[stop.provider_place_id for stop in day.experiences] for day in state.experience_plan.daily_plans]


# -- what raises the finding -----------------------------------------------------------------


@pytest.mark.parametrize("reason", ["no_route", "unroutable_endpoint"])
def test_a_definitive_failure_next_to_a_must_visit_is_disclosed_on_its_day(reason: str) -> None:
    state, requested = _single()
    day, a, b = _leg_touching(state, requested["name"])
    layout = _layout(state)
    _routed(state, {(a, b): (ProviderStatus.FAILED, reason)})

    (finding,) = _findings(state)
    other = b if a == requested["name"] else a
    assert finding.severity == ValidationSeverity.WARNING
    assert finding.affected_section == f"experience_plan.daily_plans[{day}]"
    assert finding.message == (
        f"Day {day}: A verified route between {a} and {b} could not be found. Museum req is kept because you "
        "requested it, but travel between these stops is not verified. Check the appropriate way to reach it "
        "before relying on this day's plan."
    )
    assert other in finding.message
    report = state.validation_report
    assert _CODE in report.review_codes and report.readiness_status == ReadinessStatus.NEEDS_REVIEW
    assert report.review_code_classification[_CODE] == classification.MATERIAL
    assert _layout(state) == layout  # disclosure only
    assert _day_of(state, requested["place_id"]) == day


def test_no_time_distance_mode_cause_or_access_claim_is_made() -> None:
    state, requested = _single()
    _, a, b = _leg_touching(state, requested["name"])
    _routed(state, {(a, b): (ProviderStatus.FAILED, "no_route")})

    message = _findings(state)[0].message.lower()
    for word in ("minute", " km", "walk", "drive", "car", "ferry", "transit", "closed", "inaccessible",
                 "impossible", "provider", "because the", "cannot be visited"):
        assert word not in message, word


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (ProviderStatus.FAILED, "timeout"),
        (ProviderStatus.FAILED, "rate_limited"),
        (ProviderStatus.FAILED, "server"),
        (ProviderStatus.FAILED, "network"),
        (ProviderStatus.FAILED, "provider_bad_request"),
        (ProviderStatus.FAILED, "invalid_coordinates"),
        (ProviderStatus.UNAVAILABLE, "budget_exhausted"),
        (ProviderStatus.UNAVAILABLE, None),
        (ProviderStatus.FAILED, None),  # a failure stored without a code is not definitive
    ],
)
def test_a_transient_or_unclassified_failure_raises_nothing(status: ProviderStatus, reason: str | None) -> None:
    state, requested = _single()
    _, a, b = _leg_touching(state, requested["name"])
    _routed(state, {(a, b): (status, reason)})
    assert _findings(state) == []
    assert _CODE not in state.validation_report.review_codes


def test_a_definitive_failure_between_ordinary_stops_raises_nothing() -> None:
    state, requested = _single()
    ordinary = next(
        (day.day_number, x.name, y.name)
        for day in state.experience_plan.daily_plans
        for x, y in zip(day.experiences, day.experiences[1:])
        if requested["name"] not in (x.name, y.name)
    )
    _routed(state, {(ordinary[1], ordinary[2]): (ProviderStatus.FAILED, "no_route")})
    assert _findings(state) == []


def test_successful_legs_raise_nothing() -> None:
    state, _ = _single()
    _routed(state, {})
    assert _findings(state) == []


def test_a_plan_without_route_data_raises_nothing() -> None:
    state, _ = _single()
    PlanValidatorService().run(state)  # no route report at all
    assert _findings(state) == []


def test_a_leg_between_two_must_visits_names_both_and_blames_neither() -> None:
    pois = [_museum(f"m{index}", index) for index in range(12)]
    alpha, beta = _museum("alpha", 12), _museum("beta", 13)
    state = _planned(_state([*pois, alpha, beta], must_visit={"the alpha museum": alpha, "the beta museum": beta}))
    day = _day_of(state, alpha["place_id"])
    stops = [stop.name for stop in state.experience_plan.daily_plans[day - 1].experiences]
    assert day == _day_of(state, beta["place_id"]) and abs(stops.index("Museum alpha") - stops.index("Museum beta")) == 1
    a, b = sorted(["Museum alpha", "Museum beta"], key=stops.index)
    _routed(state, {(a, b): (ProviderStatus.FAILED, "no_route")})

    (finding,) = _findings(state)
    assert finding.message == (
        f"Day {day}: A verified route between {a} and {b} could not be found. {a} and {b} are kept because you "
        "requested them, but travel between these stops is not verified. Check the appropriate way to reach them "
        "before relying on this day's plan."
    )


def test_only_the_affected_day_is_flagged() -> None:
    pois = [_clustered(f"c{cluster}m{index}", cluster, index) for cluster in range(3) for index in range(1, 4)]
    alpha, beta = _clustered("alpha", 0, 0), _clustered("beta", 2, 0)
    state = _planned(_state([*pois, alpha, beta], must_visit={"the alpha museum": alpha, "the beta museum": beta}))
    alpha_day, beta_day = _day_of(state, alpha["place_id"]), _day_of(state, beta["place_id"])
    assert alpha_day != beta_day  # the fixture really spreads them
    day, a, b = _leg_touching(state, "Museum alpha")
    _routed(state, {(a, b): (ProviderStatus.FAILED, "no_route")})

    findings = _findings(state)
    assert [finding.affected_section for finding in findings] == [f"experience_plan.daily_plans[{alpha_day}]"]
    assert "Museum beta" not in findings[0].message


def test_every_failed_leg_of_a_must_visit_gets_its_own_accurate_finding() -> None:
    """Every leg of the must-visit's day fails: one finding per leg that
    touches it, each naming exactly that leg's two stops; a failed leg not
    touching it raises nothing."""
    state, requested = _single()
    day = _day_of(state, requested["place_id"])
    day_plan = state.experience_plan.daily_plans[day - 1]
    # the must-visit in the middle of its day, so it is on two legs
    must_visit = next(stop for stop in day_plan.experiences if stop.provider_place_id == requested["place_id"])
    others = [stop for stop in day_plan.experiences if stop is not must_visit]
    day_plan.experiences = [others[0], must_visit, *others[1:]]
    stops = [stop.name for stop in day_plan.experiences]
    legs = list(zip(stops, stops[1:]))
    _routed(state, {leg: (ProviderStatus.FAILED, "no_route") for leg in legs})

    touching = [leg for leg in legs if requested["name"] in leg]
    findings = _findings(state)
    assert len(touching) == 2 and len(findings) == 2
    for finding, (a, b) in zip(findings, touching):
        assert finding.affected_section == f"experience_plan.daily_plans[{day}]"
        assert finding.message.startswith(f"Day {day}: A verified route between {a} and {b} could not be found.")


def test_matching_is_by_provider_identity_never_by_name() -> None:
    """An ordinary stop that merely carries the must-visit's name, on a
    definitively failed leg, raises nothing; the real must-visit's legs route."""
    state, requested = _single()
    must_visit_day = _day_of(state, requested["place_id"])
    other_day = next(day for day in state.experience_plan.daily_plans if day.day_number != must_visit_day)
    namesake, neighbour = other_day.experiences[0], other_day.experiences[1]
    namesake.name = requested["name"]
    _routed(state, {(namesake.name, neighbour.name): (ProviderStatus.FAILED, "no_route")})
    assert _findings(state) == []


# -- what it leaves unchanged ------------------------------------------------------------------


def test_every_other_finding_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    failing: dict[tuple[str, str], tuple[ProviderStatus, str | None]] = {}

    def run(with_new_finding: bool) -> PlanningState:
        state, requested = _single()
        _, a, b = _leg_touching(state, requested["name"])
        failing[(a, b)] = (ProviderStatus.FAILED, "no_route")
        if not with_new_finding:
            monkeypatch.setattr(validator_module, "_unrouted_must_visit_leg_issues", lambda _state: [])
        _routed(state, dict(failing))
        monkeypatch.undo()
        return state

    with_it, without_it = run(True), run(False)
    strip = lambda state: [  # noqa: E731
        (issue.category, issue.severity, issue.message, issue.affected_section)
        for issue in state.validation_report.warnings
        if issue.category != _CATEGORY
    ]
    assert strip(with_it) == strip(without_it)
    # the existing generic warning about the unchecked leg is still there, word for word
    feasibility = [issue for issue in with_it.validation_report.warnings if issue.category == "feasibility"]
    assert len(feasibility) == 1 and "1 leg(s) could not be route-checked" in feasibility[0].message
    assert set(with_it.validation_report.review_codes) - set(without_it.validation_report.review_codes) == {_CODE}


def test_the_finding_survives_save_and_reload(tmp_path: Path) -> None:
    state, requested = _single()
    _, a, b = _leg_touching(state, requested["name"])
    _routed(state, {(a, b): (ProviderStatus.FAILED, "no_route")})
    path = tmp_path / "state.json"
    PlanningStateRepository(store=LocalJsonStore(path)).save(state)

    reloaded = PlanningStateRepository(store=LocalJsonStore(path)).get_by_trip_id(state.trip_id)
    assert reloaded is not None and reloaded is not state
    assert [issue.model_dump() for issue in _findings(reloaded)] == [issue.model_dump() for issue in _findings(state)]
    assert reloaded.validation_report.review_code_classification[_CODE] == classification.MATERIAL
    # the stored leg still carries the provider's own code, and nothing invented
    leg = next(leg for leg in reloaded.route_feasibility_report.legs if leg.failure_reason == "no_route")
    assert leg.duration_seconds is None and leg.distance_meters is None and leg.mode is None
