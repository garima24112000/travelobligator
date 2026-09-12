from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.repositories.postgres_planning_state_repository import PostgresPlanningStateRepository
from app.repositories.postgres_trip_repository import PostgresTripRepository
from app.tests.conftest import create_trip_payload

# Optional live-Postgres integration tests (Step 183D). Skipped by
# default -- these are the ONLY tests in this repository that need a
# real, running, already-migrated Postgres database. Enable with:
#
#   docker compose up -d postgres
#   cd backend && alembic upgrade head
#   TRAVELOB_RUN_POSTGRES_TESTS=1 PERSISTENCE_BACKEND=postgres \
#       DATABASE_URL=postgresql://travelobligator_user:change_me@localhost:5432/travelobligator \
#       python -m pytest app/tests/repositories/test_postgres_repositories_integration.py -q
#
# See docs/14_backend_architecture.md section 105.
pytestmark = pytest.mark.skipif(
    os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1",
    reason=(
        "Optional live-Postgres integration test, skipped by default. Set "
        "TRAVELOB_RUN_POSTGRES_TESTS=1 (plus PERSISTENCE_BACKEND=postgres and "
        "a real DATABASE_URL against an alembic-upgraded database) to run it."
    ),
)


def _session_factory():
    from app.db.session import get_session_factory

    return get_session_factory()


def _trip_request() -> TripRequest:
    return TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-11",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )


def _new_trip_id() -> str:
    return f"test_183d_{uuid.uuid4().hex}"


def test_trip_repository_create_get_update_round_trip_against_real_postgres() -> None:
    repo = PostgresTripRepository(session_factory=_session_factory())
    trip_id = _new_trip_id()

    created = repo.create(trip_id)
    assert created.trip_id == trip_id
    assert created.status == "draft"

    fetched = repo.get(trip_id)
    assert fetched is not None
    assert fetched.trip_id == trip_id
    assert fetched.status == "draft"

    updated = repo.update_status(trip_id, "generating")
    assert updated is not None
    assert updated.status == "generating"

    refetched = repo.get(trip_id)
    assert refetched is not None
    assert refetched.status == "generating"


def test_trip_repository_get_returns_none_for_unknown_trip() -> None:
    repo = PostgresTripRepository(session_factory=_session_factory())
    assert repo.get(_new_trip_id()) is None


def test_planning_state_repository_round_trip_against_real_postgres() -> None:
    session_factory = _session_factory()
    trip_repo = PostgresTripRepository(session_factory=session_factory)
    state_repo = PostgresPlanningStateRepository(session_factory=session_factory)

    planning_state = PlanningState(trip_request=_trip_request())
    trip_id = planning_state.trip_id

    trip_repo.create(trip_id)
    state_repo.save(planning_state)

    reloaded = state_repo.get_by_trip_id(trip_id)
    assert reloaded is not None
    assert reloaded.trip_id == trip_id
    assert reloaded.trip_request.primary_destination == "Lisbon, Portugal"
    assert reloaded.destination_context is None


def test_planning_state_repository_ensures_trip_row_exists_without_prior_create() -> None:
    """save() must work even if the caller never called the trip
    repository's create() first -- matches the two local JSON
    repositories' decoupled behavior (see PostgresPlanningStateRepository
    .save's docstring)."""
    session_factory = _session_factory()
    state_repo = PostgresPlanningStateRepository(session_factory=session_factory)
    trip_repo = PostgresTripRepository(session_factory=session_factory)

    planning_state = PlanningState(trip_request=_trip_request())
    trip_id = planning_state.trip_id

    state_repo.save(planning_state)

    trip_record = trip_repo.get(trip_id)
    assert trip_record is not None
    assert trip_record.status == "draft"


def test_full_api_flow_uses_postgres_when_persistence_backend_is_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end proof that trips.py/PlanningOrchestrator's factory
    wiring genuinely reaches Postgres when opted in -- not just that the
    repository classes work in isolation.

    Step 184D: `/trips` now requires a real session, and `PERSISTENCE_
    BACKEND=postgres` means `/auth/signup` itself is also backed by
    `PostgresUserRepository` (the same factory every repository goes
    through) -- so this test signs up for real against Postgres too,
    proving the *entire* opted-in stack (auth included) works end to end,
    not just trip persistence in isolation.
    """
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

        get_response = client.get(f"/trips/{trip_id}")
        assert get_response.status_code == 200
        assert get_response.json()["data"]["trip_id"] == trip_id

        # Confirm it actually landed in Postgres, not the local JSON
        # singleton (which the autouse `_reset_in_memory_repositories`
        # fixture points at an empty temp file for this test either way).
        repo = PostgresPlanningStateRepository(session_factory=_session_factory())
        stored = repo.get_by_trip_id(trip_id)
        assert stored is not None
    finally:
        get_settings.cache_clear()
