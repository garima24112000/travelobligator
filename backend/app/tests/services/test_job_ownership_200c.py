from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.core.errors import AppError
from app.models.generation_job import GenerationJobStatus, GenerationJobType, create_queued_job
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.repositories.errors import ConcurrentStateUpdateError, JobAlreadyActiveError
from app.repositories.factory import get_job_repository
from app.schemas.errors import ErrorCode
from app.services import generation_job_service as svc

# Section 200C: multi-instance job ownership at the SERVICE level -- "process A" and "process B"
# are two lease owners over one shared job repository. The database-level equivalents (real
# concurrent transactions) live in the gated PostgreSQL suite.


def _state(trip_id: str = "trip_own") -> PlanningState:
    return PlanningState(
        trip_id=trip_id,
        trip_request=TripRequest(
            primary_destination="Lisbon, Portugal",
            start_date="2026-10-10",
            end_date="2026-10-11",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        ),
    )


def _queued(trip_id: str = "trip_own"):
    job = create_queued_job(trip_id=trip_id, owner_id="user_1", job_type=GenerationJobType.GENERATE)
    get_job_repository().create(job)
    return job


@pytest.fixture()
def pipeline(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def generate(trip_id: str, *a: Any, **k: Any) -> PlanningState:
        calls.append(trip_id)
        return _state(trip_id)

    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan", generate)
    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan_via_langgraph", generate)
    return calls


def test_a_completed_job_replayed_does_not_run_the_pipeline_or_change_its_result(pipeline: list[str]) -> None:
    job = _queued()
    svc.run_generate_job(job.job_id, lease_owner="inst-a")
    first = get_job_repository().get_by_job_id(job.job_id)
    assert first.status == GenerationJobStatus.SUCCEEDED and pipeline == ["trip_own"]

    svc.run_generate_job(job.job_id, lease_owner="inst-a")  # duplicate delivery of the same task
    svc.run_generate_job(job.job_id, lease_owner="inst-b")

    assert pipeline == ["trip_own"]  # no second run, hence no second version/revision
    assert get_job_repository().get_by_job_id(job.job_id).finished_at == first.finished_at


def test_process_b_does_not_steal_or_run_a_job_owned_by_a_live_process_a(pipeline: list[str]) -> None:
    job = _queued()
    repo = get_job_repository()
    repo.claim(job.job_id, "inst-a", 120)  # process A is running it, lease valid

    svc.run_generate_job(job.job_id, lease_owner="inst-b")

    assert pipeline == []
    assert repo.get_by_job_id(job.job_id).lease_owner == "inst-a"


def test_after_the_lease_expires_process_b_takes_over_and_completes_the_job(pipeline: list[str]) -> None:
    job = _queued()
    repo = get_job_repository()
    repo.claim(job.job_id, "inst-a", 120, now=datetime.now(timezone.utc) - timedelta(minutes=10))  # A died

    svc.run_generate_job(job.job_id, lease_owner="inst-b")

    finished = repo.get_by_job_id(job.job_id)
    assert pipeline == ["trip_own"] and finished.status == GenerationJobStatus.SUCCEEDED
    assert finished.lease_owner is None


def test_a_stale_owner_cannot_complete_a_job_after_another_instance_took_it_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _queued()
    repo = get_job_repository()

    def slow_generate(trip_id: str, *a: Any, **k: Any) -> PlanningState:
        # While instance A is "working", its lease expires and instance B legitimately claims the job.
        repo.claim(job.job_id, "inst-b", 120, now=datetime.now(timezone.utc) + timedelta(hours=1))
        return _state(trip_id)

    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan", slow_generate)
    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan_via_langgraph", slow_generate)
    svc.run_generate_job(job.job_id, lease_owner="inst-a")

    after = repo.get_by_job_id(job.job_id)
    assert after.status == GenerationJobStatus.RUNNING and after.lease_owner == "inst-b"  # A's result was refused


def test_a_concurrent_update_during_generation_fails_the_job_with_a_safe_conflict_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _queued()

    def stale(trip_id: str, *a: Any, **k: Any) -> PlanningState:
        raise ConcurrentStateUpdateError(trip_id)

    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan", stale)
    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan_via_langgraph", stale)
    svc.run_generate_job(job.job_id, lease_owner="inst-a")

    failed = get_job_repository().get_by_job_id(job.job_id)
    assert failed.status == GenerationJobStatus.FAILED
    assert failed.error_code == ErrorCode.CONCURRENT_UPDATE.value
    assert "SELECT" not in failed.error_message and "lock_version" not in failed.error_message


def test_an_unknown_or_missing_job_is_a_quiet_no_op(pipeline: list[str]) -> None:
    svc.run_generate_job("job_does_not_exist", lease_owner="inst-a")
    assert pipeline == []


def test_lease_based_startup_recovery_leaves_a_healthy_job_of_another_instance_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "_uses_database_leases", lambda: True)  # the PostgreSQL recovery policy
    repo = get_job_repository()
    healthy = _queued("trip_live")
    repo.claim(healthy.job_id, "inst-live", 120)  # owned by ANOTHER live instance
    dead = _queued("trip_dead")
    repo.claim(dead.job_id, "inst-dead", 120, now=datetime.now(timezone.utc) - timedelta(hours=1))

    recovered = svc.recover_interrupted_jobs()  # "process B starting up"

    assert recovered == 1
    assert repo.get_by_job_id(healthy.job_id).status == GenerationJobStatus.RUNNING
    closed = repo.get_by_job_id(dead.job_id)
    assert closed.status == GenerationJobStatus.FAILED and closed.error_code == "JOB_INTERRUPTED"


def test_single_process_startup_recovery_is_unchanged_for_local_json(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = get_job_repository()
    job = _queued("trip_local")
    repo.claim(job.job_id, "inst-x", 120)
    assert svc.recover_interrupted_jobs() == 1  # Local JSON: every non-terminal job is a leftover
    assert repo.get_by_job_id(job.job_id).status == GenerationJobStatus.FAILED


def test_reconciling_a_trip_uses_leases_not_job_age_in_postgres_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_uses_database_leases", lambda: True)
    repo = get_job_repository()
    job = _queued("trip_long")
    job.created_at = datetime.now(timezone.utc) - timedelta(days=3)  # ancient...
    repo.claim(job.job_id, "inst-live", 120)  # ...but its owner is alive and heartbeating
    svc._reconcile_stale_jobs("trip_long")
    assert repo.get_by_job_id(job.job_id).status == GenerationJobStatus.RUNNING


def test_database_uniqueness_violation_becomes_the_friendly_409(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = get_job_repository()
    monkeypatch.setattr(repo, "create", lambda job: (_ for _ in ()).throw(JobAlreadyActiveError(job.trip_id)))
    job = create_queued_job(trip_id="trip_race", owner_id="u", job_type=GenerationJobType.GENERATE)
    with pytest.raises(AppError) as excinfo:
        svc._create_job_or_conflict(job)
    assert excinfo.value.code == ErrorCode.JOB_ALREADY_RUNNING and excinfo.value.status_code == 409


def test_lease_owner_is_an_opaque_process_id_without_host_or_credentials() -> None:
    import socket

    owner = svc.current_lease_owner()
    assert owner.startswith("inst-") and socket.gethostname().lower() not in owner.lower() or len(socket.gethostname()) < 3
    assert "@" not in owner and "://" not in owner


def test_heartbeat_thread_extends_the_lease_and_reports_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_uses_database_leases", lambda: True)
    monkeypatch.setattr(svc, "_heartbeat_interval_seconds", lambda lease: 0.01)
    beats: list[str] = []

    class Repo:
        def heartbeat(self, job_id: str, owner: str, lease_seconds: int) -> bool:
            beats.append(owner)
            return len(beats) < 3  # ownership is lost on the third beat

    heartbeat = svc._LeaseHeartbeat(Repo(), "job_1", "inst-a", 30)
    with heartbeat:
        import time

        deadline = time.time() + 3
        while not heartbeat.lost and time.time() < deadline:
            time.sleep(0.01)
    assert heartbeat.lost is True and beats == ["inst-a"] * 3
