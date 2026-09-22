from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models.generation_job import (
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
    create_queued_job,
)
from app.models.targeted_regeneration_diff import TargetedRegenerationDiff
from app.models.targeted_regeneration_runtime import (
    TargetedRegenerationRuntimeResult,
    TargetedRegenerationRuntimeStatus,
)
from app.repositories.job_repository import job_repository
from app.schemas.errors import ErrorCode
from app.services import generation_job_service
from app.services.targeted_regeneration_application_service import (
    targeted_regeneration_application_service,
)

# Section 198B (Task 42): unit tests for `run_targeted_regenerate_job`,
# the background runner for `TARGETED_REGENERATION_ENABLED` +
# `ASYNC_GENERATION_ENABLED` async regeneration. Never exercised the
# actual `TargetedRegenerationApplicationService.regenerate` pipeline
# (that's the job of the 197A-197C test suites) -- these tests
# monkeypatch its `.regenerate` method with a canned
# `TargetedRegenerationRuntimeResult`, the same seam
# `run_targeted_regenerate_job` itself calls through, and assert on how
# the *job record* (never plan content) reflects each outcome. Every job
# is saved/reloaded through the real `job_repository` (the local-JSON
# backend), so a passing test also proves the new fields round-trip
# through real persistence, not just in-memory Python objects.


def _owner_id(client: TestClient) -> str:
    response = client.get("/auth/me")
    assert response.status_code == 200
    return response.json()["data"]["user"]["user_id"]


def _queued_job(trip_id: str, owner_id: str) -> GenerationJob:
    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE)
    job_repository.create(job)
    return job


def _canned_diff(trip_id: str) -> TargetedRegenerationDiff:
    return TargetedRegenerationDiff(
        trip_id=trip_id,
        source_version="v1",
        new_version="v2",
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        added_experience_ids=["exp_new"],
        removed_experience_ids=["exp_old"],
        validation_status_before="valid",
        validation_status_after="valid",
        warning_count_before=1,
        warning_count_after=0,
    )


def test_run_targeted_regenerate_job_success_carries_diff_and_status_fields(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario: async targeted completed result -- the SAME canonical
    diff/status fields the sync route returns must land on the reloaded
    job, not a second hand-derived shape (also covers "targeted diff
    preserved" and "affected/preserved days preserved")."""
    owner_id = _owner_id(client)
    diff = _canned_diff(created_trip_id)

    def _fake_regenerate(trip_id: str) -> TargetedRegenerationRuntimeResult:
        assert trip_id == created_trip_id
        return TargetedRegenerationRuntimeResult(
            trip_id=trip_id,
            status=TargetedRegenerationRuntimeStatus.COMPLETED,
            message="Targeted regeneration applied.",
            interpretation_status="grounded",
            execution_status="applied",
            source_version="v1",
            new_version="v2",
            diff=diff,
        )

    monkeypatch.setattr(targeted_regeneration_application_service, "regenerate", _fake_regenerate)

    job = _queued_job(created_trip_id, owner_id)
    generation_job_service.run_targeted_regenerate_job(job.job_id)

    reloaded = job_repository.get_by_job_id(job.job_id)
    assert reloaded is not None
    assert reloaded.status == GenerationJobStatus.SUCCEEDED
    assert reloaded.result_version == "v2"
    assert reloaded.previous_version == "v1"
    assert reloaded.targeted is True
    assert reloaded.interpretation_status == "grounded"
    assert reloaded.execution_status == "applied"
    assert reloaded.affected_day_indices == [2]
    assert reloaded.preserved_day_indices == [1, 3]
    assert reloaded.diff is not None
    assert reloaded.diff == diff
    assert reloaded.changed_sections == ["day_2"]


def test_run_targeted_regenerate_job_needs_clarification_marks_failed_not_succeeded(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario: a targeted error must never become a success -- a
    clarification outcome is a `failed` job carrying the clarification
    detail, never `succeeded` with an empty/partial diff."""
    owner_id = _owner_id(client)

    def _fake_regenerate(trip_id: str) -> TargetedRegenerationRuntimeResult:
        return TargetedRegenerationRuntimeResult(
            trip_id=trip_id,
            status=TargetedRegenerationRuntimeStatus.NEEDS_CLARIFICATION,
            message="Feedback is ambiguous.",
            interpretation_status="ambiguous",
            clarification_reason="multiple_candidate_experiences",
            clarification_possible_experience_ids=["exp_a", "exp_b"],
        )

    monkeypatch.setattr(targeted_regeneration_application_service, "regenerate", _fake_regenerate)

    job = _queued_job(created_trip_id, owner_id)
    generation_job_service.run_targeted_regenerate_job(job.job_id)

    reloaded = job_repository.get_by_job_id(job.job_id)
    assert reloaded is not None
    assert reloaded.status == GenerationJobStatus.FAILED
    assert reloaded.error_code == ErrorCode.REGENERATION_NEEDS_CLARIFICATION.value
    assert reloaded.targeted is True
    assert reloaded.interpretation_status == "ambiguous"
    assert reloaded.clarification_reason == "multiple_candidate_experiences"
    assert reloaded.clarification_possible_experience_ids == ["exp_a", "exp_b"]
    assert reloaded.result_version is None
    assert reloaded.diff is None


def test_run_targeted_regenerate_job_conflict_maps_to_conflict_error_code(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = _owner_id(client)

    def _fake_regenerate(trip_id: str) -> TargetedRegenerationRuntimeResult:
        return TargetedRegenerationRuntimeResult(
            trip_id=trip_id,
            status=TargetedRegenerationRuntimeStatus.CONFLICT,
            message="Plan changed since this feedback was read.",
        )

    monkeypatch.setattr(targeted_regeneration_application_service, "regenerate", _fake_regenerate)

    job = _queued_job(created_trip_id, owner_id)
    generation_job_service.run_targeted_regenerate_job(job.job_id)

    reloaded = job_repository.get_by_job_id(job.job_id)
    assert reloaded is not None
    assert reloaded.status == GenerationJobStatus.FAILED
    assert reloaded.error_code == ErrorCode.REGENERATION_CONFLICT.value
    assert reloaded.targeted is True


def test_run_targeted_regenerate_job_provider_unavailable_maps_to_provider_unavailable_error_code(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = _owner_id(client)

    def _fake_regenerate(trip_id: str) -> TargetedRegenerationRuntimeResult:
        return TargetedRegenerationRuntimeResult(
            trip_id=trip_id,
            status=TargetedRegenerationRuntimeStatus.PROVIDER_UNAVAILABLE,
            message="A required provider is not connected.",
        )

    monkeypatch.setattr(targeted_regeneration_application_service, "regenerate", _fake_regenerate)

    job = _queued_job(created_trip_id, owner_id)
    generation_job_service.run_targeted_regenerate_job(job.job_id)

    reloaded = job_repository.get_by_job_id(job.job_id)
    assert reloaded is not None
    assert reloaded.status == GenerationJobStatus.FAILED
    assert reloaded.error_code == ErrorCode.REGENERATION_PROVIDER_UNAVAILABLE.value


def test_run_targeted_regenerate_job_unexpected_exception_marks_failed_without_targeted_fields(
    created_trip_id: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The top-level catch-all for a truly unexpected exception
    deliberately does not set `targeted`/interpretation/execution/
    clarification fields -- there is no structured targeted result to
    report, so the job just fails generically (matching the same
    guarantee `run_regenerate_job` already makes)."""
    owner_id = _owner_id(client)

    def _raise(trip_id: str) -> TargetedRegenerationRuntimeResult:
        raise RuntimeError("simulated unexpected failure with secret-looking token sk-leak")

    monkeypatch.setattr(targeted_regeneration_application_service, "regenerate", _raise)

    job = _queued_job(created_trip_id, owner_id)
    generation_job_service.run_targeted_regenerate_job(job.job_id)

    reloaded = job_repository.get_by_job_id(job.job_id)
    assert reloaded is not None
    assert reloaded.status == GenerationJobStatus.FAILED
    assert "sk-leak" not in (reloaded.error_message or "")
    assert reloaded.targeted is False
    assert reloaded.diff is None


def test_run_targeted_regenerate_job_returns_quietly_for_unknown_job_id() -> None:
    generation_job_service.run_targeted_regenerate_job("job_does_not_exist")  # must not raise


def test_legacy_job_payload_without_new_fields_still_validates() -> None:
    """Scenario: an old row/JSON payload written before Section 198B has
    none of the new keys at all -- `GenerationJob`'s own field defaults
    must fill them in rather than raising, so a pre-existing persisted
    job (local-JSON or Postgres) keeps loading after this section ships."""
    legacy_payload = {
        "job_id": "job_legacy",
        "trip_id": "trip_legacy",
        "owner_id": "user_legacy",
        "job_type": "generate",
        "status": "succeeded",
        "created_at": "2026-01-01T00:00:00+00:00",
        "result_version": "v1",
        "changed_sections": ["traveler_profile"],
    }
    job = GenerationJob.model_validate(legacy_payload)
    assert job.targeted is False
    assert job.previous_version is None
    assert job.affected_day_indices == []
    assert job.preserved_day_indices == []
    assert job.diff is None
    assert job.clarification_possible_experience_ids == []
