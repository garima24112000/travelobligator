from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Section 193B (docs/14_backend_architecture.md section 142) must not wire
# `AIItineraryReasoningService`/the new provider package into normal
# `POST /trips/{id}/generate` -- that is Section 193C's job. These tests
# prove the new provider/service exist but remain dormant, mirroring the
# exact import-absence pattern this repo already uses for every other
# not-yet-wired AI module (e.g. `test_ai_candidate_discovery_safety.py`'s
# own `_assert_no_discovery_service_import`, Section 193A's own
# `test_ai_itinerary_reasoning_request_builder.py` import-absence tests).


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
    import app.services.planning_orchestrator as orchestrator_module

    _assert_no_reasoning_service_import(orchestrator_module)


def test_langgraph_nodes_do_not_import_reasoning_service() -> None:
    import app.graphs.planning_graph_nodes as nodes_module

    _assert_no_reasoning_service_import(nodes_module)


def test_langgraph_planning_service_does_not_import_reasoning_service() -> None:
    import app.services.langgraph_planning_service as module

    _assert_no_reasoning_service_import(module)


def test_experience_planner_does_not_import_reasoning_service() -> None:
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
# Behavioral regression: a real /generate call never populates
# ai_itinerary_reasoning_result, even with AI_ITINERARY_REASONING_ENABLED=true
# set -- because nothing in the generation path calls the new service at
# all (structural import-absence tests above prove why).
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


def test_generate_never_populates_ai_itinerary_reasoning_result_even_when_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("AI_ITINERARY_REASONING_ENABLED", "true")
    get_settings.cache_clear()

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["ai_itinerary_reasoning_result"] is None

    get_settings.cache_clear()


def test_generate_behavior_unchanged_with_reasoning_disabled(client: TestClient) -> None:
    """Sanity baseline: the default (disabled) path also never populates
    the field -- both states produce byte-identical `None` for this
    additive field."""
    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")

    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["ai_itinerary_reasoning_result"] is None
