"""Signed, expiring session-cookie utilities (Step 184B; wired into
`app.auth.dependencies` since Step 184C).

`itsdangerous`'s `URLSafeTimedSerializer` signs and timestamps a small
payload (`{"user_id": ...}` ONLY -- never a password, email, or API key)
so a session can be verified without any server-side session store: the
cookie's HMAC signature plus its embedded issue timestamp are the only
state needed. If `Settings.session_secret_key` is unset (the default),
signing/verifying is refused outright via `AuthNotConfiguredError` --
never silently falls back to a shared/predictable key.

Step 187E (docs/14_backend_architecture.md section 124):
`verify_session_token`'s *return value contract is completely
unchanged* (`str | None`, `None` for missing/malformed/tampered/
expired alike -- the response a caller ultimately sends back to a
client, via `authentication_required_error()`, still never
distinguishes any of these from one another, exactly as
`app.core.errors.authentication_required_error`'s own docstring
requires). What changed is purely internal: `SignatureExpired` (an
`itsdangerous` subclass of `BadSignature`/`BadData`) is now caught
before the broader `BadData` catch, purely so the two cases can be
told apart in a *server-side log line* -- `auth_event="session_verify"`,
`status="expired"`/`"invalid"`, `error_code="SESSION_EXPIRED"`/
`"SESSION_INVALID"`. Never the token value, never the signed payload,
never a raw `itsdangerous` exception string.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import Response
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

from app.core.config import Settings

logger = logging.getLogger(__name__)

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
    token; never prints or logs the token contents or signed payload.

    Step 187E: an empty/missing `token` is deliberately never logged
    here -- that case is a normal, extremely common "not logged in yet"
    state (hit on every unauthenticated page load), and this function
    has no way to tell "genuinely missing" apart from "caller passed an
    empty string on purpose" anyway; the caller
    (`app.auth.dependencies.get_current_user_id`) is the one place that
    knows a cookie was truly absent, and deliberately does not log that
    either -- see that function's own docstring for why.
    """
    if not token:
        return None

    serializer = _serializer(settings)
    try:
        payload = serializer.loads(token, max_age=settings.session_ttl_seconds)
    except SignatureExpired:
        logger.warning(
            "Session token expired.",
            extra={
                "auth_event": "session_verify",
                "status": "expired",
                "error_code": "SESSION_EXPIRED",
            },
        )
        return None
    except BadData:
        logger.warning(
            "Session token failed verification.",
            extra={
                "auth_event": "session_verify",
                "status": "invalid",
                "error_code": "SESSION_INVALID",
            },
        )
        return None

    if not isinstance(payload, dict) or not isinstance(payload.get("user_id"), str):
        logger.warning(
            "Session token payload malformed.",
            extra={
                "auth_event": "session_verify",
                "status": "invalid",
                "error_code": "SESSION_INVALID",
            },
        )
        return None
    return payload["user_id"]


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
