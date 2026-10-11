"""Signup and sign-in attempt limits (P0 abuse control).

Deliberately small. This is not identity verification: without email verification one person can still
open several accounts, so the GLOBAL generation limit (`generation_job_service`) stays the
authoritative cost control. What this module bounds:

  * how many accounts the whole deployment creates per window (`SIGNUP_MAX_PER_WINDOW_GLOBAL`), so
    accounts cannot be mass-produced to multiply the per-user generation allowance;
  * how many failed sign-ins ONE email takes per window (`LOGIN_MAX_FAILURES_PER_ACCOUNT`). The key is
    the email as typed, whether or not an account exists for it, and the refusal is the same 429 for
    a right and a wrong password -- so it never reveals whether the account exists;
  * per client address, signups and failed sign-ins -- only when the address can be trusted (below),
    and with generous limits, so people sharing an address are not locked out together.

Counters live in the authoritative store (`get_auth_attempt_repository()`), as keyed hashes: no email
address and no IP address is stored or logged. A check is "count, then act", not a lock: under a burst
of simultaneous requests a limit can be passed by the number of requests in flight. That is acceptable
here (these are speed bumps; the generation limits are the atomic ones).

Client address. `X-Forwarded-For` is client-controlled except for what trusted proxies append, so it
is read ONLY when `CLIENT_IP_TRUSTED_PROXY_HOPS` says how many trusted proxies there are, and then only
the entry that the outermost trusted proxy wrote (the Nth from the right). With the default 0 no
header is read and no per-address limit applies; the socket peer is never used as a client address,
because behind any proxy it is the proxy.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Request

from app.core.config import Settings, get_settings
from app.core.errors import signup_limit_error, too_many_attempts_error
from app.repositories.auth_attempt_repository import LOGIN_FAILURE, SIGNUP
from app.repositories.factory import get_auth_attempt_repository

logger = logging.getLogger(__name__)


def trusted_client_address(request: Request, settings: Settings | None = None) -> str | None:
    """The client address as reported by the outermost TRUSTED proxy, or `None` when there is none to
    trust (hops is 0, the header is shorter than the trusted chain, or the entry is not an address)."""
    hops = (settings or get_settings()).client_ip_trusted_proxy_hops
    if hops <= 0:
        return None
    entries = [
        part.strip()
        for value in request.headers.getlist("x-forwarded-for")
        for part in value.split(",")
        if part.strip()
    ]
    if len(entries) < hops:
        return None
    try:
        return str(ipaddress.ip_address(entries[-hops]))
    except ValueError:
        return None


def _digest(purpose: str, value: str, settings: Settings) -> str:
    """Keyed, one-way: the stored value cannot be turned back into an email or an address, and it is
    useless without the session secret."""
    key = (settings.session_secret_key or "").encode("utf-8")
    return hmac.new(key, f"{purpose}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _prune_before(now: datetime, settings: Settings) -> datetime:
    longest = max(settings.signup_limit_window_seconds, settings.login_failure_window_seconds)
    return now - timedelta(seconds=longest)


def _refused(event: str, error_code: str) -> None:
    logger.warning(
        "Refused an authentication request: an attempt limit was reached.",
        extra={"auth_event": event, "status": "rejected", "error_code": error_code},
    )


def ensure_signup_allowed(client_address: str | None) -> None:
    """Raises `signup_limit_error()` (429) when the deployment-wide or, with a trusted address, the
    per-address signup limit is reached. Runs before the email is looked at, so the answer says
    nothing about whether it is registered."""
    settings = get_settings()
    repository = get_auth_attempt_repository()
    since = _now() - timedelta(seconds=settings.signup_limit_window_seconds)
    over_global = (
        settings.signup_max_per_window_global > 0
        and repository.count(SIGNUP, since=since) >= settings.signup_max_per_window_global
    )
    over_address = (
        not over_global
        and client_address is not None
        and settings.signup_max_per_window_per_ip > 0
        and repository.count(SIGNUP, since=since, ip_hash=_digest("ip", client_address, settings))
        >= settings.signup_max_per_window_per_ip
    )
    if over_global or over_address:
        error = signup_limit_error()
        _refused("signup", error.code.value)
        raise error


def record_signup(client_address: str | None) -> None:
    """Counts one CREATED account."""
    settings = get_settings()
    now = _now()
    get_auth_attempt_repository().record(
        SIGNUP,
        now=now,
        ip_hash=_digest("ip", client_address, settings) if client_address else None,
        prune_before=_prune_before(now, settings),
    )


def ensure_login_allowed(email: str, client_address: str | None) -> None:
    """Raises `too_many_attempts_error()` (429) when this email -- registered or not -- or, with a
    trusted address, this address has too many recent failed sign-ins. Runs before any password work."""
    settings = get_settings()
    repository = get_auth_attempt_repository()
    since = _now() - timedelta(seconds=settings.login_failure_window_seconds)
    over_account = (
        settings.login_max_failures_per_account > 0
        and repository.count(LOGIN_FAILURE, since=since, subject_hash=_digest("email", email, settings))
        >= settings.login_max_failures_per_account
    )
    over_address = (
        not over_account
        and client_address is not None
        and settings.login_max_failures_per_ip > 0
        and repository.count(LOGIN_FAILURE, since=since, ip_hash=_digest("ip", client_address, settings))
        >= settings.login_max_failures_per_ip
    )
    if over_account or over_address:
        error = too_many_attempts_error()
        _refused("login", error.code.value)
        raise error


def record_login_failure(email: str, client_address: str | None) -> None:
    settings = get_settings()
    now = _now()
    get_auth_attempt_repository().record(
        LOGIN_FAILURE,
        now=now,
        subject_hash=_digest("email", email, settings),
        ip_hash=_digest("ip", client_address, settings) if client_address else None,
        prune_before=_prune_before(now, settings),
    )


def login_failure_key(email: str) -> str:
    """The keyed hash this email's failed sign-ins are stored under -- what account deletion removes."""
    return _digest("email", email, get_settings())


def clear_login_failures(email: str) -> None:
    """A successful sign-in ends that email's failure streak."""
    settings = get_settings()
    get_auth_attempt_repository().clear(LOGIN_FAILURE, subject_hash=_digest("email", email, settings))
