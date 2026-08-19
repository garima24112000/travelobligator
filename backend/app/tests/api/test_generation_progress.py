from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.services.planning_orchestrator as orchestrator_module
from app.models.planning_state import GENERATION_STAGE_KEYS, GenerationStageStatus, TravelGroupType, TripRequest
from app.services.planning_orchestrator import PlanningOrchestrator

# Tests for Step 163B: backend PlanningOrchestrator pipeline stage-progress
# bookkeeping (docs/13_llm_reasoning_pipeline.md section 44) and its
# read-only GET /trips/{trip_id}/generation-progress endpoint. This is
# backend-only, real pipeline stage progress -- never flight tracking, a
# real flight route, a real route/travel time, a booking status, or any
# other travel fact. Step 163C (section 45) later polls this same endpoint
# from the frontend purely for loading-UI display -- these backend-side
# tests are unaffected by that and still exercise the endpoint/model
# directly, with no dependency on any frontend code.


def _create_trip_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }
    payload.update(overrides)
    return payload


def assert_api_response_shape(body: dict[str, Any]) -> None:
    assert set(["success", "data", "message", "errors", "metadata"]).issubset(body.keys())
    assert isinstance(body["success"], bool)
    assert isinstance(body["errors"], list)
    assert isinstance(body["metadata"], dict)


# ---------------------------------------------------------------------------
# 3. create_trip initializes idle generation progress.
# ---------------------------------------------------------------------------


def test_create_trip_initializes_idle_generation_progress(client: TestClient) -> None:
    response = client.post("/trips", json=_create_trip_payload())
    assert response.status_code == 201
    body = response.json()
    assert_api_response_shape(body)

    progress = body["data"]["planning_state"]["generation_progress"]
    assert progress is not None
    assert progress["status"] == "idle"
    assert progress["current_stage"] is None
    assert progress["completed_stages"] == []
    assert progress["progress_percent"] == 0
    assert progress["total_stages"] == len(GENERATION_STAGE_KEYS)
    assert progress["is_real_backend_stage_progress"] is True


# ---------------------------------------------------------------------------
# 16a. GET /generation-progress returns idle/default before generation.
# ---------------------------------------------------------------------------


def test_generation_progress_endpoint_returns_idle_before_generate(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/generation-progress")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)

    assert body["data"]["trip_id"] == created_trip_id
    progress = body["data"]["generation_progress"]
    assert progress["status"] == "idle"
    assert progress["completed_stages"] == []
    assert progress["progress_percent"] == 0


# ---------------------------------------------------------------------------
# 16c. GET /generation-progress returns 404 for unknown trip_id.
# ---------------------------------------------------------------------------


def test_generation_progress_endpoint_returns_404_for_unknown_trip(
    client: TestClient,
) -> None:
    response = client.get("/trips/does-not-exist/generation-progress")
    assert response.status_code == 404
    body = response.json()
    assert_api_response_shape(body)
    assert body["success"] is False
    assert body["errors"][0]["code"] == "TRIP_NOT_FOUND"


# ---------------------------------------------------------------------------
# 16b, 4, 5, 6. GET /generation-progress returns completed/100 after
# generation, with the expected deterministic completed_stages, and never
# mutates state or triggers generation on repeat reads.
# ---------------------------------------------------------------------------


def test_generation_progress_endpoint_returns_completed_after_generate(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/generation-progress")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)

    progress = body["data"]["generation_progress"]
    assert progress["status"] == "completed"
    assert progress["progress_percent"] == 100
    assert progress["completed_stages"] == list(GENERATION_STAGE_KEYS)
    assert progress["is_real_backend_stage_progress"] is True

    # A repeat read returns the exact same data -- this endpoint never
    # mutates state or re-triggers generation.
    second_response = client.get(f"/trips/{generated_trip_id}/generation-progress")
    assert second_response.json()["data"]["generation_progress"] == progress


def test_generate_response_itself_carries_completed_generation_progress(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)

    progress = body["data"]["planning_state"]["generation_progress"]
    assert progress["status"] == "completed"
    assert progress["progress_percent"] == 100
    assert progress["completed_stages"] == list(GENERATION_STAGE_KEYS)
    assert progress["current_stage"] == "post_processing"


# ---------------------------------------------------------------------------
# 7. Stage order remains unchanged -- completed_stages records stages in
# the same deterministic order every time, matching GENERATION_STAGE_KEYS.
# ---------------------------------------------------------------------------


def test_completed_stages_order_matches_generation_stage_keys_order(
    client: TestClient,
) -> None:
    create_response = client.post("/trips", json=_create_trip_payload())
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    completed_stages = generate_response.json()["data"]["planning_state"][
        "generation_progress"
    ]["completed_stages"]

    assert completed_stages == [
        "traveler_profile",
        "destination_context",
        "candidate_quality",
        "ai_candidate_shadow",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation",
        "post_processing",
    ]


# ---------------------------------------------------------------------------
# Exception path: marks generation_progress failed before re-raising, and
# does not swallow or replace the original exception (Step 163B).
# ---------------------------------------------------------------------------


class _RaisingTravelerProfileService:
    def run(self, planning_state: Any) -> Any:
        raise RuntimeError("simulated stage failure")


def _trip_request() -> TripRequest:
    return TripRequest(
        primary_destination="Testville, Testland",
        origin_city="Home City",
        start_date="2026-08-10",
        end_date="2026-08-12",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )


def test_generate_full_plan_marks_generation_progress_failed_and_reraises() -> None:
    orchestrator = PlanningOrchestrator(
        traveler_profile_service=_RaisingTravelerProfileService()
    )
    planning_state = orchestrator.create_trip(_trip_request())

    with pytest.raises(RuntimeError, match="simulated stage failure"):
        orchestrator.generate_full_plan(planning_state.trip_id)

    reloaded = orchestrator.planning_state_repository.get_by_trip_id(planning_state.trip_id)
    assert reloaded is not None
    assert reloaded.generation_progress is not None
    assert reloaded.generation_progress.status == GenerationStageStatus.FAILED
    # current_stage stays pointed at whichever stage was running when it failed.
    assert reloaded.generation_progress.current_stage == "traveler_profile"
    assert reloaded.generation_progress.completed_stages == []


# ---------------------------------------------------------------------------
# 8. Existing /generate response shape remains valid.
# ---------------------------------------------------------------------------


def test_generate_response_shape_still_valid(client: TestClient, created_trip_id: str) -> None:
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    planning_state = body["data"]["planning_state"]
    assert planning_state["destination_context"] is not None
    assert planning_state["experience_plan"] is not None
    assert planning_state["validation_report"] is not None


# ---------------------------------------------------------------------------
# 11. Shadow-mode disabled default still leaves ai_candidate_proposal_batch
# and candidate_grounding_batch None (re-confirmed alongside the new
# generation_progress field, not just before this step).
# ---------------------------------------------------------------------------


def test_generation_progress_does_not_affect_default_shadow_mode_batches(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}")
    planning_state = response.json()["data"]["planning_state"]
    assert planning_state["ai_candidate_proposal_batch"] is None
    assert planning_state["candidate_grounding_batch"] is None
    assert planning_state["generation_progress"]["status"] == "completed"


# ---------------------------------------------------------------------------
# 12. LangGraph remains not wired into /generate.
# ---------------------------------------------------------------------------


def test_generate_full_plan_source_has_no_graph_reference() -> None:
    source = inspect.getsource(orchestrator_module.PlanningOrchestrator.generate_full_plan)
    assert "graph" not in source.lower()


def test_planning_orchestrator_module_does_not_import_graph_module() -> None:
    source = inspect.getsource(orchestrator_module)
    tree = ast.parse(source)
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
    assert not any("app.graphs" in name for name in imported_names)


# ---------------------------------------------------------------------------
# 9, 10. generation_progress never includes a fake travel/flight fact,
# route_time, booking status, price, rating, availability, or flight number.
# ---------------------------------------------------------------------------

_FORBIDDEN_FACTUAL_KEYS = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "booking_status",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
    "flight_number",
    "airline",
    "flight_route",
    "departure_time",
    "arrival_time",
}


def _collect_keys(value: object, keys: set[str]) -> None:
    if isinstance(value, dict):
        keys.update(value.keys())
        for nested in value.values():
            _collect_keys(nested, keys)
    elif isinstance(value, list):
        for item in value:
            _collect_keys(item, keys)


def test_generation_progress_response_has_no_forbidden_travel_fact_keys(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/generation-progress")
    progress = response.json()["data"]["generation_progress"]

    keys: set[str] = set()
    _collect_keys(progress, keys)
    overlap = keys & _FORBIDDEN_FACTUAL_KEYS
    assert overlap == set(), f"generation_progress has forbidden key(s): {overlap}"


# ---------------------------------------------------------------------------
# 13. Regeneration endpoint still refuses exactly as before.
# ---------------------------------------------------------------------------


def test_regenerate_endpoint_still_refuses_after_generation_progress_added(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "REGENERATION_NOT_AVAILABLE"


# ---------------------------------------------------------------------------
# 14. Frontend wiring boundary. Step 163B kept the frontend fully
# unaware of this feature; Step 163C (docs/13_llm_reasoning_pipeline.md
# section 45) intentionally wires frontend/lib/api.ts and
# frontend/app/page.tsx to the read-only /generation-progress endpoint for
# loading-UI display only. This test now asserts the *other* direction:
# the frontend never reaches past the response shape into backend-internal
# implementation details (the Python-only stage-key/status enum names),
# which would indicate real coupling beyond "poll this JSON endpoint and
# render a few of its fields."
# ---------------------------------------------------------------------------


def test_frontend_does_not_reference_backend_internal_generation_progress_names() -> (
    None
):
    repo_root = Path(__file__).resolve().parents[4]
    needles = ("GENERATION_STAGE_KEYS", "GenerationStageStatus")
    for subdir in ("frontend/app", "frontend/lib"):
        target = repo_root / subdir
        if not target.exists():
            continue
        for path in target.rglob("*"):
            if not path.is_file() or "node_modules" in path.parts:
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            for needle in needles:
                assert needle not in text, f"{path} unexpectedly references {needle}"
