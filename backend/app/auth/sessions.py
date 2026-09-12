"""Signed, expiring session-cookie utilities (Step 184B).

Not imported by any route yet -- foundation only (see
`app/auth/passwords.py`'s docstring for why this codebase adds
dependencies/utilities before they're wired in).

`itsdangerous`'s `URLSafeTimedSerializer` signs and timestamps a small
payload (`{"user_id": ...}` ONLY -- never a password, email, or API key)
so a session can be verified without any server-side session store: the
cookie's HMAC signature plus its embedded issue timestamp are the only
state needed. If `Settings.session_secret_key` is unset (the default),
signing/verifying is refused outright via `AuthNotConfiguredError` --
never silently falls back to a shared/predictable key.
"""

from __future__ import annotations

from typing import Literal

from fastapi import Response
from itsdangerous import BadData, URLSafeTimedSerializer

from app.core.config import Settings

# A fixed, non-secret "salt" for itsdangerous's own key-derivation --
# distinct from `Settings.session_secret_key` (the actual secret). Its
# purpose is only to separate this token namespace from any other
# itsdangerous-signed value that might ever reuse the same secret key;
# it is not sensitive and is safe to hardcode.
_SESSION_SALT = "travelobligator-session"


class AuthNotConfiguredError(RuntimeError):
    """Raised when session signing/verification is attempted without
    `SESSION_SECRET_KEY` set. A caller (the auth dependency, once wired in
    Step 184D) should turn this into a clear "auth not configured" error,
    never a raw 500 or a silent "not logged in.\""""


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    if not settings.session_secret_key:
        raise AuthNotConfiguredError(
            "SESSION_SECRET_KEY is not set -- session signing/verification "
            "is disabled until a real secret is configured in .env."
        )
    return URLSafeTimedSerializer(settings.session_secret_key, salt=_SESSION_SALT)


def create_session_token(user_id: str, settings: Settings) -> str:
    """Returns a signed, timestamped token encoding only `user_id`.

    Raises `AuthNotConfiguredError` if `settings.session_secret_key` is
    unset. Never includes a password, email, or any API key in the token
    payload.
    """
    serializer = _serializer(settings)
    return serializer.dumps({"user_id": user_id})


def verify_session_token(token: str, settings: Settings) -> str | None:
    """Returns the encoded `user_id`, or `None` if `token` is missing,
    malformed, unsigned/tampered, or expired (`settings.session_ttl_seconds`).

    Raises `AuthNotConfiguredError` if `settings.session_secret_key` is
    unset -- that is a distinct, controlled condition from "bad token"
    and callers should surface it as "auth not configured," not silently
    treat it as "not logged in." Never raises for a merely bad/expired
    token; never prints or logs the token contents.
    """
    if not token:
        return None

    serializer = _serializer(settings)
    try:
        payload = serializer.loads(token, max_age=settings.session_ttl_seconds)
    except BadData:
        return None

    if not isinstance(payload, dict):
        return None
    user_id = payload.get("user_id")
    return user_id if isinstance(user_id, str) else None


def _samesite(settings: Settings) -> Literal["lax", "strict", "none"]:
    # Settings._normalize_session_cookie_samesite already clamps this to
    # one of exactly these three values -- see backend/app/core/config.py.
    value = settings.session_cookie_samesite
    if value in ("lax", "strict", "none"):
        return value  # type: ignore[return-value]
    return "lax"


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    """Sets the session cookie on `response` per `settings`' cookie flags.

    `HttpOnly`/`Secure`/`SameSite`/`Max-Age` all follow config -- never
    hardcoded here. Never logs or prints `token`.
    """
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=settings.session_cookie_httponly,
        secure=settings.session_cookie_secure,
        samesite=_samesite(settings),
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Clears the session cookie on `response` -- same name/flags as
    `set_session_cookie` (attributes must match for browsers to reliably
    delete the cookie rather than silently ignoring the deletion)."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=settings.session_cookie_httponly,
        secure=settings.session_cookie_secure,
        samesite=_samesite(settings),
    )
