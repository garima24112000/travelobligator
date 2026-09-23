from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from app.models.itinerary_lineage import ItineraryBranch, ItineraryRevision
from app.models.user import UserRecord
from app.repositories.postgres_itinerary_lineage_repository import (
    PostgresItineraryLineageRepository,
)
from app.repositories.postgres_trip_repository import PostgresTripRepository
from app.repositories.postgres_user_repository import PostgresUserRepository

# Section 199A: optional live-Postgres integration tests, mirroring
# test_postgres_job_repository_integration.py's own gated pattern exactly
# (Step 186F). Skipped by default -- these need a real, running,
# already-migrated Postgres database. Enable with:
#
#   docker run -d --name to_pg_test -e POSTGRES_DB=to_test \
#       -e POSTGRES_USER=to_test -e POSTGRES_PASSWORD=to_test \
#       -p 15433:5432 postgres:16
#   cd backend && DATABASE_URL=postgresql://to_test:to_test@localhost:15433/to_test \
#       alembic upgrade head
#   TRAVELOB_RUN_POSTGRES_TESTS=1 PERSISTENCE_BACKEND=postgres \
#       DATABASE_URL=postgresql://to_test:to_test@localhost:15433/to_test \
#       python -m pytest app/tests/repositories/test_postgres_itinerary_lineage_repository_integration.py -q
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


def _new_id(prefix: str) -> str:
    return f"{prefix}_199a_{uuid.uuid4().hex}"


def _seed_trip_and_owner(session_factory) -> tuple[str, str]:
    trip_id = _new_id("trip")
    owner_id = _new_id("user")

    user_repo = PostgresUserRepository(session_factory=session_factory)
    now = datetime.now(timezone.utc)
    user_repo.create_user(
        UserRecord(
            user_id=owner_id,
            email=f"{owner_id}@example.com",
            password_hash="not_a_real_hash",
            created_at=now,
            updated_at=now,
        )
    )

    trip_repo = PostgresTripRepository(session_factory=session_factory)
    trip_repo.create(trip_id, owner_id=owner_id)

    return trip_id, owner_id


def test_branch_create_get_update_head_round_trip_against_real_postgres() -> None:
    session_factory = _session_factory()
    trip_id, _owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresItineraryLineageRepository(session_factory=session_factory)

    branch = repo.create_branch(ItineraryBranch(trip_id=trip_id, is_default=True))
    fetched = repo.get_branch(branch.branch_id)
    assert fetched is not None
    assert fetched.is_default is True
    assert fetched.head_revision_id is None
    assert repo.get_default_branch(trip_id) == fetched

    revision = repo.create_revision(
        ItineraryRevision(
            trip_id=trip_id,
            branch_id=branch.branch_id,
            version_label="v1",
            created_by="system_generation",
            snapshot_available=True,
            snapshot={"trip_id": trip_id, "metadata": {"current_version": "v1"}},
        )
    )
    updated_branch = repo.update_branch_head(branch.branch_id, revision.revision_id)
    assert updated_branch is not None
    assert updated_branch.head_revision_id == revision.revision_id

    reloaded_branch = repo.get_branch(branch.branch_id)
    assert reloaded_branch is not None
    assert reloaded_branch.head_revision_id == revision.revision_id


def test_revision_snapshot_round_trips_through_jsonb_against_real_postgres() -> None:
    session_factory = _session_factory()
    trip_id, _owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresItineraryLineageRepository(session_factory=session_factory)

    branch = repo.create_branch(ItineraryBranch(trip_id=trip_id, is_default=True))
    snapshot_payload = {
        "trip_id": trip_id,
        "metadata": {"current_version": "v1"},
        "experience_plan": {"daily_plans": [{"day_number": 1, "experiences": []}]},
    }
    revision = repo.create_revision(
        ItineraryRevision(
            trip_id=trip_id,
            branch_id=branch.branch_id,
            version_label="v1",
            created_by="system_generation",
            snapshot_available=True,
            snapshot=snapshot_payload,
        )
    )

    reloaded = repo.get_revision(revision.revision_id)
    assert reloaded is not None
    assert reloaded.snapshot == snapshot_payload
    assert reloaded.snapshot_available is True


def test_create_revision_idempotent_on_branch_and_version_against_real_postgres() -> None:
    """Task 29/30 at the real database level: `create_revision` for the
    same (branch_id, version_label) twice must produce exactly one row,
    via the real `ON CONFLICT DO NOTHING` + re-fetch, not two."""
    session_factory = _session_factory()
    trip_id, _owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresItineraryLineageRepository(session_factory=session_factory)
    branch = repo.create_branch(ItineraryBranch(trip_id=trip_id, is_default=True))

    first = repo.create_revision(
        ItineraryRevision(
            trip_id=trip_id, branch_id=branch.branch_id, version_label="v1", created_by="system_generation"
        )
    )
    second = repo.create_revision(
        ItineraryRevision(
            trip_id=trip_id, branch_id=branch.branch_id, version_label="v1", created_by="system_generation"
        )
    )

    assert first.revision_id == second.revision_id
    assert len(repo.list_revisions_for_branch(branch.branch_id)) == 1


def test_at_most_one_default_branch_per_trip_against_real_postgres() -> None:
    """The partial unique index enforces this at the database level --
    a second `is_default=True` branch insert for the same trip must
    raise, since 199A never allows more than one default branch."""
    from sqlalchemy.exc import IntegrityError

    session_factory = _session_factory()
    trip_id, _owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresItineraryLineageRepository(session_factory=session_factory)

    repo.create_branch(ItineraryBranch(trip_id=trip_id, is_default=True))
    with pytest.raises(IntegrityError):
        repo.create_branch(ItineraryBranch(trip_id=trip_id, is_default=True))


def test_list_revisions_for_branch_ordered_against_real_postgres() -> None:
    session_factory = _session_factory()
    trip_id, _owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresItineraryLineageRepository(session_factory=session_factory)
    branch = repo.create_branch(ItineraryBranch(trip_id=trip_id, is_default=True))

    r1 = repo.create_revision(
        ItineraryRevision(
            trip_id=trip_id, branch_id=branch.branch_id, version_label="v1", created_by="system_generation"
        )
    )
    r2 = repo.create_revision(
        ItineraryRevision(
            trip_id=trip_id,
            branch_id=branch.branch_id,
            parent_revision_id=r1.revision_id,
            version_label="v2",
            created_by="user_feedback",
        )
    )

    revisions = repo.list_revisions_for_branch(branch.branch_id)
    assert [r.revision_id for r in revisions] == [r1.revision_id, r2.revision_id]


def test_branch_display_name_unique_per_trip_case_insensitive_against_real_postgres() -> None:
    """Section 199B: the real database-level unique index on
    (trip_id, lower(display_name)) -- a second branch with the same
    name (any case) for the same trip must raise, never silently
    succeed."""
    from sqlalchemy.exc import IntegrityError

    session_factory = _session_factory()
    trip_id, _owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresItineraryLineageRepository(session_factory=session_factory)

    repo.create_branch(ItineraryBranch(trip_id=trip_id, display_name="Option B"))
    with pytest.raises(IntegrityError):
        repo.create_branch(ItineraryBranch(trip_id=trip_id, display_name="option b"))


def test_fork_and_independent_branch_advancement_against_real_postgres() -> None:
    """Section 199B Task 47: a real fork (base=head=source revision),
    activation, and independent head advancement, all through the real
    Postgres repository."""
    session_factory = _session_factory()
    trip_id, _owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresItineraryLineageRepository(session_factory=session_factory)

    main = repo.create_branch(ItineraryBranch(trip_id=trip_id, is_default=True))
    r1 = repo.create_revision(
        ItineraryRevision(
            trip_id=trip_id,
            branch_id=main.branch_id,
            version_label="v1",
            created_by="system_generation",
            snapshot_available=True,
            snapshot={"trip_id": trip_id, "metadata": {"current_version": "v1"}},
        )
    )
    repo.update_branch_head(main.branch_id, r1.revision_id)

    alternate = repo.create_branch(
        ItineraryBranch(
            trip_id=trip_id,
            display_name="Alternate",
            base_revision_id=r1.revision_id,
            head_revision_id=r1.revision_id,
        )
    )
    assert alternate.head_revision_id == r1.revision_id

    r2_alt = repo.create_revision(
        ItineraryRevision(
            trip_id=trip_id,
            branch_id=alternate.branch_id,
            parent_revision_id=r1.revision_id,
            version_label="v2",
            created_by="user_feedback",
            snapshot_available=True,
            snapshot={"trip_id": trip_id, "metadata": {"current_version": "v2"}},
        )
    )
    repo.update_branch_head(alternate.branch_id, r2_alt.revision_id)

    reloaded_main = repo.get_branch(main.branch_id)
    reloaded_alternate = repo.get_branch(alternate.branch_id)
    assert reloaded_main.head_revision_id == r1.revision_id
    assert reloaded_alternate.head_revision_id == r2_alt.revision_id
    assert repo.list_revisions_for_branch(main.branch_id) == [r1]
    assert repo.list_revisions_for_branch(alternate.branch_id) == [r2_alt]
