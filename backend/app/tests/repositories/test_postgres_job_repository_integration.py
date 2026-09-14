from __future__ import annotations

import os
import uuid

import pytest

from app.models.generation_job import (
    GenerationJobType,
    create_queued_job,
    mark_job_failed,
    mark_job_running,
    mark_job_succeeded,
)
from app.repositories.postgres_job_repository import PostgresJobRepository
from app.repositories.postgres_trip_repository import PostgresTripRepository
from app.repositories.postgres_user_repository import PostgresUserRepository
from app.models.user import UserRecord
from datetime import datetime, timezone

# Optional live-Postgres integration tests (Step 186F). Skipped by
# default -- these need a real, running, already-migrated Postgres
# database. Enable with:
#
#   POSTGRES_HOST_PORT=15432 docker compose up -d postgres
#   cd backend && DATABASE_URL=postgresql://travelobligator_user:change_me@localhost:15432/travelobligator \
#       alembic upgrade head
#   TRAVELOB_RUN_POSTGRES_TESTS=1 PERSISTENCE_BACKEND=postgres \
#       DATABASE_URL=postgresql://travelobligator_user:change_me@localhost:15432/travelobligator \
#       python -m pytest app/tests/repositories/test_postgres_job_repository_integration.py -q
#
# See docs/14_backend_architecture.md section 119.
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
    return f"{prefix}_186f_{uuid.uuid4().hex}"


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


def test_create_get_save_round_trip_against_real_postgres() -> None:
    session_factory = _session_factory()
    trip_id, owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresJobRepository(session_factory=session_factory)

    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    repo.create(job)

    fetched = repo.get_by_job_id(job.job_id)
    assert fetched is not None
    assert fetched.status.value == "queued"

    mark_job_running(job)
    repo.save(job)

    reloaded = repo.get_by_job_id(job.job_id)
    assert reloaded is not None
    assert reloaded.status.value == "running"
    assert reloaded.created_at == job.created_at

    mark_job_succeeded(job, result_version="v1", changed_sections=["stay_transport"])
    repo.save(job)

    final = repo.get_by_job_id(job.job_id)
    assert final is not None
    assert final.status.value == "succeeded"
    assert final.result_version == "v1"
    assert final.changed_sections == ["stay_transport"]


def test_list_running_by_trip_id_against_real_postgres() -> None:
    session_factory = _session_factory()
    trip_id, owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresJobRepository(session_factory=session_factory)

    running_job = create_queued_job(
        trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    mark_job_running(running_job)
    repo.create(running_job)

    finished_job = create_queued_job(
        trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE
    )
    mark_job_failed(finished_job, error_code="STAGE_FAILED", error_message="honest failure")
    repo.create(finished_job)

    running = repo.list_running_by_trip_id(trip_id)
    assert [job.job_id for job in running] == [running_job.job_id]

    all_jobs = repo.list_by_trip_id(trip_id)
    assert {job.job_id for job in all_jobs} == {running_job.job_id, finished_job.job_id}


def test_list_non_terminal_against_real_postgres() -> None:
    session_factory = _session_factory()
    trip_id, owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresJobRepository(session_factory=session_factory)

    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    repo.create(job)

    non_terminal = repo.list_non_terminal()
    assert any(candidate.job_id == job.job_id for candidate in non_terminal)


def test_startup_recovery_marks_queued_and_running_postgres_jobs_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 186E's `recover_interrupted_jobs`, exercised directly against
    a real Postgres-backed repository (not the local_json singleton) --
    proves 186F's factory wiring, not just the repository in isolation."""
    from app.core.config import get_settings
    from app.services import generation_job_service
    import app.repositories.factory as factory_module

    session_factory = _session_factory()
    trip_id, owner_id = _seed_trip_and_owner(session_factory)
    repo = PostgresJobRepository(session_factory=session_factory)

    queued_job = create_queued_job(
        trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    repo.create(queued_job)

    running_job = create_queued_job(
        trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE
    )
    mark_job_running(running_job)
    repo.create(running_job)

    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    get_settings.cache_clear()
    factory_module._postgres_job_repository.cache_clear()
    try:
        recovered_count = generation_job_service.recover_interrupted_jobs()
    finally:
        get_settings.cache_clear()
        factory_module._postgres_job_repository.cache_clear()

    assert recovered_count >= 2

    reloaded_queued = repo.get_by_job_id(queued_job.job_id)
    reloaded_running = repo.get_by_job_id(running_job.job_id)
    assert reloaded_queued is not None
    assert reloaded_running is not None
    assert reloaded_queued.status.value == "failed"
    assert reloaded_queued.error_code == "JOB_INTERRUPTED"
    assert reloaded_running.status.value == "failed"
    assert reloaded_running.error_code == "JOB_INTERRUPTED"
