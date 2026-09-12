from __future__ import annotations

import time

import pytest
from fastapi import Response

from app.auth.sessions import (
    AuthNotConfiguredError,
    clear_session_cookie,
    create_session_token,
    set_session_cookie,
    verify_session_token,
)
from app.core.config import Settings

# Tests for Step 184B's signed session-cookie utility
# (backend/app/auth/sessions.py). Not imported by any route yet.


def _configured_settings(**overrides: object) -> Settings:
    """Step 184D: conftest.py's `_configured_session_secret` autouse
    fixture sets a real `SESSION_SECRET_KEY` env var for the whole suite
    now (every `/trips/*` route requires auth). Passing an explicit
    override via the field's *alias* (`SESSION_SECRET_KEY=`), not its
    field name (`session_secret_key=`), is required for it to actually
    win over that ambient env var -- see `test_get_by_email_queries_with_
    normalized_email`-style precedent elsewhere in this codebase: for an
    aliased field with `populate_by_name=True`, an env var only loses to
    an init kwarg supplied under the *same* key (the alias); a
    field-name kwarg is stored under a different internal key and the
    higher-priority env-var source wins instead.
    """
    if "session_secret_key" in overrides:
        overrides["SESSION_SECRET_KEY"] = overrides.pop("session_secret_key")
    overrides.setdefault("SESSION_SECRET_KEY", "test-only-secret-value")
    return Settings(_env_file=None, **overrides)


# ---------------------------------------------------------------------------
# Auth-not-configured behavior
# ---------------------------------------------------------------------------


def test_create_session_token_raises_when_secret_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.session_secret_key is None

    with pytest.raises(AuthNotConfiguredError):
        create_session_token("user_1", settings)


def test_verify_session_token_raises_when_secret_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    settings = Settings(_env_file=None)
    with pytest.raises(AuthNotConfiguredError):
        verify_session_token("anything", settings)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_create_and_verify_session_token_round_trip() -> None:
    settings = _configured_settings()
    token = create_session_token("user_abc123", settings)

    assert verify_session_token(token, settings) == "user_abc123"


def test_session_token_is_not_the_raw_user_id() -> None:
    settings = _configured_settings()
    token = create_session_token("user_abc123", settings)
    assert token != "user_abc123"
    assert "user_abc123" not in token


def test_session_token_contains_no_password_email_or_api_key() -> None:
    """The token payload is `{"user_id": ...}` only -- confirmed by
    decoding it the same way itsdangerous does, without a secret (the
    unsigned payload is base64-encoded JSON, deliberately not encrypted --
    itsdangerous signs for tamper-evidence, it doesn't hide the payload).
    """
    import base64
    import json

    settings = _configured_settings()
    token = create_session_token("user_abc123", settings)

    payload_segment = token.split(".")[0]
    padded = payload_segment + "=" * (-len(payload_segment) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded))

    assert decoded == {"user_id": "user_abc123"}
    assert set(decoded.keys()) == {"user_id"}


# ---------------------------------------------------------------------------
# Invalid/expired tokens
# ---------------------------------------------------------------------------


def test_verify_session_token_returns_none_for_garbage_token() -> None:
    settings = _configured_settings()
    assert verify_session_token("not-a-real-token", settings) is None


def test_verify_session_token_returns_none_for_empty_token() -> None:
    settings = _configured_settings()
    assert verify_session_token("", settings) is None


def test_verify_session_token_returns_none_for_tampered_token() -> None:
    """Corrupts a whole run of characters in the middle of the token,
    not just one -- flipping a *single* base64url character can be a
    no-op on the decoded bytes if that character only encodes "don't
    care" padding bits (observed flakiness: the exact same single-char
    flip reliably broke the signature for one secret/payload combination
    but was silently absorbed for another). Replacing several consecutive
    characters guarantees the underlying signature bytes actually change,
    regardless of where the substitution lands."""
    settings = _configured_settings()
    token = create_session_token("user_abc123", settings)
    middle = len(token) // 2
    corrupted_segment = "0000" if token[middle : middle + 4] != "0000" else "1111"
    tampered = token[:middle] + corrupted_segment + token[middle + 4 :]
    assert verify_session_token(tampered, settings) is None


def test_verify_session_token_returns_none_for_token_signed_with_a_different_secret() -> None:
    settings_a = _configured_settings(session_secret_key="secret-a")
    settings_b = _configured_settings(session_secret_key="secret-b")
    token = create_session_token("user_abc123", settings_a)

    assert verify_session_token(token, settings_b) is None


def test_verify_session_token_returns_none_for_expired_token() -> None:
    settings = _configured_settings(session_ttl_seconds=1)
    token = create_session_token("user_abc123", settings)

    time.sleep(2)

    assert verify_session_token(token, settings) is None


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def test_set_session_cookie_uses_config_flags() -> None:
    settings = _configured_settings(
        session_cookie_name="my_cookie",
        session_ttl_seconds=1234,
        session_cookie_httponly=True,
        session_cookie_secure=True,
        session_cookie_samesite="strict",
    )
    response = Response()

    set_session_cookie(response, "some-token-value", settings)

    cookie_header = response.headers.get("set-cookie")
    assert cookie_header is not None
    assert "my_cookie=some-token-value" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "Secure" in cookie_header
    assert "SameSite=strict" in cookie_header
    assert "Max-Age=1234" in cookie_header


def test_set_session_cookie_omits_secure_and_httponly_when_disabled() -> None:
    settings = _configured_settings(
        session_cookie_httponly=False,
        session_cookie_secure=False,
    )
    response = Response()

    set_session_cookie(response, "some-token-value", settings)

    cookie_header = response.headers.get("set-cookie")
    assert cookie_header is not None
    assert "HttpOnly" not in cookie_header
    assert "Secure" not in cookie_header


def test_clear_session_cookie_targets_the_same_cookie_name() -> None:
    settings = _configured_settings(session_cookie_name="my_cookie")
    response = Response()

    clear_session_cookie(response, settings)

    cookie_header = response.headers.get("set-cookie")
    assert cookie_header is not None
    assert "my_cookie=" in cookie_header
    # Deletion is expressed as an already-expired cookie.
    assert "Max-Age=0" in cookie_header or "01 Jan 1970" in cookie_header
