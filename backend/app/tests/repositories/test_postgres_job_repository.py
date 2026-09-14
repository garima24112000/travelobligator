from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.db.models import GenerationJobRow
from app.models.generation_job import (
    GenerationJob,
    GenerationJobType,
    create_queued_job,
    mark_job_failed,
    mark_job_interrupted,
    mark_job_running,
    mark_job_succeeded,
)
from app.repositories.postgres_job_repository import PostgresJobRepository

# Unit tests for PostgresJobRepository (Step 186F) against a fake
# session -- never a live Postgres/Docker. Mirrors
# test_postgres_planning_state_repository.py's approach: statement shape
# is verified by compiling the built INSERT/SELECT constructs (pure
# string generation), and read-path behavior is verified against real
# (but never persisted) GenerationJobRow instances.


class _FakeScalars:
    def __init__(self, rows: list[GenerationJobRow]) -> None:
        self._rows = rows

    def all(self) -> list[GenerationJobRow]:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _FakeResult:
    def __init__(self, rows: list[GenerationJobRow]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(
        self,
        get_result: GenerationJobRow | None = None,
        select_rows: list[GenerationJobRow] | None = None,
    ) -> None:
        self.get_result = get_result
        self.select_rows = select_rows or []
        self.executed_statements: list[object] = []
        self.committed = False

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, model: type, pk: str) -> object | None:
        return self.get_result if model is GenerationJobRow else None

    def execute(self, stmt: object):
        self.executed_statements.append(stmt)
        return _FakeResult(self.select_rows)

    def commit(self) -> None:
        self.committed = True


def _compiled_sql(stmt: object) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _job(**overrides) -> GenerationJob:
    defaults = dict(
        trip_id="trip_1",
        owner_id="user_1",
        job_type=GenerationJobType.GENERATE,
    )
    defaults.update(overrides)
    return create_queued_job(**defaults)


def _row_from_job(job: GenerationJob) -> GenerationJobRow:
    return GenerationJobRow(
        job_id=job.job_id,
        trip_id=job.trip_id,
        owner_id=job.owner_id,
        job_type=job.job_type.value,
        status=job.status.value,
        progress_stage=job.progress_stage,
        message=job.message,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result_version=job.result_version,
        changed_sections=list(job.changed_sections),
    )


def test_create_upserts_and_returns_the_same_job() -> None:
    job = _job()
    session = _FakeSession()
    repo = PostgresJobRepository(session_factory=lambda: session)

    result = repo.create(job)

    assert result is job
    assert session.committed is True
    assert len(session.executed_statements) == 1
    sql = _compiled_sql(session.executed_statements[0])
    assert "INSERT INTO generation_jobs" in sql
    assert "ON CONFLICT" in sql
    assert "DO UPDATE SET" in sql

    params = session.executed_statements[0].compile(dialect=postgresql.dialect()).params
    assert params["job_id"] == job.job_id
    assert params["trip_id"] == "trip_1"
    assert params["owner_id"] == "user_1"
    assert params["status"] == "queued"


def test_save_preserves_created_at_on_conflict_update() -> None:
    job = _job()
    session = _FakeSession()
    repo = PostgresJobRepository(session_factory=lambda: session)

    repo.save(job)

    sql = _compiled_sql(session.executed_statements[0])
    assert "created_at" not in sql.split("DO UPDATE SET")[1]


def test_get_by_job_id_returns_none_when_missing() -> None:
    session = _FakeSession(get_result=None)
    repo = PostgresJobRepository(session_factory=lambda: session)

    assert repo.get_by_job_id("job_does_not_exist") is None


def test_get_by_job_id_validates_row_through_pydantic() -> None:
    job = mark_job_succeeded(_job(), result_version="v2", changed_sections=["stay_transport"])
    row = _row_from_job(job)
    session = _FakeSession(get_result=row)
    repo = PostgresJobRepository(session_factory=lambda: session)

    reloaded = repo.get_by_job_id(job.job_id)

    assert reloaded is not None
    assert reloaded.job_id == job.job_id
    assert reloaded.status.value == "succeeded"
    assert reloaded.result_version == "v2"
    assert reloaded.changed_sections == ["stay_transport"]


def test_get_by_job_id_raises_on_malformed_row() -> None:
    """A corrupt/malformed row (invalid enum value) must surface as a
    real validation error, never be silently coerced or discarded."""
    row = GenerationJobRow(
        job_id="job_bad",
        trip_id="trip_1",
        owner_id="user_1",
        job_type="generate",
        status="not_a_real_status",
        created_at=datetime.now(timezone.utc),
        changed_sections=[],
    )
    session = _FakeSession(get_result=row)
    repo = PostgresJobRepository(session_factory=lambda: session)

    with pytest.raises(ValidationError):
        repo.get_by_job_id("job_bad")


def test_list_by_trip_id_returns_rows_validated_back_to_jobs() -> None:
    job_a = _job(trip_id="trip_1")
    job_b = _job(trip_id="trip_1")
    session = _FakeSession(select_rows=[_row_from_job(job_a), _row_from_job(job_b)])
    repo = PostgresJobRepository(session_factory=lambda: session)

    jobs = repo.list_by_trip_id("trip_1")

    assert [job.job_id for job in jobs] == [job_a.job_id, job_b.job_id]
    sql = _compiled_sql(session.executed_statements[0])
    assert "SELECT generation_jobs" in sql
    assert "ORDER BY generation_jobs.created_at, generation_jobs.job_id" in sql


def test_list_by_trip_id_and_status_filters_statuses() -> None:
    running_job = mark_job_running(_job())
    session = _FakeSession(select_rows=[_row_from_job(running_job)])
    repo = PostgresJobRepository(session_factory=lambda: session)

    jobs = repo.list_by_trip_id_and_status("trip_1", {"running", "queued"})

    assert [job.job_id for job in jobs] == [running_job.job_id]
    sql = _compiled_sql(session.executed_statements[0])
    assert "generation_jobs.status IN" in sql


def test_list_running_by_trip_id_only_returns_queued_and_running() -> None:
    running_job = mark_job_running(_job())
    session = _FakeSession(select_rows=[_row_from_job(running_job)])
    repo = PostgresJobRepository(session_factory=lambda: session)

    jobs = repo.list_running_by_trip_id("trip_1")

    assert [job.job_id for job in jobs] == [running_job.job_id]
    params = session.executed_statements[0].compile(dialect=postgresql.dialect()).params
    status_param_value = next(value for key, value in params.items() if key.startswith("status"))
    assert set(status_param_value) == {"queued", "running"}


def test_list_non_terminal_scans_across_trips() -> None:
    job_trip_1 = mark_job_running(_job(trip_id="trip_1"))
    job_trip_2 = _job(trip_id="trip_2")
    session = _FakeSession(select_rows=[_row_from_job(job_trip_1), _row_from_job(job_trip_2)])
    repo = PostgresJobRepository(session_factory=lambda: session)

    jobs = repo.list_non_terminal()

    assert {job.job_id for job in jobs} == {job_trip_1.job_id, job_trip_2.job_id}
    sql = _compiled_sql(session.executed_statements[0])
    assert "WHERE generation_jobs.status IN" in sql
    assert "trip_id" not in sql.split("WHERE")[1].split("ORDER BY")[0]


def test_changed_sections_round_trips_through_jsonb() -> None:
    job = mark_job_succeeded(
        _job(), result_version="v4", changed_sections=["experience_plan", "trip_strategy"]
    )
    row = _row_from_job(job)
    session = _FakeSession(get_result=row)
    repo = PostgresJobRepository(session_factory=lambda: session)

    reloaded = repo.get_by_job_id(job.job_id)

    assert reloaded is not None
    assert reloaded.changed_sections == ["experience_plan", "trip_strategy"]


def test_failed_job_round_trips_error_fields() -> None:
    job = mark_job_failed(_job(), error_code="STAGE_FAILED", error_message="Stage failed honestly.")
    row = _row_from_job(job)
    session = _FakeSession(get_result=row)
    repo = PostgresJobRepository(session_factory=lambda: session)

    reloaded = repo.get_by_job_id(job.job_id)

    assert reloaded is not None
    assert reloaded.status.value == "failed"
    assert reloaded.error_code == "STAGE_FAILED"
    assert reloaded.error_message == "Stage failed honestly."


def test_interrupted_job_round_trips() -> None:
    job = mark_job_interrupted(_job())
    row = _row_from_job(job)
    session = _FakeSession(get_result=row)
    repo = PostgresJobRepository(session_factory=lambda: session)

    reloaded = repo.get_by_job_id(job.job_id)

    assert reloaded is not None
    assert reloaded.error_code == "JOB_INTERRUPTED"
    assert reloaded.status.value == "failed"


def test_created_at_is_preserved_when_reloaded() -> None:
    job = _job()
    created_at = job.created_at - timedelta(hours=1)
    job.created_at = created_at
    row = _row_from_job(job)
    session = _FakeSession(get_result=row)
    repo = PostgresJobRepository(session_factory=lambda: session)

    reloaded = repo.get_by_job_id(job.job_id)

    assert reloaded is not None
    assert reloaded.created_at == created_at
