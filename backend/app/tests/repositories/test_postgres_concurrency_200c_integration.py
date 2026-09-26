from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest
from sqlalchemy import text

from app.db.session import get_session_factory
from app.models.generation_job import (
    GenerationJobStatus,
    GenerationJobType,
    create_queued_job,
    mark_job_failed,
    mark_job_succeeded,
)
from app.models.itinerary_lineage import ItineraryBranch, ItineraryRevision
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.models.user import UserRecord
from app.repositories.errors import (
    BranchHeadConflictError,
    ConcurrentStateUpdateError,
    JobAlreadyActiveError,
)
from app.repositories.postgres_itinerary_lineage_repository import PostgresItineraryLineageRepository
from app.repositories.postgres_job_repository import PostgresJobRepository
from app.repositories.postgres_planning_state_repository import PostgresPlanningStateRepository
from app.repositories.postgres_trip_repository import PostgresTripRepository
from app.repositories.postgres_user_repository import PostgresUserRepository

# Section 200C: REAL PostgreSQL concurrency. Every racing worker below is a separate thread with
# its own connection/transaction (the engine pool hands each repository call its own), released
# together by a barrier -- these are not sequential simulations. Gated like every live-Postgres
# test:  TRAVELOB_RUN_POSTGRES_TESTS=1 DATABASE_URL=<alembic-upgraded db> pytest -m postgres_integration

pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
        reason="Optional live-Postgres test: set TRAVELOB_RUN_POSTGRES_TESTS=1 and a real DATABASE_URL.",
    ),
]

WORKERS = 6


def _race(worker: Callable[[int], object], workers: int = WORKERS) -> list[object]:
    """Run `worker(i)` in `workers` threads released simultaneously; return results/exceptions."""
    barrier = threading.Barrier(workers)

    def run(i: int) -> object:
        barrier.wait(timeout=15)
        try:
            return worker(i)
        except Exception as exc:  # noqa: BLE001 - the exception IS the observed outcome
            return exc

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, range(workers)))


def _seed_trip() -> tuple[str, str]:
    sf = get_session_factory()
    now = datetime.now(timezone.utc)
    owner_id = f"user_200c_{uuid.uuid4().hex}"
    trip_id = f"trip_200c_{uuid.uuid4().hex}"
    PostgresUserRepository(session_factory=sf).create_user(
        UserRecord(user_id=owner_id, email=f"{owner_id}@example.com", password_hash="x", created_at=now, updated_at=now)
    )
    PostgresTripRepository(session_factory=sf).create(trip_id, owner_id=owner_id)
    return trip_id, owner_id


def _state(trip_id: str) -> PlanningState:
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


def _scalar(sql: str, **params: object):
    with get_session_factory()() as session:
        return session.execute(text(sql), params).scalar_one()


# -- A. PlanningState optimistic concurrency ---------------------------------------------------------------


def test_A_writers_from_the_same_lock_version_exactly_one_wins() -> None:
    trip_id, _ = _seed_trip()
    repo = PostgresPlanningStateRepository()
    repo.save(_state(trip_id))
    loaded = [repo.get_by_trip_id(trip_id) for _ in range(WORKERS)]
    assert {s._lock_version for s in loaded} == {0}

    def write(i: int) -> str:
        loaded[i].metadata.current_version = f"v{i + 2}"
        PostgresPlanningStateRepository().save(loaded[i])
        return loaded[i].metadata.current_version

    outcomes = _race(write)

    winners = [o for o in outcomes if isinstance(o, str)]
    losers = [o for o in outcomes if isinstance(o, ConcurrentStateUpdateError)]
    assert len(winners) == 1 and len(losers) == WORKERS - 1
    stored = repo.get_by_trip_id(trip_id)
    assert stored.metadata.current_version == winners[0] and stored._lock_version == 1  # advanced exactly once


def test_a_stale_writer_never_overwrites_and_can_recover_by_reloading() -> None:
    trip_id, _ = _seed_trip()
    repo = PostgresPlanningStateRepository()
    repo.save(_state(trip_id))
    a, b = repo.get_by_trip_id(trip_id), repo.get_by_trip_id(trip_id)
    a.metadata.current_version = "v2"
    repo.save(a)
    b.metadata.current_version = "v_stale"
    with pytest.raises(ConcurrentStateUpdateError):
        repo.save(b)
    assert b._lock_version == 0  # a failed save does not pretend it advanced
    assert repo.get_by_trip_id(trip_id).metadata.current_version == "v2"
    fresh = repo.get_by_trip_id(trip_id)
    fresh.metadata.current_version = "v3"
    repo.save(fresh)  # reload -> decide again -> succeeds
    assert repo.get_by_trip_id(trip_id)._lock_version == 2


def test_an_unloaded_object_cannot_overwrite_an_existing_state() -> None:
    trip_id, _ = _seed_trip()
    repo = PostgresPlanningStateRepository()
    repo.save(_state(trip_id))
    with pytest.raises(ConcurrentStateUpdateError):
        repo.save(_state(trip_id))  # a blind write: never read, so it has no token


# -- B. one active job per trip: the DATABASE decides --------------------------------------------------------


def test_B_concurrent_active_job_creators_leave_exactly_one_active_job() -> None:
    trip_id, owner_id = _seed_trip()

    def create(i: int) -> str:
        job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
        PostgresJobRepository().create(job)
        return job.job_id

    outcomes = _race(create)

    created = [o for o in outcomes if isinstance(o, str)]
    refused = [o for o in outcomes if isinstance(o, JobAlreadyActiveError)]
    assert len(created) == 1 and len(refused) == WORKERS - 1, outcomes
    assert _scalar("select count(*) from generation_jobs where trip_id=:t and status in ('queued','running')", t=trip_id) == 1


def test_the_unique_index_only_covers_active_jobs_so_a_new_job_is_possible_after_a_terminal_one() -> None:
    trip_id, owner_id = _seed_trip()
    repo = PostgresJobRepository()
    first = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    repo.create(first)
    repo.claim(first.job_id, "inst-a", 60)
    repo.transition(first.job_id, lambda j: mark_job_succeeded(j, result_version="v1"), lease_owner="inst-a")
    second = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE)
    repo.create(second)  # allowed: the previous job is terminal
    with pytest.raises(JobAlreadyActiveError):
        repo.create(create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE))
    other_trip, other_owner = _seed_trip()
    repo.create(create_queued_job(trip_id=other_trip, owner_id=other_owner, job_type=GenerationJobType.GENERATE))


# -- D. branch-head compare-and-set ------------------------------------------------------------------------------


def _branch_with_head(trip_id: str, revisions: int = 1) -> tuple[ItineraryBranch, list[ItineraryRevision]]:
    repo = PostgresItineraryLineageRepository()
    branch = repo.get_or_create_default_branch(ItineraryBranch(trip_id=trip_id, display_name="Main", is_default=True))
    made: list[ItineraryRevision] = []
    for n in range(revisions + 1):
        made.append(
            repo.create_revision(
                ItineraryRevision(trip_id=trip_id, branch_id=branch.branch_id, version_label=f"v{n + 1}", created_by="t")
            )
        )
    repo.update_branch_head(branch.branch_id, made[0].revision_id)
    return branch, made


def test_D_a_stale_branch_head_writer_fails_and_the_head_is_never_overwritten() -> None:
    trip_id, _ = _seed_trip()
    branch, revs = _branch_with_head(trip_id, revisions=2)
    repo = PostgresItineraryLineageRepository()

    repo.update_branch_head(branch.branch_id, revs[1].revision_id, expected_head_revision_id=revs[0].revision_id)
    with pytest.raises(BranchHeadConflictError):  # a writer that still believes the head is revs[0]
        repo.update_branch_head(branch.branch_id, revs[2].revision_id, expected_head_revision_id=revs[0].revision_id)
    assert repo.get_branch(branch.branch_id).head_revision_id == revs[1].revision_id


def test_D_concurrent_head_advances_from_the_same_head_exactly_one_wins() -> None:
    trip_id, _ = _seed_trip()
    branch, revs = _branch_with_head(trip_id, revisions=WORKERS)

    def advance(i: int):
        return PostgresItineraryLineageRepository().update_branch_head(
            branch.branch_id, revs[i + 1].revision_id, expected_head_revision_id=revs[0].revision_id
        )

    outcomes = _race(advance)
    winners = [o for o in outcomes if isinstance(o, ItineraryBranch)]
    assert len(winners) == 1 and all(isinstance(o, BranchHeadConflictError) for o in outcomes if o not in winners)
    assert PostgresItineraryLineageRepository().get_branch(branch.branch_id).head_revision_id == winners[0].head_revision_id


def test_ensure_default_branch_is_race_safe() -> None:
    trip_id, _ = _seed_trip()
    outcomes = _race(
        lambda i: PostgresItineraryLineageRepository()
        .get_or_create_default_branch(ItineraryBranch(trip_id=trip_id, display_name="Main", is_default=True))
        .branch_id
    )
    assert all(isinstance(o, str) for o in outcomes) and len(set(outcomes)) == 1  # one default branch, no error
    assert _scalar("select count(*) from itinerary_branches where trip_id=:t and is_default", t=trip_id) == 1


def test_revision_uniqueness_is_enforced_per_branch_and_version() -> None:
    trip_id, _ = _seed_trip()
    branch, _ = _branch_with_head(trip_id, revisions=0)
    repo = PostgresItineraryLineageRepository()
    again = repo.create_revision(
        ItineraryRevision(trip_id=trip_id, branch_id=branch.branch_id, version_label="v1", created_by="replay")
    )
    assert again.created_by == "t"  # the replay returned the ORIGINAL revision
    assert _scalar("select count(*) from itinerary_revisions where branch_id=:b", b=branch.branch_id) == 1

    outcomes = _race(
        lambda i: repo.create_revision(
            ItineraryRevision(trip_id=trip_id, branch_id=branch.branch_id, version_label="v2", created_by=f"w{i}")
        ).revision_id
    )
    assert len(set(outcomes)) == 1  # every racer converges on the single v2 revision
    assert _scalar("select count(*) from itinerary_revisions where branch_id=:b", b=branch.branch_id) == 2


# -- E/F/G. leases -------------------------------------------------------------------------------------------------


def _queued_job() -> tuple[PostgresJobRepository, str]:
    trip_id, owner_id = _seed_trip()
    repo = PostgresJobRepository()
    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    repo.create(job)
    return repo, job.job_id


def test_E_concurrent_claimers_produce_exactly_one_lease_owner() -> None:
    repo, job_id = _queued_job()

    outcomes = _race(lambda i: PostgresJobRepository().claim(job_id, f"inst-{i}", 120))

    winners = [o for o in outcomes if o is not None]
    assert len(winners) == 1 and all(o is None for o in outcomes if o not in winners)
    stored = repo.get_by_job_id(job_id)
    assert stored.status == GenerationJobStatus.RUNNING and stored.lease_owner == winners[0].lease_owner


def test_F_an_expired_lease_can_be_claimed_by_a_second_worker_using_the_database_clock() -> None:
    repo, job_id = _queued_job()
    assert repo.claim(job_id, "inst-a", 1) is not None
    assert repo.claim(job_id, "inst-b", 1) is None  # still valid
    time.sleep(1.4)  # lease expires by the DATABASE's clock
    taken = repo.claim(job_id, "inst-b", 60)
    assert taken is not None and taken.lease_owner == "inst-b"


def test_F_expiry_with_an_explicit_clock_and_heartbeat_keeping_a_lease_alive() -> None:
    repo, job_id = _queued_job()
    t0 = datetime.now(timezone.utc)
    repo.claim(job_id, "inst-a", 60, now=t0)
    assert repo.heartbeat(job_id, "inst-a", 60, now=t0 + timedelta(seconds=50)) is True  # -> valid until +110
    assert repo.claim(job_id, "inst-b", 60, now=t0 + timedelta(seconds=100)) is None
    assert repo.claim(job_id, "inst-b", 60, now=t0 + timedelta(seconds=111)) is not None


def test_G_the_old_lease_owner_cannot_complete_or_heartbeat_after_ownership_changed() -> None:
    repo, job_id = _queued_job()
    t0 = datetime.now(timezone.utc)
    repo.claim(job_id, "inst-a", 30, now=t0)
    repo.claim(job_id, "inst-b", 30, now=t0 + timedelta(seconds=60))  # A's lease expired; B took over

    assert repo.heartbeat(job_id, "inst-a", 30, now=t0 + timedelta(seconds=61)) is False
    assert repo.transition(job_id, lambda j: mark_job_succeeded(j, result_version="v9"), lease_owner="inst-a") is None
    still = repo.get_by_job_id(job_id)
    assert still.status == GenerationJobStatus.RUNNING and still.lease_owner == "inst-b" and still.result_version is None
    assert repo.transition(job_id, lambda j: mark_job_succeeded(j, result_version="v2"), lease_owner="inst-b") is not None


def test_terminal_transitions_are_idempotent_under_concurrent_completion() -> None:
    repo, job_id = _queued_job()
    repo.claim(job_id, "inst-a", 120)

    def finish(i: int):
        mutate = (
            (lambda j: mark_job_succeeded(j, result_version="v2"))
            if i % 2 == 0
            else (lambda j: mark_job_failed(j, error_code="X", error_message="y"))
        )
        return PostgresJobRepository().transition(job_id, mutate, lease_owner="inst-a")

    outcomes = _race(finish)
    landed = [o for o in outcomes if o is not None]
    assert len(landed) == 1  # exactly one terminal result, whatever it was
    final = repo.get_by_job_id(job_id)
    assert final.status == landed[0].status and final.lease_owner is None
    assert repo.claim(job_id, "inst-z", 60, now=datetime.now(timezone.utc) + timedelta(days=1)) is None  # terminal


def test_recovery_closes_expired_jobs_exactly_once_even_when_run_by_several_instances() -> None:
    repo, job_id = _queued_job()
    t0 = datetime.now(timezone.utc)
    repo.claim(job_id, "inst-dead", 5, now=t0 - timedelta(hours=1))

    outcomes = _race(
        lambda i: PostgresJobRepository().recover_expired_jobs(stale_queued_before=t0 - timedelta(hours=2), now=t0)
    )
    closed = [job for batch in outcomes for job in batch if job.job_id == job_id]  # type: ignore[union-attr]
    assert len(closed) == 1
    assert repo.get_by_job_id(job_id).error_code == "JOB_INTERRUPTED"


def test_recovery_never_touches_a_job_with_a_valid_lease_or_a_fresh_queued_job() -> None:
    live_repo, live_id = _queued_job()
    live_repo.claim(live_id, "inst-live", 300)
    fresh_repo, fresh_id = _queued_job()

    PostgresJobRepository().recover_expired_jobs(stale_queued_before=datetime.now(timezone.utc) - timedelta(hours=1))

    assert live_repo.get_by_job_id(live_id).status == GenerationJobStatus.RUNNING
    assert fresh_repo.get_by_job_id(fresh_id).status == GenerationJobStatus.QUEUED
