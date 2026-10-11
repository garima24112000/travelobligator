from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth import abuse
from app.core.config import get_settings
from app.db.session import get_session_factory
from app.main import app
from app.models.generation_job import GenerationJobType, create_queued_job
from app.repositories.admission import AdmissionPolicy, AdmissionRefusedError
from app.repositories.errors import (
    GenerationInProgressError,
    JobAlreadyActiveError,
    NothingToDeleteError,
    TripNoLongerExistsError,
)
from app.repositories.postgres_job_repository import PostgresJobRepository
from app.services import data_deletion_service, generation_job_service
from app.tests.conftest import create_trip_payload

# P1-A deletion against REAL PostgreSQL: the real foreign keys, one real transaction, a real rollback
# caused by the database itself, and real simultaneous requests (separate threads, separate
# connections, released together). Gated like every live-Postgres test:
#   TRAVELOB_RUN_POSTGRES_TESTS=1 DATABASE_URL=<alembic-upgraded db> pytest -m postgres_integration

pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
        reason="Optional live-Postgres test: set TRAVELOB_RUN_POSTGRES_TESTS=1 and a real DATABASE_URL.",
    ),
]

_PASSWORD = "testpassword123"
_TABLES_BY_TRIP = ("trips", "planning_states", "generation_jobs", "itinerary_branches", "itinerary_revisions")


def _rows(sql: str, **params: object) -> list:
    with get_session_factory()() as session:
        return list(session.execute(text(sql), params).all())


def _scalar(sql: str, **params: object):
    return _rows(sql, **params)[0][0]


def _execute(sql: str, **params: object) -> None:
    with get_session_factory()() as session:
        session.execute(text(sql), params)
        session.commit()


def _stored_for(trip_id: str) -> dict[str, int]:
    return {table: _scalar(f"select count(*) from {table} where trip_id = :t", t=trip_id) for table in _TABLES_BY_TRIP}


_NOTHING = dict.fromkeys(_TABLES_BY_TRIP, 0)


def _account() -> tuple[TestClient, str, str]:
    browser = TestClient(app, raise_server_exceptions=False)
    email = f"p1a-{uuid.uuid4().hex}@example.com"
    signup = browser.post("/auth/signup", json={"email": email, "password": _PASSWORD})
    assert signup.status_code == 201, signup.text
    return browser, email, signup.json()["data"]["user"]["user_id"]


def _full_trip(browser: TestClient, user_id: str) -> str:
    """A generated trip with feedback, a lock, revisions, a fork and a finished job."""
    trip_id = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    assert browser.post(f"/trips/{trip_id}/generate").status_code == 200
    assert browser.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Make it less packed"}).status_code == 200
    assert (
        browser.post(f"/trips/{trip_id}/locks", json={"locked_item_type": "day_plan", "locked_item_id": "day_1"}).status_code
        == 201
    )
    revision_id = _scalar("select revision_id from itinerary_revisions where trip_id = :t limit 1", t=trip_id)
    fork = browser.post(f"/trips/{trip_id}/branches", json={"source_revision_id": revision_id, "display_name": "Alt"})
    assert fork.status_code == 201, fork.text
    _execute(
        "insert into generation_jobs(job_id, trip_id, owner_id, job_type, status, created_at) "
        "values (:j, :t, :o, 'generate', 'succeeded', now())",
        j=f"job_{uuid.uuid4().hex}",
        t=trip_id,
        o=user_id,
    )
    stored = _stored_for(trip_id)
    assert stored["trips"] == stored["planning_states"] == 1
    assert stored["generation_jobs"] >= 1 and stored["itinerary_branches"] >= 2 and stored["itinerary_revisions"] >= 1
    return trip_id


def _running_job(trip_id: str, user_id: str) -> str:
    job = create_queued_job(trip_id=trip_id, owner_id=user_id, job_type=GenerationJobType.GENERATE)
    repo = PostgresJobRepository()
    repo.create(job)
    assert repo.claim(job.job_id, "inst-other", 300) is not None
    return job.job_id


def _finish(job_id: str) -> None:
    _execute("update generation_jobs set status = 'succeeded', lease_owner = null where job_id = :j", j=job_id)


@pytest.fixture()
def no_active_jobs():
    """Other integration tests leave queued/running rows behind; they are throwaway test data."""
    _execute("update generation_jobs set status = 'failed' where status in ('queued', 'running')")
    yield
    _execute("update generation_jobs set status = 'failed' where status in ('queued', 'running')")


def _no_orphans() -> None:
    for table in ("planning_states", "generation_jobs", "itinerary_branches", "itinerary_revisions"):
        assert _scalar(f"select count(*) from {table} where trip_id not in (select trip_id from trips)") == 0
    assert _scalar("select count(*) from generation_jobs where owner_id not in (select user_id from users)") == 0
    assert (
        _scalar("select count(*) from itinerary_revisions where branch_id not in (select branch_id from itinerary_branches)")
        == 0
    )


# -- schema and foreign keys as they really are ------------------------------------------------------


def test_the_real_foreign_keys_match_what_the_deletion_code_was_written_against() -> None:
    rules = {
        (row[0], row[1]): (row[2], row[3])
        for row in _rows(
            """
            select cl.relname, att.attname, ref.relname,
                   case con.confdeltype when 'c' then 'CASCADE' when 'n' then 'SET NULL'
                                        when 'a' then 'NO ACTION' when 'r' then 'RESTRICT' else con.confdeltype::text end
              from pg_constraint con
              join pg_class cl on cl.oid = con.conrelid
              join pg_class ref on ref.oid = con.confrelid
              join pg_attribute att on att.attrelid = con.conrelid and att.attnum = con.conkey[1]
             where con.contype = 'f' and cl.relnamespace = 'public'::regnamespace
            """
        )
    }
    assert rules == {
        ("trips", "owner_id"): ("users", "SET NULL"),
        ("planning_states", "trip_id"): ("trips", "CASCADE"),
        ("generation_jobs", "trip_id"): ("trips", "CASCADE"),
        ("generation_jobs", "owner_id"): ("users", "CASCADE"),
        ("itinerary_branches", "trip_id"): ("trips", "CASCADE"),
        ("itinerary_revisions", "trip_id"): ("trips", "CASCADE"),
        ("itinerary_revisions", "branch_id"): ("itinerary_branches", "CASCADE"),
        ("itinerary_revisions", "parent_revision_id"): ("itinerary_revisions", "SET NULL"),
    }
    assert _rows("select column_name from information_schema.columns where table_name = 'deleted_job_usage' order by 1") == [
        ("created_at",),
        ("owner_id",),
        ("usage_id",),
    ]


def test_deleting_only_the_user_row_would_orphan_its_trips() -> None:
    """Why account deletion is not `DELETE FROM users`: the database keeps the trips, ownerless."""
    browser, _, user_id = _account()
    trip_id = _full_trip(browser, user_id)
    with get_session_factory()() as session:
        session.execute(text("delete from users where user_id = :u"), {"u": user_id})
        owner = session.execute(text("select owner_id from trips where trip_id = :t"), {"t": trip_id}).scalar_one()
        states = session.execute(text("select count(*) from planning_states where trip_id = :t"), {"t": trip_id}).scalar_one()
        session.rollback()  # demonstration only
    assert owner is None and states == 1  # the whole plan would have survived, belonging to nobody
    assert _stored_for(trip_id)["trips"] == 1


# -- deleting a trip ----------------------------------------------------------------------------------


def test_API_trip_deletion_removes_every_dependent_row() -> None:
    browser, _, user_id = _account()
    kept, removed = _full_trip(browser, user_id), _full_trip(browser, user_id)
    kept_before = _stored_for(kept)

    response = browser.delete(f"/trips/{removed}")

    assert response.status_code == 200 and response.json()["data"] == {"trip_id": removed, "deleted": True}
    assert _stored_for(removed) == _NOTHING
    assert _stored_for(kept) == kept_before
    assert browser.get(f"/trips/{removed}").status_code == 404
    assert [trip["trip_id"] for trip in browser.get("/trips").json()["data"]["trips"]] == [kept]
    _no_orphans()


def test_API_a_non_owner_cannot_delete() -> None:
    owner, _, owner_id = _account()
    other, _, _ = _account()
    trip_id = _full_trip(owner, owner_id)
    before = _stored_for(trip_id)
    refused = other.delete(f"/trips/{trip_id}")
    assert refused.status_code == 403 and trip_id not in refused.text
    assert _stored_for(trip_id) == before
    with pytest.raises(NothingToDeleteError):  # the transaction itself re-checks the owner
        data_deletion_service.delete_trip(trip_id, owner_id="user_someone_else")
    assert _stored_for(trip_id) == before


def test_API_a_trip_with_a_running_job_is_not_deleted(no_active_jobs: None) -> None:
    browser, _, user_id = _account()
    trip_id = _full_trip(browser, user_id)
    job_id = _running_job(trip_id, user_id)
    before = _stored_for(trip_id)

    refused = browser.delete(f"/trips/{trip_id}")
    assert refused.status_code == 409 and refused.json()["errors"][0]["code"] == "TRIP_DELETION_BLOCKED"
    assert _stored_for(trip_id) == before
    assert _scalar("select status from generation_jobs where job_id = :j", j=job_id) == "running"  # not cancelled

    _finish(job_id)
    assert browser.delete(f"/trips/{trip_id}").status_code == 200
    assert _stored_for(trip_id) == _NOTHING


def test_a_job_whose_lease_expired_does_not_block_deletion(no_active_jobs: None) -> None:
    browser, _, user_id = _account()
    trip_id = _full_trip(browser, user_id)
    job = create_queued_job(trip_id=trip_id, owner_id=user_id, job_type=GenerationJobType.GENERATE)
    repo = PostgresJobRepository()
    repo.create(job)
    an_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    assert repo.claim(job.job_id, "inst-dead", 60, now=an_hour_ago) is not None  # owner long gone
    assert browser.delete(f"/trips/{trip_id}").status_code == 200
    assert _stored_for(trip_id) == _NOTHING


def test_ROLLBACK_a_database_failure_mid_deletion_leaves_the_trip_complete() -> None:
    browser, _, user_id = _account()
    trip_id = _full_trip(browser, user_id)
    before = _stored_for(trip_id)
    usage_before = _scalar("select count(*) from deleted_job_usage")
    # The database refuses the LAST step (deleting the trip row), after the revisions, branches, jobs
    # and the plan were already deleted inside the transaction.
    _execute(
        f"""
        create or replace function p1a_block_trip_delete() returns trigger language plpgsql as $$
        begin
            if old.trip_id = '{trip_id}' then raise exception 'simulated storage failure'; end if;
            return old;
        end $$
        """
    )
    _execute("create trigger p1a_block_trip_delete before delete on trips for each row execute function p1a_block_trip_delete()")
    try:
        response = browser.delete(f"/trips/{trip_id}")
        assert response.status_code == 503
        body = response.json()
        assert body["errors"][0]["code"] == "PERSISTENCE_UNAVAILABLE"
        assert "simulated" not in response.text and "trigger" not in response.text  # no database detail
        assert _stored_for(trip_id) == before  # everything is back: nothing was half-deleted
        assert _scalar("select count(*) from deleted_job_usage") == usage_before
        assert browser.get(f"/trips/{trip_id}").status_code == 200
    finally:
        _execute("drop trigger if exists p1a_block_trip_delete on trips")
        _execute("drop function if exists p1a_block_trip_delete()")
    assert browser.delete(f"/trips/{trip_id}").status_code == 200  # and it works once the fault is gone
    assert _stored_for(trip_id) == _NOTHING


def test_API_deleting_a_trip_frees_one_place_of_the_trip_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TRIPS_PER_USER", "2")
    get_settings.cache_clear()
    browser, _, _ = _account()
    first = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    assert browser.post("/trips", json=create_trip_payload()).status_code == 201
    assert browser.post("/trips", json=create_trip_payload()).status_code == 429
    assert browser.delete(f"/trips/{first}").status_code == 200
    assert browser.post("/trips", json=create_trip_payload()).status_code == 201
    assert browser.post("/trips", json=create_trip_payload()).status_code == 429


def test_deleted_jobs_still_count_towards_the_generation_limits(no_active_jobs: None) -> None:
    browser, _, user_id = _account()
    trip_id = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    now = datetime.now(timezone.utc)
    for _ in range(2):  # two finished jobs of this window, one ancient job
        _execute(
            "insert into generation_jobs(job_id, trip_id, owner_id, job_type, status, created_at) "
            "values (:j, :t, :o, 'generate', 'succeeded', :c)",
            j=f"job_{uuid.uuid4().hex}", t=trip_id, o=user_id, c=now - timedelta(minutes=5),
        )
    _execute(
        "insert into generation_jobs(job_id, trip_id, owner_id, job_type, status, created_at) "
        "values (:j, :t, :o, 'generate', 'succeeded', :c)",
        j=f"job_{uuid.uuid4().hex}", t=trip_id, o=user_id, c=now - timedelta(days=5),
    )

    assert browser.delete(f"/trips/{trip_id}").status_code == 200

    assert _scalar("select count(*) from generation_jobs where owner_id = :o", o=user_id) == 0
    kept = _rows("select usage_id, created_at from deleted_job_usage where owner_id = :o", o=user_id)
    assert len(kept) == 2  # the two inside the window; the ancient one left nothing
    assert all(usage_id.startswith("usage_") and "job_" not in usage_id for usage_id, _ in kept)

    other_trip = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    job = create_queued_job(trip_id=other_trip, owner_id=user_id, job_type=GenerationJobType.GENERATE)
    two_per_user = AdmissionPolicy(
        max_jobs_per_user_per_window=2, max_jobs_global_per_window=0, max_concurrent_jobs=0, window_seconds=86400
    )
    with pytest.raises(AdmissionRefusedError):  # the deleted jobs are still the user's two
        PostgresJobRepository().admit(job, two_per_user, now=job.created_at)
    three_per_user = AdmissionPolicy(
        max_jobs_per_user_per_window=3, max_jobs_global_per_window=0, max_concurrent_jobs=0, window_seconds=86400
    )
    PostgresJobRepository().admit(job, three_per_user, now=job.created_at)
    _finish(job.job_id)


def test_RACE_deletion_and_job_admission_for_one_trip_never_leave_a_job_without_its_trip(
    no_active_jobs: None,
) -> None:
    """A new job and the deletion of its trip at the same instant: both take the admission lock, so
    either the job was admitted first (the deletion is refused, the trip keeps its job) or the trip was
    deleted first (the job cannot be created). Never a deleted trip with a job running for it."""
    policy = AdmissionPolicy(
        max_jobs_per_user_per_window=0, max_jobs_global_per_window=0, max_concurrent_jobs=0, window_seconds=86400
    )
    outcomes_seen = set()
    for _ in range(6):
        browser, _, user_id = _account()
        trip_id = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
        barrier = threading.Barrier(5)

        def admit(_: int) -> object:
            job = create_queued_job(trip_id=trip_id, owner_id=user_id, job_type=GenerationJobType.GENERATE)
            barrier.wait(timeout=15)
            try:
                PostgresJobRepository().admit(job, policy, now=job.created_at)
                return "admitted"
            except Exception as exc:  # noqa: BLE001 - the exception IS the observed outcome
                return exc

        def remove() -> object:
            barrier.wait(timeout=15)
            try:
                data_deletion_service.delete_trip(trip_id, owner_id=user_id)
                return "deleted"
            except Exception as exc:  # noqa: BLE001
                return exc

        with ThreadPoolExecutor(max_workers=5) as pool:
            admissions = [pool.submit(admit, index) for index in range(4)]
            deletion = pool.submit(remove)
            admitted = [future.result() for future in admissions]
            deleted = deletion.result()

        won = admitted.count("admitted")
        assert won <= 1
        for outcome in admitted:
            # a second job for the trip, or a trip that is already gone -- never a raw database error
            assert outcome == "admitted" or isinstance(
                outcome, (JobAlreadyActiveError, TripNoLongerExistsError)
            ), outcome
            assert not isinstance(outcome, IntegrityError)
        if deleted == "deleted":
            assert won == 0 and _stored_for(trip_id) == _NOTHING
        else:
            assert isinstance(deleted, GenerationInProgressError), deleted
            assert won == 1 and _stored_for(trip_id)["trips"] == 1 and _stored_for(trip_id)["generation_jobs"] == 1
        outcomes_seen.add("deleted" if deleted == "deleted" else "blocked")
        _execute("update generation_jobs set status = 'failed' where trip_id = :t", t=trip_id)
        _no_orphans()
    assert outcomes_seen <= {"deleted", "blocked"}
    assert _scalar("select count(*) from pg_locks where locktype = 'advisory'") == 0


def _per_user(limit: int) -> AdmissionPolicy:
    return AdmissionPolicy(
        max_jobs_per_user_per_window=limit, max_jobs_global_per_window=0, max_concurrent_jobs=0, window_seconds=86400
    )


def _global(limit: int) -> AdmissionPolicy:
    return AdmissionPolicy(
        max_jobs_per_user_per_window=0, max_jobs_global_per_window=limit, max_concurrent_jobs=0, window_seconds=86400
    )


def _finished_job(trip_id: str, user_id: str, created_at: datetime) -> None:
    _execute(
        "insert into generation_jobs(job_id, trip_id, owner_id, job_type, status, created_at) "
        "values (:j, :t, :o, 'generate', 'succeeded', :c)",
        j=f"job_{uuid.uuid4().hex}", t=trip_id, o=user_id, c=created_at,
    )


def _would_be_admitted(policy: AdmissionPolicy, trip_id: str, user_id: str, now: datetime) -> bool:
    """Whether a new job would be admitted at `now` -- decided by the real `admit`, then undone."""
    job = create_queued_job(trip_id=trip_id, owner_id=user_id, job_type=GenerationJobType.GENERATE)
    job.created_at = now
    try:
        PostgresJobRepository().admit(job, policy, now=now)
    except AdmissionRefusedError:
        return False
    _execute("delete from generation_jobs where job_id = :j", j=job.job_id)
    return True


def test_deleted_usage_expires_with_the_rolling_window_and_is_then_removed(no_active_jobs: None) -> None:
    browser, _, user_id = _account()
    trip_id = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    other_trip = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    admitted_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    _finished_job(trip_id, user_id, admitted_at)
    assert browser.delete(f"/trips/{trip_id}").status_code == 200
    (kept_at,) = [row[0] for row in _rows("select created_at from deleted_job_usage where owner_id = :o", o=user_id)]
    assert kept_at == admitted_at  # the usage keeps the job's own admission time, not the deletion time

    window = timedelta(seconds=86400)
    one_per_user = _per_user(1)
    # inside the window the deleted job still uses the user's one place ...
    assert _would_be_admitted(one_per_user, other_trip, user_id, admitted_at + timedelta(minutes=10)) is False
    assert _would_be_admitted(one_per_user, other_trip, user_id, admitted_at + window - timedelta(seconds=1)) is False
    # ... and exactly one window after its admission it no longer does
    assert _would_be_admitted(one_per_user, other_trip, user_id, admitted_at + window) is True

    # once it is older than the window, the next deletion of anything removes the row
    _execute(
        "update deleted_job_usage set created_at = now() - interval '2 days' where owner_id = :o", o=user_id
    )
    assert browser.delete(f"/trips/{other_trip}").status_code == 200
    assert _scalar("select count(*) from deleted_job_usage where owner_id = :o", o=user_id) == 0


def test_deleting_an_account_does_not_restore_the_global_generation_allowance(no_active_jobs: None) -> None:
    def used_in_window() -> int:
        return _scalar(
            "select (select count(*) from generation_jobs where created_at > now() - interval '24 hours')"
            " + (select count(*) from deleted_job_usage where created_at > now() - interval '24 hours')"
        )

    leaving, _, leaving_id = _account()
    watcher, _, watcher_id = _account()
    watcher_trip = watcher.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    now = datetime.now(timezone.utc)
    for _ in range(3):
        trip_id = leaving.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
        _finished_job(trip_id, leaving_id, now - timedelta(minutes=5))
    used = used_in_window()  # everything the deployment used in the window, the three jobs included
    assert _would_be_admitted(_global(used), watcher_trip, watcher_id, datetime.now(timezone.utc)) is False

    assert _delete_account(leaving).status_code == 200

    assert _scalar("select count(*) from generation_jobs where owner_id = :u", u=leaving_id) == 0
    assert used_in_window() == used  # the three jobs are gone, their usage is not
    # the deployment is exactly as full as before: still refused at that limit, admitted one above it
    assert _would_be_admitted(_global(used), watcher_trip, watcher_id, datetime.now(timezone.utc)) is False
    assert _would_be_admitted(_global(used + 1), watcher_trip, watcher_id, datetime.now(timezone.utc)) is True
    # and what is kept refers to nobody
    assert _scalar("select count(*) from deleted_job_usage where owner_id = :u", u=leaving_id) == 0


def test_deleted_usage_holds_a_time_and_an_account_id_and_nothing_else() -> None:
    browser, email, user_id = _account()
    destination = f"Privateville {uuid.uuid4().hex}"
    payload = {
        **create_trip_payload(),
        "primary_destination": destination,
        "origin_city": "Secret Origin City",
        "interests": ["a very personal interest"],
        "must_visit": ["Grandmother's house"],
        "free_text_preferences": "private notes about this trip",
    }
    trip_id = browser.post("/trips", json=payload).json()["data"]["trip_id"]
    job_id = f"job_{uuid.uuid4().hex}"
    _execute(
        "insert into generation_jobs(job_id, trip_id, owner_id, job_type, status, created_at, error_message) "
        "values (:j, :t, :o, 'generate', 'failed', now(), 'an error message naming the destination')",
        j=job_id, t=trip_id, o=user_id,
    )
    assert browser.delete(f"/trips/{trip_id}").status_code == 200

    rows = _rows("select to_jsonb(u)::text from deleted_job_usage u where owner_id = :o", o=user_id)
    assert len(rows) == 1
    kept = rows[0][0]
    assert set(__import__("json").loads(kept)) == {"usage_id", "owner_id", "created_at"}
    for private in (
        trip_id, job_id, email, destination, "Secret Origin City", "personal interest",
        "Grandmother", "private notes", "error message",
    ):
        assert private not in kept
    # the id is derived one-way from the job id: it cannot be turned back into it
    assert __import__("json").loads(kept)["usage_id"].startswith("usage_") and job_id[4:] not in kept

    # after the account is deleted not even the account id remains anywhere in the table
    assert _delete_account(browser).status_code == 200
    everything = "".join(row[0] for row in _rows("select to_jsonb(u)::text from deleted_job_usage u"))
    assert user_id not in everything and email not in everything


# -- a generation request that loses to a deletion ----------------------------------------------------
#
# The exact race: the request passes its ownership check (the trip still exists), a deletion of the trip
# commits, and only then does the request reach job admission. Reproduced deterministically: the
# deletion's transaction is opened and left uncommitted (it holds the admission lock), the request is
# started and seen WAITING on that lock, and then the deletion commits.


def _waiting_on_the_admission_lock() -> int:
    return _scalar("select count(*) from pg_locks where locktype = 'advisory' and not granted")


def _generate_while_a_deletion_commits(
    browser: TestClient, trip_id: str, user_id: str, *, whole_account: bool, email: str
):
    """Returns the response of `POST /trips/{trip_id}/generate` issued while the deletion of the trip
    (or of the whole account) is in flight and committed only once the request is waiting on it."""
    import time

    cookie_name = get_settings().session_cookie_name
    requester = TestClient(app, raise_server_exceptions=False)
    requester.cookies.set(cookie_name, browser.cookies.get(cookie_name))

    deletion = get_session_factory()()
    try:
        data_deletion_service._delete_postgres(
            deletion,
            owner_id=user_id,
            trip_id=None if whole_account else trip_id,
            email_digest=abuse.login_failure_key(email) if whole_account else None,
        )  # holds the admission lock; nothing is committed yet
        # another connection still sees the trip: the request will pass its ownership check
        assert _stored_for(trip_id)["trips"] == 1

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(requester.post, f"/trips/{trip_id}/generate")
            deadline = time.monotonic() + 15
            while _waiting_on_the_admission_lock() == 0:
                assert not pending.done(), "the request finished without waiting for the deletion"
                assert time.monotonic() < deadline, "the request never reached job admission"
                time.sleep(0.02)
            deletion.commit()  # the deletion wins
            return pending.result(timeout=30)
    finally:
        deletion.rollback()
        deletion.close()


@pytest.mark.parametrize("whole_account", [False, True], ids=["trip-deleted", "account-deleted"])
def test_RACE_a_generation_request_that_loses_to_a_deletion_is_a_404_not_a_503(
    no_active_jobs: None, monkeypatch: pytest.MonkeyPatch, whole_account: bool
) -> None:
    from app.providers.gateway import provider_gateway
    from app.services.planning_orchestrator import planning_orchestrator

    browser, email, user_id = _account()
    trip_id = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    bystander, _, bystander_id = _account()
    bystander_trip = _full_trip(bystander, bystander_id)  # built on the synchronous path
    bystander_before = _stored_for(bystander_trip)

    # From here on generation runs as jobs, and any planning or provider work would be recorded.
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    work: list[str] = []
    real_plan = planning_orchestrator.generate_full_plan_via_langgraph
    monkeypatch.setattr(planning_orchestrator, "generate_full_plan_via_langgraph", lambda trip: work.append("plan"))
    monkeypatch.setattr(planning_orchestrator, "generate_full_plan", lambda trip: work.append("plan"))
    monkeypatch.setattr(provider_gateway.places, "search_attractions", lambda *a, **k: work.append("provider"))
    jobs_before = _scalar("select count(*) from generation_jobs")
    usage_before = _scalar("select count(*) from deleted_job_usage")

    response = _generate_while_a_deletion_commits(
        browser, trip_id, user_id, whole_account=whole_account, email=email
    )

    # the trip no longer exists: the ordinary 404 -- not a 409 (nothing conflicts) and not a 503
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["success"] is False and body["data"] is None
    assert body["errors"] == [
        {"code": "TRIP_NOT_FOUND", "field": "trip_id", "message": f"Trip '{trip_id}' was not found."}
    ]
    for leaked in ("PERSISTENCE_UNAVAILABLE", "foreign key", "violates", "fk_generation_jobs", bystander_trip):
        assert leaked not in response.text
    # no job, no orphan, no usage record, no planning and no provider call came of it
    assert _stored_for(trip_id) == _NOTHING
    assert _scalar("select count(*) from generation_jobs") == jobs_before
    assert _scalar("select count(*) from generation_jobs where owner_id = :u", u=user_id) == 0
    assert _scalar("select count(*) from deleted_job_usage") == usage_before
    assert work == []
    assert _scalar("select count(*) from pg_locks where locktype = 'advisory'") == 0
    _no_orphans()
    # nobody else was affected, and their generation is still admitted and runs
    assert _stored_for(bystander_trip) == bystander_before
    monkeypatch.setattr(planning_orchestrator, "generate_full_plan_via_langgraph", real_plan)
    admitted = bystander.post(f"/trips/{bystander_trip}/generate")
    assert admitted.status_code == 202
    job_id = admitted.json()["data"]["job_id"]
    assert _scalar("select status from generation_jobs where job_id = :j", j=job_id) == "succeeded"
    if whole_account:
        assert _scalar("select count(*) from users where user_id = :u", u=user_id) == 0


def test_every_kind_of_job_for_a_deleted_trip_is_the_same_404(no_active_jobs: None) -> None:
    from app.core.errors import AppError

    browser, _, user_id = _account()
    trip_id = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    assert browser.delete(f"/trips/{trip_id}").status_code == 200

    for job_type in (GenerationJobType.GENERATE, GenerationJobType.REGENERATE):
        job = create_queued_job(trip_id=trip_id, owner_id=user_id, job_type=job_type)
        with pytest.raises(AppError) as refused:  # generate, regenerate and targeted regenerate share this
            generation_job_service._create_job_or_conflict(job)
        assert (refused.value.status_code, refused.value.code.value) == (404, "TRIP_NOT_FOUND")
        assert PostgresJobRepository().get_by_job_id(job.job_id) is None


def test_a_genuine_conflict_is_still_a_409_and_a_limit_still_a_429(
    no_active_jobs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 404 above is only for a trip that is gone. A second job for a LIVE trip is still the 409
    conflict, and an exhausted allowance still the 429 -- the admission logic is unchanged."""
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    monkeypatch.setenv("GENERATION_MAX_JOBS_PER_USER_PER_WINDOW", "1")
    get_settings.cache_clear()
    browser, _, user_id = _account()
    trip_id = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    other_trip = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]

    running = _running_job(trip_id, user_id)
    conflict = browser.post(f"/trips/{trip_id}/generate")
    assert conflict.status_code == 409 and conflict.json()["errors"][0]["code"] == "JOB_ALREADY_RUNNING"
    limited = browser.post(f"/trips/{other_trip}/generate")
    assert limited.status_code == 429 and limited.json()["errors"][0]["code"] == "GENERATION_USER_LIMIT_REACHED"
    _finish(running)


def test_a_job_whose_parent_row_is_missing_is_never_a_raw_integrity_error(no_active_jobs: None) -> None:
    """The fallback behind the explicit check: the insert's own foreign key (here the owner) reports a
    missing parent as "the trip no longer exists", with nothing written."""
    browser, _, user_id = _account()
    trip_id = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    policy = AdmissionPolicy(
        max_jobs_per_user_per_window=0, max_jobs_global_per_window=0, max_concurrent_jobs=0, window_seconds=86400
    )
    ghost = create_queued_job(trip_id=trip_id, owner_id="user_that_was_deleted", job_type=GenerationJobType.GENERATE)
    with pytest.raises(TripNoLongerExistsError):
        PostgresJobRepository().admit(ghost, policy, now=ghost.created_at)
    assert _stored_for(trip_id)["generation_jobs"] == 0


def test_a_real_database_failure_during_admission_is_still_a_503(
    no_active_jobs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    browser, _, user_id = _account()
    trip_id = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    _execute(
        f"""
        create or replace function p1a_block_job_insert() returns trigger language plpgsql as $$
        begin
            if new.trip_id = '{trip_id}' then raise exception 'simulated storage failure'; end if;
            return new;
        end $$
        """
    )
    _execute("create trigger p1a_block_job_insert before insert on generation_jobs for each row execute function p1a_block_job_insert()")
    try:
        response = browser.post(f"/trips/{trip_id}/generate")
        assert response.status_code == 503
        assert response.json()["errors"][0]["code"] == "PERSISTENCE_UNAVAILABLE"
        assert _stored_for(trip_id)["generation_jobs"] == 0
    finally:
        _execute("drop trigger if exists p1a_block_job_insert on generation_jobs")
        _execute("drop function if exists p1a_block_job_insert()")
    assert browser.post(f"/trips/{trip_id}/generate").status_code == 202


# -- deleting an account ------------------------------------------------------------------------------


def _delete_account(browser: TestClient, password: str = _PASSWORD):
    return browser.post("/auth/delete-account", json={"password": password, "confirm": True})


def test_API_account_deletion_removes_everything_the_account_owns(no_active_jobs: None) -> None:
    leaving, leaving_email, leaving_id = _account()
    staying, staying_email, staying_id = _account()
    leaving_trips = [_full_trip(leaving, leaving_id) for _ in range(3)]
    staying_trip = _full_trip(staying, staying_id)
    staying_before = _stored_for(staying_trip)
    stranger = TestClient(app)
    for email in (leaving_email, staying_email):
        assert stranger.post("/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
    leaving_key, staying_key = abuse.login_failure_key(leaving_email), abuse.login_failure_key(staying_email)
    assert _scalar("select count(*) from auth_attempts where subject_hash = :k", k=leaving_key) == 1
    usage_before = _scalar("select count(*) from deleted_job_usage")

    response = _delete_account(leaving)

    assert response.status_code == 200
    assert response.json()["data"] == {"deleted": True, "trips_deleted": 3}
    assert "max-age=0" in response.headers["set-cookie"].lower()
    for trip_id in leaving_trips:
        assert _stored_for(trip_id) == _NOTHING
    assert _scalar("select count(*) from users where user_id = :u or email = :e", u=leaving_id, e=leaving_email) == 0
    assert _scalar("select count(*) from trips where owner_id = :u", u=leaving_id) == 0
    assert _scalar("select count(*) from generation_jobs where owner_id = :u", u=leaving_id) == 0
    assert _scalar("select count(*) from auth_attempts where subject_hash = :k", k=leaving_key) == 0
    # what the generation limits keep refers to nobody
    assert _scalar("select count(*) from deleted_job_usage where owner_id = :u", u=leaving_id) == 0
    assert _scalar("select count(*) from deleted_job_usage") == usage_before + 3  # one finished job per trip
    # the session is dead everywhere, and the account cannot sign in
    assert leaving.get("/auth/me").status_code == 401
    assert stranger.post("/auth/login", json={"email": leaving_email, "password": _PASSWORD}).status_code == 401
    # the other account is exactly as it was
    assert _stored_for(staying_trip) == staying_before
    assert _scalar("select count(*) from users where user_id = :u", u=staying_id) == 1
    assert _scalar("select count(*) from auth_attempts where subject_hash = :k", k=staying_key) == 1
    assert staying.get(f"/trips/{staying_trip}").status_code == 200
    _no_orphans()


def test_API_account_deletion_needs_the_password_and_is_blocked_by_a_running_job(no_active_jobs: None) -> None:
    browser, _, user_id = _account()
    idle, busy = _full_trip(browser, user_id), _full_trip(browser, user_id)
    before = (_stored_for(idle), _stored_for(busy))

    assert _delete_account(browser, password="wrong-password").status_code == 401
    job_id = _running_job(busy, user_id)
    blocked = _delete_account(browser)
    assert blocked.status_code == 409 and blocked.json()["errors"][0]["code"] == "ACCOUNT_DELETION_BLOCKED"
    assert (_stored_for(idle)["trips"], _stored_for(busy)["trips"]) == (1, 1)
    assert _stored_for(idle) == before[0]  # not even the idle trip was touched
    assert _scalar("select count(*) from users where user_id = :u", u=user_id) == 1
    assert browser.get("/auth/me").status_code == 200

    _finish(job_id)
    assert _delete_account(browser).status_code == 200
    assert _scalar("select count(*) from users where user_id = :u", u=user_id) == 0


def test_ROLLBACK_a_database_failure_mid_account_deletion_deletes_nothing() -> None:
    browser, email, user_id = _account()
    trips = [_full_trip(browser, user_id) for _ in range(2)]
    before = [_stored_for(trip_id) for trip_id in trips]
    usage_before = _scalar("select count(*) from deleted_job_usage")
    # The database refuses the LAST step (deleting the user), after every trip was already deleted
    # inside the transaction.
    _execute(
        f"""
        create or replace function p1a_block_user_delete() returns trigger language plpgsql as $$
        begin
            if old.user_id = '{user_id}' then raise exception 'simulated storage failure'; end if;
            return old;
        end $$
        """
    )
    _execute("create trigger p1a_block_user_delete before delete on users for each row execute function p1a_block_user_delete()")
    try:
        response = _delete_account(browser)
        assert response.status_code == 503
        assert response.json()["errors"][0]["code"] == "PERSISTENCE_UNAVAILABLE"
        assert "set-cookie" not in response.headers  # still signed in: nothing happened
        assert [_stored_for(trip_id) for trip_id in trips] == before
        assert _scalar("select count(*) from users where user_id = :u", u=user_id) == 1
        assert _scalar("select count(*) from deleted_job_usage") == usage_before  # rolled back too
        assert browser.get("/auth/me").status_code == 200
        assert len(browser.get("/trips").json()["data"]["trips"]) == 2
    finally:
        _execute("drop trigger if exists p1a_block_user_delete on users")
        _execute("drop function if exists p1a_block_user_delete()")
    assert _delete_account(browser).status_code == 200
    assert _scalar("select count(*) from users where user_id = :u", u=user_id) == 0
    _no_orphans()


def test_RACE_two_simultaneous_account_deletions_delete_once() -> None:
    browser, email, user_id = _account()
    for _ in range(2):
        _full_trip(browser, user_id)
    cookie_name = get_settings().session_cookie_name
    token = browser.cookies.get(cookie_name)
    barrier = threading.Barrier(4)

    def attempt(_: int) -> int:
        tab = TestClient(app, raise_server_exceptions=False)
        tab.cookies.set(cookie_name, token)
        barrier.wait(timeout=15)
        return _delete_account(tab).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = sorted(pool.map(attempt, range(4)))

    assert statuses.count(200) == 1
    assert set(statuses) <= {200, 401}, statuses  # the others find no account: never a 500
    assert _scalar("select count(*) from users where user_id = :u", u=user_id) == 0
    assert _scalar("select count(*) from trips where owner_id = :u", u=user_id) == 0
    _no_orphans()


def test_the_service_reports_a_vanished_account() -> None:
    with pytest.raises(NothingToDeleteError):
        data_deletion_service.delete_account("user_that_never_existed", email_digest="none")
