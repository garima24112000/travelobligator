from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.core.config import get_settings
from app.models.generation_job import (
    JOB_INTERRUPTED_ERROR_CODE,
    JOB_INTERRUPTED_MESSAGE,
    GenerationJob,
    GenerationJobStatus,
    mark_job_failed,
)
from app.repositories.errors import UNSET
from app.storage.local_json_store import LocalJsonStore, get_local_json_store

_COLLECTION = "jobs"

# "queued"/"running" -- the two statuses that mean "this job is currently
# occupying a generate/regenerate slot for its trip." Matches
# `Settings.generation_job_max_running_per_trip`'s own docstring: a future
# step (186C) reads `list_running_by_trip_id` to decide whether a new job
# may start, this repository never enforces the limit itself.
_RUNNING_STATUSES = frozenset({GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING})


class JobRepository:
    """Local-file-backed `GenerationJob` repository for development (Step
    186B, docs/14_backend_architecture.md section 116).

    Mirrors `app.repositories.planning_state_repository.
    PlanningStateRepository`/`app.repositories.trip_repository.
    TripRepository` exactly: every job is cached in memory for the
    lifetime of this instance (loaded from disk at construction time) and
    persisted to the same local JSON file, under its own `"jobs"`
    collection, on every write -- so recorded jobs survive a backend
    process restart during local development. Writing to `"jobs"` never
    touches the `"trips"`/`"planning_states"`/`"users"` collections in the
    same file; `LocalJsonStore.write_collection` only ever replaces the
    one named collection.

    No route or background task constructs or calls this yet -- Step 186B
    adds no job orchestration, only this inert foundation. Not for
    production use: no multi-worker/multi-process coordination, no
    migrations -- see ARCHITECTURE.md.
    """

    def __init__(self, store: LocalJsonStore | None = None) -> None:
        self._lifecycle_lock = threading.RLock()
        self._store = store or get_local_json_store(
            get_settings().resolved_local_storage_path()
        )
        self._jobs: dict[str, GenerationJob] = {
            job_id: GenerationJob.model_validate(record)
            for job_id, record in self._store.read_collection(_COLLECTION).items()
        }

    def _persist(self) -> None:
        self._store.write_collection(
            _COLLECTION,
            {job_id: job.model_dump(mode="json") for job_id, job in self._jobs.items()},
        )

    def create(self, job: GenerationJob) -> GenerationJob:
        self._jobs[job.job_id] = job
        self._persist()
        return job

    def save(self, job: GenerationJob) -> GenerationJob:
        """Replaces whatever job already exists for `job.job_id` (or
        inserts it, if this is the first save) -- same "replace by id"
        semantics `PlanningStateRepository.save` already has for
        `trip_id`."""
        self._jobs[job.job_id] = job
        self._persist()
        return job

    def get_by_job_id(self, job_id: str) -> GenerationJob | None:
        return self._jobs.get(job_id)

    def _jobs_for_trip(self, trip_id: str) -> list[GenerationJob]:
        """Every job for `trip_id`, oldest first (ascending `created_at`)
        -- matches this codebase's existing "audit trail in stored/
        chronological order" convention (e.g.
        `PlanningState.regeneration_attempts`/`version_history`, both
        read back in append order by their own GET endpoints) rather than
        newest-first. Ties (identical `created_at`) fall back to
        `job_id` for a fully deterministic order.
        """
        matches = [job for job in self._jobs.values() if job.trip_id == trip_id]
        return sorted(matches, key=lambda job: (job.created_at, job.job_id))

    def list_by_trip_id(self, trip_id: str) -> list[GenerationJob]:
        return self._jobs_for_trip(trip_id)

    def list_by_trip_id_and_status(
        self, trip_id: str, statuses: set[str]
    ) -> list[GenerationJob]:
        return [job for job in self._jobs_for_trip(trip_id) if job.status in statuses]

    def list_running_by_trip_id(self, trip_id: str) -> list[GenerationJob]:
        return self.list_by_trip_id_and_status(
            trip_id, {status.value for status in _RUNNING_STATUSES}
        )

    def list_non_terminal(self) -> list[GenerationJob]:
        """Every `queued`/`running` job across *every* trip, oldest first
        (matching `list_by_trip_id`'s own ordering convention) -- Step
        186E's app-startup recovery pass is the only caller today, since
        it must scan the whole store, not one trip at a time.
        """
        matches = [
            job
            for job in self._jobs.values()
            if job.status in {status.value for status in _RUNNING_STATUSES}
        ]
        return sorted(matches, key=lambda job: (job.created_at, job.job_id))

    # -- Section 200C: lifecycle API, same contract as PostgresJobRepository ---------------------
    # In-process only (a lock, not a database): Local JSON gives no cross-process guarantees;
    # production-grade ownership/uniqueness needs PostgreSQL.

    def claim(
        self,
        job_id: str,
        lease_owner: str,
        lease_seconds: int,
        *,
        now: datetime | None = None,
        progress_stage: str | None = None,
    ) -> GenerationJob | None:
        with self._lifecycle_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            current = now or datetime.now(timezone.utc)
            claimable = job.status == GenerationJobStatus.QUEUED or (
                job.status == GenerationJobStatus.RUNNING
                and (job.lease_expires_at is None or job.lease_expires_at < current)
            )
            if not claimable:
                return None
            job.status = GenerationJobStatus.RUNNING
            if job.started_at is None:
                job.started_at = current
            if progress_stage is not None:
                job.progress_stage = progress_stage
            job.message = "Job is running."
            job.lease_owner = lease_owner
            job.heartbeat_at = current
            job.lease_expires_at = current + timedelta(seconds=lease_seconds)
            self._persist()
            return job

    def heartbeat(
        self, job_id: str, lease_owner: str, lease_seconds: int, *, now: datetime | None = None
    ) -> bool:
        with self._lifecycle_lock:
            job = self._jobs.get(job_id)
            if (
                job is None
                or job.status != GenerationJobStatus.RUNNING
                or job.lease_owner != lease_owner
            ):
                return False
            current = now or datetime.now(timezone.utc)
            job.heartbeat_at = current
            job.lease_expires_at = current + timedelta(seconds=lease_seconds)
            self._persist()
            return True

    def transition(
        self,
        job_id: str,
        mutate: Callable[[GenerationJob], GenerationJob],
        *,
        allowed_from: frozenset[str] = frozenset(
            {GenerationJobStatus.QUEUED.value, GenerationJobStatus.RUNNING.value}
        ),
        lease_owner: object = UNSET,
    ) -> GenerationJob | None:
        with self._lifecycle_lock:
            job = self._jobs.get(job_id)
            if job is None or job.status.value not in allowed_from:
                return None
            if (
                lease_owner is not UNSET
                and job.status == GenerationJobStatus.RUNNING
                and job.lease_owner is not None
                and job.lease_owner != lease_owner
            ):
                return None
            updated = mutate(job)
            self._jobs[job_id] = updated
            self._persist()
            return updated

    def recover_expired_jobs(
        self,
        *,
        stale_queued_before: datetime,
        trip_id: str | None = None,
        now: datetime | None = None,
    ) -> list[GenerationJob]:
        current = now or datetime.now(timezone.utc)
        recovered: list[GenerationJob] = []
        with self._lifecycle_lock:
            for job in sorted(self._jobs.values(), key=lambda j: (j.created_at, j.job_id)):
                if trip_id is not None and job.trip_id != trip_id:
                    continue
                expired_running = job.status == GenerationJobStatus.RUNNING and (
                    job.lease_expires_at is None or job.lease_expires_at < current
                )
                stale_queued = (
                    job.status == GenerationJobStatus.QUEUED and job.created_at < stale_queued_before
                )
                if expired_running or stale_queued:
                    recovered.append(
                        mark_job_failed(
                            job,
                            error_code=JOB_INTERRUPTED_ERROR_CODE,
                            error_message=JOB_INTERRUPTED_MESSAGE,
                        )
                    )
            if recovered:
                self._persist()
        return recovered


job_repository = JobRepository()
