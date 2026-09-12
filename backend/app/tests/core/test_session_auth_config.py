from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings

# Tests for the Step 184B auth/session config surface
# (backend/app/core/config.py). No route reads these yet -- see
# backend/app/auth/ for the foundation utilities that do.

_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_session_secret_key_defaults_to_none() -> None:
    """Auth must never be silently enabled with a built-in fallback
    secret -- unset means "not configured," not "use a default key.\""""
    field_info = Settings.model_fields["session_secret_key"]
    assert field_info.default is None
    assert field_info.alias == "SESSION_SECRET_KEY"


def test_settings_constructs_without_session_secret_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructing Settings() with no SESSION_SECRET_KEY set must never
    fail -- auth is simply unconfigured, matching every other optional
    credential field's safe-default convention in this file.

    Step 184D: conftest.py's `_configured_session_secret` autouse fixture
    sets a real `SESSION_SECRET_KEY` env var for the whole suite now
    (every `/trips/*` route requires auth) -- explicitly removed here so
    this test can still prove the field's own true default.
    """
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.session_secret_key is None


def test_session_cookie_name_default() -> None:
    field_info = Settings.model_fields["session_cookie_name"]
    assert field_info.default == "travelobligator_session"
    assert field_info.alias == "SESSION_COOKIE_NAME"


def test_session_ttl_seconds_default_is_one_week() -> None:
    field_info = Settings.model_fields["session_ttl_seconds"]
    assert field_info.default == 604800
    assert field_info.alias == "SESSION_TTL_SECONDS"


def test_session_ttl_seconds_rejects_zero_or_negative() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, session_ttl_seconds=0)
    with pytest.raises(Exception):
        Settings(_env_file=None, session_ttl_seconds=-1)


def test_session_cookie_secure_defaults_false() -> None:
    """False by default so local HTTP development keeps working without
    HTTPS -- a real deployment must opt in explicitly."""
    field_info = Settings.model_fields["session_cookie_secure"]
    assert field_info.default is False
    assert field_info.alias == "SESSION_COOKIE_SECURE"


def test_session_cookie_httponly_defaults_true() -> None:
    field_info = Settings.model_fields["session_cookie_httponly"]
    assert field_info.default is True
    assert field_info.alias == "SESSION_COOKIE_HTTPONLY"


def test_session_cookie_samesite_default_is_lax() -> None:
    field_info = Settings.model_fields["session_cookie_samesite"]
    assert field_info.default == "lax"
    assert field_info.alias == "SESSION_COOKIE_SAMESITE"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("lax", "lax"),
        ("Lax", "lax"),
        ("strict", "strict"),
        ("Strict", "strict"),
        ("none", "none"),
        ("None", "none"),
        ("  strict  ", "strict"),
    ],
)
def test_session_cookie_samesite_accepts_documented_values_case_insensitively(
    value: str, expected: str
) -> None:
    settings = Settings(_env_file=None, session_cookie_samesite=value)
    assert settings.session_cookie_samesite == expected


@pytest.mark.parametrize("value", ["made_up", "", "STRICTLY", "none-of-the-above"])
def test_session_cookie_samesite_falls_back_to_lax_for_unrecognized_values(
    value: str,
) -> None:
    settings = Settings(_env_file=None, session_cookie_samesite=value)
    assert settings.session_cookie_samesite == "lax"


def test_env_example_session_secret_key_is_blank() -> None:
    """The template file must never ship a real secret -- only ever a
    blank placeholder."""
    content = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.startswith("SESSION_SECRET_KEY=")]
    assert len(lines) == 1
    assert lines[0] == "SESSION_SECRET_KEY="


def test_env_example_documents_session_cookie_settings() -> None:
    content = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in (
        "SESSION_SECRET_KEY",
        "SESSION_COOKIE_NAME",
        "SESSION_TTL_SECONDS",
        "SESSION_COOKIE_SECURE",
        "SESSION_COOKIE_SAMESITE",
        "SESSION_COOKIE_HTTPONLY",
    ):
        assert name in content
