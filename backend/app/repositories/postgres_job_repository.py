"""Opt-in Postgres-backed generation-job repository (Step 186F).

Only ever constructed when `Settings.persistence_backend == "postgres"`
(see `app/repositories/factory.py`) -- the default `local_json` backend
never imports or constructs this class, so it never opens a database
connection unless an operator has explicitly opted in.

Behavior is written to match `app.repositories.job_repository.
JobRepository` exactly (same method names/signatures/return semantics --
see `app/repositories/protocols.py`'s `GenerationJobRepositoryProtocol`),
including `create`'s "replace by id" upsert semantics: the local
repository's `create()` and `save()` both just do
`self._jobs[job.job_id] = job`, so this repository's `create()` upserts
the same way rather than rejecting a duplicate `job_id`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import GenerationJobRow
from app.db.session import get_session_factory
from app.models.generation_job import GenerationJob, GenerationJobStatus

_RUNNING_STATUSES = frozenset(
    {GenerationJobStatus.QUEUED.value, GenerationJobStatus.RUNNING.value}
)


def _row_to_job(row: GenerationJobRow) -> GenerationJob:
    return GenerationJob.model_validate(
        {
            "job_id": row.job_id,
            "trip_id": row.trip_id,
            "owner_id": row.owner_id,
            "job_type": row.job_type,
            "status": row.status,
            "progress_stage": row.progress_stage,
            "message": row.message,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "result_version": row.result_version,
            "changed_sections": row.changed_sections,
        }
    )


def _job_values(job: GenerationJob) -> dict:
    return {
        "job_id": job.job_id,
        "trip_id": job.trip_id,
        "owner_id": job.owner_id,
        "job_type": job.job_type.value,
        "status": job.status.value,
        "progress_stage": job.progress_stage,
        "message": job.message,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "result_version": job.result_version,
        "changed_sections": list(job.changed_sections),
    }


class PostgresJobRepository:
    """Postgres-backed equivalent of `JobRepository`.

    Opens a short-lived `Session` per method call (via the injected/
    default session factory) rather than caching jobs in memory --
    unlike the local JSON repository, a real database is the shared
    source of truth across processes.
    """

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def create(self, job: GenerationJob) -> GenerationJob:
        """Upserts `job` by `job_id` -- matches `JobRepository.create`'s
        "replace by id" behavior (see module docstring). `created_at` is
        included in the update set here (unlike the trip/planning-state
        repositories' upserts) because a duplicate `create()` call for
        the same `job_id` is, by construction, describing the same job
        creation event, not a later update -- `save()` below is the path
        that must preserve the original `created_at`.
        """
        values = _job_values(job)
        stmt = pg_insert(GenerationJobRow.__table__).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["job_id"], set_=values)
        with self._session_factory() as session:
            session.execute(stmt)
            session.commit()
        return job

    def save(self, job: GenerationJob) -> GenerationJob:
        """Upserts `job` by `job_id`, preserving the existing row's
        `created_at` on update (a job's creation time never changes once
        recorded) -- matches `JobRepository.save`'s "replace by id"
        semantics otherwise."""
        values = _job_values(job)
        update_values = {key: value for key, value in values.items() if key != "created_at"}
        stmt = pg_insert(GenerationJobRow.__table__).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["job_id"], set_=update_values)
        with self._session_factory() as session:
            session.execute(stmt)
            session.commit()
        return job

    def get_by_job_id(self, job_id: str) -> GenerationJob | None:
        with self._session_factory() as session:
            row = session.get(GenerationJobRow, job_id)
            if row is None:
                return None
            return _row_to_job(row)

    def _jobs_for_trip(self, session: Session, trip_id: str) -> list[GenerationJobRow]:
        return list(
            session.execute(
                select(GenerationJobRow)
                .where(GenerationJobRow.trip_id == trip_id)
                .order_by(GenerationJobRow.created_at, GenerationJobRow.job_id)
            ).scalars()
        )

    def list_by_trip_id(self, trip_id: str) -> list[GenerationJob]:
        with self._session_factory() as session:
            rows = self._jobs_for_trip(session, trip_id)
            return [_row_to_job(row) for row in rows]

    def list_by_trip_id_and_status(
        self, trip_id: str, statuses: set[str]
    ) -> list[GenerationJob]:
        with self._session_factory() as session:
            rows = session.execute(
                select(GenerationJobRow)
                .where(GenerationJobRow.trip_id == trip_id, GenerationJobRow.status.in_(statuses))
                .order_by(GenerationJobRow.created_at, GenerationJobRow.job_id)
            ).scalars()
            return [_row_to_job(row) for row in rows]

    def list_running_by_trip_id(self, trip_id: str) -> list[GenerationJob]:
        return self.list_by_trip_id_and_status(trip_id, set(_RUNNING_STATUSES))

    def list_non_terminal(self) -> list[GenerationJob]:
        """Every `queued`/`running` job across *every* trip, oldest
        first -- matches `JobRepository.list_non_terminal`'s ordering
        convention. App-startup recovery (Step 186E's
        `generation_job_service.recover_interrupted_jobs`) is the only
        caller today, since it must scan the whole table, not one trip
        at a time."""
        with self._session_factory() as session:
            rows = session.execute(
                select(GenerationJobRow)
                .where(GenerationJobRow.status.in_(set(_RUNNING_STATUSES)))
                .order_by(GenerationJobRow.created_at, GenerationJobRow.job_id)
            ).scalars()
            return [_row_to_job(row) for row in rows]
