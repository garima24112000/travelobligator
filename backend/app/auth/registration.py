"""Limited-beta registration: the shareable invitation link (`SIGNUP_MODE=invite_link`).

The owner sets one secret, `BETA_REGISTRATION_TOKEN`, in the host's dashboard and shares a link that
carries it in the URL FRAGMENT (`https://<frontend>/#beta=<token>`). A fragment is never sent to any
server, so the token does not reach request logs or a `Referer` header. The page reads it, removes it
from the address bar at once, and sends it in a JSON body to `POST /auth/registration-access`.

That exchange checks the token (constant-time comparison) and, when it is the current one, sets a
short-lived registration cookie: signed, `HttpOnly`, with the session cookie's `Secure` / `SameSite`
flags, valid for `REGISTRATION_ACCESS_TTL_SECONDS` (at most 30 minutes). The cookie holds only a keyed
fingerprint of the token it was issued for -- never the token -- so replacing the token in the dashboard
revokes the old link AND every registration cookie already issued for it. `POST /auth/signup` requires
that cookie and clears it once the account exists.

What this is and is not:

  * The link is a BEARER credential: whoever holds it can create an account, including anyone it is
    forwarded to. It is not proof of identity, and it does not make registration private once shared.
  * It is enforced by the backend. The frontend only explains; a direct request to the signup API
    without a valid registration cookie is refused.
  * It sits IN FRONT of the existing limits (the deployment-wide signup cap, the per-account and
    deployment-wide generation limits), which still apply to everyone it lets in.

Nothing here logs, returns or stores the token.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Literal

from fastapi import Request, Response
from itsdangerous import BadData, URLSafeTimedSerializer

from app.core.config import Settings, get_settings
from app.core.errors import (
    registration_closed_error,
    registration_link_invalid_error,
    registration_link_required_error,
)

logger = logging.getLogger(__name__)

SIGNUP_OPEN = "open"
SIGNUP_INVITE_LINK = "invite_link"
SIGNUP_CLOSED = "closed"

REGISTRATION_COOKIE_NAME = "travelobligator_registration"
# itsdangerous namespace, distinct from the session's (`app.auth.sessions._SESSION_SALT`): a session
# token can never be accepted as a registration authorization, nor the other way round.
_REGISTRATION_SALT = "travelobligator-registration"


def effective_mode(settings: Settings | None = None) -> str:
    """The mode actually in force. An invitation link with no token configured -- or no session secret
    to sign the authorization with -- is closed, never open."""
    resolved = settings or get_settings()
    if resolved.signup_mode == SIGNUP_INVITE_LINK and not (
        resolved.beta_registration_token and resolved.session_secret_key
    ):
        return SIGNUP_CLOSED
    return resolved.signup_mode


def _token_fingerprint(token: str, settings: Settings) -> str:
    """A keyed, one-way fingerprint of a token: what the cookie carries. It cannot be turned back into
    the token and changes whenever the token (or the session secret) does."""
    key = (settings.session_secret_key or "").encode("utf-8")
    return hmac.new(key, f"beta-registration:{token}".encode("utf-8"), hashlib.sha256).hexdigest()


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret_key or "", salt=_REGISTRATION_SALT)


def _samesite(settings: Settings) -> Literal["lax", "strict", "none"]:
    value = settings.session_cookie_samesite
    return value if value in ("lax", "strict", "none") else "lax"  # type: ignore[return-value]


def token_matches(candidate: str | None, settings: Settings | None = None) -> bool:
    """True only for the current token. Constant-time, and never true when no token is configured."""
    resolved = settings or get_settings()
    expected = resolved.beta_registration_token
    if not expected or not isinstance(candidate, str) or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def grant_registration_access(candidate: str | None, response: Response) -> int:
    """`POST /auth/registration-access`: exchanges a link's token for the registration cookie and returns
    its lifetime in seconds. Raises `registration_closed_error()` when no link can be accepted at all,
    `registration_link_invalid_error()` for any token that is not the current one."""
    settings = get_settings()
    mode = effective_mode(settings)
    if mode == SIGNUP_OPEN:
        return 0  # nothing to authorize: anyone may sign up
    if mode != SIGNUP_INVITE_LINK:
        _log("rejected", "REGISTRATION_CLOSED")
        raise registration_closed_error()
    if not token_matches(candidate, settings):
        _log("rejected", "REGISTRATION_LINK_INVALID")
        raise registration_link_invalid_error()
    value = _serializer(settings).dumps({"fp": _token_fingerprint(settings.beta_registration_token, settings)})
    response.set_cookie(
        key=REGISTRATION_COOKIE_NAME,
        value=value,
        max_age=settings.registration_access_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=_samesite(settings),
        path="/",
    )
    _log("granted", None)
    return settings.registration_access_ttl_seconds


def has_registration_access(request: Request, settings: Settings | None = None) -> bool:
    """Whether this request carries a registration cookie issued for the CURRENT token and not expired."""
    resolved = settings or get_settings()
    if effective_mode(resolved) != SIGNUP_INVITE_LINK:
        return False
    value = request.cookies.get(REGISTRATION_COOKIE_NAME)
    if not value:
        return False
    try:
        payload = _serializer(resolved).loads(value, max_age=resolved.registration_access_ttl_seconds)
    except BadData:
        return False
    fingerprint = payload.get("fp") if isinstance(payload, dict) else None
    return isinstance(fingerprint, str) and hmac.compare_digest(
        fingerprint, _token_fingerprint(resolved.beta_registration_token or "", resolved)
    )


def ensure_registration_allowed(request: Request) -> None:
    """Called by `POST /auth/signup` before anything else: raises unless this mode lets the request create
    an account. Decided before the email is looked at, so the answer says nothing about the address."""
    settings = get_settings()
    mode = effective_mode(settings)
    if mode == SIGNUP_OPEN:
        return
    if mode != SIGNUP_INVITE_LINK:
        _log("rejected", "REGISTRATION_CLOSED", event="signup")
        raise registration_closed_error()
    if not has_registration_access(request, settings):
        _log("rejected", "REGISTRATION_LINK_REQUIRED", event="signup")
        raise registration_link_required_error()


def clear_registration_access(response: Response) -> None:
    """After the account exists: one accepted link authorizes one sign-up from this browser."""
    settings = get_settings()
    response.delete_cookie(
        key=REGISTRATION_COOKIE_NAME,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=_samesite(settings),
        path="/",
    )


def registration_status(request: Request) -> dict[str, object]:
    """What the sign-up screen may know: the mode and whether this browser holds a valid authorization.
    Never the token, its fingerprint or the cookie's contents."""
    settings = get_settings()
    mode = effective_mode(settings)
    return {"mode": mode, "authorized": mode == SIGNUP_OPEN or has_registration_access(request, settings)}


def _log(status: str, error_code: str | None, *, event: str = "registration_access") -> None:
    extra: dict[str, object] = {"auth_event": event, "status": status}
    if error_code:
        extra["error_code"] = error_code
    if status == "granted":
        logger.info("Registration access granted.", extra=extra)
    else:
        logger.warning("Registration refused.", extra=extra)
