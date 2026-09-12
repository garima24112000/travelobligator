from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.tests.conftest import create_trip_payload

# Optional live-Postgres API smoke test (Step 183E). Skipped unless BOTH
# TRAVELOB_RUN_POSTGRES_TESTS=1 and a real DATABASE_URL are present --
# never part of the default suite, never requires a live database for
# normal `pytest`. See docs/14_backend_architecture.md section 106 for
# the full opt-in verification walkthrough (docker compose up -d
# postgres with POSTGRES_HOST_PORT, alembic upgrade head, then this).
#
# Walks the same create -> generate -> get -> feedback -> regenerate
# flow other smoke tests in this directory exercise against local_json,
# but with PERSISTENCE_BACKEND=postgres -- proving the full API, not just
# the repository classes in isolation, genuinely persists to Postgres
# when opted in, and that a FRESH repository instance (a new Session, not
# anything cached from the request itself) reads back the same data.
pytestmark = pytest.mark.skipif(
    os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
    reason=(
        "Optional live-Postgres API smoke test, skipped by default. Set "
        "TRAVELOB_RUN_POSTGRES_TESTS=1, PERSISTENCE_BACKEND=postgres, and a "
        "real DATABASE_URL against an alembic-upgraded database to run it."
    ),
)


def test_full_trip_lifecycle_persists_to_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """Step 184D: `/trips` now requires a real session, and with
    `PERSISTENCE_BACKEND=postgres` set, `/auth/signup` itself is also
    backed by `PostgresUserRepository` -- so this signs up for real
    against Postgres before doing anything else, proving the whole opted-
    in stack (auth included) works end to end against a real database."""
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    get_settings.cache_clear()

    client = TestClient(app)
    try:
        signup_response = client.post(
            "/auth/signup",
            json={"email": f"test-184d-{uuid.uuid4().hex}@example.com", "password": "testpassword123"},
        )
        assert signup_response.status_code == 201, signup_response.text

        create_response = client.post("/trips", json=create_trip_payload())
        assert create_response.status_code == 201
        trip_id = create_response.json()["data"]["trip_id"]

        generate_response = client.post(f"/trips/{trip_id}/generate")
        assert generate_response.status_code == 200

        get_response = client.get(f"/trips/{trip_id}")
        assert get_response.status_code == 200
        planning_state_after_generate = get_response.json()["data"]["planning_state"]
        assert planning_state_after_generate["trip_id"] == trip_id

        feedback_response = client.post(
            f"/trips/{trip_id}/feedback",
            json={"feedback_text": "Make this less packed"},
        )
        assert feedback_response.status_code == 200

        # Section 174's regeneration contract is refusal-first -- either a
        # real new version (200) or one of its named refusals (409) is a
        # correct outcome here; what matters for this test is that
        # Postgres persistence didn't change that contract or crash it.
        regenerate_response = client.post(f"/trips/{trip_id}/regenerate")
        assert regenerate_response.status_code in (200, 409)

        final_response = client.get(f"/trips/{trip_id}")
        assert final_response.status_code == 200
        final_planning_state = final_response.json()["data"]["planning_state"]

        # Confirm persistence through a completely fresh repository
        # instance/session -- not anything the request handlers cached --
        # proving this round-tripped through real Postgres, not an
        # in-process object the test just happens to still hold.
        from app.db.session import get_session_factory
        from app.repositories.postgres_planning_state_repository import (
            PostgresPlanningStateRepository,
        )

        fresh_repo = PostgresPlanningStateRepository(session_factory=get_session_factory())
        stored_state = fresh_repo.get_by_trip_id(trip_id)

        assert stored_state is not None
        assert stored_state.trip_id == trip_id
        assert len(stored_state.feedback_history) == len(final_planning_state["feedback_history"])
        assert stored_state.metadata.current_version == final_planning_state["metadata"]["current_version"]
    finally:
        get_settings.cache_clear()
