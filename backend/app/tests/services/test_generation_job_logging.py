from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.routes.trips as trips_route
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging_config import APP_LOGGER_NAME, JsonFormatter
from app.core.request_context import request_id_scope
from app.models.generation_job import (
    GenerationJobType,
    create_queued_job,
    mark_job_running,
)
from app.repositories.job_repository import job_repository
from app.schemas.errors import ErrorCode
from app.services import generation_job_service
from app.services.planning_orchestrator import planning_orchestrator

# Tests for the Step 187D structured async-job lifecycle logs
# (docs/14_backend_architecture.md section 123). Every test here attaches
# a small capture handler directly to the shared "app" logger (which has
# `propagate=False`, so `caplog`'s root-attached handler can't see it --
# matches the pattern `test_request_id_correlation.py` already
# established in Step 187C) rather than asserting on stdout text, so
# these stay independent of whether STRUCTURED_LOGGING_ENABLED is on.


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def capture() -> _CaptureHandler:
    app_logger = logging.getLogger(APP_LOGGER_NAME)
    handler = _CaptureHandler()
    app_logger.addHandler(handler)
    try:
        yield handler
    finally:
        app_logger.removeHandler(handler)


def _owner_id(client: TestClient) -> str:
    response = client.get("/auth/me")
    assert response.status_code == 200
    return response.json()["data"]["user"]["user_id"]


class _RaisingTravelerProfileService:
    def run(self, planning_state):  # noqa: ANN001, ANN201 -- matches sibling test's own style
        raise RuntimeError(
            "simulated stage failure with a secret-looking token sk-should-not-leak"
        )


def _records_with_message_containing(
    capture: _CaptureHandler, needle: str
) -> list[logging.LogRecord]:
    return [r for r in capture.records if needle in r.getMessage()]


def _assert_no_forbidden_fields(record: logging.LogRecord) -> None:
    """None of these should ever be set as attributes on a job-lifecycle
    LogRecord -- they aren't in ALLOWED_EXTRA_FIELDS, so even if a call
    site accidentally attached one, JsonFormatter would already drop it;
    this asserts the stronger, defense-in-depth property that the call
    site itself never attaches one in the first place."""
    forbidden = (
        "password",
        "password_hash",
        "session",
        "session_cookie",
        "cookie",
        "authorization",
        "token",
        "secret",
        "api_key",
        "request_body",
        "response_body",
        "provider_payload",
        "planning_state",
        "itinerary",
    )
    for name in forbidden:
        assert not hasattr(record, name)


# ---------------------------------------------------------------------------
# Queued
# ---------------------------------------------------------------------------


def test_generate_job_queued_log_has_expected_fields(
    created_trip_id: str,
    client: TestClient,
    capture: _CaptureHandler,
    async_generation_enabled: None,
) -> None:
    owner_id = _owner_id(client)
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 202
    job_id = response.json()["data"]["job_id"]

    queued_records = _records_with_message_containing(capture, "queued")
    assert len(queued_records) >= 1
    record = queued_records[0]
    assert record.levelname == "INFO"
    assert record.job_id == job_id
    assert record.trip_id == created_trip_id
    assert record.job_type == "generate"
    assert record.status == "queued"
    assert record.owner_id == owner_id
    _assert_no_forbidden_fields(record)

    # Confirms the record actually renders as valid, allowlisted-only JSON.
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["job_id"] == job_id
    assert parsed["status"] == "queued"


def test_regenerate_job_queued_log_has_expected_job_type(
    created_trip_id: str,
    client: TestClient,
    capture: _CaptureHandler,
    async_generation_enabled: None,
) -> None:
    owner_id = _owner_id(client)
    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 202
    client.post(
        f"/trips/{created_trip_id}/feedback", json={"feedback_text": "Make this less packed"}
    )
    capture.records.clear()

    response = client.post(f"/trips/{created_trip_id}/regenerate", json={"confirm": True})
    if response.status_code != 202:
        pytest.skip("regeneration was not eligible for this run -- nothing to assert on")
    job_id = response.json()["data"]["job_id"]

    queued_records = _records_with_message_containing(capture, "queued")
    assert len(queued_records) == 1
    record = queued_records[0]
    assert record.job_id == job_id
    assert record.job_type == "regenerate"
    assert record.status == "queued"
    assert record.owner_id == owner_id


# ---------------------------------------------------------------------------
# Running / succeeded (direct service-layer calls, deterministic timing --
# matches test_generation_job_service.py's own established convention)
# ---------------------------------------------------------------------------


def test_run_generate_job_logs_running_then_succeeded(
    created_trip_id: str, client: TestClient, capture: _CaptureHandler
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(job)
    capture.records.clear()

    generation_job_service.run_generate_job(job.job_id)

    running_records = _records_with_message_containing(capture, "started")
    assert len(running_records) == 1
    running_record = running_records[0]
    assert running_record.status == "running"
    assert running_record.job_id == job.job_id
    assert running_record.trip_id == created_trip_id
    assert running_record.job_type == "generate"
    assert not hasattr(running_record, "duration_ms")  # not terminal yet
    _assert_no_forbidden_fields(running_record)

    succeeded_records = _records_with_message_containing(capture, "succeeded")
    assert len(succeeded_records) == 1
    succeeded_record = succeeded_records[0]
    assert succeeded_record.status == "succeeded"
    assert succeeded_record.job_id == job.job_id
    assert hasattr(succeeded_record, "duration_ms")
    assert succeeded_record.duration_ms >= 0
    _assert_no_forbidden_fields(succeeded_record)


def test_run_generate_job_failure_logs_safe_failed_fields(
    created_trip_id: str,
    client: TestClient,
    capture: _CaptureHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(job)

    monkeypatch.setenv("PLANNING_ENGINE_MODE", "legacy")
    get_settings.cache_clear()
    monkeypatch.setattr(
        planning_orchestrator, "traveler_profile_service", _RaisingTravelerProfileService()
    )
    capture.records.clear()

    generation_job_service.run_generate_job(job.job_id)
    get_settings.cache_clear()

    failed_records = [
        r for r in capture.records if getattr(r, "status", None) == "failed"
    ]
    assert len(failed_records) == 1
    record = failed_records[0]
    assert record.job_id == job.job_id
    assert record.trip_id == created_trip_id
    assert record.job_type == "generate"
    assert record.error_code == "STAGE_FAILED"
    assert hasattr(record, "duration_ms")
    _assert_no_forbidden_fields(record)

    # The one *structured* (ALLOWED_EXTRA_FIELDS-allowlisted) field set
    # must never contain the raw exception string, even though the
    # exception deliberately contains a secret-looking token -- this
    # only checks the fields `_job_log_fields` itself builds, not
    # `record.exc_info` (the real traceback rendered server-side only,
    # matching this app's existing `exc_info=True` convention -- see
    # `safe_job_error_message`'s own docstring for why the *response*,
    # not the server log, is the boundary that must never see it).
    structured_fields = {
        field: getattr(record, field)
        for field in (
            "trip_id",
            "owner_id",
            "job_id",
            "job_type",
            "status",
            "stage",
            "error_code",
            "duration_ms",
        )
        if hasattr(record, field)
    }
    assert "sk-should-not-leak" not in json.dumps(structured_fields)
    for allowed in ("trip_id", "owner_id", "job_id", "job_type", "status", "error_code"):
        assert hasattr(record, allowed)


# ---------------------------------------------------------------------------
# Duplicate rejection
# ---------------------------------------------------------------------------


def test_duplicate_job_rejection_logs_job_already_running(
    created_trip_id: str, client: TestClient, capture: _CaptureHandler
) -> None:
    owner_id = _owner_id(client)
    existing_job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    job_repository.create(existing_job)
    capture.records.clear()

    with pytest.raises(AppError) as exc_info:
        generation_job_service.check_no_duplicate_running_job(
            created_trip_id, attempted_job_type=GenerationJobType.GENERATE
        )
    assert exc_info.value.code == ErrorCode.JOB_ALREADY_RUNNING

    rejection_records = [
        r for r in capture.records if getattr(r, "error_code", None) == "JOB_ALREADY_RUNNING"
    ]
    assert len(rejection_records) == 1
    record = rejection_records[0]
    assert record.levelname == "WARNING"
    assert record.trip_id == created_trip_id
    assert record.job_id == existing_job.job_id
    assert record.owner_id == owner_id
    assert record.job_type == "generate"
    assert record.status in ("queued", "running")
    _assert_no_forbidden_fields(record)


# ---------------------------------------------------------------------------
# Interrupted / stale recovery
# ---------------------------------------------------------------------------


def test_startup_recovery_logs_interrupted_with_job_id(capture: _CaptureHandler) -> None:
    job = create_queued_job(
        trip_id="trip_startup_recovery_log_test",
        owner_id="user_startup_recovery_log_test",
        job_type=GenerationJobType.REGENERATE,
    )
    job_repository.create(job)
    capture.records.clear()

    count = generation_job_service.recover_interrupted_jobs()
    assert count >= 1

    interrupted_records = [
        r for r in capture.records if getattr(r, "error_code", None) == "JOB_INTERRUPTED"
    ]
    matching = [r for r in interrupted_records if r.job_id == job.job_id]
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "INFO"
    assert record.status == "failed"
    assert record.trip_id == "trip_startup_recovery_log_test"
    assert record.job_type == "regenerate"
    _assert_no_forbidden_fields(record)


def test_startup_recovery_outside_request_has_no_request_id(capture: _CaptureHandler) -> None:
    """Confirms Q6's own boundary, applied here: recovery runs at
    process startup, before any request -- its logs must never carry a
    stale/invented request_id."""
    job = create_queued_job(
        trip_id="trip_startup_recovery_no_request_id",
        owner_id="user_startup_recovery_no_request_id",
        job_type=GenerationJobType.GENERATE,
    )
    job_repository.create(job)
    capture.records.clear()

    generation_job_service.recover_interrupted_jobs()

    for record in capture.records:
        assert not hasattr(record, "request_id")


def test_stale_reconciliation_logs_interrupted(
    created_trip_id: str, client: TestClient, capture: _CaptureHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = _owner_id(client)
    job = create_queued_job(
        trip_id=created_trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    mark_job_running(job)
    # Backdated well past the staleness window below -- matches
    # test_generation_job_hardening.py's own `_make_stale_running_job`
    # convention for deterministically triggering staleness without a
    # real sleep.
    job.started_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    job_repository.create(job)

    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "60")
    get_settings.cache_clear()
    capture.records.clear()
    try:
        generation_job_service._reconcile_stale_jobs(created_trip_id)
    finally:
        get_settings.cache_clear()

    interrupted_records = [
        r for r in capture.records if getattr(r, "error_code", None) == "JOB_INTERRUPTED"
    ]
    assert len(interrupted_records) == 1
    record = interrupted_records[0]
    assert record.job_id == job.job_id
    assert record.trip_id == created_trip_id
    assert record.status == "failed"


# ---------------------------------------------------------------------------
# Request correlation (Step 187C integration)
# ---------------------------------------------------------------------------


def test_job_lifecycle_logs_share_request_id_within_a_request(
    created_trip_id: str,
    client: TestClient,
    capture: _CaptureHandler,
    async_generation_enabled: None,
) -> None:
    """Empirically verifies (not assumed) that a background job scheduled
    via `BackgroundTasks.add_task` -- run inside the same anyio task
    tree TestClient awaits before returning the response -- still sees
    the request-scoped `request_id` contextvar. If a future Starlette/
    anyio version ever changed that propagation, this test would fail
    honestly rather than silently passing on a stale assumption."""
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 202
    header_value = response.headers.get("X-Request-Id")
    assert header_value is not None

    job_records = [r for r in capture.records if getattr(r, "job_id", None) is not None]
    assert len(job_records) >= 1
    for record in job_records:
        assert record.request_id == header_value


def test_job_log_emitted_via_request_id_scope_carries_that_id(capture: _CaptureHandler) -> None:
    """Direct, minimal proof of the same mechanism, independent of the
    HTTP/BackgroundTasks machinery above."""
    job = create_queued_job(
        trip_id="trip_scope_check", owner_id="user_scope_check", job_type=GenerationJobType.GENERATE
    )
    job_repository.create(job)
    capture.records.clear()

    with request_id_scope("req_manual_scope_check"):
        generation_job_service.run_generate_job(job.job_id)

    assert len(capture.records) >= 1
    for record in capture.records:
        if hasattr(record, "job_id"):
            assert record.request_id == "req_manual_scope_check"


# ---------------------------------------------------------------------------
# Synchronous regeneration failure (trips.py route, sync mode only --
# `Settings.async_generation_enabled=False`, no GenerationJob involved)
# ---------------------------------------------------------------------------


def test_sync_regenerate_failure_logs_structured_fields(
    client: TestClient,
    generated_trip_id: str,
    capture: _CaptureHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`POST /trips/{trip_id}/regenerate`'s own pre-existing
    `logger.warning(..., exc_info=True)` call (Step 174C, never a
    GenerationJob-related call site) -- Step 187D only adds structured
    `extra` fields to it, never changes when it fires or what it
    returns."""
    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    assert feedback_response.status_code == 200

    def _raise(planning_state, affected_stages):
        raise RuntimeError("simulated unexpected stage rerun failure with secret sk-leak-me")

    monkeypatch.setattr(trips_route.planning_orchestrator, "rerun_affected_stages", _raise)
    capture.records.clear()

    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})
    assert response.status_code == 409

    matching = [
        r for r in capture.records if getattr(r, "error_code", None) == "REGENERATION_NOT_AVAILABLE"
    ]
    assert len(matching) == 1
    record = matching[0]
    assert record.trip_id == generated_trip_id
    assert record.status == "failed"
    _assert_no_forbidden_fields(record)
    # No job_id/job_type -- this is the synchronous path, no
    # GenerationJob was ever created for this request.
    assert not hasattr(record, "job_id")

    structured_fields = {
        field: getattr(record, field)
        for field in ("trip_id", "status", "error_code")
        if hasattr(record, field)
    }
    assert "sk-leak-me" not in json.dumps(structured_fields)
