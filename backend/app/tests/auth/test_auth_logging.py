from __future__ import annotations

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth.dependencies import get_current_user, get_current_user_id
from app.auth.sessions import create_session_token, verify_session_token
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging_config import APP_LOGGER_NAME, JsonFormatter
from app.models.user import UserRecord
from app.repositories.user_repository import user_repository as local_user_repository

# Tests for the Step 187E secret-safe auth event logs
# (docs/14_backend_architecture.md section 124). Every test attaches a
# small capture handler directly to the shared "app" logger (which has
# `propagate=False` -- `caplog`'s root-attached handler can't see it,
# matching the pattern Steps 187C/187D already established) rather than
# asserting on stdout text.

_FORBIDDEN_LOG_FIELDS = (
    "email",
    "password",
    "password_hash",
    "session",
    "session_cookie",
    "cookie",
    "authorization",
    "token",
    "secret",
    "api_key",
    "request_body",
    "response_body",
    "provider_payload",
    "planning_state",
    "itinerary",
)


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def capture() -> _CaptureHandler:
    app_logger = logging.getLogger(APP_LOGGER_NAME)
    handler = _CaptureHandler()
    app_logger.addHandler(handler)
    try:
        yield handler
    finally:
        app_logger.removeHandler(handler)


def _assert_no_forbidden_fields(record: logging.LogRecord) -> None:
    for name in _FORBIDDEN_LOG_FIELDS:
        assert not hasattr(record, name), f"log record must never carry {name!r}"


def _records_with(capture: _CaptureHandler, **attrs: object) -> list[logging.LogRecord]:
    matching = []
    for record in capture.records:
        if all(getattr(record, key, None) == value for key, value in attrs.items()):
            matching.append(record)
    return matching


def _request_with_cookie(cookie_value: str | None) -> Request:
    headers = []
    if cookie_value is not None:
        headers.append((b"cookie", cookie_value.encode()))
    scope = {"type": "http", "headers": headers, "method": "GET", "path": "/"}
    return Request(scope)


def _unique_email(tag: str) -> str:
    import uuid

    return f"test-187e-{tag}-{uuid.uuid4().hex}@example.com"


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------


def test_signup_success_logs_safe_structured_fields(
    client: TestClient, capture: _CaptureHandler
) -> None:
    email = _unique_email("signup-ok")
    response = client.post("/auth/signup", json={"email": email, "password": "testpassword123"})
    assert response.status_code == 201
    user_id = response.json()["data"]["user"]["user_id"]
    header_value = response.headers.get("X-Request-Id")

    matching = _records_with(capture, auth_event="signup", status="succeeded")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "INFO"
    assert record.user_id == user_id
    assert record.request_id == header_value
    _assert_no_forbidden_fields(record)

    # Confirms it actually renders as valid, allowlisted-only JSON.
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["auth_event"] == "signup"
    assert parsed["user_id"] == user_id
    assert "email" not in parsed


def test_duplicate_signup_logs_safe_rejected_fields(
    client: TestClient, capture: _CaptureHandler
) -> None:
    email = _unique_email("signup-dup")
    first = client.post("/auth/signup", json={"email": email, "password": "testpassword123"})
    assert first.status_code == 201
    capture.records.clear()

    second = client.post("/auth/signup", json={"email": email, "password": "differentpassword"})
    assert second.status_code == 409

    matching = _records_with(capture, auth_event="signup", status="rejected")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    assert record.error_code == "USER_ALREADY_EXISTS"
    assert not hasattr(record, "user_id")
    _assert_no_forbidden_fields(record)
    assert email not in json.dumps(JsonFormatter().format(record))


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_success_logs_safe_structured_fields(
    client: TestClient, capture: _CaptureHandler
) -> None:
    email = _unique_email("login-ok")
    signup_response = client.post(
        "/auth/signup", json={"email": email, "password": "testpassword123"}
    )
    user_id = signup_response.json()["data"]["user"]["user_id"]
    capture.records.clear()

    response = client.post("/auth/login", json={"email": email, "password": "testpassword123"})
    assert response.status_code == 200
    header_value = response.headers.get("X-Request-Id")

    matching = _records_with(capture, auth_event="login", status="succeeded")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "INFO"
    assert record.user_id == user_id
    assert record.request_id == header_value
    _assert_no_forbidden_fields(record)


def test_invalid_login_logs_safe_rejected_fields_with_no_distinction(
    client: TestClient, capture: _CaptureHandler
) -> None:
    """Unknown email and wrong password must produce byte-identical
    structured log field sets (minus request_id/timestamp, which differ
    per request by design) -- confirms the log layer never leaks the
    distinction the response itself already refuses to leak."""
    email = _unique_email("login-wrong-pw")
    client.post("/auth/signup", json={"email": email, "password": "testpassword123"})
    capture.records.clear()

    wrong_password_response = client.post(
        "/auth/login", json={"email": email, "password": "totallywrongpassword"}
    )
    assert wrong_password_response.status_code == 401
    wrong_password_records = _records_with(capture, auth_event="login", status="rejected")
    assert len(wrong_password_records) == 1
    capture.records.clear()

    unknown_email_response = client.post(
        "/auth/login",
        json={"email": _unique_email("never-signed-up"), "password": "anything123"},
    )
    assert unknown_email_response.status_code == 401
    unknown_email_records = _records_with(capture, auth_event="login", status="rejected")
    assert len(unknown_email_records) == 1

    wrong_password_record = wrong_password_records[0]
    unknown_email_record = unknown_email_records[0]

    def _comparable_fields(record: logging.LogRecord) -> dict[str, object]:
        return {
            "auth_event": getattr(record, "auth_event", None),
            "status": getattr(record, "status", None),
            "error_code": getattr(record, "error_code", None),
            "message": record.getMessage(),
            "levelname": record.levelname,
        }

    assert _comparable_fields(wrong_password_record) == _comparable_fields(unknown_email_record)
    assert wrong_password_record.error_code == "INVALID_CREDENTIALS"
    for record in (wrong_password_record, unknown_email_record):
        assert not hasattr(record, "user_id")
        _assert_no_forbidden_fields(record)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_logs_safe_succeeded_fields_no_cookie_or_token(
    client: TestClient, capture: _CaptureHandler
) -> None:
    email = _unique_email("logout-ok")
    client.post("/auth/signup", json={"email": email, "password": "testpassword123"})
    capture.records.clear()

    response = client.post("/auth/logout")
    assert response.status_code == 200

    matching = _records_with(capture, auth_event="logout", status="succeeded")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "INFO"
    _assert_no_forbidden_fields(record)
    # Documented design choice: logout takes no auth dependency, so no
    # user_id is ever available to attach here.
    assert not hasattr(record, "user_id")

    rendered = JsonFormatter().format(record)
    set_cookie_header = response.headers.get("set-cookie", "")
    assert set_cookie_header and set_cookie_header not in rendered


# ---------------------------------------------------------------------------
# Session verification
# ---------------------------------------------------------------------------


@pytest.fixture()
def _configured_settings_factory():
    def _make(**overrides: object) -> Settings:
        if "session_secret_key" in overrides:
            overrides["SESSION_SECRET_KEY"] = overrides.pop("session_secret_key")
        overrides.setdefault("SESSION_SECRET_KEY", "test-only-secret-value")
        return Settings(_env_file=None, **overrides)

    return _make


def test_tampered_session_logs_session_invalid(
    capture: _CaptureHandler, _configured_settings_factory
) -> None:
    settings = _configured_settings_factory()
    token = create_session_token("user_tampered_check", settings)
    middle = len(token) // 2
    corrupted_segment = "0000" if token[middle : middle + 4] != "0000" else "1111"
    tampered = token[:middle] + corrupted_segment + token[middle + 4 :]

    result = verify_session_token(tampered, settings)
    assert result is None

    matching = _records_with(
        capture, auth_event="session_verify", status="invalid", error_code="SESSION_INVALID"
    )
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    _assert_no_forbidden_fields(record)
    assert tampered not in JsonFormatter().format(record)


def test_expired_session_logs_session_expired(
    capture: _CaptureHandler, _configured_settings_factory
) -> None:
    settings = _configured_settings_factory(session_ttl_seconds=1)
    token = create_session_token("user_expired_check", settings)
    time.sleep(2)

    result = verify_session_token(token, settings)
    assert result is None

    matching = _records_with(
        capture, auth_event="session_verify", status="expired", error_code="SESSION_EXPIRED"
    )
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    _assert_no_forbidden_fields(record)
    assert token not in JsonFormatter().format(record)


def test_missing_cookie_produces_no_log_line(capture: _CaptureHandler) -> None:
    """Noise-level decision: a simply-missing cookie (the normal,
    extremely common "not logged in yet" state) is deliberately never
    logged -- see app.auth.dependencies' own module docstring."""
    request = _request_with_cookie(None)

    with pytest.raises(AppError):
        get_current_user_id(request)

    assert capture.records == []


def test_unauthenticated_auth_me_is_low_noise_and_response_unchanged(
    capture: _CaptureHandler,
) -> None:
    # Deliberately a bare, never-signed-up TestClient -- the shared
    # `client` fixture (used everywhere else in this file) already
    # performs a real signup before handing back the client, which would
    # make this specific "genuinely unauthenticated" case impossible to
    # exercise.
    from app.main import app as fastapi_app

    unauthenticated_client = TestClient(fastapi_app)
    response = unauthenticated_client.get("/auth/me")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"

    # Zero auth_event log lines for the ordinary "not logged in yet" case.
    assert _records_with(capture, auth_event="session_verify") == []


def test_valid_session_for_deleted_user_logs_user_not_found(
    capture: _CaptureHandler,
) -> None:
    settings = get_settings()
    token = create_session_token("user_does_not_exist_in_repo", settings)
    request = _request_with_cookie(f"{settings.session_cookie_name}={token}")

    with pytest.raises(AppError):
        get_current_user(request)

    matching = _records_with(
        capture, auth_event="session_verify", status="invalid", error_code="USER_NOT_FOUND"
    )
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    assert record.user_id == "user_does_not_exist_in_repo"
    _assert_no_forbidden_fields(record)


def test_valid_session_for_real_user_produces_no_session_verify_warning(
    capture: _CaptureHandler,
) -> None:
    created = local_user_repository.create_user(
        UserRecord(email="a@b.com", password_hash="irrelevant-for-this-test")
    )
    settings = get_settings()
    token = create_session_token(created.user_id, settings)
    request = _request_with_cookie(f"{settings.session_cookie_name}={token}")

    current_user = get_current_user(request)

    assert current_user.user_id == created.user_id
    assert _records_with(capture, auth_event="session_verify") == []
