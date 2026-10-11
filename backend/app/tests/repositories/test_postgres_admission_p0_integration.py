from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.main import app
from app.models.generation_job import (
    GenerationJobStatus,
    GenerationJobType,
    create_queued_job,
    mark_job_failed,
    mark_job_interrupted,
)
from app.models.planning_state import TravelGroupType, TripRequest
from app.models.user import UserRecord
from app.repositories.admission import (
    AdmissionPolicy,
    AdmissionRefusal,
    AdmissionRefusedError,
    TripLimitReachedError,
)
from app.repositories.auth_attempt_repository import (
    LOGIN_FAILURE,
    SIGNUP,
    PostgresAuthAttemptRepository,
)
from app.repositories.errors import JobAlreadyActiveError
from app.repositories.postgres_job_repository import PostgresJobRepository
from app.repositories.postgres_trip_repository import PostgresTripRepository
from app.repositories.postgres_user_repository import PostgresUserRepository
from app.services import generation_job_service
from app.services.planning_orchestrator import planning_orchestrator
from app.tests.conftest import create_trip_payload

# P0 admission limits against REAL PostgreSQL. Every racing worker is a separate thread with its own
# connection and transaction, released together by a barrier -- not a sequential simulation. Gated like
# every live-Postgres test:
#   TRAVELOB_RUN_POSTGRES_TESTS=1 DATABASE_URL=<alembic-upgraded db> pytest -m postgres_integration
#
# The database is shared with every other integration test and its rows persist, so:
#   * window limits are tested at a private instant far in the future (`_private_now`): only this
#     test's own jobs are inside that window;
#   * the concurrent limit counts live rows of ANY test, so it is set relative to what is there.

pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
        reason="Optional live-Postgres test: set TRAVELOB_RUN_POSTGRES_TESTS=1 and a real DATABASE_URL.",
    ),
]

WORKERS = 12
DAY = 86400


def _race(worker: Callable[[int], object], workers: int = WORKERS) -> list[object]:
    barrier = threading.Barrier(workers)

    def run(index: int) -> object:
        barrier.wait(timeout=15)
        try:
            return worker(index)
        except Exception as exc:  # noqa: BLE001 - the exception IS the observed outcome
            return exc

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, range(workers)))


def _private_now() -> datetime:
    """An instant only this test has jobs at: decades ahead. A window has no upper bound (a job stamped
    slightly ahead by another instance's clock must still count), so the future-dated rows earlier
    tests of this file left behind are removed first; no other test writes a job dated in the future."""
    with get_session_factory()() as session:
        session.execute(text("delete from generation_jobs where created_at > now() + interval '1 year'"))
        session.commit()
    offset = int(uuid.uuid4().int % (20 * 365 * DAY))
    return datetime(2070, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset)


def _seed_owner() -> str:
    now = datetime.now(timezone.utc)
    owner_id = f"user_p0_{uuid.uuid4().hex}"
    PostgresUserRepository(session_factory=get_session_factory()).create_user(
        UserRecord(user_id=owner_id, email=f"{owner_id}@example.com", password_hash="x", created_at=now, updated_at=now)
    )
    return owner_id


def _seed_trip(owner_id: str | None = None) -> tuple[str, str]:
    owner_id = owner_id or _seed_owner()
    trip_id = f"trip_p0_{uuid.uuid4().hex}"
    PostgresTripRepository(session_factory=get_session_factory()).create(trip_id, owner_id=owner_id)
    return trip_id, owner_id


def _job(trip_id: str, owner_id: str, created_at: datetime):
    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    job.created_at = created_at
    return job


def _policy(user: int = 0, total: int = 0, concurrent: int = 0, window: int = DAY) -> AdmissionPolicy:
    return AdmissionPolicy(
        max_jobs_per_user_per_window=user,
        max_jobs_global_per_window=total,
        max_concurrent_jobs=concurrent,
        window_seconds=window,
    )


def _scalar(sql: str, **params: object):
    with get_session_factory()() as session:
        return session.execute(text(sql), params).scalar_one()


def _jobs_of(owner_ids: list[str]) -> int:
    return _scalar("select count(*) from generation_jobs where owner_id = any(:owners)", owners=owner_ids)


def _active_jobs() -> int:
    return _scalar("select count(*) from generation_jobs where status in ('queued', 'running')")


def _finish(job_id: str) -> None:
    with get_session_factory()() as session:
        session.execute(
            text("update generation_jobs set status = 'succeeded', lease_owner = null where job_id = :id"),
            {"id": job_id},
        )
        session.commit()


@pytest.fixture()
def no_active_jobs():
    """Close whatever other integration tests left queued/running, so the concurrent count is this
    test's own. (Rows are throwaway test data; nothing reads them afterwards.)"""
    with get_session_factory()() as session:
        session.execute(text("update generation_jobs set status = 'failed' where status in ('queued', 'running')"))
        session.commit()
    yield
    with get_session_factory()() as session:
        session.execute(text("update generation_jobs set status = 'failed' where status in ('queued', 'running')"))
        session.commit()


# -- schema ----------------------------------------------------------------------------------------


def test_the_migrated_schema_has_the_window_indexes_and_the_attempts_table() -> None:
    indexes = {
        row[0]
        for row in get_session_factory()().execute(
            text("select indexname from pg_indexes where tablename in ('generation_jobs', 'auth_attempts')")
        )
    }
    assert {
        "ix_generation_jobs_created_at",
        "ix_generation_jobs_owner_id_created_at",
        "ix_auth_attempts_kind_created_at",
        "uq_generation_jobs_one_active_per_trip",
    } <= indexes
    columns = {
        row[0]
        for row in get_session_factory()().execute(
            text("select column_name from information_schema.columns where table_name = 'auth_attempts'")
        )
    }
    assert columns == {"attempt_id", "kind", "subject_hash", "ip_hash", "created_at"}


# -- simultaneous admissions -----------------------------------------------------------------------


def test_RACE_global_window_limit_admits_exactly_the_limit(no_active_jobs: None) -> None:
    now = _private_now()
    seeds = [_seed_trip() for _ in range(WORKERS)]  # different owners, different trips
    policy = _policy(total=3)

    outcomes = _race(lambda i: PostgresJobRepository().admit(_job(*seeds[i], now), policy, now=now))

    refused = [o for o in outcomes if isinstance(o, AdmissionRefusedError)]
    assert len([o for o in outcomes if not isinstance(o, Exception)]) == 3
    assert len(refused) == WORKERS - 3 and {r.reason for r in refused} == {AdmissionRefusal.GLOBAL_WINDOW}
    assert _jobs_of([owner for _, owner in seeds]) == 3  # exactly three rows; a refusal left none


def test_RACE_per_user_window_limit_admits_exactly_the_limit(no_active_jobs: None) -> None:
    now = _private_now()
    owner_id = _seed_owner()
    trips = [_seed_trip(owner_id)[0] for _ in range(WORKERS)]  # one owner, a different trip each
    policy = _policy(user=5)

    outcomes = _race(lambda i: PostgresJobRepository().admit(_job(trips[i], owner_id, now), policy, now=now))

    refused = [o for o in outcomes if isinstance(o, AdmissionRefusedError)]
    assert len([o for o in outcomes if not isinstance(o, Exception)]) == 5
    assert len(refused) == WORKERS - 5 and {r.reason for r in refused} == {AdmissionRefusal.USER_WINDOW}
    assert _jobs_of([owner_id]) == 5


def test_RACE_concurrent_limit_admits_exactly_the_free_slots(no_active_jobs: None) -> None:
    now = datetime.now(timezone.utc)
    seeds = [_seed_trip() for _ in range(WORKERS)]
    policy = _policy(concurrent=2)
    assert _active_jobs() == 0

    outcomes = _race(lambda i: PostgresJobRepository().admit(_job(*seeds[i], now), policy, now=now))

    refused = [o for o in outcomes if isinstance(o, AdmissionRefusedError)]
    assert len([o for o in outcomes if not isinstance(o, Exception)]) == 2
    assert len(refused) == WORKERS - 2 and {r.reason for r in refused} == {AdmissionRefusal.CONCURRENT}
    assert _active_jobs() == 2  # never more than the limit, at any outcome of the race


def test_RACE_all_three_limits_together_never_exceed_any(no_active_jobs: None) -> None:
    now = _private_now()
    owners = [_seed_owner() for _ in range(3)]
    seeds = [_seed_trip(owners[i % 3]) for i in range(WORKERS)]  # four attempts per owner
    policy = _policy(user=2, total=5, concurrent=4)

    outcomes = _race(lambda i: PostgresJobRepository().admit(_job(*seeds[i], now), policy, now=now))

    admitted = [o for o in outcomes if not isinstance(o, Exception)]
    assert all(isinstance(o, AdmissionRefusedError) for o in outcomes if isinstance(o, Exception))
    assert len(admitted) == 4  # the tightest limit (concurrent = 4) decides
    assert _active_jobs() == 4
    for owner_id in owners:
        assert _jobs_of([owner_id]) <= 2


def test_RACE_same_trip_keeps_the_one_active_job_rule(no_active_jobs: None) -> None:
    now = datetime.now(timezone.utc)
    trip_id, owner_id = _seed_trip()
    # No window or concurrent limit: this is about the per-trip rule alone, and must not depend on how
    # many jobs other tests (or earlier runs against the same database) left inside the real window.
    policy = _policy()

    outcomes = _race(lambda i: PostgresJobRepository().admit(_job(trip_id, owner_id, now), policy, now=now))

    assert len([o for o in outcomes if not isinstance(o, Exception)]) == 1
    assert all(isinstance(o, JobAlreadyActiveError) for o in outcomes if isinstance(o, Exception))
    assert _scalar("select count(*) from generation_jobs where trip_id = :t", t=trip_id) == 1


def test_the_admission_lock_is_released_with_the_transaction(no_active_jobs: None) -> None:
    now = _private_now()
    trip_id, owner_id = _seed_trip()
    repo = PostgresJobRepository()
    repo.admit(_job(trip_id, owner_id, now), _policy(user=1), now=now)
    with pytest.raises(AdmissionRefusedError):
        repo.admit(_job(_seed_trip(owner_id)[0], owner_id, now), _policy(user=1), now=now)
    # neither a commit nor a refusal (rollback) leaves an advisory lock behind
    assert _scalar("select count(*) from pg_locks where locktype = 'advisory'") == 0


# -- rolling window ---------------------------------------------------------------------------------


def test_window_boundary_and_what_is_counted(no_active_jobs: None) -> None:
    now = _private_now()
    owner_id = _seed_owner()
    repo = PostgresJobRepository()
    one_per_window = _policy(user=1)

    exactly_one_window_old = _job(_seed_trip(owner_id)[0], owner_id, now - timedelta(seconds=DAY))
    repo.create(exactly_one_window_old)
    _finish(exactly_one_window_old.job_id)
    # a job exactly one window old has left the window: the next one is admitted
    counted = _job(_seed_trip(owner_id)[0], owner_id, now)
    repo.admit(counted, one_per_window, now=now)

    # one second before it leaves the window it still counts -- succeeded, failed and interrupted alike
    almost = now + timedelta(seconds=DAY - 1)
    for finish in (
        lambda job: _finish(job.job_id),
        lambda job: repo.save(mark_job_failed(job, error_code="STAGE_FAILED", error_message="failed")),
        lambda job: repo.save(mark_job_interrupted(job)),
    ):
        finish(counted)
        with pytest.raises(AdmissionRefusedError) as refused:
            repo.admit(_job(_seed_trip(owner_id)[0], owner_id, almost), one_per_window, now=almost)
        assert refused.value.reason is AdmissionRefusal.USER_WINDOW

    # and at exactly one window, the next job is admitted
    later = now + timedelta(seconds=DAY)
    repo.admit(_job(_seed_trip(owner_id)[0], owner_id, later), one_per_window, now=later)
    assert _jobs_of([owner_id]) == 3


def test_a_regeneration_job_counts_like_a_generation(no_active_jobs: None) -> None:
    now = _private_now()
    trip_id, owner_id = _seed_trip()
    repo = PostgresJobRepository()
    regenerate = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE)
    regenerate.created_at = now
    repo.admit(regenerate, _policy(user=1), now=now)
    _finish(regenerate.job_id)
    with pytest.raises(AdmissionRefusedError):
        repo.admit(_job(trip_id, owner_id, now), _policy(user=1), now=now)


# -- stale jobs --------------------------------------------------------------------------------------


def test_a_job_whose_lease_expired_frees_its_concurrent_slot(
    no_active_jobs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATION_MAX_CONCURRENT_JOBS", "2")
    get_settings.cache_clear()
    now = datetime.now(timezone.utc)
    repo = PostgresJobRepository()

    crashed = []
    for _ in range(2):  # two running jobs whose owner died an hour ago: leases long expired
        job = _job(*_seed_trip(), now - timedelta(hours=1))
        repo.create(job)
        assert repo.claim(job.job_id, "inst-dead", 60, now=now - timedelta(hours=1)) is not None
        crashed.append(job)
    assert _active_jobs() == 2

    # admitted through the service: it closes abandoned jobs everywhere before it counts
    trip_id, owner_id = _seed_trip()
    fresh = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    generation_job_service._create_job_or_conflict(fresh)

    for job in crashed:
        stored = repo.get_by_job_id(job.job_id)
        assert stored.status is GenerationJobStatus.FAILED and stored.error_code == "JOB_INTERRUPTED"
    assert _active_jobs() == 1


def test_a_healthy_lease_keeps_its_slot(no_active_jobs: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENERATION_MAX_CONCURRENT_JOBS", "2")
    get_settings.cache_clear()
    repo = PostgresJobRepository()
    healthy = []
    for _ in range(2):
        job = _job(*_seed_trip(), datetime.now(timezone.utc))
        repo.create(job)
        assert repo.claim(job.job_id, "inst-alive", 120) is not None
        healthy.append(job)

    trip_id, owner_id = _seed_trip()
    refused_job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    with pytest.raises(Exception) as refused:
        generation_job_service._create_job_or_conflict(refused_job)
    assert getattr(refused.value, "status_code", None) == 429
    assert refused.value.code.value == "GENERATION_CAPACITY_REACHED"
    assert repo.get_by_job_id(refused_job.job_id) is None
    assert all(repo.get_by_job_id(job.job_id).status is GenerationJobStatus.RUNNING for job in healthy)


# -- through the API ---------------------------------------------------------------------------------


def test_API_per_user_limit_with_postgres(no_active_jobs: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    monkeypatch.setenv("GENERATION_MAX_JOBS_PER_USER_PER_WINDOW", "2")
    get_settings.cache_clear()
    client = TestClient(app)
    email = f"p0-{uuid.uuid4().hex}@example.com"
    assert client.post("/auth/signup", json={"email": email, "password": "testpassword123"}).status_code == 201

    statuses = []
    for _ in range(3):
        trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
        response = client.post(f"/trips/{trip_id}/generate")
        statuses.append(response.status_code)
    assert statuses == [202, 202, 429]
    assert response.json()["errors"][0]["code"] == "GENERATION_USER_LIMIT_REACHED"
    owner_id = client.get("/auth/me").json()["data"]["user"]["user_id"]
    assert _jobs_of([owner_id]) == 2
    assert _scalar("select count(*) from generation_jobs where trip_id = :t", t=trip_id) == 0


# -- saved trips per account -------------------------------------------------------------------------


def _trip_request() -> TripRequest:
    return TripRequest(
        primary_destination="Testville, Testland",
        start_date="2027-03-01",
        end_date="2027-03-03",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )


def test_RACE_trip_limit_admits_exactly_the_limit() -> None:
    owner_id = _seed_owner()

    outcomes = _race(
        lambda i: planning_orchestrator.create_trip(_trip_request(), owner_id=owner_id, max_trips_for_owner=3),
        workers=10,
    )

    assert len([o for o in outcomes if not isinstance(o, Exception)]) == 3
    assert all(isinstance(o, TripLimitReachedError) for o in outcomes if isinstance(o, Exception))
    assert _scalar("select count(*) from trips where owner_id = :o", o=owner_id) == 3
    # the trip row and its planning state are one transaction: a refusal left neither
    assert (
        _scalar(
            "select count(*) from planning_states p join trips t using (trip_id) where t.owner_id = :o",
            o=owner_id,
        )
        == 3
    )
    assert _scalar("select count(*) from planning_states where trip_id not in (select trip_id from trips)") == 0


def test_the_trip_limit_of_one_account_never_delays_or_blocks_another() -> None:
    first, second = _seed_owner(), _seed_owner()
    repo = PostgresTripRepository()
    repo.create_within_owner_limit(f"trip_p0_{uuid.uuid4().hex}", first, 1)
    with pytest.raises(TripLimitReachedError):
        repo.create_within_owner_limit(f"trip_p0_{uuid.uuid4().hex}", first, 1)
    repo.create_within_owner_limit(f"trip_p0_{uuid.uuid4().hex}", second, 1)


# -- signup / sign-in attempt counters ---------------------------------------------------------------


def test_the_attempt_counters_live_in_postgres() -> None:
    repo = PostgresAuthAttemptRepository()
    now = _private_now()
    subject, address = uuid.uuid4().hex, uuid.uuid4().hex

    for seconds_ago in (10, 20, 30):
        repo.record(LOGIN_FAILURE, now=now - timedelta(seconds=seconds_ago), subject_hash=subject, ip_hash=address)
    repo.record(SIGNUP, now=now - timedelta(seconds=5), ip_hash=address)

    since = now - timedelta(seconds=25)
    assert repo.count(LOGIN_FAILURE, since=since, subject_hash=subject) == 2  # the 30-second-old one is outside
    assert repo.count(LOGIN_FAILURE, since=now - timedelta(seconds=30), subject_hash=subject) == 2  # boundary excluded
    assert repo.count(LOGIN_FAILURE, since=now - timedelta(seconds=31), ip_hash=address) == 3
    assert repo.count(SIGNUP, since=since, ip_hash=address) == 1
    assert repo.count(LOGIN_FAILURE, since=since, subject_hash="someone-else") == 0

    # a second repository (another process, in effect) sees the same rows
    assert PostgresAuthAttemptRepository().count(LOGIN_FAILURE, since=since, subject_hash=subject) == 2

    repo.clear(LOGIN_FAILURE, subject_hash=subject)
    assert repo.count(LOGIN_FAILURE, since=now - timedelta(days=1), subject_hash=subject) == 0
    assert repo.count(SIGNUP, since=since, ip_hash=address) == 1  # another kind is untouched

    stored = get_session_factory()().execute(
        text("select subject_hash, ip_hash from auth_attempts where ip_hash = :a"), {"a": address}
    ).all()
    assert stored == [(None, address)]


def test_old_attempt_rows_are_pruned_on_write() -> None:
    repo = PostgresAuthAttemptRepository()
    marker = uuid.uuid4().hex
    ancient = datetime(1999, 1, 1, tzinfo=timezone.utc)
    repo.record(LOGIN_FAILURE, now=ancient, subject_hash=marker)
    assert _scalar("select count(*) from auth_attempts where subject_hash = :m", m=marker) == 1
    repo.record(SIGNUP, now=ancient + timedelta(days=30), prune_before=ancient + timedelta(days=1))
    assert _scalar("select count(*) from auth_attempts where subject_hash = :m", m=marker) == 0


def test_API_sign_in_lockout_with_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGIN_MAX_FAILURES_PER_ACCOUNT", "2")
    get_settings.cache_clear()
    client = TestClient(app)
    email = f"p0-{uuid.uuid4().hex}@example.com"
    assert client.post("/auth/signup", json={"email": email, "password": "testpassword123"}).status_code == 201

    wrong = {"email": email, "password": "wrong-password"}
    assert [client.post("/auth/login", json=wrong).status_code for _ in range(3)] == [401, 401, 429]
    locked = client.post("/auth/login", json={"email": email, "password": "testpassword123"})
    assert locked.status_code == 429 and locked.json()["errors"][0]["code"] == "TOO_MANY_ATTEMPTS"
    assert _scalar("select count(*) from auth_attempts where subject_hash = :e or ip_hash = :e", e=email) == 0
