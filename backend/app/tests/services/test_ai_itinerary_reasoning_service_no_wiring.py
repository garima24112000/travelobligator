from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Section 193B wired the `AIItineraryReasoningProvider`/`AIItineraryReasoningService`
# layer but deliberately kept it dormant (not called by normal
# `POST /trips/{id}/generate`). Section 193C (docs/14_backend_architecture.md
# section 143) is what actually wires `AIItineraryReasoningService` into the
# LangGraph `ai_itinerary_reasoning` node, positioned right before
# `experience_planning`. This file's name/original tests predate that
# wiring -- the tests below have been updated to assert the real, now-live
# structure instead of the now-obsolete "nothing calls it yet" claim.
# `PlanningOrchestrator`/`PlanValidatorService`/the API routes still never
# import the reasoning service or provider package directly (they reach it
# only through the same layered LangGraph node -> service -> provider
# abstraction -> adapter chain every other AI stage in this repo uses) --
# those import-absence assertions remain accurate and are kept unchanged.


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


def _assert_no_reasoning_service_import(module: object) -> None:
    imported_names = _imported_module_names(module)
    assert not any("ai_itinerary_reasoning_service" in name for name in imported_names)
    assert not any(name == "AIItineraryReasoningService" for name in imported_names)
    assert not any("ai_itinerary_reasoning" in name and "providers" in name for name in imported_names)


def test_planning_orchestrator_does_not_import_reasoning_service() -> None:
    """PlanningOrchestrator reaches the reasoning stage only indirectly,
    through LangGraphPlanningService -> PlanningGraphRunner -> the graph
    node -- exactly the same layering it already uses for
    AICandidateDiscoveryService/AICandidatePromotionService."""
    import app.services.planning_orchestrator as orchestrator_module

    _assert_no_reasoning_service_import(orchestrator_module)


def test_langgraph_nodes_import_reasoning_service() -> None:
    """Section 193C: planning_graph_nodes.py now builds and calls
    AIItineraryReasoningService directly (build_ai_itinerary_reasoning_node)
    -- the one, intentional exception to this file's otherwise-unchanged
    import-absence assertions."""
    import app.graphs.planning_graph_nodes as nodes_module

    imported_names = _imported_module_names(nodes_module)
    assert any("ai_itinerary_reasoning_service" in name for name in imported_names)
    assert any(name == "AIItineraryReasoningService" for name in imported_names)
    # Still never imports a provider adapter/LangChain/Groq/Anthropic
    # client directly -- only through the service -> provider abstraction.
    assert not any("ai_itinerary_reasoning" in name and "providers" in name for name in imported_names)


def test_langgraph_planning_service_threads_reasoning_service_parameter() -> None:
    """Section 193C: LangGraphPlanningService now accepts and forwards an
    injectable ai_itinerary_reasoning_service, mirroring every other stage
    service parameter it already threads through to PlanningGraphRunner."""
    import inspect

    from app.services.langgraph_planning_service import LangGraphPlanningService

    signature = inspect.signature(LangGraphPlanningService.__init__)
    assert "ai_itinerary_reasoning_service" in signature.parameters


def test_experience_planner_does_not_import_reasoning_service() -> None:
    """ExperiencePlannerService reads app.models.ai_itinerary_reasoning
    (the contract models, to resolve an already-computed
    AIItineraryReasoningResult from PlanningState) but never constructs or
    calls AIItineraryReasoningService/a provider itself -- it only ever
    consumes what the earlier ai_itinerary_reasoning node already stored."""
    import app.services.experience_planner_service as module

    _assert_no_reasoning_service_import(module)


def test_plan_validator_does_not_import_reasoning_service() -> None:
    import app.services.plan_validator_service as module

    _assert_no_reasoning_service_import(module)


def test_api_routes_do_not_import_reasoning_service() -> None:
    import app.api.routes.trips as trips_routes_module

    _assert_no_reasoning_service_import(trips_routes_module)


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
# Behavioral regression (Task 15/18): with reasoning disabled (the
# default), a real /generate call now honestly records a `not_connected`
# ai_itinerary_reasoning_result (Section 193C's node always runs and
# always records a real status, exactly like every other stage in this
# repo -- e.g. ItineraryNarrativeService always sets
# itinerary_narrative_report even when its own feature is disabled) --
# but every OTHER part of the generated plan (experience_plan, routing,
# validation) stays exactly what the deterministic path alone would have
# produced.
# ---------------------------------------------------------------------------


def _create_trip_payload() -> dict[str, Any]:
    return {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }


def _create_trip(client: TestClient) -> str:
    response = client.post("/trips", json=_create_trip_payload())
    assert response.status_code == 201
    return response.json()["data"]["trip_id"]


def test_generate_records_not_connected_reasoning_by_default(client: TestClient) -> None:
    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    reasoning_result = planning_state["ai_itinerary_reasoning_result"]
    assert reasoning_result is not None
    assert reasoning_result["status"] == "not_connected"
    assert reasoning_result["days"] == []


def test_generate_experience_plan_unchanged_whether_reasoning_enabled_flag_is_set(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI_ITINERARY_REASONING_ENABLED=true alone (provider still
    "not_connected") must not change the deterministic experience_plan at
    all -- the reasoning stage still records an honest not_connected
    result (no provider configured), so ExperiencePlannerService still
    falls back to its existing deterministic path exactly as when the
    flag is off."""
    from app.core.config import get_settings

    trip_id_disabled = _create_trip(client)
    baseline_response = client.post(f"/trips/{trip_id_disabled}/generate")
    assert baseline_response.status_code == 200
    baseline_plan = baseline_response.json()["data"]["planning_state"]["experience_plan"]

    monkeypatch.setenv("AI_ITINERARY_REASONING_ENABLED", "true")
    get_settings.cache_clear()
    try:
        trip_id_enabled = _create_trip(client)
        enabled_response = client.post(f"/trips/{trip_id_enabled}/generate")
    finally:
        monkeypatch.delenv("AI_ITINERARY_REASONING_ENABLED", raising=False)
        get_settings.cache_clear()

    assert enabled_response.status_code == 200
    enabled_plan = enabled_response.json()["data"]["planning_state"]["experience_plan"]

    def _scheduled_names_by_day(plan: dict[str, Any]) -> list[list[str]]:
        return [
            [experience["name"] for experience in day["experiences"]]
            for day in plan["daily_plans"]
        ]

    assert _scheduled_names_by_day(enabled_plan) == _scheduled_names_by_day(baseline_plan)
    assert enabled_plan["assumptions"] == baseline_plan["assumptions"]
