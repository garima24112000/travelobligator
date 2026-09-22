from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.models.planning_state import GENERATION_STAGE_KEYS
from app.models.targeted_regeneration_diff import TargetedRegenerationDiff

# Async job foundation (Step 186B, docs/14_backend_architecture.md section
# 116). This module adds the inert data model + creation/transition
# helpers for a future background generate/regenerate job -- no route,
# service, or background task reads or writes any of this yet
# (`app/api/routes/trips.py`'s `/generate`/`/regenerate` remain fully
# synchronous). `PlanningState.generation_progress`
# (`app/models/planning_state.py`) stays the plan-facing progress read
# model exactly as it is today; a `GenerationJob` is a separate,
# job-identity-scoped record (job_id/owner_id/status/error), not a
# replacement for it.


def new_job_id() -> str:
    """Stable id prefix mirroring `app.models.planning_state._new_id`'s
    `<prefix>_<uuid4 hex>` convention (e.g. `trip_...`, `planning_state_...`)."""
    return f"job_{uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GenerationJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationJobType(str, Enum):
    GENERATE = "generate"
    REGENERATE = "regenerate"


# Stages a job's `progress_stage` is allowed to name -- exactly
# `GENERATION_STAGE_KEYS`, the same real backend pipeline stage keys
# `PlanningOrchestrator`/`GenerationProgress` already use. A "regenerate"
# job only ever reruns a subset of these (see
# `app.services.feedback_service.derive_pending_affected_stages`, whose
# `PlanningStage` values are themselves a subset of this same key list) --
# there is no separate, cosmetic vocabulary for regeneration progress.
# `None` (not started, or a job type/stage that doesn't track a single
# current stage) is always allowed alongside these.
_ALLOWED_PROGRESS_STAGES = frozenset(GENERATION_STAGE_KEYS)


class GenerationJob(BaseModel):
    """Async generate/regenerate job record (Step 186B).

    This is job *control* state (identity, ownership, lifecycle, error) --
    never a source of travel facts, and never a claim about the resulting
    plan's quality. `status="succeeded"` means the backend pipeline ran to
    completion, exactly like a `200` from today's synchronous
    `POST /trips/{trip_id}/generate` -- it is never a claim that the
    itinerary is travel-ready, final, or guaranteed; that judgment stays
    with `PlanningState.validation_report`/`regeneration_readiness`/
    `provider_coverage`, all completely untouched by this model.

    No secret, password, session token, or API key is ever stored here --
    only trip/job/lifecycle bookkeeping. `error_message` is always a
    short, controlled, human-readable string a caller constructs
    deliberately (mirroring every other honest-refusal message in this
    codebase, e.g. `app.core.errors.REGENERATION_NOT_AVAILABLE_MESSAGE`) --
    never a raw exception string or stack trace, which could otherwise
    leak internal implementation detail or a provider payload.
    """

    job_id: str = Field(default_factory=new_job_id)
    trip_id: str
    # Required: always copied from the authenticated trip owner
    # (`current_user.user_id`, matching `PlanningOrchestrator.create_trip`'s
    # own `owner_id` handling) at job creation time by a future step
    # (186C) -- never left `None` and never inferred from `PlanningState`,
    # which has no `owner_id` field itself (see
    # `app/repositories/trip_repository.py`'s `TripRecord.owner_id`
    # docstring for why ownership lives only on `trips`).
    owner_id: str
    job_type: GenerationJobType

    status: GenerationJobStatus = GenerationJobStatus.QUEUED

    # Real generation stage keys only (see `_ALLOWED_PROGRESS_STAGES`
    # above) -- never a fake/cosmetic phase, and never a travel fact
    # (flight route, booking status, price, rating, etc.).
    progress_stage: str | None = None
    message: str | None = None

    # Populated only by `mark_job_failed` -- both `None` for every other
    # status. `error_code` is a short machine-readable label (mirroring
    # `app.schemas.errors.ErrorCode`'s style, e.g. "STAGE_FAILED"), never a
    # raw exception class name; `error_message` is never a stack trace.
    error_code: str | None = None
    error_message: str | None = None

    created_at: datetime = Field(default_factory=_utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    # Regeneration-oriented result fields (mirror
    # `app.schemas.regeneration_result.RegenerateResponseData`'s
    # `current_version`/`changed_sections`) -- optional, and only ever
    # populated by `mark_job_succeeded` for a job that actually produced a
    # new version/changed section. Always `None`/`[]` for a `generate` job
    # or any job that hasn't succeeded yet.
    result_version: str | None = None
    changed_sections: list[str] = Field(default_factory=list)

    # Section 198B: targeted-regeneration result parity between the sync
    # `RegenerateResponseData` and an async job's completed result --
    # this is the SAME canonical result the sync route already returns,
    # carried through the job record rather than hand-copied into a
    # second, divergent shape. Every field below is optional/defaulted so
    # an old, already-persisted `generate` job or legacy `regenerate` job
    # (none of which ever set these) still deserializes unchanged --
    # `targeted=False` and every other new field stays at its default for
    # any job this section didn't touch.
    previous_version: str | None = None
    targeted: bool = False
    interpretation_status: str | None = None
    execution_status: str | None = None
    affected_day_indices: list[int] = Field(default_factory=list)
    preserved_day_indices: list[int] = Field(default_factory=list)
    diff: TargetedRegenerationDiff | None = None
    # Populated only for a targeted job that ended in
    # needs_clarification -- mirrors what the sync route recovers from
    # `FeedbackEvent.interpretation` after a 409, but here it's carried
    # directly since a background job has no HTTP response to attach it
    # to.
    clarification_reason: str | None = None
    clarification_possible_experience_ids: list[str] = Field(default_factory=list)

    @field_validator("progress_stage", mode="after")
    @classmethod
    def _validate_progress_stage(cls, value: str | None) -> str | None:
        if value is not None and value not in _ALLOWED_PROGRESS_STAGES:
            raise ValueError(
                f"progress_stage must be one of {sorted(_ALLOWED_PROGRESS_STAGES)} "
                "or None -- no fabricated/cosmetic stage name is allowed."
            )
        return value


def create_queued_job(
    *, trip_id: str, owner_id: str, job_type: GenerationJobType
) -> GenerationJob:
    """Builds a brand-new `queued` job. Never persists it -- the caller
    (a future step's route/orchestrator) is responsible for calling
    `JobRepository.create` itself, mirroring every other model/repository
    split in this codebase.
    """
    return GenerationJob(trip_id=trip_id, owner_id=owner_id, job_type=job_type)


def mark_job_running(job: GenerationJob, progress_stage: str | None = None) -> GenerationJob:
    """Transitions `job` to `running`, setting `started_at` the first time
    this is called. Mutates and returns the same instance, matching
    `PlanningState`'s own mutate-and-return convention (e.g.
    `PlanningState.set_pipeline_status`) -- the caller is still
    responsible for persisting it via `JobRepository.save`.
    """
    job.status = GenerationJobStatus.RUNNING
    if job.started_at is None:
        job.started_at = _utc_now()
    if progress_stage is not None:
        job.progress_stage = progress_stage
    job.message = "Job is running."
    return job


def mark_job_succeeded(
    job: GenerationJob,
    *,
    result_version: str | None = None,
    changed_sections: list[str] | None = None,
    # Section 198B: optional targeted-result parity fields -- every
    # existing call site (legacy generate/regenerate) omits these and
    # gets the exact same behavior as before.
    previous_version: str | None = None,
    targeted: bool = False,
    interpretation_status: str | None = None,
    execution_status: str | None = None,
    affected_day_indices: list[int] | None = None,
    preserved_day_indices: list[int] | None = None,
    diff: TargetedRegenerationDiff | None = None,
) -> GenerationJob:
    """Transitions `job` to `succeeded`, setting `finished_at`. Never
    itself claims the resulting plan is travel-ready/final/guaranteed --
    see `GenerationJob`'s own docstring."""
    job.status = GenerationJobStatus.SUCCEEDED
    job.finished_at = _utc_now()
    job.result_version = result_version
    job.changed_sections = list(changed_sections) if changed_sections else []
    job.previous_version = previous_version
    job.targeted = targeted
    job.interpretation_status = interpretation_status
    job.execution_status = execution_status
    job.affected_day_indices = list(affected_day_indices) if affected_day_indices else []
    job.preserved_day_indices = list(preserved_day_indices) if preserved_day_indices else []
    job.diff = diff
    job.message = "Job completed successfully."
    job.error_code = None
    job.error_message = None
    return job


def mark_job_failed(
    job: GenerationJob,
    *,
    error_code: str,
    error_message: str,
    # Section 198B: optional targeted-result parity fields, mirroring the
    # sync route's own honest non-success outcomes -- never populated for
    # a legacy generate/regenerate job failure.
    targeted: bool = False,
    interpretation_status: str | None = None,
    execution_status: str | None = None,
    clarification_reason: str | None = None,
    clarification_possible_experience_ids: list[str] | None = None,
) -> GenerationJob:
    """Transitions `job` to `failed`, setting `finished_at`.

    `error_code`/`error_message` must already be a short, controlled,
    honest description (matching this codebase's existing refusal-message
    convention) -- never a raw exception string or stack trace. This
    never fabricates a success; a caller must never call
    `mark_job_succeeded` after this for the same job run.
    """
    job.status = GenerationJobStatus.FAILED
    job.finished_at = _utc_now()
    job.error_code = error_code
    job.error_message = error_message
    job.message = "Job failed."
    job.targeted = targeted
    job.interpretation_status = interpretation_status
    job.execution_status = execution_status
    job.clarification_reason = clarification_reason
    job.clarification_possible_experience_ids = (
        list(clarification_possible_experience_ids)
        if clarification_possible_experience_ids
        else []
    )
    return job


def mark_job_cancelled(
    job: GenerationJob, *, message: str = "Job was cancelled."
) -> GenerationJob:
    """Transitions `job` to `cancelled`, setting `finished_at`. Distinct
    from `mark_job_failed` -- a cancellation is never recorded as an
    error (`error_code`/`error_message` stay whatever they already were,
    normally both `None`)."""
    job.status = GenerationJobStatus.CANCELLED
    job.finished_at = _utc_now()
    job.message = message
    return job


# Duplicate-job/restart hardening (Step 186E,
# docs/14_backend_architecture.md section 118). FastAPI's own
# `BackgroundTasks` (the only executor this codebase uses -- no Redis/
# Celery/RQ/separate worker process) hold no durable state of their own:
# if the process restarts (crash, redeploy, manual restart) while a
# job's background task was `queued`/`running`, that in-memory thread is
# simply gone, and the persisted job record would otherwise say
# `running` forever with no way to ever complete -- blocking every
# future generate/regenerate attempt for its trip
# (`generation_job_service.check_no_duplicate_running_job`) permanently.
# `mark_job_interrupted` never fabricates a resumed run or a success --
# it is `mark_job_failed` under a specific, honest, controlled
# `error_code` so a caller (app startup recovery, or a stale-job
# reconciliation check) can explain in Developer Mode exactly what
# happened, distinctly from a real in-run exception (`STAGE_FAILED`
# etc.).
JOB_INTERRUPTED_ERROR_CODE = "JOB_INTERRUPTED"
JOB_INTERRUPTED_MESSAGE = (
    "This background job was interrupted before it completed (the "
    "backend process may have restarted, or the job ran longer than "
    "expected). Start generation again."
)


def mark_job_interrupted(
    job: GenerationJob, *, message: str = JOB_INTERRUPTED_MESSAGE
) -> GenerationJob:
    """Transitions `job` to `failed` with `error_code=JOB_INTERRUPTED` --
    never a stack trace, never a raw exception, and never a claim the
    job resumed or completed. Reuses `mark_job_failed` so every
    "finished, unsuccessfully" job shares the exact same status/
    `finished_at` contract regardless of *why* it stopped."""
    return mark_job_failed(job, error_code=JOB_INTERRUPTED_ERROR_CODE, error_message=message)
