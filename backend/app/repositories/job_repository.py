from __future__ import annotations

from app.core.config import get_settings
from app.models.generation_job import GenerationJob, GenerationJobStatus
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


job_repository = JobRepository()
