from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models.generation_job import GenerationJobStatus, GenerationJobType, create_queued_job
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.repositories.factory import get_job_repository
from app.services import generation_job_service as svc

# Section 200E: what graceful shutdown does to a job THIS process owns.


def _state(trip_id: str) -> PlanningState:
    return PlanningState(
        trip_id=trip_id,
        trip_request=TripRequest(primary_destination="X", start_date="2026-10-10", end_date="2026-10-11", travelers_count=1, travel_group_type=TravelGroupType.SOLO),
    )


def test_a_job_interrupted_by_shutdown_is_never_reported_succeeded(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = get_job_repository()
    job = create_queued_job(trip_id="trip_sd1", owner_id="u", job_type=GenerationJobType.GENERATE)
    repo.create(job)
    closed: list[int] = []

    def pipeline(trip_id: str, *a: Any, **k: Any) -> PlanningState:
        closed.append(svc.interrupt_local_jobs())  # SIGTERM arrives while the job is running
        return _state(trip_id)  # ... and the worker thread still "finishes" afterwards

    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan", pipeline)
    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan_via_langgraph", pipeline)

    svc.run_generate_job(job.job_id, lease_owner="inst-a")

    assert closed == [1]
    final = repo.get_by_job_id(job.job_id)
    assert final.status == GenerationJobStatus.FAILED and final.error_code == "JOB_INTERRUPTED"
    assert final.lease_owner is None and final.result_version is None  # not a false success


def test_shutdown_only_touches_jobs_this_process_owns(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = get_job_repository()
    other = create_queued_job(trip_id="trip_sd2", owner_id="u", job_type=GenerationJobType.GENERATE)
    repo.create(other)
    repo.claim(other.job_id, "inst-other", 120)  # owned by ANOTHER instance
    assert svc.interrupt_local_jobs() == 0
    assert repo.get_by_job_id(other.job_id).status == GenerationJobStatus.RUNNING

    # even a stale local registration cannot overwrite a job someone else took over
    with svc._LOCAL_JOBS_LOCK:
        svc._LOCAL_JOBS[other.job_id] = "inst-mine"
    try:
        assert svc.interrupt_local_jobs() == 0
    finally:
        with svc._LOCAL_JOBS_LOCK:
            svc._LOCAL_JOBS.pop(other.job_id, None)
    assert repo.get_by_job_id(other.job_id).lease_owner == "inst-other"


def test_shutdown_helpers_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    with svc._LOCAL_JOBS_LOCK:
        svc._LOCAL_JOBS["job_missing"] = "inst-x"
    try:
        assert svc.interrupt_local_jobs() == 0  # unknown job: refused quietly
    finally:
        with svc._LOCAL_JOBS_LOCK:
            svc._LOCAL_JOBS.clear()
    monkeypatch.setattr(svc, "get_job_repository", lambda: (_ for _ in ()).throw(RuntimeError("db gone")))
    with svc._LOCAL_JOBS_LOCK:
        svc._LOCAL_JOBS["job_x"] = "inst-x"
    try:
        assert svc.interrupt_local_jobs() == 0
    finally:
        with svc._LOCAL_JOBS_LOCK:
            svc._LOCAL_JOBS.clear()


def test_the_lifespan_shutdown_runs_the_steps_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    calls: list[str] = []
    monkeypatch.setattr(svc, "stop_all_heartbeats", lambda timeout=1.0: calls.append("heartbeats") or 0)
    monkeypatch.setattr(svc, "interrupt_local_jobs", lambda: calls.append("jobs") or 0)
    monkeypatch.setattr("app.core.provider_cache.shutdown_provider_cache", lambda: calls.append("cache"))
    with TestClient(app):
        pass
    assert calls == ["heartbeats", "jobs", "cache"]  # heartbeats stop first, then jobs close, then clients
