from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.generation_job import (
    GenerationJobStatus,
    GenerationJobType,
    create_queued_job,
    mark_job_interrupted,
    mark_job_running,
    mark_job_succeeded,
)
from app.repositories.job_repository import job_repository
from app.schemas.errors import ErrorCode
from app.services import generation_job_service

# Duplicate-job race / stale-job / restart-recovery / background-failure
# hardening (Step 186E, docs/14_backend_architecture.md section 118).
# Single-process hardening only -- these tests never claim or exercise
# cross-process/multi-worker locking, which remains deferred.


def _owner_id(client: TestClient) -> str:
    response = client.get("/auth/me")
    assert response.status_code == 200
    return response.json()["data"]["user"]["user_id"]


# ---------------------------------------------------------------------------
# 1. Duplicate-job race hardening (process-local lock)
# ---------------------------------------------------------------------------


def test_start_generate_job_blocks_while_another_thread_holds_the_trip_lock(
    created_trip_id: str, client: TestClient
) -> None:
    """Deterministic proof of mutual exclusion: while another thread
    holds `created_trip_id`'s lock (simulating it being mid-way through
    its own locked check-then-create section), a concurrent
    `start_generate_job` call for the *same* trip must not proceed past
    the lock at all -- it must still be blocked, waiting, moments later.
    Only once the lock is released does it run (and, since nothing was
    actually created while the lock was held here, it succeeds).
    """
    owner_id = _owner_id(client)
    background_tasks = BackgroundTasks()
    lock = generation_job_service._lock_for_trip(created_trip_id)

    lock.acquire()
    try:
        result_holder: dict[str, object] = {}

        def worker() -> None:
            result_holder["job"] = generation_job_service.start_generate_job(
                trip_id=created_trip_id, owner_id=owner_id, background_tasks=background_tasks
            )

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "start_generate_job did not block on the held trip lock"
        assert "job" not in result_holder
    finally:
        lock.release()

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "job" in result_holder


def test_start_generate_job_lock_prevents_duplicate_creation_across_the_race_window(
    created_trip_id: str, client: TestClient
) -> None:
    """Simulates the exact race the lock closes: while thread A is
    inside its locked check-then-create section (already past the
    "nothing running" check, now about to persist the new job), thread B
    calls `start_generate_job` for the same trip. Without the lock, B
    could interleave between A's check and A's create and see "nothing
    running" too, creating a second job. With the lock, B simply waits
    for A to finish -- so by the time B's own check runs, it correctly
    sees A's job and refuses.
    """
    owner_id = _owner_id(client)
    background_tasks = BackgroundTasks()
    lock = generation_job_service._lock_for_trip(created_trip_id)

    lock.acquire()
    try:
        # Thread A's own persist step, performed here while "holding its
        # place" in the critical section (the real function would do
        # this itself, under the same lock).
        job = create_queued_job(
            trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
        )
        job_repository.create(job)

        result_holder: dict[str, object] = {}

        def worker() -> None:
            try:
                generation_job_service.start_generate_job(
                    trip_id=created_trip_id, owner_id=owner_id, background_tasks=background_tasks
                )
                result_holder["outcome"] = "ok"
            except AppError as exc:
                result_holder["outcome"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "start_generate_job did not block on the held trip lock"
    finally:
        lock.release()

    thread.join(timeout=5)
    assert not thread.is_alive()
    outcome = result_holder["outcome"]
    assert isinstance(outcome, AppError)
    assert outcome.code == ErrorCode.JOB_ALREADY_RUNNING
    assert len(job_repository.list_by_trip_id(created_trip_id)) == 1


def test_start_regenerate_job_shares_the_same_per_trip_lock_as_generate(
    created_trip_id: str, client: TestClient
) -> None:
    """A generate and a regenerate request for the same trip must not be
    able to race each other into double-creating a job either -- both
    `start_generate_job`/`start_regenerate_job` lock on the same
    trip_id-keyed registry, proven here by acquiring `created_trip_id`'s
    lock externally and confirming a concurrent `start_regenerate_job`
    call blocks on it too."""
    owner_id = _owner_id(client)
    background_tasks = BackgroundTasks()
    lock = generation_job_service._lock_for_trip(created_trip_id)

    lock.acquire()
    try:
        result_holder: dict[str, object] = {}

        def worker() -> None:
            result_holder["job"] = generation_job_service.start_regenerate_job(
                trip_id=created_trip_id,
                owner_id=owner_id,
                affected_stages=[],
                applied_feedback_event_ids=[],
                background_tasks=background_tasks,
            )

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "start_regenerate_job did not block on the held trip lock"
    finally:
        lock.release()

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "job" in result_holder


def test_duplicate_guard_still_ignores_terminal_and_other_trip_jobs_under_lock(
    created_trip_id: str, client: TestClient
) -> None:
    """The per-trip lock changes nothing about which jobs count as
    "running" -- terminal jobs and other trips' jobs still never block."""
    owner_id = _owner_id(client)
    background_tasks = BackgroundTasks()

    terminal_job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    mark_job_succeeded(terminal_job)
    job_repository.create(terminal_job)

    other_trip_job = create_queued_job(
        trip_id="trip_completely_different", owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(other_trip_job)

    job = generation_job_service.start_generate_job(
        trip_id=created_trip_id, owner_id=owner_id, background_tasks=background_tasks
    )
    assert job.status == GenerationJobStatus.QUEUED


# ---------------------------------------------------------------------------
# 2. Stale/interrupted job model
# ---------------------------------------------------------------------------


def test_mark_job_interrupted_sets_failed_status_with_safe_message(
    created_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    mark_job_running(job)

    interrupted = mark_job_interrupted(job)

    assert interrupted.status == GenerationJobStatus.FAILED
    assert interrupted.error_code == "JOB_INTERRUPTED"
    assert interrupted.finished_at is not None
    assert interrupted.error_message is not None
    assert "Traceback" not in interrupted.error_message
    assert "Exception" not in interrupted.error_message


def test_mark_job_interrupted_accepts_a_custom_message(
    created_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )

    interrupted = mark_job_interrupted(job, message="Custom interrupted message.")

    assert interrupted.error_message == "Custom interrupted message."
    assert interrupted.error_code == "JOB_INTERRUPTED"


# ---------------------------------------------------------------------------
# 3 & 4. Stale guard before new job / startup recovery
# ---------------------------------------------------------------------------


def _make_stale_running_job(trip_id: str, owner_id: str, *, age_seconds: int) -> None:
    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    mark_job_running(job)
    job.started_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    job_repository.create(job)


def test_reconcile_stale_jobs_marks_old_running_job_interrupted(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = _owner_id(client)
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "60")
    get_settings.cache_clear()
    _make_stale_running_job(created_trip_id, owner_id, age_seconds=120)

    generation_job_service._reconcile_stale_jobs(created_trip_id)
    get_settings.cache_clear()

    jobs = job_repository.list_by_trip_id(created_trip_id)
    assert len(jobs) == 1
    assert jobs[0].status == GenerationJobStatus.FAILED
    assert jobs[0].error_code == "JOB_INTERRUPTED"


def test_reconcile_stale_jobs_leaves_fresh_running_job_untouched(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = _owner_id(client)
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "3600")
    get_settings.cache_clear()
    _make_stale_running_job(created_trip_id, owner_id, age_seconds=5)

    generation_job_service._reconcile_stale_jobs(created_trip_id)
    get_settings.cache_clear()

    jobs = job_repository.list_by_trip_id(created_trip_id)
    assert len(jobs) == 1
    assert jobs[0].status == GenerationJobStatus.RUNNING


def test_stale_running_job_does_not_block_a_new_generation_forever(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old stale running job must not block a new generation forever --
    the duplicate guard reconciles it first, then allows the new job."""
    owner_id = _owner_id(client)
    background_tasks = BackgroundTasks()
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "60")
    get_settings.cache_clear()
    _make_stale_running_job(created_trip_id, owner_id, age_seconds=120)

    job = generation_job_service.start_generate_job(
        trip_id=created_trip_id, owner_id=owner_id, background_tasks=background_tasks
    )
    get_settings.cache_clear()

    assert job.status == GenerationJobStatus.QUEUED
    jobs = job_repository.list_by_trip_id(created_trip_id)
    assert len(jobs) == 2
    assert {j.status for j in jobs} == {GenerationJobStatus.FAILED, GenerationJobStatus.QUEUED}


def test_freshly_queued_job_still_blocks_duplicate_after_stale_reconciliation(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A newly queued/running job (not stale) must still block a
    duplicate -- reconciliation never weakens the real guard."""
    owner_id = _owner_id(client)
    background_tasks = BackgroundTasks()
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "3600")
    get_settings.cache_clear()
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(job)

    with pytest.raises(AppError) as exc_info:
        generation_job_service.start_generate_job(
            trip_id=created_trip_id, owner_id=owner_id, background_tasks=background_tasks
        )
    get_settings.cache_clear()
    assert exc_info.value.code == ErrorCode.JOB_ALREADY_RUNNING


def test_recover_interrupted_jobs_marks_all_persisted_non_terminal_jobs_failed(
    created_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    running_job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    mark_job_running(running_job)
    job_repository.create(running_job)

    queued_job = create_queued_job(
        trip_id="trip_other", owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(queued_job)

    succeeded_job = create_queued_job(
        trip_id="trip_third", owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    mark_job_succeeded(succeeded_job)
    job_repository.create(succeeded_job)

    recovered_count = generation_job_service.recover_interrupted_jobs()

    assert recovered_count == 2
    assert job_repository.get_by_job_id(running_job.job_id).status == GenerationJobStatus.FAILED
    assert (
        job_repository.get_by_job_id(running_job.job_id).error_code == "JOB_INTERRUPTED"
    )
    assert job_repository.get_by_job_id(queued_job.job_id).status == GenerationJobStatus.FAILED
    # A job that had already succeeded before "restart" must be left
    # completely alone -- recovery never touches a terminal job.
    assert job_repository.get_by_job_id(succeeded_job.job_id).status == GenerationJobStatus.SUCCEEDED


def test_recover_interrupted_jobs_is_a_no_op_when_nothing_persisted() -> None:
    assert generation_job_service.recover_interrupted_jobs() == 0


def test_recovered_interrupted_job_no_longer_blocks_a_new_job(
    created_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    background_tasks = BackgroundTasks()
    stuck_job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    mark_job_running(stuck_job)
    job_repository.create(stuck_job)

    generation_job_service.recover_interrupted_jobs()

    job = generation_job_service.start_generate_job(
        trip_id=created_trip_id, owner_id=owner_id, background_tasks=background_tasks
    )
    assert job.status == GenerationJobStatus.QUEUED


def test_lifespan_recovers_interrupted_jobs_on_real_app_startup(
    created_trip_id: str, client: TestClient
) -> None:
    """End-to-end proof of the actual wiring in app.main -- not just the
    standalone `recover_interrupted_jobs()` function -- using a real
    ASGI lifespan startup event via `with TestClient(...) as ...`."""
    owner_id = _owner_id(client)
    stuck_job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    mark_job_running(stuck_job)
    job_repository.create(stuck_job)

    from app.main import app as real_app

    with TestClient(real_app):
        pass  # lifespan startup already ran by the time __enter__ returns

    reloaded = job_repository.get_by_job_id(stuck_job.job_id)
    assert reloaded is not None
    assert reloaded.status == GenerationJobStatus.FAILED
    assert reloaded.error_code == "JOB_INTERRUPTED"


def test_get_job_reconciles_stale_job_before_returning_it(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = _owner_id(client)
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "60")
    get_settings.cache_clear()
    _make_stale_running_job(created_trip_id, owner_id, age_seconds=120)
    stale_job = job_repository.list_by_trip_id(created_trip_id)[0]

    fetched = generation_job_service.get_job(created_trip_id, stale_job.job_id)
    get_settings.cache_clear()

    assert fetched is not None
    assert fetched.status == GenerationJobStatus.FAILED
    assert fetched.error_code == "JOB_INTERRUPTED"


def test_list_jobs_reconciles_stale_job_before_returning_them(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = _owner_id(client)
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "60")
    get_settings.cache_clear()
    _make_stale_running_job(created_trip_id, owner_id, age_seconds=120)

    jobs = generation_job_service.list_jobs(created_trip_id)
    get_settings.cache_clear()

    assert len(jobs) == 1
    assert jobs[0].status == GenerationJobStatus.FAILED
    assert jobs[0].error_code == "JOB_INTERRUPTED"


# ---------------------------------------------------------------------------
# 5. Background failure hardening / safe error helpers
# ---------------------------------------------------------------------------


def test_safe_job_error_code_ignores_exception_content() -> None:
    exc = RuntimeError("some very specific internal detail sk-leaked-secret")
    assert generation_job_service.safe_job_error_code(exc) == "STAGE_FAILED"


def test_safe_job_error_message_never_returns_exception_text() -> None:
    exc = RuntimeError("sk-leaked-secret and a full traceback would go here")
    result = generation_job_service.safe_job_error_message(exc, fallback="A safe message.")
    assert result == "A safe message."
    assert "sk-leaked-secret" not in result


def test_run_regenerate_job_unexpected_exception_after_mutation_still_marks_failed(
    generated_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 186E hardening: previously, only `RegenerationMutationError`
    from `apply_regeneration_mutation` was caught -- an unexpected
    exception from anything *after* a successful mutation (e.g.
    `record_applied_attempt`) would have escaped uncaught and left the
    job `running` forever. The new top-level guard must catch it too."""
    owner_id = _owner_id(client)
    response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed, it's too much walking"},
    )
    assert response.status_code == 200

    from app.services.feedback_service import (
        derive_pending_affected_stages,
        pending_feedback_events,
    )
    from app.repositories.planning_state_repository import planning_state_repository
    from app.services.regeneration_attempt_service import regeneration_attempt_service

    planning_state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert planning_state is not None
    pending_events = pending_feedback_events(planning_state.feedback_history)
    affected_stages = derive_pending_affected_stages(planning_state.feedback_history)

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated post-mutation crash sk-should-not-leak")

    monkeypatch.setattr(regeneration_attempt_service, "record_applied_attempt", _raise)

    job = create_queued_job(
        trip_id=generated_trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE
    )
    job_repository.create(job)

    generation_job_service.run_regenerate_job(
        job.job_id,
        [stage.value for stage in affected_stages],
        [event.feedback_event_id for event in pending_events],
    )

    reloaded = job_repository.get_by_job_id(job.job_id)
    assert reloaded is not None
    assert reloaded.status == GenerationJobStatus.FAILED
    assert reloaded.finished_at is not None
    assert reloaded.error_code == "STAGE_FAILED"
    assert reloaded.error_message is not None
    assert "sk-should-not-leak" not in reloaded.error_message
    assert "RuntimeError" not in reloaded.error_message
    assert "Traceback" not in reloaded.error_message
