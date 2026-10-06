from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.core.provider_usage import GenerationProviderContext
from app.graphs.planning_graph_nodes import build_route_aware_sequencing_node, build_route_feasibility_node
from app.models.common import ProviderStatus
from app.models.planning_state import PlanningState
from app.models.routing import RouteResult
from app.providers.gateway import ProviderGateway
from app.services.plan_validator_service import PlanValidatorService
from app.services.planning_orchestrator import PlanningOrchestrator
from app.services.route_aware_sequencing_service import RouteAwareSequencingService
from app.services.route_burden import LONG_TRAVEL_DAY, day_route_burdens
from app.services.route_feasibility_service import (
    ADAPTATION_ALLOWANCE_EXHAUSTED,
    ADAPTATION_NOT_FASTER,
    ADAPTATION_PROVIDER_FAILED,
    RouteFeasibilityService,
)
from app.storage.provider_cache_store import ProviderCacheStore
from app.tests.providers.test_geoapify_places_routing_203c2b import (  # noqa: F401 - `configured` is a fixture
    _Network,
    _context,
    _routing,
    configured,
)
from app.tests.services.test_final_quality_correction_203c2b import _poi, _point, _state

# Mixed-mode adaptation is applied ONCE, to the FINAL day order. A driving
# route asked for before route-aware sequencing has fixed the order is
# thrown away by a reorder, and the per-generation allowance
# (ROUTE_ALTERNATE_MODE_MAX_REQUESTS_PER_GENERATION = 6) is small. These
# tests run the real Geoapify routing adapter over an in-process HTTP mock,
# so "at most six driving requests" is counted at the provider boundary.
# Synthetic places on a coordinate grid; nothing is city-specific.

_CAP = 6
_WALK_SECONDS_PER_DEGREE = 60_000.0  # 0.1 degree = 6000 s on foot: over the 2400 s walking-leg limit
_DRIVE_SECONDS_PER_DEGREE = 6_000.0  # 0.1 degree = 600 s by vehicle


def _routes(request: httpx.Request) -> httpx.Response:
    """Leg times from the request's own waypoints, per mode."""
    points = [tuple(float(value) for value in waypoint.split(",")) for waypoint in request.url.params["waypoints"].split("|")]
    drive = request.url.params["mode"] == "drive"
    rate = _DRIVE_SECONDS_PER_DEGREE if drive else _WALK_SECONDS_PER_DEGREE
    legs = []
    for (lat_a, lng_a), (lat_b, lng_b) in zip(points, points[1:]):
        degrees = abs(lat_a - lat_b) + abs(lng_a - lng_b)
        legs.append({"distance": round(degrees * 100_000.0, 1), "time": round(degrees * rate, 1)})
    return httpx.Response(200, json={"features": [{"properties": {"legs": legs}, "geometry": None}]})


def _reorder_day(index: int) -> list[dict[str, Any]]:
    """Three stops in an order sequencing will change: A, B (far), C (between).
    Both legs are too long to walk, before and after the reorder."""
    lat = 50.0 + index
    return [
        _poi(f"d{index}a", f"Day {index} Alpha", _point(lat, 10.00)),
        _poi(f"d{index}b", f"Day {index} Beta", _point(lat, 10.20)),
        _poi(f"d{index}c", f"Day {index} Gamma", _point(lat, 10.10)),
    ]


def _settled_day(index: int) -> list[dict[str, Any]]:
    """Three stops already in their best order: one short leg, one long leg."""
    lat = 50.0 + index
    return [
        _poi(f"d{index}a", f"Day {index} Alpha", _point(lat, 10.00)),
        _poi(f"d{index}b", f"Day {index} Beta", _point(lat, 10.01)),
        _poi(f"d{index}c", f"Day {index} Gamma", _point(lat, 10.11)),
    ]


class _Run:
    """One generation's routing lifecycle over the real adapter."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, days: list[list[dict[str, Any]]], extra: list[dict[str, Any]] | None = None) -> None:
        self.network = _Network(routing=_routes)
        store = ProviderCacheStore(tmp_path / f"cache-{id(self)}.sqlite3")
        self.gateway = ProviderGateway(routing=_routing(monkeypatch, self.network, store))
        self.context: GenerationProviderContext = _context(trip_days=len(days))
        self.service = RouteFeasibilityService(gateway=self.gateway)
        self.sequencing = RouteAwareSequencingService(gateway=self.gateway)
        self.state: PlanningState = _state([poi for day in days for poi in day] + (extra or []), days)

    def modes(self, mode: str) -> list[httpx.Request]:
        return [request for request in self.network.requests["routing"] if request.url.params["mode"] == mode]

    @property
    def drive_requests(self) -> int:
        return len(self.modes("drive"))

    @property
    def walk_requests(self) -> int:
        return len(self.modes("walk"))

    def graph(self, sequencing: Any = None) -> dict[str, Any]:
        graph_state = {"planning_state": self.state, "provider_context": self.context}
        feasibility = build_route_feasibility_node(self.service)(graph_state)
        assert "failed_nodes" not in feasibility
        self.after_feasibility = (self.walk_requests, self.drive_requests)
        return build_route_aware_sequencing_node(sequencing or self.sequencing, self.service)(graph_state)

    def legacy(self) -> None:
        class _Planner:
            def run(self, planning_state: PlanningState) -> PlanningState:
                return planning_state

        PlanningOrchestrator(
            experience_planner_service=_Planner(),  # type: ignore[arg-type]
            route_feasibility_service=self.service,
            route_aware_sequencing_service=self.sequencing,
        ).run_experience_plan_stage(self.state, self.context)

    def previous_lifecycle(self) -> None:
        """What the engines did before: adapt at once, then reorder, then rebuild."""
        self.state.route_feasibility_report = self.service.build_report(self.state, self.context)
        report = self.sequencing.build_report(self.state, self.context)
        if self.sequencing.apply_report(self.state, report, 0.0):
            self.state.route_feasibility_report = self.service.build_report(self.state, self.context)

    def legs(self) -> list[tuple[str, str, str | None, float | None, bool, str | None]]:
        return [
            (leg.from_experience_name, leg.to_experience_name, leg.mode, leg.duration_seconds,
             leg.mode_adaptation_attempted, leg.mode_adaptation_outcome)
            for leg in self.state.route_feasibility_report.legs
        ]

    def day_names(self, day: int) -> list[str]:
        return [stop.name for stop in self.state.experience_plan.daily_plans[day].experiences]


@pytest.fixture(autouse=True)
def _scheduling_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTE_AWARE_SCHEDULING_ENABLED", "true")
    get_settings.cache_clear()


_MEXICO_SHAPED = [_reorder_day(1), _reorder_day(2), _settled_day(3)]


# -- the lifecycle hole, and its fix ----------------------------------------------------------


def test_the_previous_lifecycle_spent_the_allowance_on_orders_that_were_then_replaced(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    """Five long legs in the first order use five of the six requests; the
    two reordered days then need four new ones and get one."""
    run = _Run(monkeypatch, tmp_path, _MEXICO_SHAPED)
    run.previous_lifecycle()

    assert run.day_names(0) == ["Day 1 Alpha", "Day 1 Gamma", "Day 1 Beta"]  # reordered
    assert run.drive_requests == _CAP and run.context.alternate_mode_requests_left == 0
    refused = [leg for leg in run.legs() if leg[5] == ADAPTATION_ALLOWANCE_EXHAUSTED]
    assert len(refused) == 3 and all(leg[2] == "walk" and leg[3] == 6000.0 for leg in refused)
    assert [burden.excessive_walking for burden in day_route_burdens(run.state)] == [True, True, False]


def test_adapting_the_final_order_once_serves_every_long_leg_within_the_same_six(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    run = _Run(monkeypatch, tmp_path, _MEXICO_SHAPED)
    outcome = run.graph()

    assert outcome["completed_nodes"] == ["route_aware_sequencing"]
    # the route-feasibility step made the walking requests only
    assert run.after_feasibility == (3, 0)
    assert run.day_names(0) == ["Day 1 Alpha", "Day 1 Gamma", "Day 1 Beta"]
    # five long legs in the FINAL order: five driving requests, one allowance left over
    assert run.drive_requests == 5 and run.context.alternate_mode_requests_left == 1
    long_legs = [leg for leg in run.legs() if leg[4]]
    assert len(long_legs) == 5 and all(leg[2] == "drive" and leg[3] == 600.0 and leg[5] is None for leg in long_legs)
    assert not any(burden.long_route for burden in day_route_burdens(run.state))
    assert run.state.route_burden_repair_report is None  # nothing left to repair
    # every driving request was for a leg of the final order (no superseded leg was paid for)
    final = {
        f"{a.coordinates.lat},{a.coordinates.lng}|{b.coordinates.lat},{b.coordinates.lng}"
        for day in run.state.experience_plan.daily_plans
        for a, b in zip(day.experiences, day.experiences[1:])
    }
    assert {request.url.params["waypoints"] for request in run.modes("drive")} <= final
    assert run.context.usage_tracker.credits_used("routing_drive") == 5


# -- the cap -------------------------------------------------------------------------------------


def _four_reorder_days() -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    days = [_reorder_day(index) for index in (1, 2, 3, 4)]
    # an unused candidate beside day 4's middle stop, for its route-burden repair
    return days, [_poi("d4x", "Day 4 Delta", _point(54.0, 10.105))]


@pytest.mark.parametrize("engine", ["graph", "legacy", "previous"])
def test_alternate_mode_requests_never_exceed_six_per_generation(
    engine: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    """Eight long legs, a reorder on every day and a route-burden repair:
    exactly six driving requests reach the provider, whatever the lifecycle."""
    assert Settings.model_fields["route_alternate_mode_max_requests_per_generation"].default == _CAP
    days, extra = _four_reorder_days()
    run = _Run(monkeypatch, tmp_path, days, extra)
    assert run.context.alternate_mode_requests_left == _CAP
    {"graph": run.graph, "legacy": run.legacy, "previous": run.previous_lifecycle}[engine]()

    assert run.drive_requests == _CAP
    assert run.context.alternate_mode_requests_left == 0  # used up, never below zero
    assert run.context.usage_tracker.credits_used("routing_drive") == _CAP
    assert run.context.usage_tracker.calls_made("routing_drive") == _CAP
    # asking again changes nothing: the allowance is never refilled
    before = run.drive_requests
    run.service.adapt_report_modes(run.state, run.context)
    run.state.route_feasibility_report = run.service.build_report(run.state, run.context)
    assert run.drive_requests == before and run.context.alternate_mode_requests_left == 0


def test_no_allowance_or_counter_was_added() -> None:
    names = {name for name in GenerationProviderContext.__dataclass_fields__ if "alternate" in name}
    assert names == {"alternate_mode_requests_left", "alternate_mode_failed_legs", "alternate_mode_outcomes"}
    assert not [name for name in Settings.model_fields if "reconcil" in name or "post_repair" in name]


# -- when six are not enough ---------------------------------------------------------------------


def test_an_unresolved_post_repair_walking_leg_is_reported_and_still_fails_validation(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    days, extra = _four_reorder_days()
    run = _Run(monkeypatch, tmp_path, days, extra)
    run.graph()

    # days 1-3 took the six requests; day 4 stayed on foot and was repaired once
    (attempt,) = run.state.route_burden_repair_report.attempts
    assert attempt.day_number == 4 and attempt.accepted and attempt.reason == "accepted"
    assert (attempt.replaced_place, attempt.replacement_place) == ("Day 4 Alpha", "Day 4 Delta")
    assert attempt.after_duration_seconds < attempt.before_duration_seconds  # materially better...
    assert attempt.hard_walking_violation_remains is True  # ...and not resolved

    burden = day_route_burdens(run.state)[3]
    assert burden.excessive_walking and burden.over_leg_limit and burden.drive_legs == 0
    long_leg = next(leg for leg in run.legs() if leg[0].startswith("Day 4") and leg[3] == 6000.0)
    assert long_leg[2] == "walk" and long_leg[4] and long_leg[5] == ADAPTATION_ALLOWANCE_EXHAUSTED
    # no second replacement, no extra request, no estimate
    assert run.drive_requests == _CAP and sorted(run.day_names(3)) == ["Day 4 Beta", "Day 4 Delta", "Day 4 Gamma"]

    validation = PlanValidatorService().run(run.state).validation_report
    assert LONG_TRAVEL_DAY in validation.review_codes
    assert validation.readiness_status.value == "needs_review"  # never shown as ready


def test_a_repair_that_resolves_the_day_is_not_flagged(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    # one far stop; its replacement makes every leg walkable
    far_day = [
        _poi("a", "Near Alpha", _point(50.0, 10.000)),
        _poi("b", "Near Beta", _point(50.0, 10.010)),
        _poi("far", "Faraway", _point(50.0, 10.900)),
    ]
    run = _Run(monkeypatch, tmp_path, [far_day], [_poi("g", "Near Gamma", _point(50.0, 10.020))])
    monkeypatch.setenv("ROUTE_BURDEN_MAX_DRIVE_LEG_SECONDS", "600")  # the 0.89 degree drive is unreasonable
    get_settings.cache_clear()
    run.graph()

    (attempt,) = run.state.route_burden_repair_report.attempts
    assert attempt.accepted and attempt.hard_walking_violation_remains is False
    assert not day_route_burdens(run.state)[0].long_route


# -- nothing else changes ------------------------------------------------------------------------


def test_with_no_reorder_the_final_legs_equal_immediate_adaptation(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    days = [_settled_day(1), _settled_day(2)]
    deferred = _Run(monkeypatch, tmp_path, days)
    deferred.graph()
    immediate = _Run(monkeypatch, tmp_path, days)
    immediate.state.route_feasibility_report = immediate.service.build_report(immediate.state, immediate.context)

    assert deferred.legs() == immediate.legs()
    assert deferred.drive_requests == immediate.drive_requests == 2
    # the adaptation pass reused the walking legs: no walking request beyond the day routes
    assert deferred.walk_requests == deferred.after_feasibility[0] == 2


def test_the_adaptation_pass_is_idempotent_and_makes_no_walking_request(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    run = _Run(monkeypatch, tmp_path, [_settled_day(1)])
    run.state.route_feasibility_report = run.service.build_report(run.state, run.context, adapt_modes=False)
    assert [leg[2] for leg in run.legs()] == ["walk", "walk"] and run.drive_requests == 0
    assert [leg[4] for leg in run.legs()] == [False, False]  # nothing attempted yet

    walks = run.walk_requests
    run.service.adapt_report_modes(run.state, run.context)
    first = run.legs()
    assert [leg[2] for leg in first] == ["walk", "drive"] and run.drive_requests == 1
    run.service.adapt_report_modes(run.state, run.context)
    run.service.adapt_report_modes(run.state, None)
    assert run.legs() == first and run.drive_requests == 1 and run.walk_requests == walks
    # the adapted leg keeps the provider's walking figures, exactly as immediate adaptation records them
    leg = run.state.route_feasibility_report.legs[1]
    assert (leg.walking_duration_seconds, leg.duration_seconds) == (6000.0, 600.0)
    assert "Vehicle transfer" in leg.message and "fare" in leg.message  # the existing generic wording


def test_with_route_aware_scheduling_off_the_report_is_adapted_at_once_as_before(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    monkeypatch.setenv("ROUTE_AWARE_SCHEDULING_ENABLED", "false")
    get_settings.cache_clear()
    run = _Run(monkeypatch, tmp_path, _MEXICO_SHAPED)
    passes: list[int] = []
    original = RouteFeasibilityService.adapt_report_modes
    monkeypatch.setattr(
        RouteFeasibilityService, "adapt_report_modes",
        lambda self, *args, **kwargs: passes.append(1) or original(self, *args, **kwargs),
    )
    run.graph()

    # all driving requests were made by the route-feasibility step itself; no later pass ran
    assert run.after_feasibility == (3, 5) and run.drive_requests == 5 and passes == []
    assert run.day_names(0) == ["Day 1 Alpha", "Day 1 Beta", "Day 1 Gamma"]  # never reordered

    plain = _Run(monkeypatch, tmp_path, _MEXICO_SHAPED)
    plain.state.route_feasibility_report = plain.service.build_report(plain.state, plain.context)
    assert run.legs() == plain.legs()  # exactly `build_report`'s own output


def test_build_report_keeps_its_default_semantics_for_every_other_caller(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    run = _Run(monkeypatch, tmp_path, [_settled_day(1)])
    report = run.service.build_report(run.state, run.context)  # e.g. targeted regeneration
    assert [leg.mode for leg in report.legs] == ["walk", "drive"] and run.drive_requests == 1


def test_a_failing_sequencing_step_still_gets_its_adaptation_pass(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    class _Broken:
        def build_report(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("sequencing is down")

    run = _Run(monkeypatch, tmp_path, [_settled_day(1)])
    outcome = run.graph(sequencing=_Broken())

    assert outcome["failed_nodes"] == ["route_aware_sequencing"]
    assert [leg[2] for leg in run.legs()] == ["walk", "drive"]  # never left walking-only


def test_both_engines_follow_the_same_lifecycle(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    days, extra = _four_reorder_days()
    for scenario, pool in ((_MEXICO_SHAPED, None), (days, extra)):
        # one after the other: each run owns the mocked HTTP layer while it runs
        graph = _Run(monkeypatch, tmp_path, scenario, pool)
        graph.graph()
        legacy = _Run(monkeypatch, tmp_path, scenario, pool)
        legacy.legacy()

        assert graph.legs() == legacy.legs()
        assert [graph.day_names(day) for day in range(len(scenario))] == [
            legacy.day_names(day) for day in range(len(scenario))
        ]
        assert (graph.walk_requests, graph.drive_requests) == (legacy.walk_requests, legacy.drive_requests)
        assert graph.context.alternate_mode_requests_left == legacy.context.alternate_mode_requests_left
        graph_repair, legacy_repair = graph.state.route_burden_repair_report, legacy.state.route_burden_repair_report
        assert (graph_repair is None) == (legacy_repair is None)
        if graph_repair is not None:
            assert [(a.day_number, a.reason, a.hard_walking_violation_remains) for a in graph_repair.attempts] == [
                (a.day_number, a.reason, a.hard_walking_violation_remains) for a in legacy_repair.attempts
            ]


@pytest.mark.parametrize("concurrency", ["true", "false"])
def test_serial_and_concurrent_provider_io_give_the_same_legs(
    concurrency: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    monkeypatch.setenv("PROVIDER_IO_CONCURRENCY_ENABLED", concurrency)
    get_settings.cache_clear()
    days, extra = _four_reorder_days()
    run = _Run(monkeypatch, tmp_path, days, extra)
    run.graph()

    modes = [leg[2] for leg in run.legs()]
    # days 1-3 in day and leg order get the six requests; day 4 does not
    assert modes[:6] == ["drive"] * 6 and modes[6:] == ["walk", "walk"]
    assert run.drive_requests == _CAP


# -- the diagnostic labels -----------------------------------------------------------------------


class _Routing:
    provider_name = "fixture_routing"


class _LabelGateway:
    """One long walking leg; the driving answer is whatever the test gives."""

    routing = _Routing()

    def __init__(self, drive: RouteResult | None) -> None:
        self._drive = drive
        self.drive_calls = 0

    def get_route_sequence(self, points: list[tuple[float, float]], provider_context: Any = None) -> list[RouteResult]:
        return [
            RouteResult(
                provider="fixture_routing", status=ProviderStatus.SUCCESS, distance_meters=6000.0,
                duration_seconds=5000.0, source="fixture_routing", mode="walk",
            )
            for _ in zip(points, points[1:])
        ]

    def get_alternate_mode_route(self, origin: Any, destination: Any, provider_context: Any = None) -> RouteResult | None:
        self.drive_calls += 1
        return self._drive


def _drive(status: ProviderStatus, seconds: float | None = None, reason: str | None = None) -> RouteResult:
    return RouteResult(
        provider="fixture_routing", status=status, distance_meters=7000.0 if seconds is not None else None,
        duration_seconds=seconds, source="fixture_routing", failure_reason=reason,
    )


@pytest.mark.parametrize(
    ("drive", "label"),
    [
        (_drive(ProviderStatus.FAILED, reason="server_error"), ADAPTATION_PROVIDER_FAILED),
        (_drive(ProviderStatus.UNAVAILABLE), ADAPTATION_PROVIDER_FAILED),
        (_drive(ProviderStatus.SUCCESS, 5000.0), ADAPTATION_NOT_FASTER),
        (_drive(ProviderStatus.SUCCESS, 9000.0), ADAPTATION_NOT_FASTER),
        (_drive(ProviderStatus.UNAVAILABLE, reason="budget_exhausted"), ADAPTATION_ALLOWANCE_EXHAUSTED),
    ],
)
def test_an_unapplied_adaptation_says_why_and_the_leg_is_not_asked_again(
    drive: RouteResult, label: str
) -> None:
    gateway = _LabelGateway(drive)
    service = RouteFeasibilityService(gateway=gateway)  # type: ignore[arg-type]
    state = _state([], [[_poi("a", "Alpha", _point(50.0, 10.0)), _poi("b", "Beta", _point(50.0, 10.1))]])
    context = _context(trip_days=1)

    for _ in range(3):  # a rebuilt report reads the label back without asking again
        (leg,) = service.build_report(state, context).legs
        assert (leg.mode, leg.mode_adaptation_attempted, leg.mode_adaptation_outcome) == ("walk", True, label)
        assert (leg.status, leg.duration_seconds) == (ProviderStatus.SUCCESS, 5000.0)  # the factual walking leg stands
    assert gateway.drive_calls == 1
    assert len(context.alternate_mode_failed_legs) == 1  # handling of failed legs is unchanged


def test_an_applied_adaptation_and_a_short_leg_carry_no_label() -> None:
    gateway = _LabelGateway(_drive(ProviderStatus.SUCCESS, 400.0))
    state = _state([], [[_poi("a", "Alpha", _point(50.0, 10.0)), _poi("b", "Beta", _point(50.0, 10.1))]])
    (leg,) = RouteFeasibilityService(gateway=gateway).build_report(state, _context(trip_days=1)).legs  # type: ignore[arg-type]
    assert (leg.mode, leg.mode_adaptation_attempted, leg.mode_adaptation_outcome) == ("drive", True, None)

    # a provider with no second mode: nothing attempted, nothing labelled
    (leg,) = RouteFeasibilityService(gateway=_LabelGateway(None)).build_report(state, _context(trip_days=1)).legs  # type: ignore[arg-type]
    assert (leg.mode, leg.mode_adaptation_attempted, leg.mode_adaptation_outcome) == ("walk", False, None)


def test_the_labels_never_decide_anything() -> None:
    """The label is written to the leg and read by nothing in the application."""
    app_root = Path(__file__).resolve().parents[2]
    readers = [
        path.relative_to(app_root).as_posix()
        for path in app_root.rglob("*.py")
        if "tests" not in path.parts
        and ("mode_adaptation_outcome" in path.read_text() or "hard_walking_violation_remains" in path.read_text())
    ]
    # only the two models that carry them, the two services that WRITE them, and a comment
    # on the provider context -- no validator, planner, repair decision or acceptance code
    assert sorted(readers) == [
        "core/provider_usage.py", "models/route_burden_repair.py", "models/routing.py",
        "services/route_burden_repair_service.py", "services/route_feasibility_service.py",
    ]
    repair_source = (app_root / "services" / "route_burden_repair_service.py").read_text()
    assert repair_source.count("hard_walking_violation_remains") == 1  # written once, never read
