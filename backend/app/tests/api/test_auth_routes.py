from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings

# Full end-to-end tests for /auth/signup, /auth/login, /auth/logout,
# /auth/me (Step 184C) through the real ASGI app.
#
# Every test uses a unique, per-call email (never a fixed literal like
# "a@b.com") -- required for correctness under BOTH backends this suite
# can run against: conftest.py's autouse `_reset_in_memory_repositories`
# fixture gives `local_json` a fresh, empty store per test, but the
# optional `PERSISTENCE_BACKEND=postgres` run (see
# docs/14_backend_architecture.md section 109) points at a real,
# persistent database with NO such per-test reset -- a fixed email would
# collide with itself across runs and produce a false 409 instead of
# testing what the test actually names. Mirrors
# test_postgres_repositories_integration.py's own `_new_trip_id()`
# convention for the same reason.
#
# No test-only auth bypass exists anywhere -- every test here goes
# through the real session-cookie signing/verification path with a real
# (test-only) SESSION_SECRET_KEY.


@pytest.fixture(autouse=True)
def _configured_auth(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-only-secret-value")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _unique_email() -> str:
    return f"test-184c-{uuid.uuid4().hex}@example.com"


def _signup(client: TestClient, email: str, password: str = "longenough1"):
    return client.post("/auth/signup", json={"email": email, "password": password})


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------


def test_signup_success_sets_cookie(client: TestClient) -> None:
    response = _signup(client, _unique_email())

    assert response.status_code == 201
    assert "travelobligator_session" in response.cookies


def test_signup_returns_public_user_without_password_hash(client: TestClient) -> None:
    email = _unique_email()
    response = _signup(client, email)

    body = response.json()["data"]
    assert body["user"]["email"] == email
    assert "password_hash" not in body["user"]
    assert "password" not in body["user"]


def test_signup_response_never_contains_the_plaintext_password_anywhere(
    client: TestClient,
) -> None:
    response = _signup(client, _unique_email(), password="longenough1")
    assert "longenough1" not in response.text


def test_signup_duplicate_email_returns_409(client: TestClient) -> None:
    email = _unique_email()
    _signup(client, email)

    response = _signup(client, email.upper())

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "EMAIL_ALREADY_REGISTERED"


def test_signup_normalizes_email(client: TestClient) -> None:
    email = _unique_email()
    response = _signup(client, f"  {email.upper()}  ")

    assert response.status_code == 201
    assert response.json()["data"]["user"]["email"] == email


def test_signup_invalid_email_returns_validation_error(client: TestClient) -> None:
    response = client.post("/auth/signup", json={"email": "not-an-email", "password": "longenough1"})

    assert response.status_code == 422


def test_signup_short_password_returns_validation_error(client: TestClient) -> None:
    response = client.post("/auth/signup", json={"email": _unique_email(), "password": "short"})

    assert response.status_code == 422


def test_signup_over_long_password_returns_validation_error(client: TestClient) -> None:
    response = client.post(
        "/auth/signup", json={"email": _unique_email(), "password": "a" * 73}
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_success_sets_cookie(client: TestClient) -> None:
    email = _unique_email()
    _signup(client, email, password="longenough1")
    client.cookies.clear()

    response = client.post("/auth/login", json={"email": email, "password": "longenough1"})

    assert response.status_code == 200
    assert "travelobligator_session" in response.cookies
    assert response.json()["data"]["user"]["email"] == email


def test_login_wrong_password_returns_generic_invalid_credentials(client: TestClient) -> None:
    email = _unique_email()
    _signup(client, email, password="longenough1")

    response = client.post("/auth/login", json={"email": email, "password": "wrongpassword"})

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "INVALID_CREDENTIALS"


def test_login_unknown_email_returns_the_same_generic_error(client: TestClient) -> None:
    unknown_email = _unique_email()
    response = client.post(
        "/auth/login", json={"email": unknown_email, "password": "anything123"}
    )

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "INVALID_CREDENTIALS"

    wrong_password_response = client.post(
        "/auth/login", json={"email": unknown_email, "password": "somethingelse"}
    )
    assert wrong_password_response.json()["errors"][0]["message"] == (
        response.json()["errors"][0]["message"]
    )


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_clears_the_cookie(client: TestClient) -> None:
    _signup(client, _unique_email())

    response = client.post("/auth/logout")

    assert response.status_code == 200
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "travelobligator_session=" in set_cookie_header
    assert "Max-Age=0" in set_cookie_header or "01 Jan 1970" in set_cookie_header


def test_me_after_logout_requires_authentication_again(client: TestClient) -> None:
    _signup(client, _unique_email())
    client.post("/auth/logout")

    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


def test_me_returns_current_user_when_cookie_valid(client: TestClient) -> None:
    email = _unique_email()
    _signup(client, email)

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["data"]["user"]["email"] == email


def test_me_returns_401_when_no_cookie() -> None:
    """Step 184D: the shared `client` fixture now always signs up and
    carries a valid session (every `/trips/*` route requires one) -- this
    test specifically needs a client with NO session at all, so it builds
    its own fresh, never-authenticated `TestClient` rather than using
    that fixture."""
    from app.main import app

    unauthenticated_client = TestClient(app)

    response = unauthenticated_client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


def test_me_returns_401_when_cookie_invalid(client: TestClient) -> None:
    client.cookies.set("travelobligator_session", "not-a-real-token")

    response = client.get("/auth/me")

    assert response.status_code == 401


def test_me_returns_401_when_cookie_expired(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setenv("SESSION_TTL_SECONDS", "1")
    get_settings.cache_clear()

    _signup(client, _unique_email())
    time.sleep(2)

    response = client.get("/auth/me")

    assert response.status_code == 401
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Auth not configured (missing SESSION_SECRET_KEY)
# ---------------------------------------------------------------------------


def test_signup_fails_cleanly_when_session_secret_key_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    get_settings.cache_clear()

    response = _signup(client, _unique_email())

    assert response.status_code == 503
    assert response.json()["errors"][0]["code"] == "AUTH_NOT_CONFIGURED"
    get_settings.cache_clear()


def test_login_fails_cleanly_when_session_secret_key_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    get_settings.cache_clear()

    response = client.post(
        "/auth/login", json={"email": _unique_email(), "password": "longenough1"}
    )

    assert response.status_code == 503
    assert response.json()["errors"][0]["code"] == "AUTH_NOT_CONFIGURED"
    get_settings.cache_clear()


def test_logout_still_works_when_session_secret_key_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clearing a cookie never depends on the secret -- logout must
    always succeed."""
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    get_settings.cache_clear()

    response = client.post("/auth/logout")

    assert response.status_code == 200
    get_settings.cache_clear()


def test_signup_does_not_create_a_user_when_session_secret_key_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The auth-not-configured check must happen before account creation,
    not after -- a broken retry must not create duplicate-email
    conflicts once the secret is fixed."""
    email = _unique_email()
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    get_settings.cache_clear()

    _signup(client, email)

    monkeypatch.setenv("SESSION_SECRET_KEY", "now-configured")
    get_settings.cache_clear()

    response = _signup(client, email)

    assert response.status_code == 201
    get_settings.cache_clear()
