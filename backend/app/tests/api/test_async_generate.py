from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.generation_job import GenerationJobType, create_queued_job, mark_job_succeeded
from app.repositories.job_repository import job_repository
from app.services.planning_orchestrator import planning_orchestrator
from app.tests.conftest import create_trip_payload

# API-level tests for async `POST /trips/{trip_id}/generate` (Step 186C,
# docs/14_backend_architecture.md section 117), gated by
# `ASYNC_GENERATION_ENABLED` (the `async_generation_enabled` fixture in
# conftest.py). FastAPI's `BackgroundTasks` are awaited as part of the
# same ASGI response cycle `TestClient` waits on, so by the time
# `client.post(...)` returns here, the dispatched job has already run to
# completion -- no sleep/poll loop is needed to observe its terminal
# state.


def _owner_id(client: TestClient) -> str:
    return client.get("/auth/me").json()["data"]["user"]["user_id"]


class _RaisingTravelerProfileService:
    def run(self, planning_state: Any) -> Any:
        raise RuntimeError("simulated stage failure")


def test_async_generate_returns_202_with_queued_job_envelope(
    async_generation_enabled: None, client: TestClient, created_trip_id: str
) -> None:
    response = client.post(f"/trips/{created_trip_id}/generate")

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["trip_id"] == created_trip_id
    assert data["job_type"] == "generate"
    assert data["job_id"].startswith("job_")
    # No `planning_state`/full plan content in this response at all.
    assert "planning_state" not in data


def test_async_generate_job_reaches_succeeded_and_saves_final_plan(
    async_generation_enabled: None, client: TestClient, created_trip_id: str
) -> None:
    start_response = client.post(f"/trips/{created_trip_id}/generate")
    job_id = start_response.json()["data"]["job_id"]

    # By this point (TestClient already awaited the background task as
    # part of the prior request's ASGI cycle) the job has already run to
    # completion.
    job_response = client.get(f"/trips/{created_trip_id}/jobs/{job_id}")
    assert job_response.status_code == 200
    job_data = job_response.json()["data"]
    assert job_data["status"] == "succeeded"
    assert job_data["started_at"] is not None
    assert job_data["finished_at"] is not None
    assert job_data["result_version"] == "v1"
    assert job_data["error_code"] is None
    assert job_data["error_message"] is None

    trip_response = client.get(f"/trips/{created_trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    assert planning_state["experience_plan"] is not None
    assert planning_state["validation_report"] is not None


def test_async_generate_existing_generation_progress_endpoint_still_works(
    async_generation_enabled: None, client: TestClient, created_trip_id: str
) -> None:
    client.post(f"/trips/{created_trip_id}/generate")

    progress_response = client.get(f"/trips/{created_trip_id}/generation-progress")
    assert progress_response.status_code == 200
    progress = progress_response.json()["data"]["generation_progress"]
    assert progress["status"] == "completed"
    assert progress["progress_percent"] == 100


def test_async_generate_job_failure_marks_job_failed_and_generation_progress_failed(
    async_generation_enabled: None,
    client: TestClient,
    created_trip_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLANNING_ENGINE_MODE", "legacy")
    get_settings.cache_clear()
    monkeypatch.setattr(
        planning_orchestrator, "traveler_profile_service", _RaisingTravelerProfileService()
    )

    start_response = client.post(f"/trips/{created_trip_id}/generate")
    get_settings.cache_clear()
    assert start_response.status_code == 202
    job_id = start_response.json()["data"]["job_id"]

    job_response = client.get(f"/trips/{created_trip_id}/jobs/{job_id}")
    job_data = job_response.json()["data"]
    assert job_data["status"] == "failed"
    assert job_data["error_code"] == "STAGE_FAILED"
    assert job_data["error_message"]
    assert "RuntimeError" not in job_data["error_message"]
    assert "Traceback" not in job_data["error_message"]

    progress_response = client.get(f"/trips/{created_trip_id}/generation-progress")
    progress = progress_response.json()["data"]["generation_progress"]
    assert progress["status"] == "failed"


def test_async_generate_duplicate_running_job_returns_409(
    async_generation_enabled: None, client: TestClient, created_trip_id: str
) -> None:
    owner_id = _owner_id(client)
    existing_job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(existing_job)

    response = client.post(f"/trips/{created_trip_id}/generate")

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "JOB_ALREADY_RUNNING"


def test_async_generate_terminal_job_does_not_block_new_job(
    async_generation_enabled: None, client: TestClient, created_trip_id: str
) -> None:
    owner_id = _owner_id(client)
    finished_job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    mark_job_succeeded(finished_job)
    job_repository.create(finished_job)

    response = client.post(f"/trips/{created_trip_id}/generate")

    assert response.status_code == 202


def test_sync_generate_default_is_unaffected_by_async_job_machinery(
    client: TestClient, created_trip_id: str
) -> None:
    """Sanity re-check alongside the async tests above: with the default
    ASYNC_GENERATION_ENABLED=false (no fixture applied here), /generate
    still returns 200 with the full plan synchronously, and no job is
    ever created -- see also test_async_job_foundation_noop.py."""
    response = client.post(f"/trips/{created_trip_id}/generate")

    assert response.status_code == 200
    assert response.json()["data"]["planning_state"]["experience_plan"] is not None
    assert job_repository.list_by_trip_id(created_trip_id) == []
