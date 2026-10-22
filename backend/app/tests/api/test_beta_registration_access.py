from __future__ import annotations

import ast
import inspect
import logging
import threading
import uuid
from http.cookies import SimpleCookie

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from app.auth import registration
from app.auth.sessions import create_session_token
from app.core.config import Settings, get_settings
from app.core.operational_config import _production_registration_problems
from app.main import app
from app.repositories.job_repository import job_repository
from app.repositories.user_repository import user_repository
from app.services import generation_job_service
from app.services.planning_orchestrator import planning_orchestrator
from app.tests.conftest import create_trip_payload, new_authenticated_client

# Limited beta: the shareable invitation link (`app/auth/registration.py`) and the owner's emergency
# switches. Hermetic: in-process app, Local JSON storage, no provider. Token values below are
# throwaway test strings.

_TOKEN = "beta-test-token-" + "a" * 32
_OTHER_TOKEN = "beta-test-token-" + "b" * 32
_PASSWORD = "testpassword123"
_COOKIE = registration.REGISTRATION_COOKIE_NAME


def _env(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> None:
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture()
def invite_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, SIGNUP_MODE="invite_link", BETA_REGISTRATION_TOKEN=_TOKEN)


@pytest.fixture()
def http() -> TestClient:
    return TestClient(app)


def _email() -> str:
    return f"beta-{uuid.uuid4().hex}@example.com"


def _signup(client: TestClient, email: str | None = None):
    return client.post("/auth/signup", json={"email": email or _email(), "password": _PASSWORD})


def _exchange(client: TestClient, token: str = _TOKEN):
    return client.post("/auth/registration-access", json={"token": token})


def _error_code(response) -> str:
    body = response.json()
    assert body["success"] is False and body["data"] is None
    return body["errors"][0]["code"]


def _set_cookies(response) -> SimpleCookie:
    cookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookie.load(header)
    return cookie


def _users() -> int:
    return len(user_repository._users)


# -- open mode is unchanged (development default) ---------------------------------------------------


def test_the_defaults_keep_open_registration_and_generation_on() -> None:
    settings = Settings(_env_file=None)
    assert (settings.signup_mode, settings.beta_registration_token) == ("open", None)
    assert settings.registration_access_ttl_seconds == 1800 and settings.generation_enabled is True


def test_open_mode_signs_up_without_a_link(http: TestClient) -> None:
    assert _signup(http).status_code == 201
    assert http.get("/auth/registration").json()["data"] == {"mode": "open", "authorized": True}


# -- the link ----------------------------------------------------------------------------------------


def test_without_the_link_no_account_can_be_created(http: TestClient, invite_mode: None) -> None:
    before = _users()
    response = _signup(http)
    assert response.status_code == 403 and _error_code(response) == "REGISTRATION_LINK_REQUIRED"
    assert "invitation link" in response.json()["errors"][0]["message"]
    assert _users() == before and "set-cookie" not in response.headers  # no account, no session
    assert http.get("/auth/registration").json()["data"] == {"mode": "invite_link", "authorized": False}


def test_a_valid_link_allows_exactly_this_browser_to_sign_up(http: TestClient, invite_mode: None) -> None:
    granted = _exchange(http)
    assert granted.status_code == 200
    assert granted.json()["data"] == {"authorized": True, "expires_in_seconds": 1800}
    assert http.get("/auth/registration").json()["data"] == {"mode": "invite_link", "authorized": True}

    created = _signup(http)
    assert created.status_code == 201, created.text
    cookies = _set_cookies(created)
    assert cookies[get_settings().session_cookie_name].value  # signed in
    assert cookies[_COOKIE]["max-age"] == "0"  # the authorization is used up in this browser
    http.cookies.delete(_COOKIE)

    # a second account from the same browser needs the link again
    second = _signup(http)
    assert second.status_code == 403 and _error_code(second) == "REGISTRATION_LINK_REQUIRED"
    # another browser without the link never could
    assert _error_code(_signup(TestClient(app))) == "REGISTRATION_LINK_REQUIRED"


@pytest.mark.parametrize("token", [_OTHER_TOKEN, _TOKEN[:-1], _TOKEN + "x", _TOKEN.upper(), " ", "a" * 256])
def test_any_other_token_is_refused_and_sets_nothing(http: TestClient, invite_mode: None, token: str) -> None:
    response = _exchange(http, token)
    assert response.status_code in (403, 422)
    if response.status_code == 403:
        assert _error_code(response) == "REGISTRATION_LINK_INVALID"
    assert "set-cookie" not in response.headers
    assert _error_code(_signup(http)) == "REGISTRATION_LINK_REQUIRED"


def test_an_oversized_or_missing_token_is_rejected_before_any_comparison(http: TestClient, invite_mode: None) -> None:
    assert http.post("/auth/registration-access", json={"token": "a" * 257}).status_code == 422
    assert http.post("/auth/registration-access", json={}).status_code == 422


# -- direct API bypass attempts ----------------------------------------------------------------------


def _forged(value: str) -> TestClient:
    client = TestClient(app)
    client.cookies.set(_COOKIE, value)
    return client


def test_forged_registration_cookies_are_refused(invite_mode: None) -> None:
    settings = get_settings()
    session_token = create_session_token("user_x", settings)
    wrong_salt = URLSafeTimedSerializer(settings.session_secret_key, salt="travelobligator-session").dumps(
        {"fp": registration._token_fingerprint(_TOKEN, settings)}
    )
    wrong_key = URLSafeTimedSerializer("not-the-secret-" + "k" * 32, salt="travelobligator-registration").dumps(
        {"fp": registration._token_fingerprint(_TOKEN, settings)}
    )
    other_fingerprint = URLSafeTimedSerializer(settings.session_secret_key, salt="travelobligator-registration").dumps(
        {"fp": registration._token_fingerprint(_OTHER_TOKEN, settings)}
    )
    for value in ("true", "1", _TOKEN, session_token, wrong_salt, wrong_key, other_fingerprint, ""):
        response = _signup(_forged(value))
        assert response.status_code == 403 and _error_code(response) == "REGISTRATION_LINK_REQUIRED", value


def test_a_session_cookie_is_not_a_registration_authorization(http: TestClient, invite_mode: None) -> None:
    signed_in = TestClient(app)
    signed_in.cookies.set(get_settings().session_cookie_name, create_session_token("user_x", get_settings()))
    assert _error_code(_signup(signed_in)) == "REGISTRATION_LINK_REQUIRED"


def test_an_expired_authorization_is_refused(monkeypatch: pytest.MonkeyPatch, invite_mode: None) -> None:
    import itsdangerous.timed as timed

    settings = get_settings()
    real_time = timed.time.time
    monkeypatch.setattr(timed.time, "time", lambda: real_time() - settings.registration_access_ttl_seconds - 5)
    stale = registration._serializer(settings).dumps({"fp": registration._token_fingerprint(_TOKEN, settings)})
    monkeypatch.setattr(timed.time, "time", real_time)
    assert _error_code(_signup(_forged(stale))) == "REGISTRATION_LINK_REQUIRED"


# -- rotation, revocation and the emergency switches --------------------------------------------------


def test_replacing_the_token_revokes_the_old_link_and_every_authorization(
    monkeypatch: pytest.MonkeyPatch, http: TestClient, invite_mode: None
) -> None:
    assert _exchange(http).status_code == 200  # this browser holds an authorization for the old token
    _env(monkeypatch, BETA_REGISTRATION_TOKEN=_OTHER_TOKEN)  # the owner rotates it (a restart)

    assert _error_code(_signup(http)) == "REGISTRATION_LINK_REQUIRED"  # the issued cookie no longer works
    assert _error_code(_exchange(http, _TOKEN)) == "REGISTRATION_LINK_INVALID"  # nor does the old link
    assert _exchange(http, _OTHER_TOKEN).status_code == 200  # the new link does
    assert _signup(http).status_code == 201


@pytest.mark.parametrize(
    "env",
    [
        {"SIGNUP_MODE": "closed"},
        {"SIGNUP_MODE": "invite_link", "BETA_REGISTRATION_TOKEN": None},  # a link with no token: closed
        {"SIGNUP_MODE": "invite_link", "BETA_REGISTRATION_TOKEN": "   "},
        {"SIGNUP_MODE": "invite-only"},  # a misspelt mode never opens registration
    ],
)
def test_closed_registration_refuses_new_accounts_but_not_existing_users(
    monkeypatch: pytest.MonkeyPatch, http: TestClient, invite_mode: None, env: dict[str, str | None]
) -> None:
    assert _exchange(http).status_code == 200
    existing = _email()
    assert _signup(http, existing).status_code == 201  # an account made while the link was open
    http.cookies.clear()
    assert _exchange(http).status_code == 200  # an authorization issued before the switch

    _env(monkeypatch, **env)
    assert http.get("/auth/registration").json()["data"] == {"mode": "closed", "authorized": False}
    assert _error_code(_signup(http)) == "REGISTRATION_CLOSED"
    assert _error_code(_exchange(http)) == "REGISTRATION_CLOSED"
    # sign-in is untouched
    login = TestClient(app).post("/auth/login", json={"email": existing, "password": _PASSWORD})
    assert login.status_code == 200


def test_existing_users_sign_in_without_any_link(http: TestClient, invite_mode: None) -> None:
    assert _exchange(http).status_code == 200
    email = _email()
    assert _signup(http, email).status_code == 201
    fresh = TestClient(app)
    response = fresh.post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert response.status_code == 200 and fresh.get("/auth/me").status_code == 200


# -- no secret leaks ---------------------------------------------------------------------------------


def test_the_token_never_appears_in_a_response_a_cookie_or_a_log(
    http: TestClient, invite_mode: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    responses = [
        _exchange(http, _OTHER_TOKEN), _exchange(http), http.get("/auth/registration"), _signup(http),
    ]
    for response in responses:
        assert _TOKEN not in response.text and _OTHER_TOKEN not in response.text
        for header in response.headers.get_list("set-cookie"):
            assert _TOKEN not in header and _OTHER_TOKEN not in header
    logged = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert _TOKEN not in logged and _OTHER_TOKEN not in logged
    assert "Registration access granted." in logged and "Registration refused." in logged


def test_the_token_is_only_ever_read_from_the_body() -> None:
    """Never a query string or a path, which could reach request logs and Referer headers."""
    route = next(r for r in app.routes if getattr(r, "path", "") == "/auth/registration-access")
    assert route.methods == {"POST"}
    assert [param.name for param in route.dependant.query_params] == []
    assert [param.name for param in route.dependant.path_params] == []


# -- cookie flags and cross-site requests ------------------------------------------------------------


def test_the_authorization_cookie_is_httponly_scoped_and_short_lived(
    monkeypatch: pytest.MonkeyPatch, invite_mode: None
) -> None:
    _env(monkeypatch, SESSION_COOKIE_SECURE="true", REGISTRATION_ACCESS_TTL_SECONDS="600")
    response = _exchange(TestClient(app, base_url="https://testserver"))
    (header,) = [h for h in response.headers.get_list("set-cookie") if h.startswith(f"{_COOKIE}=")]
    lowered = header.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=lax" in lowered and "path=/" in lowered
    assert "max-age=600" in lowered and "domain=" not in lowered


def test_the_lifetime_cannot_be_configured_above_thirty_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, REGISTRATION_ACCESS_TTL_SECONDS=1801)


def test_a_cross_site_form_post_cannot_exchange_a_token(http: TestClient, invite_mode: None) -> None:
    """A cross-site HTML form can only send form encodings or text/plain, never JSON without a CORS
    preflight; the exchange accepts JSON only, so such a request sets no cookie."""
    for content_type in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data; boundary=x"):
        response = http.post(
            "/auth/registration-access", content=f'{{"token": "{_TOKEN}"}}', headers={"Content-Type": content_type}
        )
        assert response.status_code == 422 and "set-cookie" not in response.headers, content_type


# -- concurrency -------------------------------------------------------------------------------------


def test_concurrent_signups_create_distinct_accounts_and_never_fail_unexpectedly(
    monkeypatch: pytest.MonkeyPatch, invite_mode: None
) -> None:
    """The authorization is a stateless signed cookie: while it is valid, simultaneous sign-ups from the
    browser that holds it can each succeed (the response clears it). The deployment-wide signup cap still
    applies, atomically (next test). What is guaranteed here: every outcome is a created account or a
    typed refusal, and no account is created without the cookie."""
    _env(monkeypatch, SIGNUP_MAX_PER_WINDOW_GLOBAL="1000")
    holder = TestClient(app)
    assert _exchange(holder).status_code == 200
    cookie = holder.cookies.get(_COOKIE)
    results: list[int] = []

    def attempt(with_cookie: bool) -> None:
        client = TestClient(app)
        if with_cookie:
            client.cookies.set(_COOKIE, cookie)
        results.append(_signup(client).status_code)

    threads = [threading.Thread(target=attempt, args=(index % 2 == 0,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(set(results)) == [201, 403]
    assert results.count(201) == 6 and results.count(403) == 6


def test_simultaneous_signups_never_pass_the_deployment_wide_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cap is reserved atomically before the account is created (it used to be count-then-act, which a
    burst could pass). The PostgreSQL version of the race is in test_postgres_admission_p0_integration.py."""
    _env(monkeypatch, SIGNUP_MAX_PER_WINDOW_GLOBAL="3")
    before = _users()
    barrier = threading.Barrier(12)
    results: list[int] = []

    def attempt() -> None:
        client = TestClient(app)
        barrier.wait(timeout=15)
        results.append(_signup(client).status_code)

    threads = [threading.Thread(target=attempt) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(201) == 3 and results.count(429) == 9, results
    assert _users() == before + 3


def test_a_signup_that_creates_no_account_gives_its_place_back(monkeypatch: pytest.MonkeyPatch, http: TestClient) -> None:
    _env(monkeypatch, SIGNUP_MAX_PER_WINDOW_GLOBAL="2")
    email = _email()
    assert _signup(http, email).status_code == 201
    for _ in range(3):  # the same email again: refused, and the refused attempt takes no place
        assert _signup(TestClient(app), email).status_code == 409
    assert _signup(TestClient(app)).status_code == 201  # the second place is still free
    assert _error_code(_signup(TestClient(app))) == "SIGNUP_LIMIT_REACHED"


# -- production configuration ------------------------------------------------------------------------


def _production(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "SESSION_SECRET_KEY": "s" * 40, "SIGNUP_MODE": "invite_link", "BETA_REGISTRATION_TOKEN": _TOKEN,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_never_runs_with_open_registration_or_a_weak_token() -> None:
    assert _production_registration_problems(_production()) == []
    assert _production_registration_problems(_production(SIGNUP_MODE="closed", BETA_REGISTRATION_TOKEN=None)) == []
    (open_problem,) = _production_registration_problems(_production(SIGNUP_MODE="open"))
    assert "SIGNUP_MODE=open" in open_problem
    (short,) = _production_registration_problems(_production(BETA_REGISTRATION_TOKEN="short-token"))
    assert "BETA_REGISTRATION_TOKEN" in short and "short-token" not in short
    (missing,) = _production_registration_problems(_production(BETA_REGISTRATION_TOKEN=None))
    assert "BETA_REGISTRATION_TOKEN" in missing
    (reused,) = _production_registration_problems(_production(BETA_REGISTRATION_TOKEN="s" * 40))
    assert "SESSION_SECRET_KEY" in reused and "s" * 40 not in reused


# -- the generation switch ---------------------------------------------------------------------------


def test_switching_generation_off_refuses_new_jobs_and_keeps_trips_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true")
    client = new_authenticated_client()
    trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    jobs_before = len(job_repository._jobs)

    _env(monkeypatch, GENERATION_ENABLED="false")
    response = client.post(f"/trips/{trip_id}/generate")
    assert response.status_code == 503 and _error_code(response) == "GENERATION_DISABLED"
    assert "paused" in response.json()["errors"][0]["message"]
    assert len(job_repository._jobs) == jobs_before  # nothing admitted, counted or scheduled
    assert client.get(f"/trips/{trip_id}").status_code == 200  # saved trips stay readable

    _env(monkeypatch, GENERATION_ENABLED="true")
    assert client.post(f"/trips/{trip_id}/generate").status_code == 202


def test_the_synchronous_path_is_switched_off_too(monkeypatch: pytest.MonkeyPatch) -> None:
    client = new_authenticated_client()
    trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    _env(monkeypatch, GENERATION_ENABLED="false")

    def must_not_plan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("planning started while generation is switched off")

    monkeypatch.setattr(planning_orchestrator, "generate_full_plan", must_not_plan)
    monkeypatch.setattr(planning_orchestrator, "generate_full_plan_via_langgraph", must_not_plan)
    response = client.post(f"/trips/{trip_id}/generate")
    assert response.status_code == 503 and _error_code(response) == "GENERATION_DISABLED"


def test_admission_itself_refuses_when_generation_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every job (generate, regenerate, targeted) is admitted in one place; the switch is checked there
    too, before any count or insert."""
    from app.core.errors import AppError
    from app.models.generation_job import GenerationJobType, create_queued_job

    _env(monkeypatch, GENERATION_ENABLED="false")
    job = create_queued_job(trip_id="trip_switch", owner_id="user_switch", job_type=GenerationJobType.REGENERATE)
    with pytest.raises(AppError) as refused:
        generation_job_service._create_job_or_conflict(job)
    assert refused.value.code.value == "GENERATION_DISABLED" and job.job_id not in job_repository._jobs


def test_a_database_failure_at_admission_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the admission store cannot answer, no job exists and no planning starts."""
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true")
    client = new_authenticated_client()
    trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    jobs_before = len(job_repository._jobs)

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    def must_not_plan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("planning started although admission failed")

    monkeypatch.setattr(job_repository, "admit", unavailable)
    monkeypatch.setattr(planning_orchestrator, "generate_full_plan", must_not_plan)
    monkeypatch.setattr(planning_orchestrator, "generate_full_plan_via_langgraph", must_not_plan)
    response = TestClient(app, raise_server_exceptions=False, cookies=client.cookies).post(f"/trips/{trip_id}/generate")
    assert response.status_code >= 500
    assert len(job_repository._jobs) == jobs_before


def test_every_route_that_starts_planning_checks_the_switch() -> None:
    from app.api.routes import trips as trip_routes

    def calls_switch(function) -> bool:
        tree = ast.parse(inspect.getsource(function).lstrip())
        return any(isinstance(node, ast.Attribute) and node.attr == "ensure_generation_enabled" for node in ast.walk(tree))

    for name in ("generate_trip_plan", "regenerate_trip_plan", "_regenerate_targeted"):
        assert calls_switch(getattr(trip_routes, name)), name


def test_concurrent_generation_requests_are_all_refused_while_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, ASYNC_GENERATION_ENABLED="true")
    client = new_authenticated_client()
    trips = [client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"] for _ in range(4)]
    _env(monkeypatch, GENERATION_ENABLED="false")
    jobs_before = len(job_repository._jobs)
    codes: list[int] = []
    threads = [threading.Thread(target=lambda t=t: codes.append(client.post(f"/trips/{t}/generate").status_code)) for t in trips]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert codes == [503] * 4 and len(job_repository._jobs) == jobs_before
