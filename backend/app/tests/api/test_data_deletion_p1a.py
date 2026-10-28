from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth import abuse
from app.core.config import get_settings
from app.main import app
from app.models.generation_job import (
    GenerationJobStatus,
    GenerationJobType,
    create_queued_job,
    mark_job_succeeded,
)
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.gateway import provider_gateway
from app.repositories.auth_attempt_repository import LOGIN_FAILURE, SIGNUP, auth_attempt_repository
from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository
from app.repositories.job_repository import job_repository
from app.repositories.planning_state_repository import planning_state_repository
from app.repositories.trip_repository import trip_repository
from app.repositories.user_repository import user_repository
from app.services import data_deletion_service
from app.tests.conftest import create_trip_payload, new_authenticated_client

# P1-A: deleting a trip and deleting an account, hermetic (Local JSON repositories). The single
# PostgreSQL transaction, its rollback and the real foreign keys are exercised in
# tests/repositories/test_postgres_data_deletion_p1a_integration.py.

_PASSWORD = "testpassword123"


def _env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _account() -> tuple[TestClient, str]:
    """A signed-in browser and its email (the conftest helper hides the email)."""
    browser = TestClient(app)
    email = f"p1a-{uuid.uuid4().hex}@example.com"
    assert browser.post("/auth/signup", json={"email": email, "password": _PASSWORD}).status_code == 201
    return browser, email


def _user_id(browser: TestClient) -> str:
    return browser.get("/auth/me").json()["data"]["user"]["user_id"]


def _new_trip(browser: TestClient) -> str:
    response = browser.post("/trips", json=create_trip_payload())
    assert response.status_code == 201, response.text
    return response.json()["data"]["trip_id"]


def _full_trip(browser: TestClient) -> str:
    """A generated trip with feedback, a lock, a revision on its default branch, a fork and a finished
    job -- something in every place a trip keeps data."""
    trip_id = _new_trip(browser)
    assert browser.post(f"/trips/{trip_id}/generate").status_code == 200
    assert browser.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Make it less packed"}).status_code == 200
    lock = browser.post(f"/trips/{trip_id}/locks", json={"locked_item_type": "day_plan", "locked_item_id": "day_1"})
    assert lock.status_code == 201
    revisions = [r for r in itinerary_lineage_repository._revisions.values() if r.trip_id == trip_id]
    assert revisions, "generation records a revision"
    fork = browser.post(
        f"/trips/{trip_id}/branches",
        json={"source_revision_id": revisions[0].revision_id, "display_name": "Alternative"},
    )
    assert fork.status_code == 201, fork.text
    finished = create_queued_job(trip_id=trip_id, owner_id=_user_id(browser), job_type=GenerationJobType.GENERATE)
    job_repository._jobs[finished.job_id] = mark_job_succeeded(finished, result_version="v1", changed_sections=[])
    return trip_id


def _stored_for(trip_id: str) -> dict[str, int]:
    return {
        "trips": int(trip_id in trip_repository._trips),
        "planning_states": int(trip_id in planning_state_repository._states),
        "jobs": sum(1 for job in job_repository._jobs.values() if job.trip_id == trip_id),
        "branches": sum(1 for b in itinerary_lineage_repository._branches.values() if b.trip_id == trip_id),
        "revisions": sum(1 for r in itinerary_lineage_repository._revisions.values() if r.trip_id == trip_id),
    }


_NOTHING = {"trips": 0, "planning_states": 0, "jobs": 0, "branches": 0, "revisions": 0}


def _active_job(trip_id: str, owner_id: str, *, running: bool):
    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    job_repository._jobs[job.job_id] = job
    if running:
        assert job_repository.claim(job.job_id, "inst-other", 120) is not None
    return job


def _delete_account(browser: TestClient, password: str = _PASSWORD, confirm: object = True):
    return browser.post("/auth/delete-account", json={"password": password, "confirm": confirm})


# == deleting a trip ================================================================================


def test_the_owner_deletes_a_trip_and_everything_stored_for_it(client: TestClient) -> None:
    trip_id = _full_trip(client)
    before = _stored_for(trip_id)
    assert before["trips"] == before["planning_states"] == 1
    assert before["jobs"] >= 1 and before["branches"] >= 2 and before["revisions"] >= 1

    response = client.delete(f"/trips/{trip_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True and body["message"] == "Trip deleted."
    assert body["data"] == {"trip_id": trip_id, "deleted": True}
    assert _stored_for(trip_id) == _NOTHING


def test_a_deleted_trip_is_gone_from_every_endpoint(client: TestClient) -> None:
    trip_id = _full_trip(client)
    assert client.delete(f"/trips/{trip_id}").status_code == 200

    assert client.get("/trips").json()["data"]["trips"] == []
    for method, path in (
        ("get", f"/trips/{trip_id}"),
        ("get", f"/trips/{trip_id}/summary"),
        ("get", f"/trips/{trip_id}/experience-plan"),
        ("get", f"/trips/{trip_id}/jobs"),
        ("get", f"/trips/{trip_id}/branches"),
        ("post", f"/trips/{trip_id}/generate"),
        ("post", f"/trips/{trip_id}/feedback"),
        ("delete", f"/trips/{trip_id}"),  # deleting twice
    ):
        response = getattr(client, method)(path, **({"json": {"feedback_text": "x"}} if path.endswith("feedback") else {}))
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["errors"][0]["code"] == "TRIP_NOT_FOUND"


def test_deleting_one_trip_leaves_the_owners_other_trips_alone(client: TestClient) -> None:
    kept, removed = _full_trip(client), _full_trip(client)
    kept_before = _stored_for(kept)
    kept_plan = client.get(f"/trips/{kept}").json()

    assert client.delete(f"/trips/{removed}").status_code == 200

    assert _stored_for(kept) == kept_before
    assert client.get(f"/trips/{kept}").json()["data"] == kept_plan["data"]
    assert [trip["trip_id"] for trip in client.get("/trips").json()["data"]["trips"]] == [kept]


def test_deleting_requires_a_session() -> None:
    owner = new_authenticated_client()
    trip_id = _new_trip(owner)
    anonymous = TestClient(app)
    for target in (trip_id, "trip_that_does_not_exist"):
        response = anonymous.delete(f"/trips/{target}")
        assert response.status_code == 401
        assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"
    assert _stored_for(trip_id)["trips"] == 1


def test_a_tampered_or_expired_session_cannot_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    owner = new_authenticated_client()
    trip_id = _new_trip(owner)
    cookie_name = get_settings().session_cookie_name

    forged = TestClient(app)
    forged.cookies.set(cookie_name, owner.cookies.get(cookie_name)[:-4] + "AAAA")
    assert forged.delete(f"/trips/{trip_id}").status_code == 401

    _env(monkeypatch, SESSION_TTL_SECONDS="1")
    import time

    time.sleep(2.1)
    assert owner.delete(f"/trips/{trip_id}").status_code == 401
    assert _stored_for(trip_id)["trips"] == 1


def test_a_non_owner_cannot_delete_and_learns_nothing_about_the_trip(client: TestClient, second_client: TestClient) -> None:
    trip_id = _full_trip(client)
    before = _stored_for(trip_id)

    response = second_client.delete(f"/trips/{trip_id}")

    assert response.status_code == 403
    body = response.json()
    assert body["data"] is None
    assert body["errors"] == [
        {"code": "FORBIDDEN", "field": None, "message": "You do not have access to this resource."}
    ]
    # nothing of the trip in the answer: not its destination, dates, owner, nor its own id
    text = response.text
    for secret in ("Testville", "Home City", "2026-08-10", _user_id(client), trip_id):
        assert secret not in text
    assert _stored_for(trip_id) == before
    assert client.get(f"/trips/{trip_id}").status_code == 200


def test_an_unowned_legacy_trip_cannot_be_deleted_by_anyone(client: TestClient) -> None:
    legacy = PlanningState(
        trip_request=TripRequest(
            primary_destination="Oldtown",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 2),
            travelers_count=1,
            travel_group_type=TravelGroupType.SOLO,
        )
    )
    trip_repository.create(legacy.trip_id, owner_id=None)  # a trip from before ownership existed
    planning_state_repository.save(legacy)
    assert client.delete(f"/trips/{legacy.trip_id}").status_code == 403
    assert _stored_for(legacy.trip_id)["trips"] == 1


# -- generation in progress -------------------------------------------------------------------------


@pytest.mark.parametrize("running", [False, True], ids=["queued", "running"])
def test_a_trip_with_a_queued_or_running_job_is_not_deleted(client: TestClient, running: bool) -> None:
    trip_id = _full_trip(client)
    job = _active_job(trip_id, _user_id(client), running=running)
    before = _stored_for(trip_id)

    response = client.delete(f"/trips/{trip_id}")

    assert response.status_code == 409
    error = response.json()["errors"][0]
    assert error["code"] == "TRIP_DELETION_BLOCKED"
    assert "being built right now" in error["message"]
    assert _stored_for(trip_id) == before  # nothing was removed
    stored_job = job_repository.get_by_job_id(job.job_id)
    assert stored_job.status in (GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)  # and not cancelled


def test_the_trip_can_be_deleted_once_the_job_has_finished(client: TestClient) -> None:
    trip_id = _new_trip(client)
    job = _active_job(trip_id, _user_id(client), running=True)
    assert client.delete(f"/trips/{trip_id}").status_code == 409
    mark_job_succeeded(job, result_version="v1", changed_sections=[])
    assert client.delete(f"/trips/{trip_id}").status_code == 200


def test_an_abandoned_job_does_not_make_a_trip_undeletable(client: TestClient) -> None:
    trip_id = _new_trip(client)
    job = _active_job(trip_id, _user_id(client), running=True)
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=get_settings().generation_job_stale_after_seconds + 60)
    job.started_at = job.created_at = long_ago

    assert client.delete(f"/trips/{trip_id}").status_code == 200
    assert _stored_for(trip_id) == _NOTHING


def test_another_trips_running_job_does_not_block_this_one(client: TestClient) -> None:
    busy, idle = _new_trip(client), _new_trip(client)
    _active_job(busy, _user_id(client), running=True)
    assert client.delete(f"/trips/{idle}").status_code == 200
    assert client.delete(f"/trips/{busy}").status_code == 409


def test_deleting_calls_no_provider_and_runs_no_planning(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    trip_id = _full_trip(client)
    calls: list[str] = []
    for name in ("search_attractions", "search_restaurants", "search_accommodation_pois"):
        monkeypatch.setattr(provider_gateway.places, name, lambda *a, _n=name, **k: calls.append(_n))
    from app.services.planning_orchestrator import planning_orchestrator

    monkeypatch.setattr(planning_orchestrator, "generate_full_plan", lambda trip: calls.append("plan"))
    monkeypatch.setattr(planning_orchestrator, "generate_full_plan_via_langgraph", lambda trip: calls.append("plan"))
    assert client.delete(f"/trips/{trip_id}").status_code == 200
    assert calls == []


# -- limits -----------------------------------------------------------------------------------------


def test_deleting_a_trip_frees_one_place_of_the_trip_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, MAX_TRIPS_PER_USER="3")
    trips = [_new_trip(client) for _ in range(3)]
    assert client.post("/trips", json=create_trip_payload()).json()["errors"][0]["code"] == "TRIP_LIMIT_REACHED"

    assert client.delete(f"/trips/{trips[0]}").status_code == 200

    assert client.post("/trips", json=create_trip_payload()).status_code == 201  # exactly one place
    assert client.post("/trips", json=create_trip_payload()).status_code == 429


def test_the_default_twenty_trip_limit_is_usable_again_after_a_deletion(client: TestClient) -> None:
    trips = [_new_trip(client) for _ in range(20)]
    assert client.post("/trips", json=create_trip_payload()).status_code == 429
    assert client.delete(f"/trips/{trips[7]}").status_code == 200
    assert client.post("/trips", json=create_trip_payload()).status_code == 201


def test_deleting_a_trip_does_not_hand_back_generation_allowance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true", GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="2")
    used = []
    for _ in range(2):
        trip_id = _new_trip(client)
        assert client.post(f"/trips/{trip_id}/generate").status_code == 202
        used.append(trip_id)
    for trip_id in used:
        assert client.delete(f"/trips/{trip_id}").status_code == 200
    assert job_repository._jobs == {}  # the job rows are really gone...

    fresh = _new_trip(client)
    refused = client.post(f"/trips/{fresh}/generate")
    assert refused.status_code == 429  # ...and still count in the window
    assert refused.json()["errors"][0]["code"] == "GENERATION_USER_LIMIT_REACHED"
    # what is kept is a timestamp and the account -- no trip, job or destination
    assert all(owner == _user_id(client) for owner, _ in job_repository._deleted_usage)
    assert len(job_repository._deleted_usage) == 2

    other = new_authenticated_client()
    assert other.post(f"/trips/{_new_trip(other)}/generate").status_code == 202  # nobody else is affected


def test_deleted_usage_leaves_the_window_like_any_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true", GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="1")
    trip_id = _new_trip(client)
    assert client.post(f"/trips/{trip_id}/generate").status_code == 202
    assert client.delete(f"/trips/{trip_id}").status_code == 200
    assert client.post(f"/trips/{_new_trip(client)}/generate").status_code == 429

    owner, _ = job_repository._deleted_usage[0]
    job_repository._deleted_usage[0] = (owner, datetime.now(timezone.utc) - timedelta(days=1, seconds=5))
    assert client.post(f"/trips/{_new_trip(client)}/generate").status_code == 202


def test_a_job_older_than_the_window_leaves_no_usage_record(client: TestClient) -> None:
    trip_id = _new_trip(client)
    old = create_queued_job(trip_id=trip_id, owner_id=_user_id(client), job_type=GenerationJobType.GENERATE)
    old.created_at = datetime.now(timezone.utc) - timedelta(days=3)
    job_repository._jobs[old.job_id] = mark_job_succeeded(old, result_version="v1", changed_sections=[])
    assert client.delete(f"/trips/{trip_id}").status_code == 200
    assert job_repository._deleted_usage == []


# -- older saved trips ------------------------------------------------------------------------------


def test_an_older_saved_trip_is_deleted_like_any_other(client: TestClient) -> None:
    """Trips saved by earlier versions: no branch, revision or job rows at all, a request beyond
    today's size limits, and a stored document with fields this version does not know."""
    owner_id = _user_id(client)
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Oldtown",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 15),  # 46 days: far over MAX_TRIP_DAYS
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
            interests=[f"interest {index}" for index in range(60)],
        )
    )
    trip_repository.create(state.trip_id, owner_id=owner_id)
    planning_state_repository.save(state)
    assert _stored_for(state.trip_id) == {**_NOTHING, "trips": 1, "planning_states": 1}

    assert client.delete(f"/trips/{state.trip_id}").status_code == 200
    assert _stored_for(state.trip_id) == _NOTHING


def test_a_trip_row_without_a_planning_state_can_still_be_deleted(client: TestClient) -> None:
    orphan_id = f"trip_{uuid.uuid4().hex}"
    trip_repository.create(orphan_id, owner_id=_user_id(client))
    assert client.delete(f"/trips/{orphan_id}").status_code == 200
    assert orphan_id not in trip_repository._trips


# == deleting an account ============================================================================


def test_account_deletion_removes_the_account_and_every_trip() -> None:
    browser, email = _account()
    user_id = _user_id(browser)
    trips = [_full_trip(browser) for _ in range(3)]

    response = _delete_account(browser)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True and body["message"] == "Your account and trips were deleted."
    assert body["data"] == {"deleted": True, "trips_deleted": 3}
    for trip_id in trips:
        assert _stored_for(trip_id) == _NOTHING
    assert user_repository.get_by_user_id(user_id) is None and user_repository.get_by_email(email) is None
    assert not any(record.owner_id == user_id for record in trip_repository._trips.values())
    assert not any(job.owner_id == user_id for job in job_repository._jobs.values())


def test_account_deletion_clears_the_session_cookie_and_ends_every_session() -> None:
    browser, email = _account()
    cookie_name = get_settings().session_cookie_name
    token = browser.cookies.get(cookie_name)
    other_tab = TestClient(app)
    other_tab.cookies.set(cookie_name, token)  # the same session, somewhere else
    assert other_tab.get("/auth/me").status_code == 200

    response = _delete_account(browser)

    set_cookie = response.headers["set-cookie"].lower()
    assert set_cookie.startswith(f'{cookie_name}="";') and "max-age=0" in set_cookie
    assert browser.get("/auth/me").status_code == 401
    # a copy of the session that was not cleared is useless: its user no longer exists
    for request in (
        lambda: other_tab.get("/auth/me"),
        lambda: other_tab.get("/trips"),
        lambda: other_tab.post("/trips", json=create_trip_payload()),
    ):
        refused = request()
        assert refused.status_code == 401
        assert refused.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


def test_the_deleted_account_can_no_longer_sign_in() -> None:
    browser, email = _account()
    assert _delete_account(browser).status_code == 200

    attempt = TestClient(app).post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert attempt.status_code == 401
    assert attempt.json()["errors"][0]["code"] == "INVALID_CREDENTIALS"  # exactly as for an unknown email
    assert "set-cookie" not in attempt.headers


def test_the_email_can_register_again_as_a_new_empty_account() -> None:
    browser, email = _account()
    old_user_id = _user_id(browser)
    _full_trip(browser)
    assert _delete_account(browser).status_code == 200

    again = TestClient(app)
    assert again.post("/auth/signup", json={"email": email, "password": _PASSWORD}).status_code == 201
    assert _user_id(again) != old_user_id
    assert again.get("/trips").json()["data"]["trips"] == []  # nothing of the old account comes back


def test_account_deletion_requires_a_session() -> None:
    response = TestClient(app).post("/auth/delete-account", json={"password": _PASSWORD, "confirm": True})
    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


def test_a_session_alone_is_not_enough_the_current_password_is_required() -> None:
    browser, email = _account()
    user_id = _user_id(browser)
    trip_id = _full_trip(browser)

    wrong = _delete_account(browser, password="not-the-password")

    assert wrong.status_code == 401
    assert wrong.json()["errors"][0]["code"] == "INVALID_CREDENTIALS"
    assert "set-cookie" not in wrong.headers  # the session is untouched
    assert user_repository.get_by_user_id(user_id) is not None
    assert _stored_for(trip_id)["trips"] == 1
    assert browser.get("/auth/me").status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"password": _PASSWORD},  # no confirmation
        {"password": _PASSWORD, "confirm": False},
        {"password": _PASSWORD, "confirm": "yes"},
        {"confirm": True},  # no password
        {"password": "", "confirm": True},
        {},
    ],
)
def test_without_an_explicit_confirmation_and_password_nothing_is_deleted(body: dict) -> None:
    browser, email = _account()
    user_id = _user_id(browser)
    response = browser.post("/auth/delete-account", json=body)
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "VALIDATION_ERROR"
    assert user_repository.get_by_user_id(user_id) is not None


def test_wrong_passwords_are_counted_by_the_sign_in_attempt_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, LOGIN_MAX_FAILURES_PER_ACCOUNT="2")
    browser, email = _account()
    user_id = _user_id(browser)
    assert _delete_account(browser, password="wrong-1").status_code == 401
    assert _delete_account(browser, password="wrong-2").status_code == 401

    locked = _delete_account(browser)  # even the right password, while locked
    assert locked.status_code == 429
    assert locked.json()["errors"][0]["code"] == "TOO_MANY_ATTEMPTS"
    assert user_repository.get_by_user_id(user_id) is not None


@pytest.mark.parametrize("running", [False, True], ids=["queued", "running"])
def test_an_account_with_a_job_in_progress_is_not_deleted(running: bool) -> None:
    browser, email = _account()
    user_id = _user_id(browser)
    idle, busy = _full_trip(browser), _full_trip(browser)
    job = _active_job(busy, user_id, running=running)
    before = (_stored_for(idle), _stored_for(busy))

    response = _delete_account(browser)

    assert response.status_code == 409
    error = response.json()["errors"][0]
    assert error["code"] == "ACCOUNT_DELETION_BLOCKED" and "Nothing was deleted" in error["message"]
    assert "set-cookie" not in response.headers
    assert (_stored_for(idle), _stored_for(busy)) == before  # not even the idle trip
    assert user_repository.get_by_user_id(user_id) is not None
    assert browser.get("/auth/me").status_code == 200
    assert job_repository.get_by_job_id(job.job_id).status in (GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)

    mark_job_succeeded(job, result_version="v1", changed_sections=[])
    assert _delete_account(browser).status_code == 200


def test_account_deletion_does_not_touch_anyone_else() -> None:
    leaving, _ = _account()
    staying, staying_email = _account()
    staying_trips = [_full_trip(staying) for _ in range(2)]
    _full_trip(leaving)
    snapshot = {trip_id: _stored_for(trip_id) for trip_id in staying_trips}
    plans = {trip_id: staying.get(f"/trips/{trip_id}").json()["data"] for trip_id in staying_trips}

    assert _delete_account(leaving).status_code == 200

    assert {trip_id: _stored_for(trip_id) for trip_id in staying_trips} == snapshot
    assert {trip_id: staying.get(f"/trips/{trip_id}").json()["data"] for trip_id in staying_trips} == plans
    assert staying.get("/auth/me").json()["data"]["user"]["email"] == staying_email
    assert TestClient(app).post("/auth/login", json={"email": staying_email, "password": _PASSWORD}).status_code == 200


def test_an_account_with_no_trips_can_be_deleted() -> None:
    browser, email = _account()
    response = _delete_account(browser)
    assert response.status_code == 200 and response.json()["data"]["trips_deleted"] == 0


def test_the_accounts_failed_sign_in_counters_are_removed_and_nothing_else() -> None:
    leaving, leaving_email = _account()
    staying, staying_email = _account()
    stranger = TestClient(app)
    for email in (leaving_email, staying_email):
        assert stranger.post("/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
    signups_before = sum(1 for attempt in auth_attempt_repository._attempts if attempt.kind == SIGNUP)

    assert _delete_account(leaving).status_code == 200

    failures = {attempt.subject_hash for attempt in auth_attempt_repository._attempts if attempt.kind == LOGIN_FAILURE}
    assert failures == {abuse.login_failure_key(staying_email)}
    # signup counters refer to no account, so they stay: deleting an account does not buy a signup back
    assert sum(1 for attempt in auth_attempt_repository._attempts if attempt.kind == SIGNUP) == signups_before


def test_deleting_an_account_does_not_hand_back_the_deployments_generation_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true", GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW="2")
    browser, email = _account()
    for _ in range(2):
        assert browser.post(f"/trips/{_new_trip(browser)}/generate").status_code == 202
    assert _delete_account(browser).status_code == 200
    assert job_repository._jobs == {}

    newcomer = new_authenticated_client()
    refused = newcomer.post(f"/trips/{_new_trip(newcomer)}/generate")
    assert refused.status_code == 429
    assert refused.json()["errors"][0]["code"] == "GENERATION_GLOBAL_LIMIT_REACHED"
    # what remains of the deleted account's jobs refers to nobody
    assert [owner for owner, _ in job_repository._deleted_usage] == [None, None]


def test_usage_kept_from_an_earlier_trip_deletion_is_anonymised_with_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true")
    browser, email = _account()
    user_id = _user_id(browser)
    trip_id = _new_trip(browser)
    assert browser.post(f"/trips/{trip_id}/generate").status_code == 202
    assert browser.delete(f"/trips/{trip_id}").status_code == 200
    assert [owner for owner, _ in job_repository._deleted_usage] == [user_id]

    assert _delete_account(browser).status_code == 200
    assert [owner for owner, _ in job_repository._deleted_usage] == [None]


def test_nothing_that_identifies_the_account_is_left_in_any_store() -> None:
    browser, email = _account()
    user_id = _user_id(browser)
    trips = [_full_trip(browser) for _ in range(2)]
    TestClient(app).post("/auth/login", json={"email": email, "password": "wrong"})
    assert _delete_account(browser).status_code == 200

    import json

    stores = json.dumps(
        {
            "users": [user.model_dump(mode="json") for user in user_repository._users.values()],
            "trips": [trip.model_dump(mode="json") for trip in trip_repository._trips.values()],
            "states": [state.model_dump(mode="json") for state in planning_state_repository._states.values()],
            "jobs": [job.model_dump(mode="json") for job in job_repository._jobs.values()],
            "branches": [b.model_dump(mode="json") for b in itinerary_lineage_repository._branches.values()],
            "revisions": [r.model_dump(mode="json") for r in itinerary_lineage_repository._revisions.values()],
            "usage": [str(usage) for usage in job_repository._deleted_usage],
            "attempts": [str(attempt) for attempt in auth_attempt_repository._attempts],
        }
    )
    for identifier in (user_id, email, abuse.login_failure_key(email), *trips):
        assert identifier not in stores


def test_account_deletion_is_logged_without_the_email_or_password(caplog: pytest.LogCaptureFixture) -> None:
    browser, email = _account()
    with caplog.at_level("INFO", logger="app"):
        _delete_account(browser, password="wrong-password")
        _delete_account(browser)
    events = [(r.auth_event, r.status) for r in caplog.records if getattr(r, "auth_event", None) == "account_delete"]
    assert events == [("account_delete", "rejected"), ("account_delete", "succeeded")]
    assert email not in caplog.text and _PASSWORD not in caplog.text and "wrong-password" not in caplog.text


# == a generation request that loses to a deletion ===================================================


@pytest.mark.usefixtures("synthetic_legacy_regeneration_support")
def test_a_job_for_a_trip_deleted_under_the_request_is_a_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repository reports "the trip is gone" when a deletion won the race between the ownership
    check and the admission (reproduced for real against PostgreSQL in the integration tests). The API
    answers the ordinary 404 -- not a 409, not a 503 -- for generation and for regeneration, schedules
    no background work and calls nothing."""
    from app.repositories.errors import TripNoLongerExistsError
    from app.services.planning_orchestrator import planning_orchestrator

    trip_id = _new_trip(client)
    assert client.post(f"/trips/{trip_id}/generate").status_code == 200
    assert client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Make it less packed"}).status_code == 200
    # Final Batch 2: /generate admits only a trip without a plan, so its race is shown on one of those.
    unplanned_trip_id = _new_trip(client)
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true")

    def gone(job, policy, now):
        raise TripNoLongerExistsError(job.trip_id)

    monkeypatch.setattr(job_repository, "admit", gone)
    work: list[str] = []
    monkeypatch.setattr(planning_orchestrator, "generate_full_plan_via_langgraph", lambda trip: work.append("plan"))
    monkeypatch.setattr(planning_orchestrator, "rerun_affected_stages", lambda *a, **k: work.append("rerun"))

    for trip_id, response in (
        (unplanned_trip_id, client.post(f"/trips/{unplanned_trip_id}/generate")),
        (trip_id, client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})),
    ):
        assert response.status_code == 404, response.text
        assert response.json()["errors"] == [
            {"code": "TRIP_NOT_FOUND", "field": "trip_id", "message": f"Trip '{trip_id}' was not found."}
        ]
    assert work == [] and job_repository._jobs == {}


# == the service ====================================================================================


def test_the_service_refuses_a_trip_that_is_not_the_owners() -> None:
    owner, _ = _account()
    other, _ = _account()
    trip_id = _new_trip(owner)
    from app.repositories.errors import NothingToDeleteError

    with pytest.raises(NothingToDeleteError):
        data_deletion_service.delete_trip(trip_id, owner_id=_user_id(other))
    with pytest.raises(NothingToDeleteError):
        data_deletion_service.delete_trip("trip_unknown", owner_id=_user_id(owner))
    assert _stored_for(trip_id)["trips"] == 1


def test_the_summary_reports_what_was_removed(client: TestClient) -> None:
    trip_id = _full_trip(client)
    before = _stored_for(trip_id)
    summary = data_deletion_service.delete_trip(trip_id, owner_id=_user_id(client))
    assert (summary.trips, summary.planning_states, summary.jobs, summary.branches, summary.revisions) == (
        before["trips"],
        before["planning_states"],
        before["jobs"],
        before["branches"],
        before["revisions"],
    )
    assert summary.account is False
