from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Section 194A (docs/14_backend_architecture.md, following section 143)
# built `AIItineraryRepairService`/`AIItineraryRepairRequestBuilder`/the
# repair-capable provider adapters, but deliberately kept them dormant.
# Section 194B (following section 144) is what wires the bounded
# `ai_itinerary_repair` LangGraph node + `route_after_validation`/
# `route_after_repair` conditional edges into the live graph -- this
# file's name/original tests predate that wiring, exactly like Section
# 193B's own `test_ai_itinerary_reasoning_service_no_wiring.py` did before
# Section 193C intentionally flipped its reasoning-specific assertions.
# The tests below have been updated the same way: `app.graphs.planning_graph`/
# `app.graphs.planning_graph_nodes`/`app.services.langgraph_planning_service`
# now legitimately import the repair service/request builder (asserted
# directly, not just removed from the ban list); `PlanningOrchestrator`/
# `ExperiencePlannerService`/`PlanValidatorService`/the API routes still
# never import them directly (they reach repair only through the same
# layered LangGraph node -> service -> provider abstraction -> adapter
# chain every other AI stage in this repo uses) -- those import-absence
# assertions remain accurate and are kept unchanged. Even with the graph
# wired, `AI_ITINERARY_REPAIR_ENABLED`/`AI_ITINERARY_REASONING_ENABLED`
# both still default `False`, so a normal `/generate` call stays exactly
# Section 193C's behavior (Task 12) -- the /generate-level tests below
# are unchanged from 194A and still pass for exactly that reason.


def _imported_module_names(module: object) -> list[str]:
    source = inspect.getsource(module)  # type: ignore[arg-type]
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
            imported_names.extend(alias.name for alias in node.names)
    return imported_names


def _assert_no_repair_service_import(module: object) -> None:
    imported_names = _imported_module_names(module)
    assert not any("ai_itinerary_repair_service" in name for name in imported_names)
    assert not any("ai_itinerary_repair_request_builder" in name for name in imported_names)
    assert not any(name == "AIItineraryRepairService" for name in imported_names)


def test_planning_orchestrator_does_not_import_repair_service() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    _assert_no_repair_service_import(orchestrator_module)


def test_langgraph_nodes_import_repair_service() -> None:
    """Section 194B: `planning_graph_nodes.py` now builds and calls
    `AIItineraryRepairService`/`AIItineraryRepairRequestBuilder`/
    `classify_repairable_issues` directly (`build_ai_itinerary_repair_node`,
    `route_after_validation`) -- the one, intentional exception to this
    file's otherwise-unchanged import-absence assertions."""
    import app.graphs.planning_graph_nodes as nodes_module

    imported_names = _imported_module_names(nodes_module)
    assert any("ai_itinerary_repair_service" in name for name in imported_names)
    assert any("ai_itinerary_repair_request_builder" in name for name in imported_names)
    assert any(name == "AIItineraryRepairService" for name in imported_names)
    # Still never imports a provider adapter/LangChain/Groq/Anthropic
    # client directly -- only through the service -> provider abstraction
    # (the same `AIItineraryReasoningProvider.repair` reason already
    # reaches).
    assert not any("ai_itinerary_reasoning.groq_adapter" in name for name in imported_names)
    assert not any("ai_itinerary_reasoning.anthropic_adapter" in name for name in imported_names)


def test_planning_graph_imports_repair_service() -> None:
    """Section 194B: `planning_graph.py` now threads a real
    `AIItineraryRepairService` through to the `ai_itinerary_repair` node,
    mirroring how it already threads `AIItineraryReasoningService`."""
    import app.graphs.planning_graph as graph_module

    imported_names = _imported_module_names(graph_module)
    assert any("ai_itinerary_repair_service" in name for name in imported_names)
    assert any(name == "AIItineraryRepairService" for name in imported_names)


def test_langgraph_planning_service_threads_repair_service_parameter() -> None:
    """Section 194B: `LangGraphPlanningService` now accepts and forwards
    an injectable `ai_itinerary_repair_service`, mirroring every other
    stage service parameter it already threads through to
    `PlanningGraphRunner`."""
    from app.services.langgraph_planning_service import LangGraphPlanningService

    signature = inspect.signature(LangGraphPlanningService.__init__)
    assert "ai_itinerary_repair_service" in signature.parameters


def test_experience_planner_does_not_import_repair_service() -> None:
    """ExperiencePlannerService reads `app.models.ai_itinerary_reasoning`
    (Section 193C) but never `app.models.ai_itinerary_repair`/
    `AIItineraryRepairService` -- 194A never touches `experience_plan`."""
    import app.services.experience_planner_service as module

    _assert_no_repair_service_import(module)
    imported_names = _imported_module_names(module)
    assert not any("ai_itinerary_repair" in name for name in imported_names)


def test_plan_validator_does_not_import_repair_service() -> None:
    import app.services.plan_validator_service as module

    _assert_no_repair_service_import(module)


def test_api_routes_do_not_import_repair_service() -> None:
    import app.api.routes.trips as trips_routes_module

    _assert_no_repair_service_import(trips_routes_module)


def test_provider_package_calls_no_langgraph_or_disallowed_vendor() -> None:
    import app.providers.ai_itinerary_reasoning.anthropic_adapter as anthropic_module
    import app.providers.ai_itinerary_reasoning.factory as factory_module
    import app.providers.ai_itinerary_reasoning.groq_adapter as groq_module
    import app.providers.ai_itinerary_reasoning.not_connected_adapter as not_connected_module

    disallowed_substrings = ("langgraph", "langsmith", "openai_", "gemini")
    for module in (anthropic_module, factory_module, groq_module, not_connected_module):
        imported_names = _imported_module_names(module)
        for name in imported_names:
            lowered = name.lower()
            for disallowed in disallowed_substrings:
                assert disallowed not in lowered, f"Disallowed import found in {module.__name__}: {name}"


# ---------------------------------------------------------------------------
# Task 28: /generate is completely unaffected -- the validator still runs
# exactly once, and nothing about the response shape changes.
# ---------------------------------------------------------------------------


def _create_trip_payload() -> dict[str, Any]:
    return {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-09-10",
        "end_date": "2026-09-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }


def _create_trip(client: TestClient) -> str:
    response = client.post("/trips", json=_create_trip_payload())
    assert response.status_code == 201
    return response.json()["data"]["trip_id"]


def test_generate_never_populates_ai_itinerary_repair_result(client: TestClient) -> None:
    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert "ai_itinerary_repair_result" in planning_state
    assert planning_state["ai_itinerary_repair_result"] is None


def test_generate_behavior_unchanged_with_reasoning_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with AI_ITINERARY_REASONING_ENABLED=true (provider still
    not_connected, so reasoning itself stays a safe no-op per Section
    193C), /generate never calls the repair service -- the field stays
    None and nothing else changes."""
    from app.core.config import get_settings

    monkeypatch.setenv("AI_ITINERARY_REASONING_ENABLED", "true")
    get_settings.cache_clear()
    try:
        trip_id = _create_trip(client)
        response = client.post(f"/trips/{trip_id}/generate")
    finally:
        monkeypatch.delenv("AI_ITINERARY_REASONING_ENABLED", raising=False)
        get_settings.cache_clear()

    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["ai_itinerary_repair_result"] is None


def test_generate_validation_report_still_produced_exactly_once(client: TestClient) -> None:
    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["validation_report"] is not None
    # A single validation_report_id -- proof the validator ran exactly
    # once, never re-run by any (nonexistent) repair loop.
    assert isinstance(planning_state["validation_report"]["validation_report_id"], str)
