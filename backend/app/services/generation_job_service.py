from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from fastapi import BackgroundTasks

from app.core.config import get_settings
from app.core.errors import REGENERATION_NOT_AVAILABLE_MESSAGE, job_already_running_error
from app.models.generation_job import (
    GenerationJob,
    GenerationJobType,
    create_queued_job,
    mark_job_failed,
    mark_job_interrupted,
    mark_job_running,
    mark_job_succeeded,
)
from app.models.planning_state import PlanningStage
from app.models.targeted_regeneration_runtime import TargetedRegenerationRuntimeStatus
from app.repositories.factory import get_job_repository, get_planning_state_repository
from app.schemas.errors import ErrorCode
from app.services.planning_orchestrator import planning_orchestrator
from app.services.regeneration_attempt_service import regeneration_attempt_service
from app.services.regeneration_mutation_service import (
    RegenerationMutationError,
    apply_regeneration_mutation,
)
from app.services.targeted_regeneration_application_service import (
    targeted_regeneration_application_service,
)

logger = logging.getLogger(__name__)

# Async job orchestration (Step 186C, docs/14_backend_architecture.md
# section 117; duplicate/failure/restart hardening in Step 186E, section
# 118; structured lifecycle logging in Step 187D, section 123), gated
# entirely by `Settings.async_generation_enabled` (default False --
# nothing in this module is ever called while it's off). Uses FastAPI's
# own `BackgroundTasks` (Starlette runs a sync callable via its thread
# pool, so a real ASGI server's event loop is never blocked by the
# synchronous `PlanningOrchestrator` calls below) -- no Redis, Celery,
# RQ, or separate worker process. Every job runner here calls the exact
# same `PlanningOrchestrator`/regeneration-mutation entry points the
# synchronous routes already call -- no planning stage is duplicated, no
# provider call added, and `PlanningState.generation_progress` keeps
# being updated by the orchestrator exactly as before; `GenerationJob` is
# job *control* state layered on top, never a replacement for it.
#
# Step 187D: every real, persisted lifecycle transition below now also
# emits one structured log line via `_job_log_fields` -- job_id/trip_id/
# owner_id/job_type/status/stage/error_code/duration_ms only (the exact
# `app.core.logging_config.ALLOWED_EXTRA_FIELDS` names already reserved
# for this since Step 187B), all derived from the already-persisted
# `GenerationJob` fields this module was already computing -- never a
# raw exception string, a `GenerationJob`/`PlanningState` model dump, a
# request/response body, or provider data. No job/duplicate/startup-
# recovery *behavior* changed by this step -- every log line is placed
# immediately after the state transition it describes already happened
# and was already persisted.

_GENERATE_JOB_FAILED_ERROR_CODE = "STAGE_FAILED"
_GENERATE_JOB_FAILED_MESSAGE = (
    "Trip plan generation failed unexpectedly. Check "
    "GET /trips/{trip_id}/generation-progress for which stage was "
    "running when it failed."
)

_REGENERATE_JOB_NO_LONGER_ELIGIBLE_MESSAGE = (
    "Regeneration could not run: the trip's pending feedback or affected "
    "stages changed before the job started running."
)

_REGENERATE_JOB_UNEXPECTED_FAILURE_MESSAGE = (
    "Regeneration failed unexpectedly. No new version was created and no "
    "feedback was marked applied."
)


def safe_job_error_code(exc: BaseException) -> str:
    """Short, machine-readable failure code for an unexpected background
    job exception. `exc` is accepted for call-site symmetry with
    `safe_job_error_message` (and so a future caller could branch on
    `type(exc)` without ever reading its message/args) but its contents
    are deliberately never read here -- an exception's message/args could
    incidentally contain a file path, a request-derived string, or other
    detail this codebase's no-secret-leak policy doesn't want surfaced.
    Every call today returns this one fixed, controlled value, matching
    this module's pre-existing generate-failure code.
    """
    del exc  # deliberately unused -- see docstring
    return _GENERATE_JOB_FAILED_ERROR_CODE


def safe_job_error_message(exc: BaseException, *, fallback: str) -> str:
    """Returns `fallback` unchanged -- never `str(exc)`, `repr(exc)`, or
    a traceback. Exists so every background job failure path calls
    through one function that can never be handed a raw exception
    string by accident; `fallback` must already be a short, controlled,
    honest, pre-written description (this module's own module-level
    `_..._MESSAGE` constants, or an equivalent caller-owned constant).
    `exc` is accepted only for call-site symmetry with
    `safe_job_error_code` -- its contents are never read.
    """
    del exc  # deliberately unused -- see docstring
    return fallback


def _job_log_fields(
    job: GenerationJob, *, status: str | None = None, error_code: str | None = None
) -> dict[str, object]:
    """Builds a safe, allowlisted `extra=` dict describing `job`'s
    current identity/lifecycle fields (Step 187D) -- job_id/trip_id/
    owner_id/job_type always; `status`/`stage`/`error_code`/`duration_ms`
    only when a real value exists. Never a raw exception message, never
    a `GenerationJob.model_dump()`, never `PlanningState`/provider data.

    `status`/`error_code` default to `job.status.value`/`job.error_code`
    -- the caller only needs to pass them explicitly for the one case
    (a background job's own exception-handling `except` block) where the
    log call happens *before* `mark_job_failed` has updated `job` in
    place, so `job.status` is still `"running"` at that exact line.

    `duration_ms` is never passed in -- it is always derived here, from
    `job.started_at`/`job.finished_at` (both real, already-persisted
    timestamps), and only included once both exist (i.e. only on a
    terminal job); this is never a fabricated or estimated duration.

    A field whose value is `None`/absent is omitted from the returned
    dict entirely, never included as `null` -- matches this app's
    existing "omit an absent field" convention (see
    `app.core.logging_config`'s own `request_id` handling, Step 187C).
    """
    fields: dict[str, object] = {
        "trip_id": job.trip_id,
        "owner_id": job.owner_id,
        "job_id": job.job_id,
        "job_type": job.job_type.value,
        "status": status if status is not None else job.status.value,
    }
    if job.progress_stage is not None:
        fields["stage"] = job.progress_stage
    resolved_error_code = error_code if error_code is not None else job.error_code
    if resolved_error_code is not None:
        fields["error_code"] = resolved_error_code
    if job.started_at is not None and job.finished_at is not None:
        duration = (job.finished_at - job.started_at).total_seconds() * 1000
        fields["duration_ms"] = round(duration, 3)
    return fields


# --- Step 186E: duplicate-job race hardening (single-process only) ---
#
# `check_no_duplicate_running_job` (below) reads the job repository, and
# `start_generate_job`/`start_regenerate_job` then write to it -- two
# separate steps with no atomicity between them. FastAPI runs a sync
# `def` route (like both `/generate`/`/regenerate` handlers) in a real
# thread-pool thread (via anyio), so two near-simultaneous requests for
# the same trip_id can genuinely interleave between that read and the
# later write, both seeing "no running job" and both creating one --
# defeating the guard. A per-trip `threading.Lock` closes that window
# for this single process; it is NOT a substitute for a real
# cross-process/multi-worker lock (Redis, a database row lock, etc.),
# which remains deferred (see docs/14_backend_architecture.md section
# 118) -- this guards against two threads in *this* process racing each
# other, nothing more. The lock registry grows by one entry per distinct
# trip_id ever seen by this process and is never pruned -- acceptable
# for this local-dev-scale MVP (see that same doc section for why this
# isn't a real leak concern here).
_trip_lock_registry: dict[str, threading.Lock] = {}
_trip_lock_registry_guard = threading.Lock()


def _lock_for_trip(trip_id: str) -> threading.Lock:
    with _trip_lock_registry_guard:
        lock = _trip_lock_registry.get(trip_id)
        if lock is None:
            lock = threading.Lock()
            _trip_lock_registry[trip_id] = lock
        return lock


def _reconcile_stale_jobs(trip_id: str) -> None:
    """Marks any `queued`/`running` job for `trip_id` older than
    `Settings.generation_job_stale_after_seconds` as interrupted/failed
    (`mark_job_interrupted`) -- called before any duplicate-job check so
    a stale job can never block a fresh attempt forever.

    This is a narrower, always-on complement to app-startup recovery
    (`recover_interrupted_jobs`, called once per process start): startup
    recovery only ever sees jobs left over from a *previous* process;
    this also catches the rarer case of a background task that silently
    died without the process itself restarting (e.g. an exception truly
    unexpected enough to have escaped this module's own try/except
    somehow). Never touches a job younger than the staleness window,
    never touches a terminal job, and never touches a different trip's
    jobs -- `list_running_by_trip_id` is already scoped to `trip_id`.
    """
    job_repo = get_job_repository()
    stale_after_seconds = get_settings().generation_job_stale_after_seconds
    now = datetime.now(timezone.utc)
    for job in job_repo.list_running_by_trip_id(trip_id):
        reference_time = job.started_at or job.created_at
        age_seconds = (now - reference_time).total_seconds()
        if age_seconds > stale_after_seconds:
            interrupted_job = mark_job_interrupted(job)
            logger.warning(
                "Marking stale %s job %s for trip %s as interrupted "
                "(queued/running for %.0fs, past the %ds staleness window).",
                job.job_type.value,
                job.job_id,
                trip_id,
                age_seconds,
                stale_after_seconds,
                extra=_job_log_fields(interrupted_job),
            )
            job_repo.save(interrupted_job)


def check_no_duplicate_running_job(
    trip_id: str, *, attempted_job_type: GenerationJobType | None = None
) -> None:
    """Reconciles any stale `queued`/`running` job for `trip_id` first
    (see `_reconcile_stale_jobs`), then raises
    `job_already_running_error(trip_id)` (409) when
    `Settings.generation_job_max_running_per_trip` genuinely active
    queued/running jobs remain. Never blocks a different trip, never
    blocks on a terminal (succeeded/failed/cancelled) job, and never
    leaks any other user's jobs -- `list_running_by_trip_id` is scoped to
    `trip_id` only. Local_json single-process is sufficient for this MVP
    guard; true cross-process locking is deferred (see this module's own
    `_trip_lock_registry` comment and docs/14_backend_architecture.md
    section 118).

    `attempted_job_type` (Step 187D, optional, defaults to `None`) is
    only used to enrich the structured log line emitted on rejection --
    it never changes which trip/jobs are checked or whether this raises.
    The rejection log's `job_id`/`status`/`owner_id` describe the
    *blocking* job, not the rejected attempt (no job is ever created for
    a rejected attempt) -- safe to log here because that blocking job
    always belongs to the same `trip_id`/owner this call was already
    scoped to (both callers, `start_generate_job`/`start_regenerate_job`,
    are only ever reached through an owner-protected route), never a
    different user's job.
    """
    _reconcile_stale_jobs(trip_id)
    running = get_job_repository().list_running_by_trip_id(trip_id)
    if len(running) >= get_settings().generation_job_max_running_per_trip:
        blocking_job = running[0]
        logger.warning(
            "Rejected a new %s job for trip %s: job %s is already %s "
            "(JOB_ALREADY_RUNNING).",
            attempted_job_type.value if attempted_job_type is not None else "generate/regenerate",
            trip_id,
            blocking_job.job_id,
            blocking_job.status.value,
            extra={
                "trip_id": trip_id,
                "owner_id": blocking_job.owner_id,
                "job_id": blocking_job.job_id,
                "job_type": (
                    attempted_job_type.value
                    if attempted_job_type is not None
                    else blocking_job.job_type.value
                ),
                "status": blocking_job.status.value,
                "error_code": "JOB_ALREADY_RUNNING",
            },
        )
        raise job_already_running_error(trip_id)


def start_generate_job(
    *, trip_id: str, owner_id: str, background_tasks: BackgroundTasks
) -> GenerationJob:
    """Creates a queued `generate` job for `trip_id` and schedules its
    background execution. Raises `job_already_running_error` instead of
    creating a second job when one is already queued/running for this
    trip. The stale-reconciliation-check-create sequence runs under a
    per-trip lock (see `_lock_for_trip`) so two near-simultaneous
    requests for the same trip within this process can never both pass
    the check before either creates a job.
    """
    with _lock_for_trip(trip_id):
        check_no_duplicate_running_job(trip_id, attempted_job_type=GenerationJobType.GENERATE)
        job = create_queued_job(
            trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
        )
        get_job_repository().create(job)
        logger.info(
            "Generate job %s queued for trip %s.",
            job.job_id,
            trip_id,
            extra=_job_log_fields(job),
        )
    background_tasks.add_task(run_generate_job, job.job_id)
    return job


def start_regenerate_job(
    *,
    trip_id: str,
    owner_id: str,
    affected_stages: list[PlanningStage],
    applied_feedback_event_ids: list[str],
    background_tasks: BackgroundTasks,
) -> GenerationJob:
    """Creates a queued `regenerate` job for `trip_id` and schedules its
    background execution. Must only be called after the caller (`POST
    /trips/{trip_id}/regenerate`) has already synchronously confirmed
    every cheap refusal check (confirm/locks/pending feedback/derivable
    affected stage) passes -- this function performs none of those checks
    itself, only the duplicate-job guard, under the same per-trip lock
    `start_generate_job` uses (shared registry, keyed by `trip_id`, so a
    generate and a regenerate request for the same trip can never race
    each other into double-creating a job either).
    `affected_stages`/`applied_feedback_event_ids` are the exact values
    the route already computed for this decision; the background runner
    re-resolves them against a freshly-loaded `PlanningState` rather than
    trusting any live object reference from the request, so this also
    works correctly against a repository backend where a background task
    cannot assume it holds the same object identity a request-time load
    returned.
    """
    with _lock_for_trip(trip_id):
        check_no_duplicate_running_job(trip_id, attempted_job_type=GenerationJobType.REGENERATE)
        job = create_queued_job(
            trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE
        )
        if affected_stages:
            # Best-known stage at creation time -- not a fabricated
            # cosmetic phase, and not re-derived while queued/running;
            # the runner doesn't recompute this stage-by-stage while it
            # executes (that granularity remains PlanningState.
            # generation_progress's job), only the terminal outcome
            # updates it again.
            job.progress_stage = affected_stages[0].value
        get_job_repository().create(job)
        logger.info(
            "Regenerate job %s queued for trip %s.",
            job.job_id,
            trip_id,
            extra=_job_log_fields(job),
        )
    background_tasks.add_task(
        run_regenerate_job,
        job.job_id,
        [stage.value for stage in affected_stages],
        list(applied_feedback_event_ids),
    )
    return job


def get_job(trip_id: str, job_id: str) -> GenerationJob | None:
    """Returns the job only if it belongs to `trip_id` -- a job_id that
    exists but belongs to a different trip is reported exactly like a
    job that doesn't exist at all, never leaking its existence.
    Reconciles stale jobs for `trip_id` first (Step 186E) so a caller
    reading a job's status right after a long-hung background task sees
    an honest `failed`/`JOB_INTERRUPTED` state rather than a `running`
    status that will never change.
    """
    _reconcile_stale_jobs(trip_id)
    job = get_job_repository().get_by_job_id(job_id)
    if job is None or job.trip_id != trip_id:
        return None
    return job


def list_jobs(trip_id: str) -> list[GenerationJob]:
    """Every job recorded for `trip_id`, oldest first. Reconciles stale
    jobs for `trip_id` first (Step 186E) for the same reason `get_job`
    does."""
    _reconcile_stale_jobs(trip_id)
    return get_job_repository().list_by_trip_id(trip_id)


def recover_interrupted_jobs() -> int:
    """App-startup recovery (Step 186E, called once from `app.main`'s
    lifespan handler, docs/14_backend_architecture.md section 118).

    FastAPI's own `BackgroundTasks` are in-process and hold no durable
    state -- if the backend process restarts (crash, redeploy, a manual
    restart, `--reload` picking up a code change) while a job's
    background task was `queued`/`running`, that thread is gone. Without
    this, the persisted job record would say `running` forever, and
    `check_no_duplicate_running_job` would refuse every future generate/
    regenerate attempt for that trip permanently, with no way out short
    of manually editing the local JSON file.

    Called synchronously, exactly once, before the app starts accepting
    any request -- by construction, any job already persisted as
    `queued`/`running` at this exact point cannot belong to the process
    now starting (which hasn't had a chance to create one yet), so every
    one of them is unambiguously left over from a previous process. This
    is not a "can't tell which process a job belongs to, so give up and
    mark everything" fallback -- the ordering guarantee makes it
    correct, not just convenient. This never resumes a job (there is no
    safe way to resume a partially-run `PlanningOrchestrator` call) and
    never fabricates a completed plan -- it marks each one `failed` with
    the honest `JOB_INTERRUPTED` error (`mark_job_interrupted`) so
    Developer Mode can explain what happened and so a fresh attempt is
    never blocked forever.

    A no-op, and safe to call, when no non-terminal jobs are persisted --
    normal startup is never slowed or broken by this, and it never opens
    a database/network connection (the local_json job repository is
    already loaded into memory by the time this runs).
    """
    job_repo = get_job_repository()
    non_terminal = job_repo.list_non_terminal()
    for job in non_terminal:
        interrupted_job = mark_job_interrupted(job)
        job_repo.save(interrupted_job)
        # Step 187D: per-job structured line -- the aggregate warning
        # below only ever carried a count, with no way to trace which
        # specific trip/job was affected; this fills that gap without
        # changing the aggregate warning's own existing text/level.
        logger.info(
            "Job %s for trip %s marked interrupted at startup.",
            interrupted_job.job_id,
            interrupted_job.trip_id,
            extra=_job_log_fields(interrupted_job),
        )
    if non_terminal:
        logger.warning(
            "Marked %d job(s) left queued/running by a previous process "
            "as failed (JOB_INTERRUPTED) at startup.",
            len(non_terminal),
        )
    return len(non_terminal)


def run_generate_job(job_id: str) -> None:
    """Background execution for a `generate` job. Calls the exact same
    `PlanningOrchestrator` entry point `POST /trips/{trip_id}/generate`
    already calls when async mode is off -- no planning stage is
    duplicated or reimplemented here, no provider call is added, and
    `PlanningState.generation_progress` is updated by the orchestrator
    itself exactly as before. Always reloads its own repository
    references rather than depending on anything the dispatching request
    might have held, since (on a real ASGI server) this runs after that
    request has already returned its response to the caller.
    """
    job_repo = get_job_repository()
    job = job_repo.get_by_job_id(job_id)
    if job is None:
        return

    job = mark_job_running(job)
    job_repo.save(job)
    logger.info(
        "Generate job %s started for trip %s.",
        job_id,
        job.trip_id,
        extra=_job_log_fields(job),
    )

    try:
        if get_settings().planning_engine_mode == "langgraph":
            planning_state = planning_orchestrator.generate_full_plan_via_langgraph(job.trip_id)
        else:
            planning_state = planning_orchestrator.generate_full_plan(job.trip_id)
    except Exception as exc:
        error_code = safe_job_error_code(exc)
        job = mark_job_failed(
            job,
            error_code=error_code,
            error_message=safe_job_error_message(exc, fallback=_GENERATE_JOB_FAILED_MESSAGE),
        )
        # Logged *after* mark_job_failed (Step 187D) so `_job_log_fields`
        # can read the now-final `finished_at` and include a real
        # `duration_ms` -- this only reorders when the log line is
        # emitted relative to an in-memory mutation that was already
        # about to happen either way; `job_repo.save(job)` below still
        # persists exactly once, with the exact same final job state as
        # before this step.
        logger.warning(
            "Background generate job %s failed unexpectedly for trip %s.",
            job_id,
            job.trip_id,
            exc_info=True,
            extra=_job_log_fields(job),
        )
        job_repo.save(job)
        return

    result_version = planning_state.metadata.current_version
    changed_sections = (
        list(planning_state.version_history[-1].changed_sections)
        if planning_state.version_history
        else []
    )
    job = mark_job_succeeded(
        job, result_version=result_version, changed_sections=changed_sections
    )
    # Honest final snapshot of a real stage key the orchestrator itself
    # already recorded on PlanningState.generation_progress -- never a
    # fabricated phase, and never updated incrementally mid-run (that
    # granularity stays GET /trips/{trip_id}/generation-progress's job).
    if planning_state.generation_progress is not None:
        job.progress_stage = planning_state.generation_progress.current_stage
    job_repo.save(job)
    logger.info(
        "Generate job %s succeeded for trip %s.",
        job_id,
        job.trip_id,
        extra=_job_log_fields(job),
    )


def run_regenerate_job(
    job_id: str, affected_stage_values: list[str], applied_feedback_event_ids: list[str]
) -> None:
    """Background execution for a `regenerate` job. Calls the exact same
    `apply_regeneration_mutation` helper the synchronous route calls --
    no regeneration semantics are duplicated or reimplemented. Re-derives
    `affected_stages`/`pending_events` from a freshly-loaded
    `PlanningState` rather than trusting any live object from the
    dispatching request; if the trip's state no longer supports the
    decision already made at request time (e.g. the named feedback events
    are no longer present), this fails the job safely rather than acting
    on stale data or crashing.
    """
    job_repo = get_job_repository()
    job = job_repo.get_by_job_id(job_id)
    if job is None:
        return

    job = mark_job_running(job, progress_stage=job.progress_stage)
    job_repo.save(job)
    logger.info(
        "Regenerate job %s started for trip %s.",
        job_id,
        job.trip_id,
        extra=_job_log_fields(job),
    )

    # Step 186E: everything below is wrapped in one top-level guard --
    # before this, only `apply_regeneration_mutation`'s own
    # `RegenerationMutationError` was caught, so an unexpected exception
    # from anywhere else in this function (e.g. `record_applied_attempt`/
    # `state_repo.save` after an otherwise-successful mutation) would
    # have escaped uncaught, left the job `running` forever, and blocked
    # every future attempt for this trip via `check_no_duplicate_
    # running_job` until the staleness window or a restart cleared it.
    # This guarantees "any exception marks job failed" universally, per
    # this module's own safety contract.
    try:
        state_repo = get_planning_state_repository()
        planning_state = state_repo.get_by_trip_id(job.trip_id)
        if planning_state is None:
            job = mark_job_failed(
                job,
                error_code=ErrorCode.TRIP_NOT_FOUND.value,
                error_message=f"Trip '{job.trip_id}' no longer exists.",
            )
            job_repo.save(job)
            # Step 187D: a real, clean (non-exception) failure -- the
            # trip was deleted/never existed by the time this background
            # task ran. `info`, not `warning`: this is an honest,
            # expected outcome of the freshly-reloaded-state check this
            # function's own docstring describes, not an anomaly.
            logger.info(
                "Regenerate job %s failed for trip %s: trip no longer exists.",
                job_id,
                job.trip_id,
                extra=_job_log_fields(job),
            )
            return

        try:
            affected_stages = [PlanningStage(value) for value in affected_stage_values]
        except ValueError:
            affected_stages = []

        event_id_set = set(applied_feedback_event_ids)
        pending_events = [
            event
            for event in planning_state.feedback_history
            if event.feedback_event_id in event_id_set
        ]

        if not affected_stages or not pending_events:
            job = mark_job_failed(
                job,
                error_code=ErrorCode.REGENERATION_NOT_AVAILABLE.value,
                error_message=_REGENERATE_JOB_NO_LONGER_ELIGIBLE_MESSAGE,
            )
            job_repo.save(job)
            # Step 187D: same reasoning as above -- a clean, expected
            # refusal (the trip's pending feedback/affected stages
            # changed between request time and this background run), not
            # an unexpected exception.
            logger.info(
                "Regenerate job %s failed for trip %s: no longer eligible.",
                job_id,
                job.trip_id,
                extra=_job_log_fields(job),
            )
            return

        try:
            result = apply_regeneration_mutation(planning_state, affected_stages, pending_events)
        except RegenerationMutationError as exc:
            failed_state = regeneration_attempt_service.record_blocked_attempt(
                exc.planning_state,
                message=REGENERATION_NOT_AVAILABLE_MESSAGE,
                status="failed",
            )
            state_repo.save(failed_state)
            job = mark_job_failed(
                job,
                error_code=ErrorCode.REGENERATION_NOT_AVAILABLE.value,
                error_message=REGENERATION_NOT_AVAILABLE_MESSAGE,
            )
            # Logged after mark_job_failed (Step 187D) so `_job_log_fields`
            # can include a real `duration_ms` -- see run_generate_job's
            # equivalent comment; no side effect above was reordered.
            logger.warning(
                "Background regenerate job %s failed unexpectedly for trip %s.",
                job_id,
                job.trip_id,
                exc_info=True,
                extra=_job_log_fields(job),
            )
            job_repo.save(job)
            return

        final_state = regeneration_attempt_service.record_applied_attempt(result.planning_state)
        state_repo.save(final_state)

        job = mark_job_succeeded(
            job,
            result_version=result.new_version_label,
            changed_sections=result.changed_sections,
            # Section 198B (Task 5, legacy-async regression fix): a
            # legacy (non-targeted) async job carries the same real
            # `previous_version` the synchronous legacy route already
            # returns (`RegenerationMutationResult.previous_version`,
            # Step 186C's own dataclass) -- before this fix, the async
            # success banner showed "None yet -> vN" instead of the true
            # "vN-1 -> vN" transition, since `previous_version` simply
            # went unset (`targeted` stays `False`, matching a legacy
            # job -- untouched by this fix).
            previous_version=result.previous_version,
        )
        job_repo.save(job)
        logger.info(
            "Regenerate job %s succeeded for trip %s.",
            job_id,
            job.trip_id,
            extra=_job_log_fields(job),
        )
    except Exception as exc:
        error_code = safe_job_error_code(exc)
        job = mark_job_failed(
            job,
            error_code=error_code,
            error_message=safe_job_error_message(
                exc, fallback=_REGENERATE_JOB_UNEXPECTED_FAILURE_MESSAGE
            ),
        )
        logger.warning(
            "Background regenerate job %s failed unexpectedly for trip %s "
            "(escaped the inner guards).",
            job_id,
            job.trip_id,
            exc_info=True,
            extra=_job_log_fields(job),
        )
        job_repo.save(job)


# -- Section 197C: targeted-mode async regeneration -------------------------


def start_targeted_regenerate_job(
    *, trip_id: str, owner_id: str, background_tasks: BackgroundTasks
) -> GenerationJob:
    """The targeted-mode counterpart to `start_regenerate_job`. Reuses the
    exact same duplicate-job guard/per-trip lock (`check_no_duplicate_
    running_job`/`_lock_for_trip`) -- a targeted and a legacy regenerate
    request for the same trip can never race each other into double-
    creating a job either (Task 30). Unlike the legacy starter, this
    takes no `affected_stages`/`applied_feedback_event_ids` -- the
    background runner delegates entirely to
    `TargetedRegenerationApplicationService.regenerate(trip_id)`, which
    reloads state and re-derives everything itself, exactly like the
    synchronous route does.
    """
    with _lock_for_trip(trip_id):
        check_no_duplicate_running_job(trip_id, attempted_job_type=GenerationJobType.REGENERATE)
        job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE)
        get_job_repository().create(job)
        logger.info(
            "Targeted regenerate job %s queued for trip %s.",
            job.job_id,
            trip_id,
            extra=_job_log_fields(job),
        )
    background_tasks.add_task(run_targeted_regenerate_job, job.job_id)
    return job


def run_targeted_regenerate_job(job_id: str) -> None:
    """Background execution for a targeted-mode regenerate job. Calls the
    exact same `TargetedRegenerationApplicationService.regenerate` the
    synchronous route calls (Task 28: semantic parity by construction --
    no regeneration/persistence/versioning logic is duplicated here).
    Every exception is caught (mirroring `run_regenerate_job`'s own Step
    186E guarantee) so a failure here always marks the job `failed`
    rather than leaving it `running` forever.
    """
    job_repo = get_job_repository()
    job = job_repo.get_by_job_id(job_id)
    if job is None:
        return

    job = mark_job_running(job, progress_stage=None)
    job_repo.save(job)
    logger.info(
        "Targeted regenerate job %s started for trip %s.",
        job_id,
        job.trip_id,
        extra=_job_log_fields(job),
    )

    try:
        result = targeted_regeneration_application_service.regenerate(job.trip_id)

        if result.status == TargetedRegenerationRuntimeStatus.COMPLETED:
            # Section 198B: carry the SAME canonical result the sync route
            # returns through the job record -- never a second, hand-
            # derived diff/summary.
            job = mark_job_succeeded(
                job,
                result_version=result.new_version,
                changed_sections=[
                    f"day_{day}" for day in (result.diff.affected_day_indices if result.diff else [])
                ],
                previous_version=result.source_version,
                targeted=True,
                interpretation_status=result.interpretation_status,
                execution_status=result.execution_status,
                affected_day_indices=result.diff.affected_day_indices if result.diff else [],
                preserved_day_indices=result.diff.preserved_day_indices if result.diff else [],
                diff=result.diff,
            )
            job_repo.save(job)
            logger.info(
                "Targeted regenerate job %s succeeded for trip %s.",
                job_id,
                job.trip_id,
                extra=_job_log_fields(job),
            )
            return

        error_code_by_status = {
            TargetedRegenerationRuntimeStatus.NEEDS_CLARIFICATION: ErrorCode.REGENERATION_NEEDS_CLARIFICATION.value,
            TargetedRegenerationRuntimeStatus.CONFLICT: ErrorCode.REGENERATION_CONFLICT.value,
            TargetedRegenerationRuntimeStatus.PROVIDER_UNAVAILABLE: ErrorCode.REGENERATION_PROVIDER_UNAVAILABLE.value,
        }
        error_code = error_code_by_status.get(result.status, ErrorCode.REGENERATION_NOT_AVAILABLE.value)
        # Task 5: structured targeted failure information (interpretation/
        # execution status, and clarification detail when the runtime
        # result carried one) is preserved on the job record rather than
        # collapsed into an opaque generic string -- the frontend can
        # distinguish clarification/provider-unavailable/conflict exactly
        # like it already does for the sync path.
        job = mark_job_failed(
            job,
            error_code=error_code,
            error_message=result.message,
            targeted=True,
            interpretation_status=result.interpretation_status,
            execution_status=result.execution_status,
            clarification_reason=result.clarification_reason,
            clarification_possible_experience_ids=result.clarification_possible_experience_ids,
        )
        job_repo.save(job)
        logger.info(
            "Targeted regenerate job %s did not complete for trip %s: %s.",
            job_id,
            job.trip_id,
            result.status.value,
            extra=_job_log_fields(job),
        )
    except Exception as exc:
        error_code = safe_job_error_code(exc)
        job = mark_job_failed(
            job,
            error_code=error_code,
            error_message=safe_job_error_message(
                exc, fallback="Targeted regeneration failed unexpectedly."
            ),
        )
        logger.warning(
            "Background targeted regenerate job %s failed unexpectedly for trip %s.",
            job_id,
            job.trip_id,
            exc_info=True,
            extra=_job_log_fields(job),
        )
        job_repo.save(job)
