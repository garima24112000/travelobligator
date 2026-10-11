from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.models.generation_job import (
    GenerationJobStatus,
    GenerationJobType,
    create_queued_job,
    mark_job_failed,
    mark_job_interrupted,
    mark_job_succeeded,
)
from app.providers.gateway import provider_gateway
from app.repositories.admission import AdmissionPolicy, AdmissionRefusal, AdmissionRefusedError
from app.repositories.job_repository import job_repository
from app.services import generation_job_service
from app.services.planning_orchestrator import planning_orchestrator
from app.tests.conftest import create_trip_payload, new_authenticated_client

# P0 generation admission limits, hermetic (Local JSON job repository -- the same `admit` contract the
# PostgreSQL repository implements; the real-database races are in
# tests/repositories/test_postgres_admission_p0_integration.py).
#
# `TestClient` runs a request's background task before the call returns, so a job started through the
# API here is already finished (never occupying a concurrent slot) unless a test stores one itself.

NOW = datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc)
DAY = 86400


def _env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture()
def jobs_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true")


def _owner_id(client: TestClient) -> str:
    return client.get("/auth/me").json()["data"]["user"]["user_id"]


def _new_trip(client: TestClient) -> str:
    response = client.post("/trips", json=create_trip_payload())
    assert response.status_code == 201, response.text
    return response.json()["data"]["trip_id"]


def _generate(client: TestClient) -> tuple[str, object]:
    trip_id = _new_trip(client)
    return trip_id, client.post(f"/trips/{trip_id}/generate")


def _refused(response, code: str) -> dict:
    assert response.status_code == 429, response.text
    body = response.json()
    assert body["success"] is False and body["data"] is None
    assert [error["code"] for error in body["errors"]] == [code]
    return body["errors"][0]


def _stored_job(*, owner_id: str = "user_other", trip_id: str | None = None, created_at: datetime | None = None):
    job = create_queued_job(
        trip_id=trip_id or f"trip_{len(job_repository._jobs)}", owner_id=owner_id, job_type=GenerationJobType.GENERATE
    )
    if created_at is not None:
        job.created_at = created_at
    job_repository._jobs[job.job_id] = job
    return job


def _policy(user: int = 0, total: int = 0, concurrent: int = 0, window: int = DAY) -> AdmissionPolicy:
    return AdmissionPolicy(
        max_jobs_per_user_per_window=user,
        max_jobs_global_per_window=total,
        max_concurrent_jobs=concurrent,
        window_seconds=window,
    )


# -- configuration ----------------------------------------------------------------------------------


def test_the_approved_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.generation_max_jobs_per_user_per_window == 5
    assert settings.generation_max_jobs_global_per_window == 25
    assert settings.generation_max_concurrent_jobs == 2
    assert settings.generation_limit_window_seconds == 86400


@pytest.mark.parametrize(
    "variable, value",
    [
        ("GENERATION_MAX_JOBS_PER_USER_PER_WINDOW", "-1"),
        ("GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW", "-1"),
        ("GENERATION_MAX_CONCURRENT_JOBS", "101"),
        ("GENERATION_LIMIT_WINDOW_SECONDS", "59"),
        ("GENERATION_MAX_CONCURRENT_JOBS", "two"),
    ],
)
def test_an_invalid_limit_is_rejected_when_settings_load(variable: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{variable: value})


def test_the_service_policy_is_the_configured_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="3",
        GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW="9",
        GENERATION_MAX_CONCURRENT_JOBS="4",
        GENERATION_LIMIT_WINDOW_SECONDS="3600",
    )
    assert generation_job_service.admission_policy() == _policy(user=3, total=9, concurrent=4, window=3600)


# -- the decision -----------------------------------------------------------------------------------


def test_a_limit_is_reached_at_the_limit_not_after_it() -> None:
    policy = _policy(user=5, total=25, concurrent=2)
    assert policy.refusal(user_jobs_in_window=4, global_jobs_in_window=24, active_jobs=1) is None
    assert policy.refusal(user_jobs_in_window=5, global_jobs_in_window=5, active_jobs=0) is AdmissionRefusal.USER_WINDOW
    assert policy.refusal(user_jobs_in_window=0, global_jobs_in_window=25, active_jobs=0) is AdmissionRefusal.GLOBAL_WINDOW
    assert policy.refusal(user_jobs_in_window=0, global_jobs_in_window=0, active_jobs=2) is AdmissionRefusal.CONCURRENT


def test_the_most_personal_limit_is_reported_first() -> None:
    policy = _policy(user=1, total=1, concurrent=1)
    everything = dict(user_jobs_in_window=9, global_jobs_in_window=9, active_jobs=9)
    assert policy.refusal(**everything) is AdmissionRefusal.USER_WINDOW
    assert _policy(total=1, concurrent=1).refusal(**everything) is AdmissionRefusal.GLOBAL_WINDOW
    assert _policy(concurrent=1).refusal(**everything) is AdmissionRefusal.CONCURRENT


def test_zero_means_not_enforced() -> None:
    assert _policy().refusal(user_jobs_in_window=10**6, global_jobs_in_window=10**6, active_jobs=10**6) is None


# -- rolling window ---------------------------------------------------------------------------------


def _admit(owner_id: str, policy: AdmissionPolicy, now: datetime = NOW):
    job = create_queued_job(trip_id=f"trip_new_{len(job_repository._jobs)}", owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    job.created_at = now
    return job_repository.admit(job, policy, now=now)


def test_a_job_exactly_one_window_old_no_longer_counts() -> None:
    policy = _policy(user=1)
    _stored_job(owner_id="user_a", created_at=NOW - timedelta(seconds=DAY))
    _admit("user_a", policy)  # the old job left the window at this very instant


def test_a_job_one_second_inside_the_window_still_counts() -> None:
    policy = _policy(user=1)
    _stored_job(owner_id="user_a", created_at=NOW - timedelta(seconds=DAY - 1))
    with pytest.raises(AdmissionRefusedError) as refused:
        _admit("user_a", policy)
    assert refused.value.reason is AdmissionRefusal.USER_WINDOW
    # one second later it has left the window
    _admit("user_a", policy, now=NOW + timedelta(seconds=1))


def test_the_window_rolls_it_does_not_reset_on_a_calendar_boundary() -> None:
    policy = _policy(user=2)
    _stored_job(owner_id="user_a", created_at=NOW - timedelta(hours=23))
    _stored_job(owner_id="user_a", created_at=NOW - timedelta(hours=2))
    with pytest.raises(AdmissionRefusedError):
        _admit("user_a", policy)
    # 61 minutes later only the first job has left: exactly one more is admitted
    later = NOW + timedelta(minutes=61)
    _admit("user_a", policy, now=later)
    with pytest.raises(AdmissionRefusedError):
        _admit("user_a", policy, now=later)


def test_the_window_length_is_configurable() -> None:
    hour = _policy(user=1, window=3600)
    _stored_job(owner_id="user_a", created_at=NOW - timedelta(minutes=59))
    with pytest.raises(AdmissionRefusedError):
        _admit("user_a", hour)
    _admit("user_a", hour, now=NOW + timedelta(minutes=2))


def test_a_refused_admission_stores_nothing() -> None:
    _stored_job(owner_id="user_a", created_at=NOW)
    before = dict(job_repository._jobs)
    with pytest.raises(AdmissionRefusedError):
        _admit("user_a", _policy(user=1))
    assert job_repository._jobs == before


# -- per-user limit ---------------------------------------------------------------------------------


def test_a_user_gets_five_generations_per_window_by_default(jobs_enabled: None, client: TestClient) -> None:
    for _ in range(5):
        assert _generate(client)[1].status_code == 202

    trip_id, response = _generate(client)
    error = _refused(response, "GENERATION_USER_LIMIT_REACHED")
    assert error["message"] == (
        "You have reached the limit of 5 itineraries per 24 hours. Please try again later."
    )
    assert job_repository.list_by_trip_id(trip_id) == []  # no job, queued or otherwise
    assert len(job_repository._jobs) == 5


def test_the_per_user_limit_does_not_touch_another_user(
    jobs_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="2")
    assert _generate(client)[1].status_code == 202
    assert _generate(client)[1].status_code == 202
    _refused(_generate(client)[1], "GENERATION_USER_LIMIT_REACHED")

    other = new_authenticated_client()
    assert _generate(other)[1].status_code == 202
    assert _generate(other)[1].status_code == 202
    _refused(_generate(other)[1], "GENERATION_USER_LIMIT_REACHED")


def test_generating_one_trip_again_counts_again(
    jobs_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="2")
    trip_id = _new_trip(client)
    assert client.post(f"/trips/{trip_id}/generate").status_code == 202
    assert client.post(f"/trips/{trip_id}/generate").status_code == 202
    _refused(client.post(f"/trips/{trip_id}/generate"), "GENERATION_USER_LIMIT_REACHED")


def test_the_user_is_admitted_again_once_a_job_leaves_the_window(
    jobs_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="1")
    trip_id, first = _generate(client)
    assert first.status_code == 202
    _refused(_generate(client)[1], "GENERATION_USER_LIMIT_REACHED")

    job = job_repository.list_by_trip_id(trip_id)[0]
    job.created_at = datetime.now(timezone.utc) - timedelta(seconds=DAY + 5)
    assert _generate(client)[1].status_code == 202


# -- global limit -----------------------------------------------------------------------------------


def test_the_global_limit_stops_everyone(
    jobs_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW="3", GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="2")
    assert _generate(client)[1].status_code == 202
    second_user = new_authenticated_client()
    assert _generate(second_user)[1].status_code == 202
    assert _generate(second_user)[1].status_code == 202

    # the deployment's allowance is used up: a user with allowance left, and a brand-new user, are refused
    error = _refused(_generate(client)[1], "GENERATION_GLOBAL_LIMIT_REACHED")
    assert "Your trip is saved" in error["message"]
    _refused(_generate(new_authenticated_client())[1], "GENERATION_GLOBAL_LIMIT_REACHED")
    assert len(job_repository._jobs) == 3


def test_more_accounts_do_not_buy_more_generations(
    jobs_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-user limits can be multiplied by opening accounts; the global limit cannot."""
    _env(monkeypatch, GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW="4", GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="1")
    admitted = 0
    for _ in range(8):
        account = new_authenticated_client()
        admitted += _generate(account)[1].status_code == 202
    assert admitted == 4
    assert len(job_repository._jobs) == 4


def test_the_default_global_limit_is_twenty_five(jobs_enabled: None, client: TestClient) -> None:
    for index in range(25):
        _stored_job(owner_id=f"user_{index}", created_at=datetime.now(timezone.utc) - timedelta(hours=1))
        job_repository._jobs[list(job_repository._jobs)[-1]].status = GenerationJobStatus.SUCCEEDED
    _refused(_generate(client)[1], "GENERATION_GLOBAL_LIMIT_REACHED")


# -- concurrent limit -------------------------------------------------------------------------------


def _running(owner_id: str = "user_other"):
    job = _stored_job(owner_id=owner_id)
    return job_repository.claim(job.job_id, "inst-other", 120)


def test_two_jobs_may_run_at_once_and_a_third_is_refused(jobs_enabled: None, client: TestClient) -> None:
    _running()
    _stored_job()  # queued counts as occupying a slot too
    trip_id, response = _generate(client)
    error = _refused(response, "GENERATION_CAPACITY_REACHED")
    assert "try again in a minute" in error["message"]
    assert job_repository.list_by_trip_id(trip_id) == []


def test_a_slot_is_free_again_when_a_job_finishes(jobs_enabled: None, client: TestClient) -> None:
    first, second = _running(), _running()
    _refused(_generate(client)[1], "GENERATION_CAPACITY_REACHED")
    mark_job_succeeded(first, result_version="v1", changed_sections=[])
    assert _generate(client)[1].status_code == 202
    assert second.status is GenerationJobStatus.RUNNING


def test_the_concurrent_limit_is_configurable(
    jobs_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, GENERATION_MAX_CONCURRENT_JOBS="1")
    _running()
    _refused(_generate(client)[1], "GENERATION_CAPACITY_REACHED")
    _env(monkeypatch, GENERATION_MAX_CONCURRENT_JOBS="3")
    assert _generate(client)[1].status_code == 202


def test_a_capacity_refusal_does_not_use_up_the_users_allowance(
    jobs_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="1")
    busy = [_running(), _running()]
    for _ in range(3):
        _refused(_generate(client)[1], "GENERATION_CAPACITY_REACHED")
    for job in busy:
        mark_job_succeeded(job, result_version="v1", changed_sections=[])
    assert _generate(client)[1].status_code == 202  # the refusals above counted for nothing


# -- failed / interrupted jobs count ----------------------------------------------------------------


def test_a_failed_generation_still_counts_in_the_window(
    jobs_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="2")

    def _fail(trip_id: str):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(planning_orchestrator, "generate_full_plan_via_langgraph", _fail)
    for _ in range(2):
        trip_id, response = _generate(client)
        assert response.status_code == 202
        assert job_repository.list_by_trip_id(trip_id)[0].status is GenerationJobStatus.FAILED
    _refused(_generate(client)[1], "GENERATION_USER_LIMIT_REACHED")


def test_an_interrupted_generation_still_counts_in_the_window_but_frees_its_slot(
    jobs_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="2", GENERATION_MAX_CONCURRENT_JOBS="1")
    owner_id = _owner_id(client)
    interrupted = mark_job_interrupted(_running(owner_id))
    failed = mark_job_failed(_running(owner_id), error_code="STAGE_FAILED", error_message="failed")
    assert {interrupted.status, failed.status} == {GenerationJobStatus.FAILED}
    # neither holds the single concurrent slot, both count against the user's two per window
    _refused(_generate(client)[1], "GENERATION_USER_LIMIT_REACHED")
    assert _generate(new_authenticated_client())[1].status_code == 202


# -- stale / abandoned jobs do not keep a slot ------------------------------------------------------


def test_an_abandoned_running_job_does_not_block_admission(jobs_enabled: None, client: TestClient) -> None:
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=get_settings().generation_job_stale_after_seconds + 60)
    abandoned = [_running(), _running()]
    for job in abandoned:
        job.started_at = long_ago
        job.created_at = long_ago

    assert _generate(client)[1].status_code == 202
    for job in abandoned:
        stored = job_repository.get_by_job_id(job.job_id)
        assert stored.status is GenerationJobStatus.FAILED and stored.error_code == "JOB_INTERRUPTED"


def test_a_never_claimed_queued_job_does_not_block_admission_forever(
    jobs_enabled: None, client: TestClient
) -> None:
    stale = datetime.now(timezone.utc) - timedelta(seconds=get_settings().generation_job_stale_after_seconds + 60)
    orphans = [_stored_job(created_at=stale), _stored_job(created_at=stale)]
    assert _generate(client)[1].status_code == 202
    assert all(job_repository.get_by_job_id(job.job_id).error_code == "JOB_INTERRUPTED" for job in orphans)


def test_a_healthy_running_job_is_never_closed_to_make_room(jobs_enabled: None, client: TestClient) -> None:
    healthy = [_running(), _running()]
    _refused(_generate(client)[1], "GENERATION_CAPACITY_REACHED")
    assert all(job_repository.get_by_job_id(job.job_id).status is GenerationJobStatus.RUNNING for job in healthy)


# -- per-trip duplicate protection is unchanged ------------------------------------------------------


def test_a_second_job_for_the_same_trip_is_still_a_409(jobs_enabled: None, client: TestClient) -> None:
    trip_id = _new_trip(client)
    job_repository._jobs.clear()
    _stored_job(owner_id=_owner_id(client), trip_id=trip_id)
    response = client.post(f"/trips/{trip_id}/generate")
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "JOB_ALREADY_RUNNING"
    assert len(job_repository.list_by_trip_id(trip_id)) == 1


# -- regeneration is admitted the same way ----------------------------------------------------------


def _generated_trip_with_feedback(client: TestClient) -> str:
    trip_id = _new_trip(client)
    assert client.post(f"/trips/{trip_id}/generate").status_code == 200  # synchronous: no job
    feedback = client.post(
        f"/trips/{trip_id}/feedback", json={"feedback_text": "Make this less packed, it's too much walking"}
    )
    assert feedback.status_code == 200
    return trip_id


@pytest.mark.usefixtures("synthetic_legacy_regeneration_support")
def test_a_regeneration_counts_like_a_generation(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    trip_id = _generated_trip_with_feedback(client)
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true", GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="2")

    regenerate = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})
    assert regenerate.status_code == 202
    assert job_repository.list_by_trip_id(trip_id)[0].job_type is GenerationJobType.REGENERATE

    assert _generate(client)[1].status_code == 202  # regeneration + generation = the user's two
    _refused(_generate(client)[1], "GENERATION_USER_LIMIT_REACHED")


@pytest.mark.usefixtures("synthetic_legacy_regeneration_support")
def test_a_refused_regeneration_changes_nothing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    trip_id = _generated_trip_with_feedback(client)
    before = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true", GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="1")
    _stored_job(owner_id=_owner_id(client))  # the user's one job of the window

    refused = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})
    _refused(refused, "GENERATION_USER_LIMIT_REACHED")

    after = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    assert job_repository.list_by_trip_id(trip_id) == []
    assert after["metadata"]["current_version"] == before["metadata"]["current_version"]
    assert after["version_history"] == before["version_history"]
    assert after["regeneration_attempts"] == before["regeneration_attempts"]
    assert [event["applied_at"] for event in after["feedback_history"]] == [None]  # still pending


@pytest.mark.parametrize(
    "limits, code",
    [
        ({"GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW": "1"}, "GENERATION_GLOBAL_LIMIT_REACHED"),
        ({"GENERATION_MAX_CONCURRENT_JOBS": "1"}, "GENERATION_CAPACITY_REACHED"),
    ],
)
@pytest.mark.usefixtures("synthetic_legacy_regeneration_support")
def test_regeneration_obeys_the_global_and_concurrent_limits(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, limits: dict, code: str
) -> None:
    trip_id = _generated_trip_with_feedback(client)
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true", **limits)
    _stored_job()  # someone else's queued job, created now
    _refused(client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True}), code)
    assert job_repository.list_by_trip_id(trip_id) == []


def test_a_targeted_regeneration_job_is_admitted_the_same_way(
    jobs_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="1")
    owner_id = _owner_id(client)
    trip_id = _new_trip(client)
    _stored_job(owner_id=owner_id)

    tasks = BackgroundTasks()
    with pytest.raises(AppError) as refused:
        generation_job_service.start_targeted_regenerate_job(
            trip_id=trip_id, owner_id=owner_id, background_tasks=tasks
        )
    assert refused.value.status_code == 429
    assert refused.value.code.value == "GENERATION_USER_LIMIT_REACHED"
    assert tasks.tasks == [] and job_repository.list_by_trip_id(trip_id) == []


def test_every_job_starter_goes_through_the_one_admission_function() -> None:
    import inspect

    for starter in (
        generation_job_service.start_generate_job,
        generation_job_service.start_regenerate_job,
        generation_job_service.start_targeted_regenerate_job,
    ):
        source = inspect.getsource(starter)
        assert "_create_job_or_conflict(job)" in source
        assert source.index("_create_job_or_conflict(job)") < source.index("background_tasks.add_task")
    module = inspect.getsource(generation_job_service)
    assert module.count(".admit(") == 1
    # no path creates a job without the admission decision
    assert "get_job_repository().create(" not in module


# -- refused before any expensive work --------------------------------------------------------------


class _SpyPlaces:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name: str):
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def counted(*args, **kwargs):
            self.calls += 1
            return attribute(*args, **kwargs)

        return counted


@pytest.mark.parametrize(
    "limits, code",
    [
        ({"GENERATION_MAX_JOBS_PER_USER_PER_WINDOW": "1"}, "GENERATION_USER_LIMIT_REACHED"),
        ({"GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW": "1"}, "GENERATION_GLOBAL_LIMIT_REACHED"),
        ({"GENERATION_MAX_CONCURRENT_JOBS": "1"}, "GENERATION_CAPACITY_REACHED"),
    ],
)
def test_a_refused_request_never_reaches_the_planner_or_a_provider(
    jobs_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch, limits: dict, code: str
) -> None:
    _env(monkeypatch, **limits)
    trip_id = _new_trip(client)
    _stored_job(owner_id=_owner_id(client))  # uses the one slot of whichever limit is 1

    planner_calls: list[str] = []
    monkeypatch.setattr(
        planning_orchestrator, "generate_full_plan_via_langgraph", lambda trip: planner_calls.append(trip)
    )
    monkeypatch.setattr(planning_orchestrator, "generate_full_plan", lambda trip: planner_calls.append(trip))
    spy = _SpyPlaces(provider_gateway.places)
    monkeypatch.setattr(provider_gateway, "places", spy)
    before = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]

    _refused(client.post(f"/trips/{trip_id}/generate"), code)

    assert planner_calls == [] and spy.calls == 0
    after = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    assert after == before  # no stage ran, no progress was recorded, the trip is untouched
    assert job_repository.list_by_trip_id(trip_id) == []


def test_the_synchronous_development_path_creates_no_job_and_is_not_limited(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documented scope: the limits are enforced where a JOB is created. Production must therefore run
    generation as jobs -- see `test_production_requires_generation_to_run_as_jobs`."""
    _env(monkeypatch, GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="1")
    for _ in range(2):
        trip_id = _new_trip(client)
        assert client.post(f"/trips/{trip_id}/generate").status_code == 200
    assert job_repository._jobs == {}


# -- simultaneous admissions ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "policy, same_owner",
    [(_policy(total=3), False), (_policy(user=3), True), (_policy(concurrent=3), False)],
    ids=["global", "per-user", "concurrent"],
)
def test_simultaneous_admissions_never_exceed_a_limit(policy: AdmissionPolicy, same_owner: bool) -> None:
    workers = 16
    barrier = threading.Barrier(workers)
    outcomes: list[str] = []
    guard = threading.Lock()

    def attempt(index: int) -> None:
        job = create_queued_job(
            trip_id=f"trip_race_{index}",
            owner_id="user_race" if same_owner else f"user_race_{index}",
            job_type=GenerationJobType.GENERATE,
        )
        barrier.wait(timeout=10)
        try:
            job_repository.admit(job, policy, now=job.created_at)
            outcome = "admitted"
        except AdmissionRefusedError:
            outcome = "refused"
        with guard:
            outcomes.append(outcome)

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert outcomes.count("admitted") == 3 and outcomes.count("refused") == workers - 3
    assert len(job_repository._jobs) == 3
