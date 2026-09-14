from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.generation_job import GenerationJobType, create_queued_job, mark_job_running
from app.repositories.job_repository import job_repository
from app.tests.conftest import create_trip_payload

# API-level tests for the read-only async job endpoints (Step 186C,
# docs/14_backend_architecture.md section 117):
# GET /trips/{trip_id}/jobs and GET /trips/{trip_id}/jobs/{job_id}.
# Ownership/401/403 coverage for these two routes also lives in the full
# matrix in test_trip_route_auth_enforcement.py -- these tests focus on
# job-specific behavior the generic matrix doesn't exercise: cross-trip
# job isolation, empty-list-before-any-job, and multi-trip scoping.


def _enable_async_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()


def test_jobs_list_is_empty_before_any_job_exists(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/jobs")

    assert response.status_code == 200
    assert response.json()["data"]["jobs"] == []


def test_jobs_list_unknown_trip_returns_404(client: TestClient) -> None:
    response = client.get("/trips/does-not-exist/jobs")
    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "TRIP_NOT_FOUND"


def test_job_by_id_unknown_job_returns_404(client: TestClient, created_trip_id: str) -> None:
    response = client.get(f"/trips/{created_trip_id}/jobs/job_does_not_exist")

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "JOB_NOT_FOUND"


def test_owner_can_list_and_get_own_job(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_async_generation(monkeypatch)
    start_response = client.post(f"/trips/{created_trip_id}/generate")
    job_id = start_response.json()["data"]["job_id"]

    list_response = client.get(f"/trips/{created_trip_id}/jobs")
    assert list_response.status_code == 200
    jobs = list_response.json()["data"]["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == job_id

    get_response = client.get(f"/trips/{created_trip_id}/jobs/{job_id}")
    assert get_response.status_code == 200
    assert get_response.json()["data"]["job_id"] == job_id


def test_wrong_user_cannot_list_or_get_another_users_job(
    client: TestClient,
    second_client: TestClient,
    created_trip_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_async_generation(monkeypatch)
    start_response = client.post(f"/trips/{created_trip_id}/generate")
    job_id = start_response.json()["data"]["job_id"]

    list_response = second_client.get(f"/trips/{created_trip_id}/jobs")
    assert list_response.status_code == 403
    assert list_response.json()["errors"][0]["code"] == "FORBIDDEN"

    get_response = second_client.get(f"/trips/{created_trip_id}/jobs/{job_id}")
    assert get_response.status_code == 403
    assert get_response.json()["errors"][0]["code"] == "FORBIDDEN"


def test_unauthenticated_cannot_list_or_get_jobs(created_trip_id: str) -> None:
    from app.main import app

    unauthenticated_client = TestClient(app)

    list_response = unauthenticated_client.get(f"/trips/{created_trip_id}/jobs")
    assert list_response.status_code == 401
    assert list_response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"

    get_response = unauthenticated_client.get(
        f"/trips/{created_trip_id}/jobs/job_placeholder"
    )
    assert get_response.status_code == 401
    assert get_response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


def test_job_from_a_different_trip_returns_404_under_this_trip_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job that genuinely exists (under trip A, owned by this same
    user) must still 404 as JOB_NOT_FOUND when looked up under a
    different trip_id (trip B) -- proving the lookup is trip-scoped, not
    just owner-scoped, and never leaks that the job exists elsewhere."""
    _enable_async_generation(monkeypatch)

    trip_a_response = client.post("/trips", json=create_trip_payload())
    trip_a_id = trip_a_response.json()["data"]["trip_id"]
    job_response = client.post(f"/trips/{trip_a_id}/generate")
    job_id = job_response.json()["data"]["job_id"]

    trip_b_response = client.post("/trips", json=create_trip_payload())
    trip_b_id = trip_b_response.json()["data"]["trip_id"]

    response = client.get(f"/trips/{trip_b_id}/jobs/{job_id}")

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "JOB_NOT_FOUND"


def test_job_response_never_exposes_owner_id(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`owner_id` is deliberately omitted from the public job schema (see
    JobResponseData's own docstring) -- every job endpoint is already
    owner-scoped via require_trip_owner, so echoing it back would add no
    information for the caller and is simplest to just never expose."""
    _enable_async_generation(monkeypatch)
    start_response = client.post(f"/trips/{created_trip_id}/generate")
    assert "owner_id" not in start_response.json()["data"]

    job_id = start_response.json()["data"]["job_id"]
    get_response = client.get(f"/trips/{created_trip_id}/jobs/{job_id}")
    assert "owner_id" not in get_response.json()["data"]

    list_response = client.get(f"/trips/{created_trip_id}/jobs")
    assert all("owner_id" not in job for job in list_response.json()["data"]["jobs"])


# ---------------------------------------------------------------------------
# Stale-job reconciliation surfaced through the read endpoints (Step
# 186E, docs/14_backend_architecture.md section 118). These endpoints
# never fabricate a "running" status once the staleness window has
# genuinely passed -- they reconcile before responding, so the owner
# always sees an honest, current status.
# ---------------------------------------------------------------------------


def _seed_stale_running_job(trip_id: str, owner_id: str) -> str:
    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    mark_job_running(job)
    job.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    job_repository.create(job)
    return job.job_id


def test_owner_sees_recovered_interrupted_job_via_get_job(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_async_generation(monkeypatch)
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "60")
    get_settings.cache_clear()
    owner_id = client.get("/auth/me").json()["data"]["user"]["user_id"]
    job_id = _seed_stale_running_job(created_trip_id, owner_id)

    response = client.get(f"/trips/{created_trip_id}/jobs/{job_id}")
    get_settings.cache_clear()

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "failed"
    assert data["error_code"] == "JOB_INTERRUPTED"
    assert "Traceback" not in (data["error_message"] or "")


def test_owner_sees_recovered_interrupted_job_via_list_jobs(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_async_generation(monkeypatch)
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "60")
    get_settings.cache_clear()
    owner_id = client.get("/auth/me").json()["data"]["user"]["user_id"]
    _seed_stale_running_job(created_trip_id, owner_id)

    response = client.get(f"/trips/{created_trip_id}/jobs")
    get_settings.cache_clear()

    assert response.status_code == 200
    jobs = response.json()["data"]["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["error_code"] == "JOB_INTERRUPTED"


def test_wrong_user_still_gets_403_for_a_recovered_job(
    client: TestClient,
    second_client: TestClient,
    created_trip_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_async_generation(monkeypatch)
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "60")
    get_settings.cache_clear()
    owner_id = client.get("/auth/me").json()["data"]["user"]["user_id"]
    job_id = _seed_stale_running_job(created_trip_id, owner_id)

    response = second_client.get(f"/trips/{created_trip_id}/jobs/{job_id}")
    get_settings.cache_clear()

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "FORBIDDEN"


def test_recovered_job_no_longer_blocks_a_new_generate_via_the_route(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_async_generation(monkeypatch)
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "60")
    get_settings.cache_clear()
    owner_id = client.get("/auth/me").json()["data"]["user"]["user_id"]
    _seed_stale_running_job(created_trip_id, owner_id)

    response = client.post(f"/trips/{created_trip_id}/generate")
    get_settings.cache_clear()

    assert response.status_code == 202


def test_jobs_list_does_not_include_another_trips_jobs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_async_generation(monkeypatch)

    trip_a_response = client.post("/trips", json=create_trip_payload())
    trip_a_id = trip_a_response.json()["data"]["trip_id"]
    client.post(f"/trips/{trip_a_id}/generate")

    trip_b_response = client.post("/trips", json=create_trip_payload())
    trip_b_id = trip_b_response.json()["data"]["trip_id"]

    trip_a_jobs = client.get(f"/trips/{trip_a_id}/jobs").json()["data"]["jobs"]
    trip_b_jobs = client.get(f"/trips/{trip_b_id}/jobs").json()["data"]["jobs"]

    assert len(trip_a_jobs) == 1
    assert trip_b_jobs == []
