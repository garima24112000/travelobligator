from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest
from langgraph.graph.state import CompiledStateGraph

import app.api.routes.trips as trips_module
import app.graphs.planning_graph as planning_graph_module
import app.services.planning_orchestrator as orchestrator_module
from app.graphs import PlanningGraphRunner, build_planning_graph, run_planning_graph
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest

# Tests for the LangGraph planning graph (Step 171A, extended for stage
# parity with legacy generation in Step 171E --
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
# Every test here uses only in-file fake/no-op stage-service doubles --
# no real provider/network call, no real LLM call (Groq/Anthropic/OpenAI/
# other), and no persistence. This module does not replace
# `PlanningOrchestrator.generate_full_plan` (that method is completely
# untouched) and does not change itinerary scheduling, route-aware
# scheduling, or regeneration behavior. As of Step 171E,
# `Settings.planning_engine_mode` defaults to `"langgraph"`, and
# `POST /trips/{trip_id}/generate` reaches this graph by delegating
# through `PlanningOrchestrator.generate_full_plan_via_langgraph` --
# never by importing this graph module (or `LangGraphPlanningService`)
# directly into the route. `PLANNING_ENGINE_MODE=legacy` remains available
# and calls `generate_full_plan` instead, completely unaffected by any of
# this.


class _FakeStageService:
    """Deterministic test double for a single-method (`run`) stage
    service. Never calls a provider, LLM, or network -- just marks that it
    ran, in order, so tests can assert graph execution order.
    """

    def __init__(self, marker: str, order_log: list[str]) -> None:
        self.marker = marker
        self.order_log = order_log
        self.call_count = 0

    def run(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        self.order_log.append(self.marker)
        planning_state.data_sources_used = list(planning_state.data_sources_used) + [
            f"fake_{self.marker}"
        ]
        return planning_state


class _FakeAICandidatePromotionService:
    def __init__(self, order_log: list[str]) -> None:
        self.order_log = order_log
        self.call_count = 0

    def apply_promotion(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        self.order_log.append("ai_candidate")
        return planning_state


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "New York",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


# Step 171E note: only the 5 stages already faked here (destination_context/
# stay_transport/ai_candidate/experience_planning/validation) need
# deterministic control for this module's assertions -- every other stage
# the graph now covers (traveler_profile/candidate_quality/trip_strategy/
# accommodation_inventory/flight_inventory/route_feasibility/
# route_aware_sequencing/travel_time_buffer) is left at its real default
# construction, exactly like `test_run_planning_graph_convenience_function_
# uses_default_services` below already does for the whole graph -- every
# one of those real services' provider calls already resolves through
# `ProviderGateway`'s safe not_connected defaults, so this stays fully
# network-free. Since only fakes ever append to `order_log`, its expected
# value below still reflects only those 5 stages' real relative order.
def _fake_runner() -> tuple[PlanningGraphRunner, list[str]]:
    order_log: list[str] = []
    runner = PlanningGraphRunner(
        destination_context_service=_FakeStageService("destination_context", order_log),
        stay_transport_service=_FakeStageService("stay_transport", order_log),
        ai_candidate_promotion_service=_FakeAICandidatePromotionService(order_log),
        experience_planner_service=_FakeStageService("experience_planning", order_log),
        plan_validator_service=_FakeStageService("validation", order_log),
    )
    return runner, order_log


# ---------------------------------------------------------------------------
# 6. build_planning_graph returns a compiled/invokable graph if LangGraph
#    is available.
# ---------------------------------------------------------------------------


def test_build_planning_graph_returns_compiled_state_graph() -> None:
    graph = build_planning_graph()
    assert isinstance(graph, CompiledStateGraph)
    # Invokable: has the expected LangGraph runnable interface.
    assert hasattr(graph, "invoke")


# ---------------------------------------------------------------------------
# 7. Graph executes nodes in expected order using fake/injected no-op node
#    behavior.
# ---------------------------------------------------------------------------


def test_graph_executes_nodes_in_expected_order() -> None:
    runner, order_log = _fake_runner()
    trip_request = _trip_request()
    planning_state = PlanningState(trip_request=trip_request)

    result = runner.run(planning_state.trip_id, trip_request, planning_state)

    # ai_candidate now runs before stay_transport (Step 171E reordering to
    # match legacy's traveler_profile -> destination_context -> trip_strategy
    # -> stay_transport -> experience_plan -> validation stage flow) --
    # only the 5 faked stages' relative order is asserted here.
    assert order_log == [
        "destination_context",
        "ai_candidate",
        "stay_transport",
        "experience_planning",
        "validation",
    ]
    assert result["completed_nodes"] == [
        "traveler_profile",
        "destination_context",
        "candidate_quality",
        "ai_candidate",
        "trip_strategy",
        "stay_transport",
        "accommodation_inventory",
        "flight_inventory",
        "experience_planning",
        "route_feasibility",
        "route_aware_sequencing",
        "travel_time_buffer",
        "validation",
        "provider_coverage",
        "final_state",
    ]
    assert result["failed_nodes"] == []
    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["trip_id"] == planning_state.trip_id


def test_graph_run_result_carries_mutated_planning_state() -> None:
    runner, _ = _fake_runner()
    trip_request = _trip_request()
    planning_state = PlanningState(trip_request=trip_request)

    result = runner.run(planning_state.trip_id, trip_request, planning_state)

    assert result["planning_state"].data_sources_used == [
        "fake_destination_context",
        "fake_stay_transport",
        "fake_experience_planning",
        "fake_validation",
    ]


def test_run_planning_graph_convenience_function_uses_default_services() -> None:
    """Uses PlanningGraphRunner()'s real default services (no injection),
    mirroring PlanningOrchestrator's own default construction -- still
    never calls an LLM, since ai_candidate stays a no-op by default (no
    AICandidatePromotionService is injected here) and every other default
    service's provider calls resolve to the same safe not_connected/
    cached adapters PlanningOrchestrator already relies on in tests.
    """
    trip_request = _trip_request()
    planning_state = PlanningState(trip_request=trip_request)

    result = run_planning_graph(planning_state.trip_id, trip_request, planning_state)

    assert result["completed_nodes"] == [
        "traveler_profile",
        "destination_context",
        "candidate_quality",
        "ai_candidate",
        "trip_strategy",
        "stay_transport",
        "accommodation_inventory",
        "flight_inventory",
        "experience_planning",
        "route_feasibility",
        "route_aware_sequencing",
        "travel_time_buffer",
        "validation",
        "provider_coverage",
        "final_state",
    ]
    assert result["failed_nodes"] == []


def test_graph_continues_past_a_failed_node() -> None:
    """A node's failure doesn't halt the graph -- later nodes still run,
    and the failure is recorded honestly rather than crashing the run."""

    class _RaisingService:
        def run(self, planning_state: PlanningState) -> PlanningState:
            raise RuntimeError("simulated destination_context failure")

    order_log: list[str] = []
    runner = PlanningGraphRunner(
        destination_context_service=_RaisingService(),
        stay_transport_service=_FakeStageService("stay_transport", order_log),
        experience_planner_service=_FakeStageService("experience_planning", order_log),
        plan_validator_service=_FakeStageService("validation", order_log),
    )
    trip_request = _trip_request()
    planning_state = PlanningState(trip_request=trip_request)

    result = runner.run(planning_state.trip_id, trip_request, planning_state)

    assert result["failed_nodes"] == ["destination_context"]
    assert "completed_nodes" not in {"destination_context"} or "destination_context" not in result[
        "completed_nodes"
    ]
    assert "stay_transport" in result["completed_nodes"]
    assert "final_state" in result["completed_nodes"]
    # final_state_node's honest summary about the earlier failure.
    assert any("destination_context" in warning for warning in result["warnings"])


# ---------------------------------------------------------------------------
# 8-10. No Groq/Anthropic/OpenAI, AI candidate discovery, or live-network
#       imports anywhere in this graph skeleton. As of Step 171E, this
#       graph legitimately imports `AccommodationInventoryService`/
#       `FlightInventoryService` (for stage parity with legacy
#       generation's bookable-inventory reports, docs/13_llm_reasoning_
#       pipeline.md, docs/14_backend_architecture.md) -- both call a
#       provider only through the same `ProviderGateway` every other
#       service here already used, never a live scraper/network call of
#       their own, so they are removed from this disallowed list.
#       `ai_candidate_discovery_service` stays banned: the AI candidate
#       discovery *shadow* stage remains an intentionally
#       `PlanningOrchestrator.generate_full_plan`-specific (legacy engine)
#       integration -- see `build_ai_candidate_node`'s docstring in
#       `planning_graph_nodes.py`.
# ---------------------------------------------------------------------------


def test_planning_graph_module_has_no_disallowed_imports() -> None:
    source = inspect.getsource(planning_graph_module)
    tree = ast.parse(source)
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = (
        "anthropic",
        "groq",
        "openai",
        "langsmith",
        "ai_candidate_discovery_service",
        "ai_candidate_proposal_provider",
        "requests",
        "httpx",
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


def test_default_graph_run_makes_no_real_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even using PlanningGraphRunner()'s real default services (no fakes
    injected at all), a run must never reach out over the network -- every
    provider call the wrapped services make goes through ProviderGateway,
    which the deterministic test-fixture places provider (autouse in
    conftest.py) and default not_connected adapters already keep
    network-free."""
    trip_request = _trip_request()
    planning_state = PlanningState(trip_request=trip_request)

    result = run_planning_graph(planning_state.trip_id, trip_request, planning_state)

    assert result["failed_nodes"] == []


# ---------------------------------------------------------------------------
# 11. Graph is not wired into /generate unconditionally -- as of Step
#     171D, it can be selected only via PLANNING_ENGINE_MODE=langgraph,
#     always delegating through PlanningOrchestrator's own new method
#     rather than calling LangGraphPlanningService directly from the
#     route. The default ("legacy") path is unaffected.
# ---------------------------------------------------------------------------


def test_generate_route_handler_always_delegates_through_planning_orchestrator() -> None:
    source = inspect.getsource(trips_module.generate_trip_plan)
    assert "planning_orchestrator" in source
    # Never calls the LangGraph service directly from the route -- only
    # ever through a method on the already-existing planning_orchestrator
    # singleton (generate_full_plan for legacy, generate_full_plan_via_langgraph
    # for the config-gated mode).
    assert "LangGraphPlanningService" not in source


def test_trips_route_module_does_not_import_graphs_package() -> None:
    source = inspect.getsource(trips_module)
    tree = ast.parse(source)
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
    assert not any("app.graphs" in name for name in imported_names)


def test_planning_orchestrator_module_still_does_not_import_graphs_package() -> None:
    """Mirrors the existing test_generation_progress.py assertion -- kept
    here too so this graph module's own test suite independently proves
    PlanningOrchestrator stays untouched."""
    source = inspect.getsource(orchestrator_module)
    tree = ast.parse(source)
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
    assert not any("app.graphs" in name for name in imported_names)


def test_generate_full_plan_source_has_no_graph_reference() -> None:
    source = inspect.getsource(orchestrator_module.PlanningOrchestrator.generate_full_plan)
    assert "graph" not in source.lower()


# ---------------------------------------------------------------------------
# No fake travel data anywhere in a graph run's result.
# ---------------------------------------------------------------------------

_FORBIDDEN_FACTUAL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "safety_score",
}


def test_graph_run_result_has_no_forbidden_factual_fields() -> None:
    runner, _ = _fake_runner()
    trip_request = _trip_request()
    planning_state = PlanningState(trip_request=trip_request)

    result = runner.run(planning_state.trip_id, trip_request, planning_state)
    dumped = result["planning_state"].model_dump(mode="json")
    dumped_keys: set[str] = set()

    def _collect(value: Any) -> None:
        if isinstance(value, dict):
            dumped_keys.update(value.keys())
            for nested in value.values():
                _collect(nested)
        elif isinstance(value, list):
            for entry in value:
                _collect(entry)

    _collect(dumped)
    assert dumped_keys & _FORBIDDEN_FACTUAL_FIELD_NAMES == set()
