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

from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import DateTime, and_, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import GenerationJobRow
from app.db.session import get_session_factory
from app.db.transactions import record_conflict, session_scope
from app.models.generation_job import (
    JOB_INTERRUPTED_ERROR_CODE,
    JOB_INTERRUPTED_MESSAGE,
    GenerationJob,
    GenerationJobStatus,
)
from app.repositories.errors import UNSET, JobAlreadyActiveError
from app.models.targeted_regeneration_diff import TargetedRegenerationDiff

_RUNNING_STATUSES = frozenset(
    {GenerationJobStatus.QUEUED.value, GenerationJobStatus.RUNNING.value}
)
_ACTIVE_JOB_INDEX = "uq_generation_jobs_one_active_per_trip"


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
            # Section 198B: a row's `targeted`/list columns are `NOT
            # NULL` with a Postgres-side `server_default`, but that
            # default is applied by Postgres at INSERT time, not by
            # SQLAlchemy in Python -- an in-memory `GenerationJobRow`
            # that was never actually inserted (as some tests construct
            # directly) still has `None` for these attributes. Falling
            # back to `GenerationJob`'s own field defaults here keeps
            # `_row_to_job` correct in both cases rather than relying on
            # every caller having gone through a real INSERT.
            "previous_version": row.previous_version,
            "targeted": bool(row.targeted),
            "interpretation_status": row.interpretation_status,
            "execution_status": row.execution_status,
            "affected_day_indices": row.affected_day_indices or [],
            "preserved_day_indices": row.preserved_day_indices or [],
            "diff": (
                TargetedRegenerationDiff.model_validate(row.diff) if row.diff else None
            ),
            "clarification_reason": row.clarification_reason,
            "clarification_possible_experience_ids": row.clarification_possible_experience_ids
            or [],
            "lease_owner": row.lease_owner,
            "lease_expires_at": row.lease_expires_at,
            "heartbeat_at": row.heartbeat_at,
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
        "previous_version": job.previous_version,
        "targeted": job.targeted,
        "interpretation_status": job.interpretation_status,
        "execution_status": job.execution_status,
        "affected_day_indices": list(job.affected_day_indices),
        "preserved_day_indices": list(job.preserved_day_indices),
        "diff": job.diff.model_dump(mode="json") if job.diff else None,
        "clarification_reason": job.clarification_reason,
        "clarification_possible_experience_ids": list(
            job.clarification_possible_experience_ids
        ),
        "lease_owner": job.lease_owner,
        "lease_expires_at": job.lease_expires_at,
        "heartbeat_at": job.heartbeat_at,
    }


def _now_expr(now: datetime | None):
    """The clock every lease comparison uses: the DATABASE clock by default (so several
    backend instances with skewed clocks agree), or an explicit `now` for deterministic tests."""
    return func.now() if now is None else literal(now, DateTime(timezone=True))


class PostgresJobRepository:
    """Postgres-backed equivalent of `JobRepository`.

    Opens a short-lived `Session` per method call (via the injected/default session factory)
    rather than caching jobs in memory -- a real database is the shared source of truth
    across processes. Section 200C: with a bound `session` (unit of work) no method commits,
    and the lifecycle methods below (`claim`, `heartbeat`, `transition`, `recover_expired_jobs`)
    make every ownership/status decision in PostgreSQL itself, never in process memory.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        session: Session | None = None,
    ) -> None:
        self._session = session
        self._session_factory = session_factory or (
            None if session is not None else get_session_factory()
        )

    def _scope(self):
        return session_scope(self._session_factory, self._session)

    def create(self, job: GenerationJob) -> GenerationJob:
        """Upserts `job` by `job_id` -- matches `JobRepository.create`'s "replace by id"
        behavior. `created_at` is included in the update set here (unlike the trip/planning-
        state upserts) because a duplicate `create()` for the same `job_id` describes the same
        creation event; `save()` is the path that preserves the original `created_at`.

        Section 200C: inserting a SECOND active (queued/running) job for a trip violates the
        partial unique index `uq_generation_jobs_one_active_per_trip`; that is reported as
        `JobAlreadyActiveError` (the database is the final arbiter of "one active job per trip").
        """
        values = _job_values(job)
        stmt = pg_insert(GenerationJobRow.__table__).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["job_id"], set_=values)
        try:
            with self._scope() as session:
                session.execute(stmt)
        except IntegrityError as exc:
            constraint = getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None)
            if constraint == _ACTIVE_JOB_INDEX:
                record_conflict("job_active")
                raise JobAlreadyActiveError(job.trip_id) from None
            raise
        return job

    def save(self, job: GenerationJob) -> GenerationJob:
        """Unconditional upsert by `job_id`, preserving the existing row's `created_at`.
        Kept for legacy callers; lifecycle transitions use `claim`/`transition` instead, which
        are conditional."""
        values = _job_values(job)
        update_values = {key: value for key, value in values.items() if key != "created_at"}
        stmt = pg_insert(GenerationJobRow.__table__).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["job_id"], set_=update_values)
        with self._scope() as session:
            session.execute(stmt)
        return job

    def get_by_job_id(self, job_id: str) -> GenerationJob | None:
        with self._scope() as session:
            row = session.get(GenerationJobRow, job_id, populate_existing=True)
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
        with self._scope() as session:
            rows = self._jobs_for_trip(session, trip_id)
            return [_row_to_job(row) for row in rows]

    def list_by_trip_id_and_status(
        self, trip_id: str, statuses: set[str]
    ) -> list[GenerationJob]:
        with self._scope() as session:
            rows = session.execute(
                select(GenerationJobRow)
                .where(GenerationJobRow.trip_id == trip_id, GenerationJobRow.status.in_(statuses))
                .order_by(GenerationJobRow.created_at, GenerationJobRow.job_id)
            ).scalars()
            return [_row_to_job(row) for row in rows]

    def list_running_by_trip_id(self, trip_id: str) -> list[GenerationJob]:
        return self.list_by_trip_id_and_status(trip_id, set(_RUNNING_STATUSES))

    def list_non_terminal(self) -> list[GenerationJob]:
        """Every `queued`/`running` job across every trip, oldest first."""
        with self._scope() as session:
            rows = session.execute(
                select(GenerationJobRow)
                .where(GenerationJobRow.status.in_(set(_RUNNING_STATUSES)))
                .order_by(GenerationJobRow.created_at, GenerationJobRow.job_id)
            ).scalars()
            return [_row_to_job(row) for row in rows]

    # -- Section 200C: ownership / lifecycle (decided in PostgreSQL) ---------------------------

    def claim(
        self,
        job_id: str,
        lease_owner: str,
        lease_seconds: int,
        *,
        now: datetime | None = None,
        progress_stage: str | None = None,
    ) -> GenerationJob | None:
        """Atomically take ownership and mark the job running: succeeds (returning the job)
        only for a `queued` job, or a `running` job whose lease has expired / never existed.
        A terminal job, or a running job with a valid lease held by anyone (including the
        caller), is NOT claimable -> `None`. One conditional `UPDATE ... RETURNING`."""
        now_expr = _now_expr(now)
        values: dict = {
            "status": GenerationJobStatus.RUNNING.value,
            "started_at": func.coalesce(GenerationJobRow.started_at, now_expr),
            "lease_owner": lease_owner,
            "lease_expires_at": now_expr + timedelta(seconds=lease_seconds),
            "heartbeat_at": now_expr,
            "message": "Job is running.",
        }
        if progress_stage is not None:
            values["progress_stage"] = progress_stage
        stmt = (
            update(GenerationJobRow)
            .where(
                GenerationJobRow.job_id == job_id,
                or_(
                    GenerationJobRow.status == GenerationJobStatus.QUEUED.value,
                    and_(
                        GenerationJobRow.status == GenerationJobStatus.RUNNING.value,
                        or_(
                            GenerationJobRow.lease_expires_at.is_(None),
                            GenerationJobRow.lease_expires_at < now_expr,
                        ),
                    ),
                ),
            )
            .values(**values)
            .returning(GenerationJobRow)
            .execution_options(synchronize_session=False)
        )
        with self._scope() as session:
            row = session.execute(stmt).scalar_one_or_none()
            return _row_to_job(row) if row is not None else None

    def heartbeat(
        self, job_id: str, lease_owner: str, lease_seconds: int, *, now: datetime | None = None
    ) -> bool:
        """Extend the lease -- only for the CURRENT owner of a running job. Another owner (or a
        terminal/unknown job) updates zero rows -> `False`."""
        now_expr = _now_expr(now)
        stmt = (
            update(GenerationJobRow)
            .where(
                GenerationJobRow.job_id == job_id,
                GenerationJobRow.status == GenerationJobStatus.RUNNING.value,
                GenerationJobRow.lease_owner == lease_owner,
            )
            .values(heartbeat_at=now_expr, lease_expires_at=now_expr + timedelta(seconds=lease_seconds))
            .execution_options(synchronize_session=False)
        )
        with self._scope() as session:
            return session.execute(stmt).rowcount == 1

    def transition(
        self,
        job_id: str,
        mutate: Callable[[GenerationJob], GenerationJob],
        *,
        allowed_from: frozenset[str] = frozenset(_RUNNING_STATUSES),
        lease_owner: object = UNSET,
    ) -> GenerationJob | None:
        """Conditional, idempotent status transition. Locks the ONE job row (`FOR UPDATE`),
        requires its current status to be in `allowed_from` (default: non-terminal) and -- when
        `lease_owner` is given and the job is running with an owner -- that the caller still IS
        that owner, applies `mutate` to a copy, and writes it. Returns the new job, or `None`
        when the transition was refused (job unknown, already terminal -> a replay changes
        nothing, or ownership was lost -> a stale owner cannot finish the job)."""
        with self._scope() as session:
            row = session.execute(
                select(GenerationJobRow)
                .where(GenerationJobRow.job_id == job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if row is None or row.status not in allowed_from:
                return None
            if (
                lease_owner is not UNSET
                and row.status == GenerationJobStatus.RUNNING.value
                and row.lease_owner is not None
                and row.lease_owner != lease_owner
            ):
                return None
            updated = mutate(_row_to_job(row))
            values = _job_values(updated)
            values.pop("created_at")
            values.pop("job_id")
            session.execute(
                update(GenerationJobRow)
                .where(GenerationJobRow.job_id == job_id)
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            return updated

    def recover_expired_jobs(
        self,
        *,
        stale_queued_before: datetime,
        trip_id: str | None = None,
        now: datetime | None = None,
    ) -> list[GenerationJob]:
        """Close (failed / `JOB_INTERRUPTED`) exactly the jobs whose owner is gone: `running`
        jobs whose lease has expired (or that never had one -- a pre-200C row), and `queued`
        jobs created before `stale_queued_before` that nobody ever claimed. A `running` job with
        a valid lease -- owned by ANY live instance, including a different one -- is never
        touched, and terminal jobs never match. One `UPDATE ... RETURNING`; safe to run from
        several instances at once (each job is closed exactly once)."""
        now_expr = _now_expr(now)
        conditions = [
            or_(
                and_(
                    GenerationJobRow.status == GenerationJobStatus.RUNNING.value,
                    or_(
                        GenerationJobRow.lease_expires_at.is_(None),
                        GenerationJobRow.lease_expires_at < now_expr,
                    ),
                ),
                and_(
                    GenerationJobRow.status == GenerationJobStatus.QUEUED.value,
                    GenerationJobRow.created_at < stale_queued_before,
                ),
            )
        ]
        if trip_id is not None:
            conditions.append(GenerationJobRow.trip_id == trip_id)
        stmt = (
            update(GenerationJobRow)
            .where(*conditions)
            .values(
                status=GenerationJobStatus.FAILED.value,
                error_code=JOB_INTERRUPTED_ERROR_CODE,
                error_message=JOB_INTERRUPTED_MESSAGE,
                message="Job failed.",
                finished_at=now_expr,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(GenerationJobRow)
            .execution_options(synchronize_session=False)
        )
        with self._scope() as session:
            return [_row_to_job(row) for row in session.execute(stmt).scalars()]
