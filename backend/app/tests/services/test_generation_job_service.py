from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.generation_job import (
    GenerationJobStatus,
    GenerationJobType,
    create_queued_job,
    mark_job_succeeded,
)
from app.models.planning_state import PlanningStage
from app.repositories.job_repository import job_repository
from app.repositories.planning_state_repository import planning_state_repository
from app.schemas.errors import ErrorCode
from app.services import generation_job_service
from app.services.feedback_service import derive_pending_affected_stages, pending_feedback_events
from app.services.planning_orchestrator import planning_orchestrator

# Unit tests for the async job orchestration service (Step 186C,
# docs/14_backend_architecture.md section 117), exercised directly
# against the service layer -- bypassing FastAPI/BackgroundTasks -- so
# each transition (queued -> running -> succeeded/failed) can be observed
# deterministically without relying on Starlette's background-task timing.


def _owner_id(client: TestClient) -> str:
    response = client.get("/auth/me")
    assert response.status_code == 200
    return response.json()["data"]["user"]["user_id"]


class _RaisingTravelerProfileService:
    def run(self, planning_state: Any) -> Any:
        raise RuntimeError("simulated stage failure with a secret-looking token sk-should-not-leak")


# ---------------------------------------------------------------------------
# check_no_duplicate_running_job
# ---------------------------------------------------------------------------


def test_check_no_duplicate_running_job_allows_when_nothing_running(
    created_trip_id: str,
) -> None:
    generation_job_service.check_no_duplicate_running_job(created_trip_id)  # must not raise


def test_check_no_duplicate_running_job_raises_when_queued_job_exists(
    created_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(job)

    with pytest.raises(AppError) as exc_info:
        generation_job_service.check_no_duplicate_running_job(created_trip_id)
    assert exc_info.value.code == ErrorCode.JOB_ALREADY_RUNNING
    assert exc_info.value.status_code == 409


def test_check_no_duplicate_running_job_ignores_terminal_jobs(
    created_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    mark_job_succeeded(job)
    job_repository.create(job)

    generation_job_service.check_no_duplicate_running_job(created_trip_id)  # must not raise


def test_check_no_duplicate_running_job_ignores_a_different_trip(
    created_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    other_trip_job = create_queued_job(
        trip_id="trip_completely_different", owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(other_trip_job)

    generation_job_service.check_no_duplicate_running_job(created_trip_id)  # must not raise


# ---------------------------------------------------------------------------
# run_generate_job
# ---------------------------------------------------------------------------


def test_run_generate_job_success_transitions_to_succeeded(
    created_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(job)
    assert job.status == GenerationJobStatus.QUEUED
    assert job.started_at is None

    generation_job_service.run_generate_job(job.job_id)

    reloaded = job_repository.get_by_job_id(job.job_id)
    assert reloaded is not None
    assert reloaded.status == GenerationJobStatus.SUCCEEDED
    assert reloaded.started_at is not None
    assert reloaded.finished_at is not None
    assert reloaded.result_version == "v1"
    assert reloaded.error_code is None
    assert reloaded.error_message is None

    planning_state = planning_state_repository.get_by_trip_id(created_trip_id)
    assert planning_state is not None
    assert planning_state.experience_plan is not None
    assert planning_state.generation_progress is not None
    assert planning_state.generation_progress.status.value == "completed"


def test_run_generate_job_failure_sets_failed_status_with_safe_message(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(job)

    # The default `planning_engine_mode="langgraph"` path captures its own
    # service references once, at `PlanningOrchestrator.__init__` time
    # (see `LangGraphPlanningService`/`build_planning_graph`'s closures) --
    # forcing "legacy" here is what makes `generate_full_plan`'s dynamic
    # `self.traveler_profile_service.run(...)` lookup actually honor this
    # monkeypatch, mirroring
    # test_generation_progress.py's own equivalent failure-injection test.
    monkeypatch.setenv("PLANNING_ENGINE_MODE", "legacy")
    get_settings.cache_clear()
    monkeypatch.setattr(
        planning_orchestrator, "traveler_profile_service", _RaisingTravelerProfileService()
    )

    generation_job_service.run_generate_job(job.job_id)
    get_settings.cache_clear()

    reloaded = job_repository.get_by_job_id(job.job_id)
    assert reloaded is not None
    assert reloaded.status == GenerationJobStatus.FAILED
    assert reloaded.finished_at is not None
    assert reloaded.error_code == "STAGE_FAILED"
    assert reloaded.error_message is not None
    # Never leaks the raw exception text (which deliberately contains a
    # secret-looking token above) or a stack trace.
    assert "sk-should-not-leak" not in reloaded.error_message
    assert "Traceback" not in reloaded.error_message
    assert "RuntimeError" not in reloaded.error_message

    planning_state = planning_state_repository.get_by_trip_id(created_trip_id)
    assert planning_state is not None
    assert planning_state.generation_progress is not None
    assert planning_state.generation_progress.status.value == "failed"


def test_run_generate_job_returns_quietly_for_unknown_job_id() -> None:
    generation_job_service.run_generate_job("job_does_not_exist")  # must not raise


# ---------------------------------------------------------------------------
# run_regenerate_job
# ---------------------------------------------------------------------------


def _submit_feedback_with_affected_stage(client: TestClient, trip_id: str) -> None:
    response = client.post(
        f"/trips/{trip_id}/feedback",
        json={"feedback_text": "Make this less packed, it's too much walking"},
    )
    assert response.status_code == 200


def test_run_regenerate_job_success_creates_version_and_marks_feedback_applied(
    generated_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    _submit_feedback_with_affected_stage(client, generated_trip_id)

    planning_state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert planning_state is not None
    pending_events = pending_feedback_events(planning_state.feedback_history)
    affected_stages = derive_pending_affected_stages(planning_state.feedback_history)
    assert affected_stages, "test fixture feedback must classify to a real stage"

    job = create_queued_job(
        trip_id=generated_trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE
    )
    job_repository.create(job)

    generation_job_service.run_regenerate_job(
        job.job_id,
        [stage.value for stage in affected_stages],
        [event.feedback_event_id for event in pending_events],
    )

    reloaded_job = job_repository.get_by_job_id(job.job_id)
    assert reloaded_job is not None
    assert reloaded_job.status == GenerationJobStatus.SUCCEEDED
    assert reloaded_job.result_version == "v2"
    assert reloaded_job.changed_sections

    reloaded_state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert reloaded_state is not None
    assert len(reloaded_state.version_history) == 2
    applied_ids = {event.feedback_event_id for event in pending_events}
    for event in reloaded_state.feedback_history:
        if event.feedback_event_id in applied_ids:
            assert event.applied_at is not None
            assert event.handling_status == "applied"


def test_run_regenerate_job_failure_does_not_create_version_or_mark_feedback_applied(
    generated_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = _owner_id(client)
    _submit_feedback_with_affected_stage(client, generated_trip_id)

    planning_state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert planning_state is not None
    pending_events = pending_feedback_events(planning_state.feedback_history)
    affected_stages = derive_pending_affected_stages(planning_state.feedback_history)
    version_count_before = len(planning_state.version_history)

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated rerun failure")

    monkeypatch.setattr(planning_orchestrator, "rerun_affected_stages", _raise)

    job = create_queued_job(
        trip_id=generated_trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE
    )
    job_repository.create(job)

    generation_job_service.run_regenerate_job(
        job.job_id,
        [stage.value for stage in affected_stages],
        [event.feedback_event_id for event in pending_events],
    )

    reloaded_job = job_repository.get_by_job_id(job.job_id)
    assert reloaded_job is not None
    assert reloaded_job.status == GenerationJobStatus.FAILED
    assert reloaded_job.error_code == ErrorCode.REGENERATION_NOT_AVAILABLE.value
    assert "simulated rerun failure" not in (reloaded_job.error_message or "")

    reloaded_state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert reloaded_state is not None
    assert len(reloaded_state.version_history) == version_count_before
    for event in reloaded_state.feedback_history:
        assert event.applied_at is None
    # A failed regeneration attempt is still recorded, matching the
    # existing synchronous refusal-audit contract.
    assert reloaded_state.regeneration_attempts
    assert reloaded_state.regeneration_attempts[-1].status == "failed"


def test_run_regenerate_job_fails_safely_when_feedback_no_longer_present(
    generated_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=generated_trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE
    )
    job_repository.create(job)

    generation_job_service.run_regenerate_job(
        job.job_id, [PlanningStage.TRIP_STRATEGY.value], ["feedback_does_not_exist"]
    )

    reloaded_job = job_repository.get_by_job_id(job.job_id)
    assert reloaded_job is not None
    assert reloaded_job.status == GenerationJobStatus.FAILED
    assert reloaded_job.error_code == ErrorCode.REGENERATION_NOT_AVAILABLE.value


def test_run_regenerate_job_returns_quietly_for_unknown_job_id() -> None:
    generation_job_service.run_regenerate_job("job_does_not_exist", [], [])  # must not raise


# ---------------------------------------------------------------------------
# get_job / list_jobs
# ---------------------------------------------------------------------------


def test_get_job_returns_none_for_a_different_trip(
    created_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(job)

    assert generation_job_service.get_job("some_other_trip_id", job.job_id) is None
    assert generation_job_service.get_job(created_trip_id, job.job_id) is job


def test_list_jobs_only_returns_jobs_for_the_given_trip(
    created_trip_id: str, client: TestClient
) -> None:
    owner_id = _owner_id(client)
    own_job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    other_job = create_queued_job(
        trip_id="trip_completely_different", owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(own_job)
    job_repository.create(other_job)

    jobs = generation_job_service.list_jobs(created_trip_id)

    assert [job.job_id for job in jobs] == [own_job.job_id]
