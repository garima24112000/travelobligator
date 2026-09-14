from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.generation_job import (
    JOB_INTERRUPTED_ERROR_CODE,
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
    create_queued_job,
    mark_job_cancelled,
    mark_job_failed,
    mark_job_interrupted,
    mark_job_running,
    mark_job_succeeded,
    new_job_id,
)
from app.models.planning_state import GENERATION_STAGE_KEYS

# Model tests for the async job foundation (Step 186B,
# docs/14_backend_architecture.md section 116). This is job *control*
# state only -- never a source of travel facts, and a `succeeded` job is
# never a claim the resulting plan is travel-ready/final/guaranteed. No
# route or background task reads/writes any of this yet.


def _job(**overrides: object) -> GenerationJob:
    defaults: dict[str, object] = {
        "trip_id": "trip_abc123",
        "owner_id": "user_abc123",
        "job_type": GenerationJobType.GENERATE,
    }
    defaults.update(overrides)
    return GenerationJob(**defaults)  # type: ignore[arg-type]


def test_new_job_id_has_stable_prefix() -> None:
    job_id = new_job_id()
    assert job_id.startswith("job_")
    assert job_id != new_job_id()


def test_generation_job_default_job_id_uses_new_job_id_prefix() -> None:
    job = _job()
    assert job.job_id.startswith("job_")


def test_generation_job_status_values_are_exactly_the_documented_five() -> None:
    assert {member.value for member in GenerationJobStatus} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_generation_job_type_values_are_exactly_the_documented_two() -> None:
    assert {member.value for member in GenerationJobType} == {"generate", "regenerate"}


def test_queued_job_defaults() -> None:
    job = _job()

    assert job.status == GenerationJobStatus.QUEUED
    assert job.progress_stage is None
    assert job.message is None
    assert job.error_code is None
    assert job.error_message is None
    assert job.started_at is None
    assert job.finished_at is None
    assert job.result_version is None
    assert job.changed_sections == []
    assert job.created_at is not None


def test_create_queued_job_helper() -> None:
    job = create_queued_job(
        trip_id="trip_abc123", owner_id="user_abc123", job_type=GenerationJobType.REGENERATE
    )

    assert job.status == GenerationJobStatus.QUEUED
    assert job.trip_id == "trip_abc123"
    assert job.owner_id == "user_abc123"
    assert job.job_type == GenerationJobType.REGENERATE


def test_mark_job_running_sets_started_at_and_status() -> None:
    job = _job()
    assert job.started_at is None

    updated = mark_job_running(job, progress_stage="traveler_profile")

    assert updated is job
    assert updated.status == GenerationJobStatus.RUNNING
    assert updated.started_at is not None
    assert updated.progress_stage == "traveler_profile"
    assert updated.message is not None


def test_mark_job_running_does_not_overwrite_an_existing_started_at() -> None:
    job = _job()
    mark_job_running(job)
    first_started_at = job.started_at

    mark_job_running(job, progress_stage="destination_context")

    assert job.started_at == first_started_at
    assert job.progress_stage == "destination_context"


def test_mark_job_succeeded_sets_finished_at_and_result_fields() -> None:
    job = _job(job_type=GenerationJobType.REGENERATE)
    mark_job_running(job)

    updated = mark_job_succeeded(
        job, result_version="v2", changed_sections=["trip_strategy", "experience_plan"]
    )

    assert updated.status == GenerationJobStatus.SUCCEEDED
    assert updated.finished_at is not None
    assert updated.result_version == "v2"
    assert updated.changed_sections == ["trip_strategy", "experience_plan"]
    assert updated.error_code is None
    assert updated.error_message is None


def test_mark_job_succeeded_defaults_result_fields_when_not_provided() -> None:
    job = _job()
    mark_job_running(job)

    updated = mark_job_succeeded(job)

    assert updated.result_version is None
    assert updated.changed_sections == []


def test_mark_job_failed_sets_status_and_error_fields_without_stack_trace() -> None:
    job = _job()
    mark_job_running(job, progress_stage="stay_transport")

    updated = mark_job_failed(
        job, error_code="STAGE_FAILED", error_message="The stay/transport stage failed."
    )

    assert updated.status == GenerationJobStatus.FAILED
    assert updated.finished_at is not None
    assert updated.error_code == "STAGE_FAILED"
    assert updated.error_message == "The stay/transport stage failed."
    # current_stage-equivalent is left as-is, mirroring
    # PlanningOrchestrator._fail_generation_progress's own "leave
    # current_stage pointed at whatever was running" behavior.
    assert updated.progress_stage == "stay_transport"
    assert "Traceback" not in updated.error_message
    assert "File \"" not in updated.error_message


def test_mark_job_cancelled_sets_status_without_touching_error_fields() -> None:
    job = _job()
    mark_job_running(job)

    updated = mark_job_cancelled(job)

    assert updated.status == GenerationJobStatus.CANCELLED
    assert updated.finished_at is not None
    assert updated.error_code is None
    assert updated.error_message is None


def test_mark_job_cancelled_accepts_a_custom_message() -> None:
    job = _job()
    updated = mark_job_cancelled(job, message="Cancelled by a duplicate-job guard.")

    assert updated.message == "Cancelled by a duplicate-job guard."


# ---------------------------------------------------------------------------
# mark_job_interrupted (Step 186E, docs/14_backend_architecture.md
# section 118)
# ---------------------------------------------------------------------------


def test_mark_job_interrupted_sets_failed_status_and_controlled_error_code() -> None:
    job = _job()
    mark_job_running(job)

    updated = mark_job_interrupted(job)

    assert updated.status == GenerationJobStatus.FAILED
    assert updated.finished_at is not None
    assert updated.error_code == JOB_INTERRUPTED_ERROR_CODE
    assert updated.error_message is not None


def test_mark_job_interrupted_default_message_never_claims_success_or_resumption() -> None:
    job = _job()
    updated = mark_job_interrupted(job)

    message = (updated.error_message or "").lower()
    assert "interrupted" in message
    assert "resume" not in message
    assert "succeeded" not in message
    assert "Traceback" not in (updated.error_message or "")


def test_mark_job_interrupted_accepts_a_custom_message() -> None:
    job = _job()
    updated = mark_job_interrupted(job, message="Custom interrupted message.")

    assert updated.error_message == "Custom interrupted message."
    assert updated.error_code == JOB_INTERRUPTED_ERROR_CODE


def test_mark_job_interrupted_is_indistinguishable_in_shape_from_mark_job_failed() -> None:
    """`mark_job_interrupted` must reuse `mark_job_failed`'s exact
    contract (status/finished_at) -- only `error_code` distinguishes an
    interrupted job from any other failure reason."""
    interrupted_job = mark_job_interrupted(_job())
    failed_job = mark_job_failed(_job(), error_code="STAGE_FAILED", error_message="x")

    assert interrupted_job.status == failed_job.status == GenerationJobStatus.FAILED
    assert interrupted_job.finished_at is not None
    assert failed_job.finished_at is not None


def test_progress_stage_accepts_every_real_generation_stage_key() -> None:
    for stage_key in GENERATION_STAGE_KEYS:
        job = _job(progress_stage=stage_key)
        assert job.progress_stage == stage_key


def test_progress_stage_rejects_a_fabricated_cosmetic_stage_name() -> None:
    with pytest.raises(ValidationError):
        _job(progress_stage="booking_your_flights")


def test_generation_job_serializes_and_deserializes() -> None:
    job = _job(job_type=GenerationJobType.REGENERATE)
    mark_job_running(job, progress_stage="experience_plan")
    mark_job_succeeded(job, result_version="v2", changed_sections=["experience_plan"])

    dumped = job.model_dump(mode="json")
    reloaded = GenerationJob.model_validate(dumped)

    assert reloaded == job


_FORBIDDEN_SECRET_FIELD_NAMES = {
    "password",
    "password_hash",
    "session_token",
    "session_cookie",
    "api_key",
    "secret",
    "secret_key",
    "access_token",
    "refresh_token",
}


def test_generation_job_has_no_secret_or_credential_fields() -> None:
    field_names = set(GenerationJob.model_fields.keys())
    overlap = field_names & _FORBIDDEN_SECRET_FIELD_NAMES
    assert overlap == set(), f"GenerationJob has forbidden field(s): {overlap}"


def test_generation_job_dump_never_contains_a_percent_field() -> None:
    """No fabricated/derived percentage field exists on this model --
    percent-complete stays owned entirely by
    `PlanningState.generation_progress` (Step 163B), which already derives
    it deterministically from real stage counts. Adding a second,
    independently-drifting percent field here would risk disagreeing with
    that one."""
    job = _job()
    dumped = job.model_dump(mode="json")
    assert "progress_percent" not in dumped
    assert "percent" not in dumped
    assert "eta" not in dumped
    assert "estimated_time_remaining" not in dumped
