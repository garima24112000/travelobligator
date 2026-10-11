from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import abuse, passwords
from app.auth import service as auth_service
from app.core.config import Settings, get_settings
from app.main import app
from app.repositories.auth_attempt_repository import LOGIN_FAILURE, SIGNUP, auth_attempt_repository
from app.repositories.user_repository import user_repository

# P0: signup and sign-in attempt limits (`app/auth/abuse.py`). Hermetic: the Local JSON attempt
# counters (process memory, reset per test); the PostgreSQL table is exercised in
# tests/repositories/test_postgres_admission_p0_integration.py.

_PASSWORD = "testpassword123"


def _env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _email() -> str:
    return f"p0-{uuid.uuid4().hex}@example.com"


@pytest.fixture()
def http() -> TestClient:
    return TestClient(app)


def _signup(http: TestClient, email: str | None = None, **kwargs):
    return http.post("/auth/signup", json={"email": email or _email(), "password": _PASSWORD}, **kwargs)


def _login(http: TestClient, email: str, password: str = _PASSWORD, **kwargs):
    return http.post("/auth/login", json={"email": email, "password": password}, **kwargs)


def _refused(response, code: str) -> dict:
    assert response.status_code == 429, response.text
    body = response.json()
    assert body["success"] is False and body["data"] is None
    assert [error["code"] for error in body["errors"]] == [code]
    assert "set-cookie" not in response.headers  # never a session
    return body["errors"][0]


def _request(headers: dict[str, str] | list[tuple[str, str]], peer: str = "203.0.113.50") -> Request:
    pairs = headers.items() if isinstance(headers, dict) else headers
    return Request(
        {
            "type": "http",
            "headers": [(name.lower().encode(), value.encode()) for name, value in pairs],
            "client": (peer, 4711),
        }
    )


# -- configuration ----------------------------------------------------------------------------------


def test_the_documented_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.signup_max_per_window_global == 30
    assert settings.signup_max_per_window_per_ip == 5
    assert settings.signup_limit_window_seconds == 86400
    assert settings.login_max_failures_per_account == 10
    assert settings.login_max_failures_per_ip == 50
    assert settings.login_failure_window_seconds == 900
    assert settings.client_ip_trusted_proxy_hops == 0


@pytest.mark.parametrize(
    "variable, value",
    [
        ("SIGNUP_MAX_PER_WINDOW_GLOBAL", "-1"),
        ("LOGIN_MAX_FAILURES_PER_ACCOUNT", "-1"),
        ("LOGIN_FAILURE_WINDOW_SECONDS", "30"),
        ("CLIENT_IP_TRUSTED_PROXY_HOPS", "6"),
        ("CLIENT_IP_TRUSTED_PROXY_HOPS", "-1"),
    ],
)
def test_an_invalid_limit_is_rejected_when_settings_load(variable: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{variable: value})


# -- which client address is trusted ----------------------------------------------------------------


def test_with_no_trusted_proxy_no_address_is_used_at_all() -> None:
    settings = Settings(_env_file=None, CLIENT_IP_TRUSTED_PROXY_HOPS=0)
    for headers in ({}, {"X-Forwarded-For": "198.51.100.7"}, {"X-Real-IP": "198.51.100.7", "Forwarded": "for=198.51.100.7"}):
        # neither a client-supplied header nor the socket peer (behind a proxy that is the proxy)
        assert abuse.trusted_client_address(_request(headers), settings) is None


def test_the_address_is_the_one_the_outermost_trusted_proxy_appended() -> None:
    one = Settings(_env_file=None, CLIENT_IP_TRUSTED_PROXY_HOPS=1)
    two = Settings(_env_file=None, CLIENT_IP_TRUSTED_PROXY_HOPS=2)
    header = {"X-Forwarded-For": "198.51.100.7, 192.0.2.10"}
    assert abuse.trusted_client_address(_request(header), one) == "192.0.2.10"
    assert abuse.trusted_client_address(_request(header), two) == "198.51.100.7"


def test_entries_a_client_prepends_are_never_trusted() -> None:
    one = Settings(_env_file=None, CLIENT_IP_TRUSTED_PROXY_HOPS=1)
    forged = {"X-Forwarded-For": "10.0.0.1, 127.0.0.1, 8.8.8.8, 192.0.2.10"}
    assert abuse.trusted_client_address(_request(forged), one) == "192.0.2.10"
    # the same when the forged part arrives as a separate header line
    split = [("X-Forwarded-For", "8.8.8.8"), ("X-Forwarded-For", "192.0.2.10")]
    assert abuse.trusted_client_address(_request(split), one) == "192.0.2.10"


@pytest.mark.parametrize(
    "header",
    [
        {},  # no proxy wrote anything
        {"X-Forwarded-For": "192.0.2.10"},  # shorter than the trusted chain (hops=2)
        {"X-Forwarded-For": "not-an-address, 192.0.2.10"},
        {"X-Forwarded-For": "' OR 1=1 --, 192.0.2.10"},
        {"X-Forwarded-For": " , "},
    ],
)
def test_a_header_that_cannot_be_trusted_gives_no_address(header: dict) -> None:
    two = Settings(_env_file=None, CLIENT_IP_TRUSTED_PROXY_HOPS=2)
    assert abuse.trusted_client_address(_request(header), two) is None


def test_ipv6_addresses_are_normalised() -> None:
    one = Settings(_env_file=None, CLIENT_IP_TRUSTED_PROXY_HOPS=1)
    header = {"X-Forwarded-For": "2001:0DB8:0000:0000:0000:0000:0000:0001"}
    assert abuse.trusted_client_address(_request(header), one) == "2001:db8::1"


# -- signup: deployment-wide limit ------------------------------------------------------------------


def test_only_so_many_accounts_are_created_per_window(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, SIGNUP_MAX_PER_WINDOW_GLOBAL="3")
    for _ in range(3):
        assert _signup(TestClient(app)).status_code == 201

    error = _refused(_signup(http), "SIGNUP_LIMIT_REACHED")
    assert error["message"] == "New accounts cannot be created right now. Please try again later."
    assert len(user_repository._users) == 3  # no fourth account


def test_the_default_signup_limit_is_thirty(http: TestClient) -> None:
    for _ in range(30):
        auth_attempt_repository.record(SIGNUP, now=datetime.now(timezone.utc))
    _refused(_signup(http), "SIGNUP_LIMIT_REACHED")


def test_the_signup_window_rolls(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, SIGNUP_MAX_PER_WINDOW_GLOBAL="1", SIGNUP_LIMIT_WINDOW_SECONDS="3600")
    now = datetime.now(timezone.utc)
    auth_attempt_repository.record(SIGNUP, now=now - timedelta(seconds=3590))
    _refused(_signup(http), "SIGNUP_LIMIT_REACHED")

    auth_attempt_repository.reset()
    auth_attempt_repository.record(SIGNUP, now=now - timedelta(seconds=3601))
    assert _signup(http).status_code == 201


def test_the_refusal_says_nothing_about_the_email(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    taken = _email()
    assert _signup(TestClient(app), taken).status_code == 201
    _env(monkeypatch, SIGNUP_MAX_PER_WINDOW_GLOBAL="1")
    registered = _signup(http, taken)
    unregistered = _signup(http, _email())
    assert registered.status_code == unregistered.status_code == 429
    assert registered.json()["errors"] == unregistered.json()["errors"]


def test_only_created_accounts_are_counted(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, SIGNUP_MAX_PER_WINDOW_GLOBAL="2")
    taken = _email()
    assert _signup(http, taken).status_code == 201
    for _ in range(5):
        assert _signup(http, taken).status_code == 409  # refused: not an account, not counted
        assert http.post("/auth/signup", json={"email": "nope", "password": "x"}).status_code == 422
    assert _signup(http).status_code == 201
    _refused(_signup(http), "SIGNUP_LIMIT_REACHED")


def test_zero_switches_the_signup_limit_off(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, SIGNUP_MAX_PER_WINDOW_GLOBAL="0")
    for _ in range(35):
        auth_attempt_repository.record(SIGNUP, now=datetime.now(timezone.utc))
    assert _signup(http).status_code == 201


# -- signup: per-address limit, only with a trusted address ------------------------------------------


def test_a_forwarded_header_changes_nothing_while_no_proxy_is_trusted(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, SIGNUP_MAX_PER_WINDOW_PER_IP="1")
    same_address = {"X-Forwarded-For": "198.51.100.7"}
    for _ in range(4):  # one address "limit" of 1, yet nothing is limited: the header is not read
        assert _signup(TestClient(app), headers=same_address).status_code == 201
    assert all(attempt.ip_hash is None for attempt in auth_attempt_repository._attempts)


def test_one_address_cannot_mass_produce_accounts_behind_a_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch, CLIENT_IP_TRUSTED_PROXY_HOPS="1", SIGNUP_MAX_PER_WINDOW_PER_IP="2")
    attacker = {"X-Forwarded-For": "198.51.100.7"}
    assert _signup(TestClient(app), headers=attacker).status_code == 201
    assert _signup(TestClient(app), headers=attacker).status_code == 201
    _refused(_signup(TestClient(app), headers=attacker), "SIGNUP_LIMIT_REACHED")

    # prepending forged entries does not get a fresh allowance: the trusted entry is unchanged
    forged = {"X-Forwarded-For": "203.0.113.99, 198.51.100.7"}
    _refused(_signup(TestClient(app), headers=forged), "SIGNUP_LIMIT_REACHED")

    # an unrelated address is not affected
    assert _signup(TestClient(app), headers={"X-Forwarded-For": "192.0.2.77"}).status_code == 201


def test_the_per_address_defaults_leave_room_for_people_who_share_an_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch, CLIENT_IP_TRUSTED_PROXY_HOPS="1")
    shared = {"X-Forwarded-For": "198.51.100.7"}
    for _ in range(5):  # five different people behind one address can all sign up
        assert _signup(TestClient(app), headers=shared).status_code == 201
    _refused(_signup(TestClient(app), headers=shared), "SIGNUP_LIMIT_REACHED")


def test_without_a_trusted_address_the_request_is_not_refused_for_its_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch, CLIENT_IP_TRUSTED_PROXY_HOPS="2", SIGNUP_MAX_PER_WINDOW_PER_IP="1")
    short_chain = {"X-Forwarded-For": "198.51.100.7"}  # fewer entries than trusted proxies
    for _ in range(3):
        assert _signup(TestClient(app), headers=short_chain).status_code == 201


# -- sign-in: failures per email --------------------------------------------------------------------


def test_too_many_failures_lock_the_email_whatever_the_password(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, LOGIN_MAX_FAILURES_PER_ACCOUNT="3")
    email = _email()
    assert _signup(TestClient(app), email).status_code == 201
    for _ in range(3):
        response = _login(http, email, "wrong-password")
        assert response.status_code == 401
        assert response.json()["errors"][0]["code"] == "INVALID_CREDENTIALS"

    error = _refused(_login(http, email, "wrong-password"), "TOO_MANY_ATTEMPTS")
    assert error["message"] == "Too many sign-in attempts. Please wait a few minutes and try again."
    # even the right password is refused while the email is locked: a guess cannot be confirmed
    _refused(_login(http, email, _PASSWORD), "TOO_MANY_ATTEMPTS")


def test_a_registered_and_an_unregistered_email_are_indistinguishable(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, LOGIN_MAX_FAILURES_PER_ACCOUNT="2")
    registered, unknown = _email(), _email()
    assert _signup(TestClient(app), registered).status_code == 201

    def transcript(email: str) -> list[tuple[int, list]]:
        return [
            (response.status_code, response.json()["errors"])
            for response in (_login(http, email, "wrong-password") for _ in range(4))
        ]

    assert transcript(registered) == transcript(unknown)
    assert [status for status, _ in transcript(unknown)] == [429, 429, 429, 429]


def test_an_unknown_email_costs_the_same_password_work(monkeypatch: pytest.MonkeyPatch, http: TestClient) -> None:
    checked: list[str] = []
    real = auth_service.verify_password

    def counting(plain: str, hashed: str) -> bool:
        checked.append(hashed)
        return real(plain, hashed)

    monkeypatch.setattr(auth_service, "verify_password", counting)
    assert _login(http, _email(), "anything").status_code == 401
    assert checked == [passwords.unknown_account_hash()]  # one real bcrypt verification
    assert not real("anything", passwords.unknown_account_hash())
    assert passwords.unknown_account_hash().startswith("$2")


def test_a_successful_sign_in_ends_the_failure_streak(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, LOGIN_MAX_FAILURES_PER_ACCOUNT="3")
    email = _email()
    assert _signup(TestClient(app), email).status_code == 201
    for _ in range(2):
        assert _login(http, email, "wrong-password").status_code == 401
    assert _login(http, email).status_code == 200
    for _ in range(2):  # a fresh streak, not the third failure of the old one
        assert _login(http, email, "wrong-password").status_code == 401
    assert _login(http, email).status_code == 200


def test_the_lock_ends_when_the_failures_leave_the_window(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, LOGIN_MAX_FAILURES_PER_ACCOUNT="1", LOGIN_FAILURE_WINDOW_SECONDS="60")
    email = _email()
    assert _signup(TestClient(app), email).status_code == 201
    assert _login(http, email, "wrong-password").status_code == 401
    _refused(_login(http, email), "TOO_MANY_ATTEMPTS")

    old = datetime.now(timezone.utc) - timedelta(seconds=61)
    auth_attempt_repository._attempts = [
        type(attempt)(attempt.kind, attempt.subject_hash, attempt.ip_hash, old)
        for attempt in auth_attempt_repository._attempts
    ]
    assert _login(http, email).status_code == 200


def test_one_locked_email_does_not_lock_anyone_else(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, LOGIN_MAX_FAILURES_PER_ACCOUNT="1")
    locked, other = _email(), _email()
    for email in (locked, other):
        assert _signup(TestClient(app), email).status_code == 201
    assert _login(http, locked, "wrong-password").status_code == 401
    _refused(_login(http, locked), "TOO_MANY_ATTEMPTS")
    assert _login(http, other).status_code == 200


def test_the_email_is_matched_as_the_account_is(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, LOGIN_MAX_FAILURES_PER_ACCOUNT="2")
    email = _email()
    assert _login(http, email.upper(), "wrong-password").status_code == 401
    assert _login(http, f"  {email}  ", "wrong-password").status_code == 401
    _refused(_login(http, email, "wrong-password"), "TOO_MANY_ATTEMPTS")


def test_failures_per_address_are_limited_only_behind_a_trusted_proxy(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, LOGIN_MAX_FAILURES_PER_IP="3")
    spray = {"X-Forwarded-For": "198.51.100.7"}
    for _ in range(5):  # not trusted: different emails from "one address" are never grouped
        assert _login(http, _email(), "x", headers=spray).status_code == 401

    auth_attempt_repository.reset()
    _env(monkeypatch, CLIENT_IP_TRUSTED_PROXY_HOPS="1")
    for _ in range(3):
        assert _login(http, _email(), "x", headers=spray).status_code == 401
    _refused(_login(http, _email(), "x", headers=spray), "TOO_MANY_ATTEMPTS")
    assert _login(http, _email(), "x", headers={"X-Forwarded-For": "192.0.2.77"}).status_code == 401


# -- what is stored ---------------------------------------------------------------------------------


def test_no_email_and_no_address_is_ever_stored(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, CLIENT_IP_TRUSTED_PROXY_HOPS="1")
    email, address = _email(), "198.51.100.7"
    assert _signup(http, email, headers={"X-Forwarded-For": address}).status_code == 201
    assert _login(http, email, "wrong-password", headers={"X-Forwarded-For": address}).status_code == 401

    kinds = sorted(attempt.kind for attempt in auth_attempt_repository._attempts)
    assert kinds == [LOGIN_FAILURE, SIGNUP]
    for attempt in auth_attempt_repository._attempts:
        for value in (attempt.subject_hash, attempt.ip_hash):
            if value is not None:
                assert len(value) == 64 and int(value, 16) >= 0  # a SHA-256 hex digest
                assert email not in value and address not in value
    signup = next(a for a in auth_attempt_repository._attempts if a.kind == SIGNUP)
    assert signup.subject_hash is None  # a signup is counted without any reference to the account


def test_the_digests_depend_on_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    first = abuse._digest("email", "a@example.com", get_settings())
    _env(monkeypatch, SESSION_SECRET_KEY="another-test-only-session-secret")
    assert abuse._digest("email", "a@example.com", get_settings()) != first
    assert abuse._digest("email", "a@example.com", get_settings()) != abuse._digest(
        "ip", "a@example.com", get_settings()
    )


def test_old_rows_are_removed_when_a_new_one_is_written(http: TestClient) -> None:
    long_ago = datetime.now(timezone.utc) - timedelta(days=3)
    for _ in range(10):
        auth_attempt_repository.record(LOGIN_FAILURE, now=long_ago, subject_hash="x")
    assert _signup(http).status_code == 201
    assert [attempt.kind for attempt in auth_attempt_repository._attempts] == [SIGNUP]


def test_a_refusal_is_logged_without_the_email_or_the_address(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _env(monkeypatch, LOGIN_MAX_FAILURES_PER_ACCOUNT="1", CLIENT_IP_TRUSTED_PROXY_HOPS="1")
    email, address = _email(), "198.51.100.7"
    headers = {"X-Forwarded-For": address}
    with caplog.at_level("WARNING", logger="app.auth.abuse"):
        _login(http, email, "wrong-password", headers=headers)
        _refused(_login(http, email, "wrong-password", headers=headers), "TOO_MANY_ATTEMPTS")
    records = [record for record in caplog.records if record.name == "app.auth.abuse"]
    assert [record.error_code for record in records] == ["TOO_MANY_ATTEMPTS"]
    assert email not in caplog.text and address not in caplog.text


# -- the limits stack: accounts cannot multiply generations ------------------------------------------


def test_signups_and_generations_are_bounded_together(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without email verification one person CAN open several accounts (a documented limitation).
    What bounds the damage: accounts per window are capped, and however many exist, the global
    generation limit is the same."""
    from app.repositories.job_repository import job_repository
    from app.tests.conftest import create_trip_payload

    _env(
        monkeypatch,
        ASYNC_GENERATION_ENABLED="true",
        SIGNUP_MAX_PER_WINDOW_GLOBAL="4",
        GENERATION_MAX_JOBS_PER_USER_PER_WINDOW="2",
        GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW="5",
    )
    accounts, admitted = 0, 0
    for _ in range(7):
        browser = TestClient(app)
        if _signup(browser).status_code != 201:
            continue
        accounts += 1
        for _ in range(3):
            trip_id = browser.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
            admitted += browser.post(f"/trips/{trip_id}/generate").status_code == 202
    assert accounts == 4  # the signup limit
    assert admitted == 5  # the global generation limit, not 4 accounts x 2
    assert len(job_repository._jobs) == 5
