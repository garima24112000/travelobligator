from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.generation_job import (
    GenerationJobStatus,
    GenerationJobType,
    create_queued_job,
    mark_job_failed,
    mark_job_succeeded,
)
from app.repositories.errors import UNSET
from app.repositories.job_repository import JobRepository
from app.storage.local_json_store import LocalJsonStore

# Section 200C: the job LIFECYCLE contract (claim / heartbeat / conditional transitions /
# lease-based recovery). `JobRepository` (Local JSON) implements the exact contract
# `PostgresJobRepository` implements with conditional SQL; the same scenarios run against real
# PostgreSQL in the gated suite. Time is passed explicitly, so nothing sleeps.

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def repo(tmp_path: Path) -> JobRepository:
    return JobRepository(store=LocalJsonStore(tmp_path / "state.json"))


def _queued(repo: JobRepository, trip_id: str = "trip_1"):
    job = create_queued_job(trip_id=trip_id, owner_id="user_1", job_type=GenerationJobType.GENERATE)
    job.created_at = T0
    repo.create(job)
    return job


def test_one_worker_claims_a_queued_job_and_becomes_its_lease_owner(repo: JobRepository) -> None:
    job = _queued(repo)
    claimed = repo.claim(job.job_id, "inst-a", 120, now=T0)
    assert claimed is not None
    assert (claimed.status, claimed.lease_owner) == (GenerationJobStatus.RUNNING, "inst-a")
    assert claimed.lease_expires_at == T0 + timedelta(seconds=120) and claimed.started_at == T0


def test_a_valid_lease_cannot_be_stolen_not_even_by_the_same_owner_name_twice(repo: JobRepository) -> None:
    job = _queued(repo)
    repo.claim(job.job_id, "inst-a", 120, now=T0)
    assert repo.claim(job.job_id, "inst-b", 120, now=T0 + timedelta(seconds=119)) is None
    assert repo.get_by_job_id(job.job_id).lease_owner == "inst-a"


def test_an_expired_lease_can_be_reclaimed_by_another_worker(repo: JobRepository) -> None:
    job = _queued(repo)
    repo.claim(job.job_id, "inst-a", 120, now=T0)
    taken = repo.claim(job.job_id, "inst-b", 120, now=T0 + timedelta(seconds=121))
    assert taken is not None and taken.lease_owner == "inst-b"
    assert taken.started_at == T0  # first start time is preserved


def test_terminal_jobs_are_never_reclaimable(repo: JobRepository) -> None:
    job = _queued(repo)
    repo.claim(job.job_id, "inst-a", 120, now=T0)
    repo.transition(job.job_id, lambda j: mark_job_succeeded(j, result_version="v1"), lease_owner="inst-a")
    assert repo.claim(job.job_id, "inst-b", 120, now=T0 + timedelta(days=1)) is None
    assert repo.get_by_job_id(job.job_id).status == GenerationJobStatus.SUCCEEDED


def test_heartbeat_extends_the_lease_only_for_the_current_owner(repo: JobRepository) -> None:
    job = _queued(repo)
    repo.claim(job.job_id, "inst-a", 120, now=T0)
    assert repo.heartbeat(job.job_id, "inst-a", 120, now=T0 + timedelta(seconds=100)) is True
    assert repo.get_by_job_id(job.job_id).lease_expires_at == T0 + timedelta(seconds=220)
    assert repo.heartbeat(job.job_id, "inst-b", 120, now=T0 + timedelta(seconds=100)) is False  # not the owner
    assert repo.get_by_job_id(job.job_id).lease_expires_at == T0 + timedelta(seconds=220)
    # the heartbeat kept the job alive: another worker still cannot claim it at T0+121s
    assert repo.claim(job.job_id, "inst-b", 120, now=T0 + timedelta(seconds=121)) is None


def test_a_stale_owner_cannot_finish_a_job_after_losing_ownership(repo: JobRepository) -> None:
    job = _queued(repo)
    repo.claim(job.job_id, "inst-a", 120, now=T0)
    repo.claim(job.job_id, "inst-b", 120, now=T0 + timedelta(seconds=500))  # A's lease expired; B owns it now

    stale = repo.transition(job.job_id, lambda j: mark_job_succeeded(j, result_version="v9"), lease_owner="inst-a")
    assert stale is None
    assert repo.heartbeat(job.job_id, "inst-a", 120, now=T0 + timedelta(seconds=501)) is False
    still = repo.get_by_job_id(job.job_id)
    assert still.status == GenerationJobStatus.RUNNING and still.lease_owner == "inst-b"

    ok = repo.transition(job.job_id, lambda j: mark_job_succeeded(j, result_version="v2"), lease_owner="inst-b")
    assert ok is not None and ok.result_version == "v2" and ok.lease_owner is None  # lease cleared when terminal


def test_terminal_transitions_are_idempotent_and_never_overwrite_a_different_terminal_result(
    repo: JobRepository,
) -> None:
    job = _queued(repo)
    repo.claim(job.job_id, "inst-a", 120, now=T0)
    first = repo.transition(job.job_id, lambda j: mark_job_succeeded(j, result_version="v2"), lease_owner="inst-a")
    assert first is not None

    replay = repo.transition(job.job_id, lambda j: mark_job_succeeded(j, result_version="v2"), lease_owner="inst-a")
    conflicting = repo.transition(
        job.job_id, lambda j: mark_job_failed(j, error_code="X", error_message="y"), lease_owner="inst-a"
    )
    assert replay is None and conflicting is None
    final = repo.get_by_job_id(job.job_id)
    assert final.status == GenerationJobStatus.SUCCEEDED and final.result_version == "v2" and final.error_code is None


def test_a_running_job_never_regresses_to_queued_or_running_by_a_late_claim(repo: JobRepository) -> None:
    job = _queued(repo)
    repo.claim(job.job_id, "inst-a", 120, now=T0)
    repo.transition(job.job_id, lambda j: mark_job_failed(j, error_code="E", error_message="m"), lease_owner="inst-a")
    assert repo.claim(job.job_id, "inst-a", 120, now=T0) is None
    assert repo.get_by_job_id(job.job_id).status == GenerationJobStatus.FAILED


def test_mutate_is_not_called_when_the_transition_is_refused(repo: JobRepository) -> None:
    job = _queued(repo)
    repo.claim(job.job_id, "inst-a", 120, now=T0)
    calls: list[int] = []
    result = repo.transition(job.job_id, lambda j: calls.append(1) or j, lease_owner="inst-b")
    assert result is None and calls == []  # no side effects for a rejected owner


def test_a_queued_job_can_fail_without_an_owner_check(repo: JobRepository) -> None:
    job = _queued(repo)
    failed = repo.transition(job.job_id, lambda j: mark_job_failed(j, error_code="E", error_message="m"), lease_owner="inst-a")
    assert failed is not None and failed.status == GenerationJobStatus.FAILED


def test_recovery_closes_only_expired_running_and_stale_queued_jobs(repo: JobRepository) -> None:
    healthy = _queued(repo, "trip_healthy")
    repo.claim(healthy.job_id, "inst-live", 120, now=T0)  # valid lease -> untouched
    dead = _queued(repo, "trip_dead")
    repo.claim(dead.job_id, "inst-dead", 120, now=T0 - timedelta(hours=1))  # lease long expired
    stale_queued = _queued(repo, "trip_stale")
    stale_queued.created_at = T0 - timedelta(days=1)
    fresh_queued = _queued(repo, "trip_fresh")

    recovered = repo.recover_expired_jobs(stale_queued_before=T0 - timedelta(hours=1), now=T0 + timedelta(seconds=60))

    assert {j.job_id for j in recovered} == {dead.job_id, stale_queued.job_id}
    assert repo.get_by_job_id(healthy.job_id).status == GenerationJobStatus.RUNNING  # "process B" left it alone
    assert repo.get_by_job_id(fresh_queued.job_id).status == GenerationJobStatus.QUEUED
    closed = repo.get_by_job_id(dead.job_id)
    assert closed.status == GenerationJobStatus.FAILED and closed.error_code == "JOB_INTERRUPTED"
    assert closed.lease_owner is None
    # recovering twice closes nothing more
    assert repo.recover_expired_jobs(stale_queued_before=T0 - timedelta(hours=1), now=T0) == []


def test_recovery_can_be_scoped_to_one_trip(repo: JobRepository) -> None:
    a = _queued(repo, "trip_a")
    b = _queued(repo, "trip_b")
    for job in (a, b):
        repo.claim(job.job_id, "gone", 1, now=T0 - timedelta(hours=1))
    recovered = repo.recover_expired_jobs(stale_queued_before=T0, trip_id="trip_a", now=T0)
    assert [j.job_id for j in recovered] == [a.job_id]
    assert repo.get_by_job_id(b.job_id).status == GenerationJobStatus.RUNNING


def test_unset_sentinel_means_no_owner_check(repo: JobRepository) -> None:
    job = _queued(repo)
    repo.claim(job.job_id, "inst-a", 120, now=T0)
    done = repo.transition(job.job_id, lambda j: mark_job_failed(j, error_code="E", error_message="m"), lease_owner=UNSET)
    assert done is not None  # system-level transition (no owner supplied)
