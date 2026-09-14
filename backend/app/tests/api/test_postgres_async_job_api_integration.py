from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.tests.conftest import create_trip_payload

# Optional live-Postgres API smoke test for async jobs (Step 186F).
# Skipped unless BOTH TRAVELOB_RUN_POSTGRES_TESTS=1 and a real
# DATABASE_URL are present -- never part of the default suite, never
# requires a live database for normal `pytest`. Mirrors
# test_postgres_api_smoke.py's gating and structure. See
# docs/14_backend_architecture.md section 119 for the full opt-in
# verification walkthrough (docker compose up -d postgres with
# POSTGRES_HOST_PORT, alembic upgrade head, then this).
pytestmark = pytest.mark.skipif(
    os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
    reason=(
        "Optional live-Postgres API smoke test, skipped by default. Set "
        "TRAVELOB_RUN_POSTGRES_TESTS=1, PERSISTENCE_BACKEND=postgres, "
        "ASYNC_GENERATION_ENABLED=true, and a real DATABASE_URL against an "
        "alembic-upgraded database to run it."
    ),
)


def _signup(client: TestClient) -> None:
    signup_response = client.post(
        "/auth/signup",
        json={"email": f"test-186f-{uuid.uuid4().hex}@example.com", "password": "testpassword123"},
    )
    assert signup_response.status_code == 201, signup_response.text


def test_async_generate_creates_and_completes_a_job_against_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full stack proof: auth, trip creation, and the async job itself
    all persist to Postgres when opted in -- FastAPI's `BackgroundTasks`
    are awaited as part of the same ASGI response cycle `TestClient`
    waits on, so by the time `client.post(.../generate)` returns, the
    job has already run to completion (see test_async_generate.py's own
    docstring for why no sleep/poll loop is needed here)."""
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()

    client = TestClient(app)
    try:
        _signup(client)

        create_response = client.post("/trips", json=create_trip_payload())
        assert create_response.status_code == 201
        trip_id = create_response.json()["data"]["trip_id"]

        generate_response = client.post(f"/trips/{trip_id}/generate")
        assert generate_response.status_code == 202
        job_id = generate_response.json()["data"]["job_id"]

        job_response = client.get(f"/trips/{trip_id}/jobs/{job_id}")
        assert job_response.status_code == 200
        job_data = job_response.json()["data"]
        assert job_data["status"] == "succeeded"
        assert job_data["error_code"] is None

        # Confirm it actually landed in Postgres, not the local_json
        # singleton, through a completely fresh repository/session.
        from app.db.session import get_session_factory
        from app.repositories.postgres_job_repository import PostgresJobRepository

        fresh_repo = PostgresJobRepository(session_factory=get_session_factory())
        stored_job = fresh_repo.get_by_job_id(job_id)
        assert stored_job is not None
        assert stored_job.status.value == "succeeded"
        assert stored_job.trip_id == trip_id
    finally:
        get_settings.cache_clear()


def test_async_regenerate_creates_and_completes_a_job_against_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()

    client = TestClient(app)
    try:
        _signup(client)

        create_response = client.post("/trips", json=create_trip_payload())
        trip_id = create_response.json()["data"]["trip_id"]

        generate_response = client.post(f"/trips/{trip_id}/generate")
        assert generate_response.status_code == 202

        feedback_response = client.post(
            f"/trips/{trip_id}/feedback",
            json={"feedback_text": "Make this less packed"},
        )
        assert feedback_response.status_code == 200

        regenerate_response = client.post(
            f"/trips/{trip_id}/regenerate", json={"confirm": True}
        )
        # Section 174's regeneration contract is refusal-first -- either a
        # real new async job (202) or one of its named refusals (409) is a
        # correct outcome here; what matters is that Postgres persistence
        # didn't change that contract or crash it.
        assert regenerate_response.status_code in (202, 409)
        if regenerate_response.status_code == 202:
            job_id = regenerate_response.json()["data"]["job_id"]
            job_response = client.get(f"/trips/{trip_id}/jobs/{job_id}")
            assert job_response.status_code == 200
            assert job_response.json()["data"]["status"] in ("succeeded", "failed")
    finally:
        get_settings.cache_clear()


def test_wrong_user_job_access_still_403_against_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()

    owner_client = TestClient(app)
    other_client = TestClient(app)
    try:
        _signup(owner_client)
        _signup(other_client)

        create_response = owner_client.post("/trips", json=create_trip_payload())
        trip_id = create_response.json()["data"]["trip_id"]

        generate_response = owner_client.post(f"/trips/{trip_id}/generate")
        job_id = generate_response.json()["data"]["job_id"]

        forbidden_list = other_client.get(f"/trips/{trip_id}/jobs")
        assert forbidden_list.status_code == 403

        forbidden_get = other_client.get(f"/trips/{trip_id}/jobs/{job_id}")
        assert forbidden_get.status_code == 403
    finally:
        get_settings.cache_clear()


def test_job_under_wrong_trip_still_404_against_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()

    client = TestClient(app)
    try:
        _signup(client)

        first_trip = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
        second_trip = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]

        generate_response = client.post(f"/trips/{first_trip}/generate")
        job_id = generate_response.json()["data"]["job_id"]

        response = client.get(f"/trips/{second_trip}/jobs/{job_id}")
        assert response.status_code == 404
    finally:
        get_settings.cache_clear()
