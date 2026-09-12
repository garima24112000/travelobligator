from __future__ import annotations

import pytest
from starlette.requests import Request

from app.auth.dependencies import get_current_user, get_current_user_id, get_session_token_from_request
from app.auth.sessions import create_session_token
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.models.user import UserRecord
from app.repositories.user_repository import user_repository
from app.schemas.errors import ErrorCode

# Tests for Step 184B's auth dependency foundation
# (backend/app/auth/dependencies.py). Not applied to any route yet --
# app/api/routes/trips.py is completely untouched by this step. These
# call `get_current_user_id` directly as a plain function (bypassing
# FastAPI's own dependency-injection machinery, which requires an
# `AppError` exception handler registered on a real app -- see
# app/main.py) so this stays a focused unit test of the function itself.


def _request_with_cookie(cookie_value: str | None) -> Request:
    headers = []
    if cookie_value is not None:
        headers.append((b"cookie", cookie_value.encode()))
    scope = {"type": "http", "headers": headers, "method": "GET", "path": "/"}
    return Request(scope)


@pytest.fixture(autouse=True)
def _configured_auth(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-only-secret-value")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_get_session_token_from_request_reads_the_configured_cookie_name() -> None:
    settings = Settings(_env_file=None, session_cookie_name="my_cookie")
    request = _request_with_cookie("my_cookie=abc123")

    assert get_session_token_from_request(request, settings) == "abc123"


def test_get_session_token_from_request_returns_none_when_absent() -> None:
    settings = Settings(_env_file=None, session_cookie_name="my_cookie")
    request = _request_with_cookie(None)

    assert get_session_token_from_request(request, settings) is None


def test_get_current_user_id_raises_401_when_cookie_missing() -> None:
    request = _request_with_cookie(None)

    with pytest.raises(AppError) as exc_info:
        get_current_user_id(request)

    assert exc_info.value.code == ErrorCode.AUTHENTICATION_REQUIRED
    assert exc_info.value.status_code == 401


def test_get_current_user_id_raises_401_when_cookie_invalid() -> None:
    request = _request_with_cookie("travelobligator_session=not-a-real-token")

    with pytest.raises(AppError) as exc_info:
        get_current_user_id(request)

    assert exc_info.value.code == ErrorCode.AUTHENTICATION_REQUIRED
    assert exc_info.value.status_code == 401


def test_get_current_user_id_returns_user_id_for_a_valid_session() -> None:
    settings = get_settings()
    token = create_session_token("user_valid_123", settings)
    request = _request_with_cookie(f"{settings.session_cookie_name}={token}")

    assert get_current_user_id(request) == "user_valid_123"


def test_get_current_user_id_raises_auth_not_configured_when_secret_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    get_settings.cache_clear()

    request = _request_with_cookie("travelobligator_session=whatever")

    with pytest.raises(AppError) as exc_info:
        get_current_user_id(request)

    assert exc_info.value.code == ErrorCode.AUTH_NOT_CONFIGURED
    assert exc_info.value.status_code == 503

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Step 184C: get_current_user (loads the full PublicUser via the user
# repository, not just a bare user_id). Relies on conftest.py's autouse
# `_reset_in_memory_repositories` fixture, which points the real
# `user_repository` singleton at a fresh, isolated temp-file store for
# every test -- so writing to it directly here is safe and never touches
# real local dev data.
# ---------------------------------------------------------------------------


def test_get_current_user_returns_public_user_for_a_valid_session() -> None:
    created = user_repository.create_user(
        UserRecord(email="a@b.com", password_hash="irrelevant-for-this-test")
    )
    settings = get_settings()
    token = create_session_token(created.user_id, settings)
    request = _request_with_cookie(f"{settings.session_cookie_name}={token}")

    current_user = get_current_user(request)

    assert current_user.user_id == created.user_id
    assert current_user.email == "a@b.com"
    assert "password_hash" not in type(current_user).model_fields


def test_get_current_user_raises_401_when_cookie_missing() -> None:
    request = _request_with_cookie(None)

    with pytest.raises(AppError) as exc_info:
        get_current_user(request)

    assert exc_info.value.code == ErrorCode.AUTHENTICATION_REQUIRED
    assert exc_info.value.status_code == 401


def test_get_current_user_raises_401_when_token_valid_but_user_no_longer_exists() -> None:
    """A stale session (the user was deleted after the cookie was issued)
    must never resolve to a phantom user."""
    settings = get_settings()
    token = create_session_token("user_does_not_exist_anymore", settings)
    request = _request_with_cookie(f"{settings.session_cookie_name}={token}")

    with pytest.raises(AppError) as exc_info:
        get_current_user(request)

    assert exc_info.value.code == ErrorCode.AUTHENTICATION_REQUIRED
    assert exc_info.value.status_code == 401


def test_get_current_user_raises_auth_not_configured_when_secret_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    get_settings.cache_clear()

    request = _request_with_cookie("travelobligator_session=whatever")

    with pytest.raises(AppError) as exc_info:
        get_current_user(request)

    assert exc_info.value.code == ErrorCode.AUTH_NOT_CONFIGURED
    assert exc_info.value.status_code == 503

    get_settings.cache_clear()
