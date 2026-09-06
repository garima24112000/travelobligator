from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.providers.ai_candidate_proposal.anthropic_adapter import AnthropicAICandidateProposalProvider
from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider
from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService
from app.services.destination_context_service import DestinationContextService

# API tests for the read-only LangGraph shadow-run endpoint
# (`POST /trips/{trip_id}/langgraph-shadow-run`, Step 171C,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
# This endpoint executes the Step 171A/171B LangGraph planning graph
# (`LangGraphPlanningService`) against an isolated deep copy of a trip's
# `PlanningState`, purely for inspection -- it is not the official
# `/generate` path and never persists anything. Every test here relies on
# the same deterministic, network-free test fixtures (autouse in
# conftest.py) the rest of the suite already uses -- no real
# provider/LLM/network call.


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


# ---------------------------------------------------------------------------
# 1. Unknown trip returns 404.
# ---------------------------------------------------------------------------


def test_shadow_run_unknown_trip_returns_404(client: TestClient) -> None:
    response = client.post("/trips/does-not-exist/langgraph-shadow-run")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"


# ---------------------------------------------------------------------------
# 2-4. Existing trip: completed_nodes in graph order, planning_state
#      included, persisted=false.
# ---------------------------------------------------------------------------


def test_shadow_run_on_existing_trip_returns_expected_shape(client: TestClient) -> None:
    trip_id = _create_trip(client)

    response = client.post(f"/trips/{trip_id}/langgraph-shadow-run")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]

    assert data["trip_id"] == trip_id
    assert data["status"] == "completed"
    assert data["persisted"] is False
    assert data["failed_nodes"] == []
    assert data["errors"] == []
    assert data["completed_nodes"] == [
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
    # planning_state is included and is a real PlanningState shape.
    assert data["planning_state"]["trip_id"] == trip_id
    assert data["planning_state"]["destination_context"] is not None
    assert data["planning_state"]["experience_plan"] is not None


# ---------------------------------------------------------------------------
# 5. Shadow run does not overwrite the stored PlanningState.
# ---------------------------------------------------------------------------


def test_shadow_run_does_not_overwrite_stored_planning_state(client: TestClient) -> None:
    trip_id = _create_trip(client)

    before_response = client.get(f"/trips/{trip_id}")
    before_state = before_response.json()["data"]["planning_state"]
    # Before the shadow run, this fresh trip has no destination_context/
    # experience_plan yet -- confirming the shadow run's own graph output
    # (which does compute them) is never fed back into the stored trip.
    assert before_state["destination_context"] is None
    assert before_state["experience_plan"] is None

    shadow_response = client.post(f"/trips/{trip_id}/langgraph-shadow-run")
    assert shadow_response.status_code == 200
    assert shadow_response.json()["data"]["planning_state"]["destination_context"] is not None

    after_response = client.get(f"/trips/{trip_id}")
    after_state = after_response.json()["data"]["planning_state"]

    assert after_state == before_state
    assert after_state["destination_context"] is None
    assert after_state["experience_plan"] is None
    assert after_state["metadata"]["updated_at"] == before_state["metadata"]["updated_at"]


def test_shadow_run_does_not_overwrite_an_already_generated_trip(client: TestClient) -> None:
    """Same guarantee, but starting from an already-generated trip -- the
    shadow run re-derives its own destination_context/experience_plan
    from a deep copy, but the stored trip's real generated content must
    stay byte-for-byte identical."""
    trip_id = _create_trip(client)
    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    before_response = client.get(f"/trips/{trip_id}")
    before_state = before_response.json()["data"]["planning_state"]

    shadow_response = client.post(f"/trips/{trip_id}/langgraph-shadow-run")
    assert shadow_response.status_code == 200

    after_response = client.get(f"/trips/{trip_id}")
    after_state = after_response.json()["data"]["planning_state"]

    assert after_state == before_state


# ---------------------------------------------------------------------------
# 6-8. Shadow run does not create a new trip version, does not modify
#      regeneration attempts, and does not modify locks.
# ---------------------------------------------------------------------------


def test_shadow_run_does_not_create_new_version_or_touch_regeneration_attempts_or_locks(
    client: TestClient,
) -> None:
    trip_id = _create_trip(client)
    client.post(f"/trips/{trip_id}/generate")

    before_response = client.get(f"/trips/{trip_id}")
    before_state = before_response.json()["data"]["planning_state"]

    client.post(f"/trips/{trip_id}/langgraph-shadow-run")

    after_response = client.get(f"/trips/{trip_id}")
    after_state = after_response.json()["data"]["planning_state"]

    assert after_state["version_history"] == before_state["version_history"]
    assert after_state["regeneration_attempts"] == before_state["regeneration_attempts"]
    assert after_state["user_locks"] == before_state["user_locks"]
    assert after_state["plan_diff_preview"] == before_state["plan_diff_preview"]
    assert after_state["regeneration_readiness"] == before_state["regeneration_readiness"]


# ---------------------------------------------------------------------------
# 9. Shadow run does not call POST /generate internally.
# ---------------------------------------------------------------------------


def test_shadow_run_handler_does_not_reference_generate_or_orchestrator() -> None:
    """Checks the handler's actual code (not its docstring, which
    legitimately mentions `PlanningOrchestrator.generate_full_plan` in
    prose to explain what this endpoint is *not*) never calls it."""
    import ast

    import app.api.routes.trips as trips_module

    tree = ast.parse(inspect.getsource(trips_module.run_langgraph_shadow))
    function_def = tree.body[0]
    assert isinstance(function_def, ast.FunctionDef)
    body_source = "\n".join(
        ast.unparse(statement)
        for statement in function_def.body
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    )
    assert "generate_full_plan" not in body_source
    assert "planning_orchestrator" not in body_source


# ---------------------------------------------------------------------------
# 10. POST /trips/{trip_id}/generate still uses PlanningOrchestrator, not
#     LangGraph.
# ---------------------------------------------------------------------------


def test_generate_endpoint_by_default_uses_orchestrator_legacy_path(client: TestClient) -> None:
    """As of Step 171D, `POST /generate` can select a LangGraph-backed
    path via `Settings.planning_engine_mode == "langgraph"` (see
    `test_langgraph_generate_mode.py`) -- so the route handler's source
    now legitimately mentions LangGraph/`generate_full_plan_via_langgraph`.
    What still holds regardless of config: the route always
    delegates through `planning_orchestrator` and never calls
    `LangGraphPlanningService` directly, and with the *default* config
    (this test's setup, via the autouse env-isolation fixture), a normal
    `/generate` call still produces the same `PlanningOrchestrator`-owned
    fields as before this step.
    """
    import app.api.routes.trips as trips_module

    source = inspect.getsource(trips_module.generate_trip_plan)
    assert "planning_orchestrator" in source
    assert "LangGraphPlanningService" not in source

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/generate")
    assert response.status_code == 200
    assert response.json()["data"]["planning_state"]["experience_plan"] is not None


# ---------------------------------------------------------------------------
# 11. LangGraph endpoint uses LangGraphPlanningService.
# ---------------------------------------------------------------------------


def test_shadow_run_handler_uses_langgraph_planning_service() -> None:
    import app.api.routes.trips as trips_module

    source = inspect.getsource(trips_module.run_langgraph_shadow)
    assert "LangGraphPlanningService" in source


def test_shadow_run_actually_invokes_langgraph_planning_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.langgraph_planning_service as service_module

    call_count = 0
    original_run = service_module.LangGraphPlanningService.run

    def _spy_run(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(service_module.LangGraphPlanningService, "run", _spy_run)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/langgraph-shadow-run")

    assert response.status_code == 200
    assert call_count == 1


# ---------------------------------------------------------------------------
# 12-13. Node failure appears in failed_nodes/errors, and does not
#        fabricate PlanningState fields.
# ---------------------------------------------------------------------------


def test_shadow_run_node_failure_is_reported_safely(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(self: Any, planning_state: Any) -> Any:
        raise RuntimeError("simulated destination_context failure")

    monkeypatch.setattr(DestinationContextService, "run", _raise)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/langgraph-shadow-run")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "completed_with_failures"
    assert data["failed_nodes"] == ["destination_context"]
    assert len(data["errors"]) == 1
    assert "simulated" not in data["errors"][0]
    # The graph still continues to later nodes.
    assert "final_state" in data["completed_nodes"]
    # No fabricated destination_context: it stays None, exactly as a
    # brand-new trip's planning_state already had it, rather than a
    # guessed/partial value.
    assert data["planning_state"]["destination_context"] is None


# ---------------------------------------------------------------------------
# 14. No Groq/Anthropic/OpenAI calls.
# ---------------------------------------------------------------------------


def test_shadow_run_does_not_call_anthropic_or_groq(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("Anthropic/Groq provider must never be called by the shadow endpoint.")

    monkeypatch.setattr(AnthropicAICandidateProposalProvider, "propose", _fail)
    monkeypatch.setattr(GroqAICandidateProposalProvider, "propose", _fail)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/langgraph-shadow-run")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 15. No AI candidate proposal provider calls directly.
# ---------------------------------------------------------------------------


def test_shadow_run_does_not_call_ai_candidate_discovery_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(self: Any, *args: object, **kwargs: object) -> None:
        raise AssertionError(
            "AICandidateDiscoveryService.dry_run must never be called by the shadow "
            "endpoint's default ai_candidate node."
        )

    monkeypatch.setattr(AICandidateDiscoveryService, "dry_run", _fail)

    trip_id = _create_trip(client)
    response = client.post(f"/trips/{trip_id}/langgraph-shadow-run")
    assert response.status_code == 200
