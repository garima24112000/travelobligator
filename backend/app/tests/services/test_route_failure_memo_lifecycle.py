from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.core.provider_usage import GenerationProviderContext, route_request_allowance
from app.graphs.planning_graph_nodes import (
    build_route_aware_sequencing_node,
    build_route_feasibility_node,
    build_travel_time_buffer_node,
)
from app.models.planning_state import PlanningState
from app.providers.gateway import ProviderGateway
from app.services.planning_orchestrator import PlanningOrchestrator
from app.services.route_aware_sequencing_service import RouteAwareSequencingService
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.travel_time_buffer_service import TravelTimeBufferService
from app.storage.provider_cache_store import ProviderCacheStore
from app.tests.providers.test_geoapify_places_routing_203c2b import (  # noqa: F401 - `configured` is a fixture
    _Network,
    _context,
    _routing,
    configured,
)
from app.tests.services.test_final_order_mode_adaptation import _routes
from app.tests.services.test_final_quality_correction_203c2b import _poi, _point, _state

# A generation runs its routing chain TWICE when the AI repair re-enters the
# planner: feasibility -> sequencing -> routability repair -> burden repair ->
# travel-time buffers, and then all of it again on the rebuilt (identical)
# plan, on the SAME route-request allowance. A request the provider answered
# "this cannot be routed" used to be sent again by every one of those stages,
# in both passes, until the allowance was gone and the second pass's repairs
# were refused. These tests run the real Geoapify routing adapter over an
# in-process HTTP mock and count requests at the provider boundary.
# Synthetic places on a coordinate grid; nothing is city-specific.

_A = _poi("a", "Museum Alpha", _point(50.000, 10.000))
_B = _poi("b", "Museum Beta", _point(50.002, 10.002))
_C = _poi("c", "Museum Gamma", _point(50.004, 10.000))
_D = _poi("d", "Museum Delta", _point(50.050, 10.050))
_X = _poi("x", "Museum Unroutable", _point(50.052, 10.052))
_E = _poi("e", "Museum Epsilon", _point(50.054, 10.050))
_NEAR = _poi("near", "Museum Nearby", _point(50.053, 10.051))

_UNROUTABLE_WAYPOINT = "50.052,10.052"


def _provider(request: httpx.Request) -> httpx.Response:
    """Rejects any request that contains the unroutable point, as a routing
    engine does for a well-formed request it cannot satisfy."""
    if _UNROUTABLE_WAYPOINT in request.url.params["waypoints"].split("|"):
        return httpx.Response(400, json={"statusCode": 400, "error": "Bad Request", "message": "No path could be found for input"})
    return _routes(request)


class _Lifecycle:
    """One generation over the real adapter, in either engine. `run_pass`
    rebuilds the plan first (what the planner does when it is re-entered)."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        engine: str,
        days: list[list[dict[str, Any]]],
        unused: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self.engine = engine
        self.network = _Network(routing=_provider)
        store = ProviderCacheStore(tmp_path / f"cache-{engine}-{id(self)}.sqlite3")
        self.gateway = ProviderGateway(routing=_routing(monkeypatch, self.network, store))
        self.context: GenerationProviderContext = _context(trip_days=len(days))
        self.feasibility = RouteFeasibilityService(gateway=self.gateway)
        self.sequencing = RouteAwareSequencingService(gateway=self.gateway)
        self.buffers = TravelTimeBufferService(gateway=self.gateway)
        self.state: PlanningState = _state([poi for day in days for poi in day] + list(unused), days)
        self._planned = copy.deepcopy(self.state.experience_plan)

    @property
    def requests(self) -> list[tuple[str, str]]:
        """Every routing request that reached the provider, sorted: the days
        of one report are fetched as a concurrent batch, so their arrival
        order is not part of the contract -- what was asked, and how often, is."""
        return sorted(
            (request.url.params["waypoints"], request.url.params["mode"]) for request in self.network.requests["routing"]
        )

    def _rebuild_plan(self) -> None:
        self.state.experience_plan = copy.deepcopy(self._planned)

    def run_pass(self) -> None:
        if self.engine == "graph":
            self._rebuild_plan()
            graph_state = {"planning_state": self.state, "provider_context": self.context}
            for node in (
                build_route_feasibility_node(self.feasibility),
                build_route_aware_sequencing_node(self.sequencing, self.feasibility),
                build_travel_time_buffer_node(self.buffers),
            ):
                assert "failed_nodes" not in node(graph_state)
            return

        lifecycle = self

        class _Planner:
            def run(self, planning_state: PlanningState) -> PlanningState:
                lifecycle._rebuild_plan()
                return planning_state

        PlanningOrchestrator(
            experience_planner_service=_Planner(),  # type: ignore[arg-type]
            route_feasibility_service=self.feasibility,
            route_aware_sequencing_service=self.sequencing,
            travel_time_buffer_service=self.buffers,
        ).run_experience_plan_stage(self.state, self.context)

    def day_names(self, day: int) -> list[str]:
        return [stop.name for stop in self.state.experience_plan.daily_plans[day].experiences]

    def snapshot(self) -> dict[str, Any]:
        return {
            "days": [self.day_names(index) for index in range(len(self.state.experience_plan.daily_plans))],
            "legs": [
                (leg.from_experience_name, leg.to_experience_name, leg.status.value, leg.failure_reason, leg.duration_seconds)
                for leg in self.state.route_feasibility_report.legs
            ],
            "repair": [
                (attempt.day_number, attempt.reason, attempt.accepted, attempt.verification_attempts)
                for attempt in self.state.routability_repair_report.attempts
            ],
            "buffers": [buffer.status.value for buffer in self.state.travel_time_buffer_report.buffers],
        }


@pytest.fixture(autouse=True)
def _scheduling_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTE_AWARE_SCHEDULING_ENABLED", "true")
    get_settings.cache_clear()


# Two balanced days, five stops (= R): day 2 is one leg to a stop the provider cannot route to.
_AMBIGUOUS = [[_A, _B, _C], [_D, _X]]
_DAY_ONE = ("50.0,10.0|50.002,10.002|50.004,10.0", "walk")
_DAY_TWO = ("50.05,10.05|50.052,10.052", "walk")
_FIRST_VERIFY = ("50.053,10.051|50.052,10.052", "walk")  # the replacement routed TO the unroutable stop
_SECOND_VERIFY = ("50.05,10.05|50.053,10.051", "walk")  # the other end stop replaced


@pytest.mark.parametrize("engine", ["graph", "legacy"])
def test_a_second_pass_replays_every_definitive_answer_without_a_request(
    engine: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    run = _Lifecycle(monkeypatch, tmp_path, engine, _AMBIGUOUS, unused=(_NEAR,))
    allowance = run.context.route_requests_left
    assert allowance == route_request_allowance(2) == 8

    run.run_pass()

    # Feasibility asks for both days. Sequencing and the buffers then read day 2's answer from the
    # generation's memory instead of asking again; the repair makes its two bounded verifications.
    assert run.requests == sorted([_DAY_ONE, _DAY_TWO, _FIRST_VERIFY, _SECOND_VERIFY])
    first = run.snapshot()
    assert first["days"] == [["Museum Alpha", "Museum Beta", "Museum Gamma"], ["Museum Delta", "Museum Nearby"]]
    assert first["repair"] == [(2, "accepted", True, 2)]
    assert [leg[2] for leg in first["legs"]] == ["success"] * 3 and first["buffers"] == ["success"] * 3
    used = allowance - run.context.route_requests_left
    credits = run.context.usage_tracker.credits_used()
    assert used == 4 and credits == 3  # two legs of day 1 and the accepted leg; a rejected request costs nothing

    # The AI repair re-enters the planner: the plan is rebuilt as it was and the whole chain runs again.
    run.run_pass()

    assert run.requests == sorted([_DAY_ONE, _DAY_TWO, _FIRST_VERIFY, _SECOND_VERIFY])  # zero HTTP requests
    assert len(run.network.requests["routing"]) == 4
    assert allowance - run.context.route_requests_left == used  # zero allowance
    assert run.context.usage_tracker.credits_used() == credits  # zero credits
    assert run.context.usage_tracker.snapshot()["refused_calls"] == 0
    assert run.snapshot() == first  # and the same repaired plan, not a refused repair
    assert len(run.context.route_failure_memo) == 2  # day 2 as planned, and the first verification


def test_both_engines_spend_the_allowance_identically(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    runs = []
    for engine in ("graph", "legacy"):
        # one after the other: each run owns the mocked HTTP layer while it runs
        run = _Lifecycle(monkeypatch, tmp_path, engine, _AMBIGUOUS, unused=(_NEAR,))
        run.run_pass()
        run.run_pass()
        runs.append((run.requests, run.snapshot(), run.context.route_requests_left, run.context.usage_tracker.credits_used()))
    assert runs[0] == runs[1]


@pytest.mark.parametrize("engine", ["graph", "legacy"])
def test_an_unroutable_day_with_no_repair_costs_one_request_however_often_it_is_read(
    engine: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    # no candidate and the plan is at R: nothing can be replaced or removed, the failure simply stands
    run = _Lifecycle(monkeypatch, tmp_path, engine, _AMBIGUOUS)
    allowance = run.context.route_requests_left

    for _ in range(3):
        run.run_pass()

    # feasibility, sequencing, the repair and the buffers of three passes: day 2 was asked for ONCE
    assert run.requests == sorted([_DAY_ONE, _DAY_TWO])
    assert allowance - run.context.route_requests_left == 2
    snapshot = run.snapshot()
    assert snapshot["days"][1] == ["Museum Delta", "Museum Unroutable"]
    assert snapshot["legs"][2][2:4] == ("failed", "no_route") and snapshot["buffers"][2] == "failed"
    assert snapshot["repair"] == [(2, "no_suitable_candidate", False, 0)]


@pytest.mark.parametrize("engine", ["graph", "legacy"])
def test_a_proven_suspect_is_removed_again_from_memory_on_the_second_pass(
    engine: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    # six stops (R = 5): the unroutable END stop has no replacement and is removed
    run = _Lifecycle(monkeypatch, tmp_path, engine, [[_A, _B, _C], [_X, _D, _E]])
    allowance = run.context.route_requests_left

    run.run_pass()

    day_two = ("50.052,10.052|50.05,10.05|50.054,10.05", "walk")
    localised = [("50.052,10.052|50.05,10.05", "walk"), ("50.05,10.05|50.054,10.05", "walk")]
    assert run.requests == sorted([_DAY_ONE, day_two, *localised])
    first = run.snapshot()
    assert first["days"][1] == ["Museum Delta", "Museum Epsilon"] and first["repair"] == [(2, "suspect_removed", True, 0)]
    assert [leg[2] for leg in first["legs"]] == ["success"] * 3

    run.run_pass()

    assert run.requests == sorted([_DAY_ONE, day_two, *localised])  # the second pass asked the provider for nothing
    assert allowance - run.context.route_requests_left == 4 and run.snapshot() == first


@pytest.mark.parametrize("concurrency", ["true", "false"])
def test_serial_and_concurrent_provider_io_give_the_same_lifecycle(
    concurrency: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    monkeypatch.setenv("PROVIDER_IO_CONCURRENCY_ENABLED", concurrency)
    get_settings.cache_clear()
    run = _Lifecycle(monkeypatch, tmp_path, "graph", _AMBIGUOUS, unused=(_NEAR,))
    run.run_pass()
    run.run_pass()
    assert run.requests == sorted([_DAY_ONE, _DAY_TWO, _FIRST_VERIFY, _SECOND_VERIFY])
    assert run.snapshot()["repair"] == [(2, "accepted", True, 2)] and run.context.route_requests_left == 4


def test_a_transient_failure_is_asked_for_again_by_the_next_stage(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    run = _Lifecycle(monkeypatch, tmp_path, "graph", _AMBIGUOUS, unused=(_NEAR,))
    outages = [httpx.Response(503)]

    def flaky(request: httpx.Request) -> httpx.Response:
        # every stop routes here: the point is only that a 503 is not an answer about the stops
        if request.url.params["waypoints"] == _DAY_TWO[0] and outages:
            return outages.pop()
        return _routes(request)

    run.network._routing = flaky

    run.run_pass()

    # feasibility got the 503; sequencing asked again and got the route
    assert run.requests.count(_DAY_TWO) == 2 and run.context.route_failure_memo == {}
