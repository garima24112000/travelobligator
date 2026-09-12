"""Auth dependency foundation (Step 184B; `get_current_user` added in
Step 184C now that a user repository exists).

`get_current_user_id` resolves an authenticated request down to a plain
`user_id` string; `get_current_user` goes one step further and loads the
full `PublicUser` record via `app/auth/service.py`.

Not applied to `app/api/routes/trips.py` in this step -- every `/trips/*`
route keeps its current zero-authentication behavior exactly as before.
Wiring this dependency (plus a per-trip owner check) into those routes is
Step 184D's job. `app/api/routes/auth.py`'s `GET /auth/me` is the first
and only route that uses `get_current_user` as of this step.
"""

from __future__ import annotations

from fastapi import Request

from app.auth.service import get_current_user_by_id
from app.auth.sessions import AuthNotConfiguredError, verify_session_token
from app.core.config import Settings, get_settings
from app.core.errors import auth_not_configured_error, authentication_required_error
from app.models.user import PublicUser


def get_session_token_from_request(request: Request, settings: Settings) -> str | None:
    """Reads the raw session cookie value from `request`, or `None` if
    absent. Never logs or prints the value."""
    return request.cookies.get(settings.session_cookie_name)


def get_current_user_id(request: Request) -> str:
    """FastAPI dependency: returns the authenticated user's `user_id`.

    Raises `authentication_required_error()` (401) when the cookie is
    missing or fails to verify (malformed, unsigned/tampered, or
    expired), and `auth_not_configured_error()` (503) when
    `Settings.session_secret_key` is unset -- a server misconfiguration,
    distinct from "not logged in."

    TODO (Step 184C): once `UserRepository`/`get_user_repository()` exist,
    add a sibling dependency (e.g. `get_current_user`) that loads the full
    `PublicUser` record by this id instead of returning just the id --
    kept id-only here so 184B doesn't need a persistence-backend decision
    (`local_json` vs `postgres`) for this foundation to exist.
    """
    settings = get_settings()
    token = get_session_token_from_request(request, settings)
    if token is None:
        raise authentication_required_error()

    try:
        user_id = verify_session_token(token, settings)
    except AuthNotConfiguredError:
        raise auth_not_configured_error() from None

    if user_id is None:
        raise authentication_required_error()

    return user_id


def get_current_user(request: Request) -> PublicUser:
    """FastAPI dependency: returns the authenticated user's `PublicUser`.

    Same 401/503 semantics as `get_current_user_id` for a missing/
    invalid/expired cookie or an unset secret. Additionally raises
    `authentication_required_error()` (401, not a 500) when the token
    itself verifies but no user with that id exists any more -- a stale
    session must never resolve to a phantom user. No test-only bypass
    exists anywhere in this dependency.
    """
    user_id = get_current_user_id(request)
    user = get_current_user_by_id(user_id)
    if user is None:
        raise authentication_required_error()

    return user


__all__ = [
    "get_current_user",
    "get_current_user_id",
    "get_session_token_from_request",
]
