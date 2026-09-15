"""Request-scoped correlation ID foundation (Step 187C,
docs/14_backend_architecture.md section 122).

Step 187A found that `ResponseMetadata.request_id` (`schemas/
api_responses.py`) was generated fresh, independently, at response-
serialization time -- never threaded into any log line, and never tied
to the request that produced it. This module is the one place a single
request-scoped id lives for the lifetime of one HTTP request: a
`contextvars.ContextVar` set by `app.core.request_id_middleware.
RequestIdMiddleware` before a route runs, read by `ResponseMetadata`'s
own default factory and by `app.core.logging_config`'s logging filter,
and reset once the request finishes.

A request id is a **correlation label for reading logs side by side
with a response, nothing more.** It is never a security token, never
proof of identity, and never trusted as an authorization signal -- an
incoming `X-Request-Id` header is client-supplied and could be anything
(missing, forged, absurdly long, or containing control characters), so
it is honored only after `is_safe_request_id` accepts it; anything else
is silently replaced with a freshly generated id, never rejected with an
error (a malformed correlation header must never break a request).

This `ContextVar` holds exactly one thing: a short id string. It must
never be extended to also carry a user id, a cookie, a token, a secret,
or any request body/payload -- correlation happens by *looking up* a log
line with a matching `request_id`, never by stashing sensitive data here
for a logging filter to later read back out.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator
from uuid import uuid4

_REQUEST_ID_PREFIX = "req_"

# Generous enough for any real client-supplied correlation id (most are
# short UUIDs or similar) while still bounding how much of a log line/
# response header a single request id can ever consume.
MAX_REQUEST_ID_LENGTH = 128

# Letters, digits, and a small set of separator characters commonly used
# in real correlation ids (UUIDs, trace ids, etc.) -- deliberately does
# NOT allow whitespace, newlines, or other control characters, which
# could otherwise inject extra fields/lines into a log line or corrupt
# the `X-Request-Id` response header.
_SAFE_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")

_current_request_id: ContextVar[str | None] = ContextVar(
    "travelobligator_current_request_id", default=None
)


def new_request_id() -> str:
    """Generates a fresh id in this app's existing `req_<uuid4 hex>`
    style -- the same format `ResponseMetadata.request_id` has always
    used (see `app.models.planning_state._new_id`'s sibling convention
    for every other id in this codebase)."""
    return f"{_REQUEST_ID_PREFIX}{uuid4().hex}"


def is_safe_request_id(value: str | None) -> bool:
    """True only for a short, printable, log-safe/header-safe string --
    never for `None`/empty, anything over `MAX_REQUEST_ID_LENGTH`, or
    anything containing whitespace/newlines/control characters or a
    character outside the safe set. This is a syntactic safety check
    only -- it says nothing about whether the caller who sent it is
    trustworthy, which is exactly why an incoming value is never treated
    as an authorization signal even when it passes this check.
    """
    if not value:
        return False
    if len(value) > MAX_REQUEST_ID_LENGTH:
        return False
    return bool(_SAFE_REQUEST_ID_PATTERN.match(value))


def normalize_request_id(value: str | None) -> str:
    """Returns `value` unchanged if `is_safe_request_id(value)`,
    otherwise returns a freshly generated `new_request_id()` -- an
    unsafe or missing incoming `X-Request-Id` is always replaced, never
    rejected with an error and never partially sanitized/truncated (a
    truncated attacker-controlled string is still attacker-controlled;
    replacing it outright is simpler and safer).
    """
    if is_safe_request_id(value):
        return value  # type: ignore[return-value]
    return new_request_id()


def get_current_request_id() -> str | None:
    """The current request's id, or `None` outside any request context
    (e.g. a unit test constructing a model directly, a startup-time
    call, or a background job not wrapped by the HTTP middleware)."""
    return _current_request_id.get()


def set_current_request_id(value: str) -> Token:
    """Sets the current context's request id and returns a `Token` for
    `reset_current_request_id` to restore the previous value with --
    mirrors `contextvars.ContextVar.set`/`.reset`'s own contract exactly,
    for a caller (namely the middleware) that needs precise control over
    when the reset happens relative to other work."""
    return _current_request_id.set(value)


def reset_current_request_id(token: Token) -> None:
    """Restores the request id that was current before the matching
    `set_current_request_id` call -- always call this, even on the
    error/exception path, so one request's id can never leak into
    whatever runs next in the same context (e.g. a reused thread-pool
    thread)."""
    _current_request_id.reset(token)


@contextmanager
def request_id_scope(value: str) -> Iterator[str]:
    """Convenience `with` block around `set_current_request_id`/
    `reset_current_request_id` -- guarantees the reset happens even if
    the wrapped code raises. Equivalent to calling those two functions
    directly (as `RequestIdMiddleware` does, since it needs the reset to
    happen in its own `finally` alongside other cleanup); provided for
    any simpler caller (tests included) that just wants the id active
    for one indented block.
    """
    token = set_current_request_id(value)
    try:
        yield value
    finally:
        reset_current_request_id(token)
