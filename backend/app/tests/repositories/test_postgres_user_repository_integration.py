from __future__ import annotations

import os
import uuid

import pytest

from app.models.user import UserRecord
from app.repositories.postgres_user_repository import PostgresUserRepository
from app.repositories.protocols import UserAlreadyExistsError

# Optional live-Postgres integration tests (Step 184C). Skipped by
# default -- these are among the few tests in this repository that need a
# real, running, already-migrated Postgres database. Enable with:
#
#   docker compose up -d postgres
#   cd backend && alembic upgrade head
#   TRAVELOB_RUN_POSTGRES_TESTS=1 PERSISTENCE_BACKEND=postgres \
#       DATABASE_URL=postgresql://travelobligator_user:change_me@localhost:5432/travelobligator \
#       python -m pytest app/tests/repositories/test_postgres_user_repository_integration.py -q
#
# See docs/14_backend_architecture.md section 109.
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


def _unique_email() -> str:
    return f"test-184c-{uuid.uuid4().hex}@example.com"


def test_create_get_by_id_and_get_by_email_round_trip_against_real_postgres() -> None:
    repo = PostgresUserRepository(session_factory=_session_factory())
    email = _unique_email()

    created = repo.create_user(UserRecord(email=email, password_hash="fake-hash-value"))

    by_id = repo.get_by_user_id(created.user_id)
    assert by_id is not None
    assert by_id.email == email

    by_email = repo.get_by_email(email.upper())
    assert by_email is not None
    assert by_email.user_id == created.user_id


def test_get_by_user_id_returns_none_for_unknown_id_against_real_postgres() -> None:
    repo = PostgresUserRepository(session_factory=_session_factory())
    assert repo.get_by_user_id("does_not_exist") is None


def test_create_user_raises_for_duplicate_email_against_real_postgres() -> None:
    repo = PostgresUserRepository(session_factory=_session_factory())
    email = _unique_email()
    repo.create_user(UserRecord(email=email, password_hash="hash1"))

    with pytest.raises(UserAlreadyExistsError):
        repo.create_user(UserRecord(email=email.upper(), password_hash="hash2"))


def test_owner_id_foreign_key_accepts_a_real_user_id_against_real_postgres() -> None:
    """Confirms `trips.owner_id`'s FK to `users.user_id` actually accepts
    a real user row -- proving the Step 184C migration's two halves
    (users table + trips.owner_id FK) are mutually consistent against a
    real database, not just individually plausible."""
    from app.repositories.postgres_trip_repository import PostgresTripRepository
    from sqlalchemy import text

    session_factory = _session_factory()
    user_repo = PostgresUserRepository(session_factory=session_factory)
    trip_repo = PostgresTripRepository(session_factory=session_factory)

    user = user_repo.create_user(UserRecord(email=_unique_email(), password_hash="hash"))
    trip = trip_repo.create(f"trip_184c_{uuid.uuid4().hex}")

    with session_factory() as session:
        session.execute(
            text("UPDATE trips SET owner_id = :owner_id WHERE trip_id = :trip_id"),
            {"owner_id": user.user_id, "trip_id": trip.trip_id},
        )
        session.commit()

        owner_id = session.execute(
            text("SELECT owner_id FROM trips WHERE trip_id = :trip_id"),
            {"trip_id": trip.trip_id},
        ).scalar_one()
        assert owner_id == user.user_id
