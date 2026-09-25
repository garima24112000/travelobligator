from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Section 196 (docs/14_backend_architecture.md, following section 146)
# built `AIFeedbackInterpreterService`/`AIFeedbackInterpretationRequestBuilder`/
# the interpreter-capable provider adapters dormant -- not called by
# `POST /trips/{id}/feedback`, not called by `POST /trips/{id}/regenerate`,
# no LangGraph node, no `PlanningOrchestrator` stage. Sections 197A/197B
# built the deterministic plan compiler/executor on top, equally dormant.
#
# Section 197C (docs/14_backend_architecture.md, following section
# 149.1) is the intentional flip, mirroring the established convention
# Sections 193C/194B used for their own "not wired yet" suites:
# `POST /trips/{trip_id}/regenerate` now DOES wire the whole
# 196->197A->197B pipeline in, through exactly one new module,
# `app.services.targeted_regeneration_application_service`, and ONLY
# when `Settings.targeted_regeneration_enabled=True` (default `False`).
# `feedback_service.py`/`regeneration_mutation_service.py`/
# `planning_orchestrator.py`/`app/graphs/planning_graph_nodes.py` remain
# entirely untouched by this -- the legacy coarse regeneration path they
# implement is still exactly what runs whenever targeted mode is off,
# proven directly below by real HTTP calls with the flag left at its
# default. `test_api_routes_do_not_import_interpreter` (the fifth
# no-wiring check, for `app/api/routes/trips.py`) is retired below in
# favor of its own flipped counterpart -- everything else in this file is
# still exactly true and unchanged.


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


def _assert_no_interpreter_import(module: object) -> None:
    imported_names = _imported_module_names(module)
    assert not any("ai_feedback_interpreter_service" in name for name in imported_names)
    assert not any("ai_feedback_interpretation_request_builder" in name for name in imported_names)
    assert not any(name == "AIFeedbackInterpreterService" for name in imported_names)


def test_feedback_service_does_not_import_interpreter() -> None:
    import app.services.feedback_service as module

    _assert_no_interpreter_import(module)


def test_regeneration_mutation_service_does_not_import_interpreter() -> None:
    import app.services.regeneration_mutation_service as module

    _assert_no_interpreter_import(module)


def test_planning_orchestrator_does_not_import_interpreter() -> None:
    import app.services.planning_orchestrator as module

    _assert_no_interpreter_import(module)


def test_langgraph_nodes_do_not_import_interpreter() -> None:
    import app.graphs.planning_graph_nodes as module

    _assert_no_interpreter_import(module)


def test_api_routes_now_wire_targeted_regeneration_application_service() -> None:
    """Section 197C's intentional flip of the old `test_api_routes_do_not_
    import_interpreter` check: `app/api/routes/trips.py` now imports the
    ONE targeted-regeneration orchestration boundary
    (`targeted_regeneration_application_service`) -- never the interpreter/
    plan-builder/executor modules directly, and never any provider-vendor
    module (matching the same disallowed-vendor discipline
    `test_provider_package_calls_no_langgraph_or_disallowed_vendor` already
    enforces on the provider package itself).
    """
    import app.api.routes.trips as module

    imported_names = _imported_module_names(module)
    assert any("targeted_regeneration_application_service" in name for name in imported_names)
    # Never a direct import of the interpreter class or a vendor adapter --
    # only the application service boundary. (This route file legitimately
    # imports an unrelated pre-existing LangGraph shadow-run schema, so the
    # broader "no langgraph/vendor substring anywhere" check used for the
    # provider package itself doesn't apply to this whole route file.)
    assert not any(name == "AIFeedbackInterpreterService" for name in imported_names)
    assert not any("groq_adapter" in name or "anthropic_adapter" in name for name in imported_names)
    assert not any("langsmith" in name.lower() for name in imported_names)


def test_provider_package_calls_no_langgraph_or_disallowed_vendor() -> None:
    import app.providers.ai_feedback_interpreter.anthropic_adapter as anthropic_module
    import app.providers.ai_feedback_interpreter.factory as factory_module
    import app.providers.ai_feedback_interpreter.groq_adapter as groq_module
    import app.providers.ai_feedback_interpreter.not_connected_adapter as not_connected_module

    disallowed_substrings = ("langgraph", "langsmith", "openai_", "gemini")
    for module in (anthropic_module, factory_module, groq_module, not_connected_module):
        imported_names = _imported_module_names(module)
        for name in imported_names:
            lowered = name.lower()
            for disallowed in disallowed_substrings:
                assert disallowed not in lowered, f"Disallowed import found in {module.__name__}: {name}"


# ---------------------------------------------------------------------------
# Task 26 (Section 196) / Task 34 (Section 197C): current /feedback and
# /regenerate behavior is unchanged with targeted mode at its default
# (off) -- these three tests already prove Section 197C's own Task 34
# feature-disabled regression requirement, unmodified, since they never
# set TARGETED_REGENERATION_ENABLED and the default is False.
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


def test_feedback_endpoint_behavior_unchanged(client: TestClient) -> None:
    trip_id = _create_trip(client)
    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    feedback_response = client.post(
        f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove the museum, please."}
    )

    assert feedback_response.status_code == 200
    data = feedback_response.json()["data"]
    planning_state = data["planning_state"]
    assert len(planning_state["feedback_history"]) == 1
    event = planning_state["feedback_history"][0]
    # Deterministic keyword classification is still the only classifier
    # -- Section 196 never overwrites or replaces it with an AI-derived
    # interpretation, and the existing honest "no AI interpretation
    # provider is connected" note is still exactly true today.
    assert event["feedback_type"] == "remove_or_avoid"
    assert event["interpretation"]["method"] == "deterministic_rule_based"
    assert (
        "AI interpretation is not applied when feedback is captured."
        in event["interpretation"]["change_preview"]["blocked_by"]
    )


def test_regenerate_refuses_without_confirm_exactly_as_before(client: TestClient) -> None:
    trip_id = _create_trip(client)
    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove the museum, please."})

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": False})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NOT_AVAILABLE"


@pytest.mark.usefixtures("synthetic_legacy_regeneration_support")
def test_regenerate_success_path_unchanged(client: TestClient) -> None:
    trip_id = _create_trip(client)
    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove the museum, please."})

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "applied"
    assert data["current_version"] == "v2"
    assert len(data["applied_feedback_event_ids"]) == 1
    assert "experience_plan" in data["changed_sections"]

    trip_response = client.get(f"/trips/{trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    assert planning_state["feedback_history"][0]["handling_status"] == "applied"
    assert len(planning_state["version_history"]) == 2
