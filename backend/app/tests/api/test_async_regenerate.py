from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.generation_job import GenerationJobType, create_queued_job
from app.repositories.job_repository import job_repository
from app.services.planning_orchestrator import planning_orchestrator

# API-level tests for async `POST /trips/{trip_id}/regenerate` (Step 186C,
# docs/14_backend_architecture.md section 117). Outcomes 1-4 (confirm/
# locks/pending-feedback/derivable-stage refusals) must stay exactly
# synchronous and byte-identical to the sync-mode error codes regardless
# of ASYNC_GENERATION_ENABLED -- only outcome 5 (the real mutation) moves
# to a background job when the flag is on.
#
# Deliberately does NOT use the `async_generation_enabled` fixture
# together with `generated_trip_id`: the shared `generated_trip_id`
# fixture (conftest.py) itself calls `POST /generate` and asserts a
# synchronous `200` -- if async mode were already enabled by the time
# that fixture runs, its own setup would break (get a `202` instead).
# `_enable_async_generation` below is called from inside each test body
# instead, strictly after every fixture (including `generated_trip_id`)
# has already finished its own setup.


def _enable_async_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()


def _owner_id(client: TestClient) -> str:
    return client.get("/auth/me").json()["data"]["user"]["user_id"]


def _submit_affecting_feedback(client: TestClient, trip_id: str) -> None:
    response = client.post(
        f"/trips/{trip_id}/feedback",
        json={"feedback_text": "Make this less packed, it's too much walking"},
    )
    assert response.status_code == 200


def test_async_regenerate_missing_confirm_stays_synchronous_refusal(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_async_generation(monkeypatch)

    response = client.post(f"/trips/{generated_trip_id}/regenerate")

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NOT_AVAILABLE"
    assert job_repository.list_by_trip_id(generated_trip_id) == []


def test_async_regenerate_blocked_by_locks_stays_synchronous_refusal(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _submit_affecting_feedback(client, generated_trip_id)
    lock_response = client.post(
        f"/trips/{generated_trip_id}/locks",
        json={"locked_item_type": "day_plan", "locked_item_id": "day_1"},
    )
    assert lock_response.status_code == 201

    _enable_async_generation(monkeypatch)
    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_BLOCKED_BY_LOCKS"
    assert job_repository.list_by_trip_id(generated_trip_id) == []


def test_async_regenerate_no_pending_feedback_stays_synchronous_refusal(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_async_generation(monkeypatch)

    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NO_PENDING_FEEDBACK"
    assert job_repository.list_by_trip_id(generated_trip_id) == []


def test_async_regenerate_allowed_returns_202_with_queued_job(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _submit_affecting_feedback(client, generated_trip_id)
    _enable_async_generation(monkeypatch)

    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["job_type"] == "regenerate"
    assert data["trip_id"] == generated_trip_id
    assert data["progress_stage"] is not None


def test_async_regenerate_success_creates_version_and_marks_feedback_applied(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _submit_affecting_feedback(client, generated_trip_id)
    _enable_async_generation(monkeypatch)

    start_response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})
    job_id = start_response.json()["data"]["job_id"]

    job_response = client.get(f"/trips/{generated_trip_id}/jobs/{job_id}")
    job_data = job_response.json()["data"]
    assert job_data["status"] == "succeeded"
    assert job_data["result_version"] == "v2"
    assert job_data["changed_sections"]

    trip_response = client.get(f"/trips/{generated_trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    assert len(planning_state["version_history"]) == 2
    applied_events = [
        event for event in planning_state["feedback_history"] if event["applied_at"] is not None
    ]
    assert applied_events


def test_async_regenerate_failure_marks_job_failed_and_does_not_fake_success(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _submit_affecting_feedback(client, generated_trip_id)

    before_state = client.get(f"/trips/{generated_trip_id}").json()["data"]["planning_state"]
    version_count_before = len(before_state["version_history"])

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated rerun failure")

    monkeypatch.setattr(planning_orchestrator, "rerun_affected_stages", _raise)
    _enable_async_generation(monkeypatch)

    start_response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})
    assert start_response.status_code == 202
    job_id = start_response.json()["data"]["job_id"]

    job_data = client.get(f"/trips/{generated_trip_id}/jobs/{job_id}").json()["data"]
    assert job_data["status"] == "failed"
    assert job_data["error_code"] == "REGENERATION_NOT_AVAILABLE"
    assert "simulated rerun failure" not in (job_data["error_message"] or "")

    after_state = client.get(f"/trips/{generated_trip_id}").json()["data"]["planning_state"]
    assert len(after_state["version_history"]) == version_count_before
    assert all(event["applied_at"] is None for event in after_state["feedback_history"])
    assert after_state["regeneration_attempts"][-1]["status"] == "failed"


def test_async_regenerate_duplicate_running_job_returns_409(
    client: TestClient, generated_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = _owner_id(client)
    _submit_affecting_feedback(client, generated_trip_id)
    existing_job = create_queued_job(
        trip_id=generated_trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE
    )
    job_repository.create(existing_job)

    _enable_async_generation(monkeypatch)
    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "JOB_ALREADY_RUNNING"


def test_sync_regenerate_default_is_unaffected_by_async_job_machinery(
    client: TestClient, generated_trip_id: str
) -> None:
    """Sanity re-check: with the default ASYNC_GENERATION_ENABLED=false
    (never touched in this test), /regenerate still refuses synchronously
    with the pre-existing error code, and no job is ever created."""
    response = client.post(f"/trips/{generated_trip_id}/regenerate")

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NOT_AVAILABLE"
    assert job_repository.list_by_trip_id(generated_trip_id) == []
