from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.repositories.errors import BranchHeadConflictError, ConcurrentStateUpdateError, JobAlreadyActiveError
from app.repositories.factory import get_job_repository
from app.services.planning_orchestrator import planning_orchestrator

# Section 200C: concurrency conflicts reach the client as SAFE, structured 409s.

_FORBIDDEN = ("lock_version", "UPDATE ", "SELECT ", "psycopg", "sqlalchemy", "Traceback", "DATABASE_URL", "postgresql")


def _assert_safe(response) -> None:
    body = response.text
    for needle in _FORBIDDEN:
        assert needle not in body, needle


def _stale_repo(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    real = planning_orchestrator.planning_state_repository

    def stale_save(state):
        raise error

    monkeypatch.setattr(
        planning_orchestrator,
        "_planning_state_repo_override",
        SimpleNamespace(get_by_trip_id=real.get_by_trip_id, save=stale_save),
    )


def test_a_stale_state_write_is_a_409_concurrent_update(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stale_repo(monkeypatch, ConcurrentStateUpdateError(created_trip_id))

    response = client.post(f"/trips/{created_trip_id}/feedback", json={"feedback_text": "Make this less packed"})

    assert response.status_code == 409
    payload = response.json()
    assert payload["success"] is False and payload["errors"][0]["code"] == "CONCURRENT_UPDATE"
    assert "reload" in payload["message"].lower()
    _assert_safe(response)


def test_a_stale_branch_head_is_also_a_409_concurrent_update(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stale_repo(monkeypatch, BranchHeadConflictError("branch_secret_id"))
    response = client.post(f"/trips/{created_trip_id}/feedback", json={"feedback_text": "Make this less packed"})
    assert response.status_code == 409 and response.json()["errors"][0]["code"] == "CONCURRENT_UPDATE"
    assert "branch_secret_id" not in response.text
    _assert_safe(response)


def test_losing_the_database_race_for_the_active_job_is_the_existing_job_already_running_409(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    repo = get_job_repository()
    monkeypatch.setattr(repo, "create", lambda job: (_ for _ in ()).throw(JobAlreadyActiveError(job.trip_id)))

    response = client.post(f"/trips/{created_trip_id}/generate")

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "JOB_ALREADY_RUNNING"
    _assert_safe(response)


def test_other_persistence_errors_keep_their_own_503_mapping(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    _stale_repo(monkeypatch, OperationalError("stmt", {}, Exception("host=db user=secret")))
    response = client.post(f"/trips/{created_trip_id}/feedback", json={"feedback_text": "Make this less packed"})
    assert response.status_code == 503 and "secret" not in response.text
