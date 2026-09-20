"""Stdlib-only structured logging foundation (Step 187B,
docs/14_backend_architecture.md section 121).

This module adds a centralized way to render this codebase's *existing*
`logging.getLogger(__name__)` call sites (see Step 187A's audit,
docs/14_backend_architecture.md section 120's era of docs, and the
`logger.warning(...)` calls already scattered across `app/providers/*`,
`app/services/*`, and `app/api/routes/trips.py`) as one JSON object per
line on stdout -- it does not add any new logging call site, does not
change what is logged, and does not change any request/response/provider/
auth/persistence/async-job behavior. Nothing here talks to a network --
no OpenTelemetry, no Elastic APM, no Sentry, no Datadog, no external log
shipping. Step 187C (docs/14_backend_architecture.md section 122) added
the one piece this module's own `ALLOWED_EXTRA_FIELDS` had already
reserved a slot for: `RequestIdLogFilter` below now fills in
`request_id` automatically, from `app.core.request_context`'s
request-scoped `ContextVar`, on every record handled by the `"app"`
logger during an HTTP request -- no call site needs to pass
`extra={"request_id": ...}` itself. Step 187D (docs/14_backend_
architecture.md section 123) put this module's `trip_id`/`owner_id`/
`job_id`/`job_type`/`status`/`stage`/`error_code`/`duration_ms` fields
to real use too, in `app.services.generation_job_service`'s async-job
lifecycle logs -- this module itself needed no change for that; the
allowlist below already reserved every one of those names since Step
187B. Step 187D put `trip_id`/`owner_id`/`job_id`/`job_type`/`status`/
`stage`/`error_code`/`duration_ms` to real use in
`app.services.generation_job_service`'s async-job logs. Step 187E
(docs/14_backend_architecture.md section 124) added one new allowlisted
name, `auth_event` ("signup"/"login"/"logout"/"session_verify"), and put
it to use in `app.auth.service`/`app.auth.sessions`/
`app.auth.dependencies` -- still never an email, password, session
token/cookie value, or `Authorization` header. Sections 194A/194B
(docs/14_backend_architecture.md, following section 144) added
`attempt_number`/`repairable_issue_count`/`affected_day_count` (used by
`AIItineraryRepairService`'s own per-call log) and
`max_attempts`/`affected_days`/`repair_status`/`loop_back`/
`remaining_repairable_issue_count` (used by the `ai_itinerary_repair`
LangGraph node's per-invocation loop-decision log) -- all plain counts/
booleans/day-index lists/status strings, never a prompt, a candidate
name, or a credential. Later steps still add provider/gateway logging
and frontend visibility -- none of that exists yet.

Every backend module that wants structured fields on a log line already
uses the stdlib pattern `logger.warning("...", extra={"trip_id": ...})`
-- this module's `JsonFormatter` reads those `extra` values back off the
`LogRecord` (Python's `logging` module attaches each `extra` key as a
plain attribute on the record), but only for the names in
`ALLOWED_EXTRA_FIELDS` below. Never `record.__dict__` wholesale, and
never a sensitive-looking name -- see `SENSITIVE_LOG_FIELD_NAMES`/
`_is_sensitive_field_name` and the module-load-time assertion tying the
two together.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

from app.core.request_context import get_current_request_id

# The single logger namespace this module owns. Every application module
# in this codebase logs via `logging.getLogger(__name__)`, and every such
# name (e.g. "app.services.generation_job_service") is a dotted child of
# "app" -- so attaching one handler here, with `propagate=False`, is
# sufficient to capture every existing and future `app.*` log call
# without touching the root logger or Uvicorn's own "uvicorn"/
# "uvicorn.error"/"uvicorn.access" loggers at all. Uvicorn's own startup/
# access logging (configured by Uvicorn itself, independently, whenever
# it runs) is completely unaffected either way.
APP_LOGGER_NAME = "app"

# Distinguishes "the one handler this module manages" from any other
# handler something else might attach to the same logger -- lets
# `configure_logging()` find (and reuse, never duplicate) its own handler
# across repeated calls, which real app startup never does but tests
# (and, incidentally, `pytest` re-importing `app.main` across multiple
# test files) safely can.
_HANDLER_NAME = "travelobligator_structured_log_handler"

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

# A raw exception traceback (rendered only when the *caller* already
# passed `exc_info=True` to an existing `logger.warning(...)` call -- this
# module never sets `exc_info` itself) can be arbitrarily long. Bounded
# here so one runaway exception can never make a single log line
# unbounded; this is a size cap, not a content filter -- see
# `SENSITIVE_LOG_FIELD_NAMES` for what content is excluded outright.
_MAX_EXC_INFO_CHARS = 4000

# Optional structured fields a log call may attach via
# `logger.warning("...", extra={"trip_id": trip_id})`. This is an
# allowlist, not a denylist -- `JsonFormatter` only ever reads these exact
# attribute names off a `LogRecord`; anything else present on the record
# (including every standard `LogRecord` attribute this module doesn't
# name below, and any `extra` key a caller might pass that isn't listed
# here) is silently dropped, never serialized. `request_id` is reserved
# for Step 187C (request-scoped correlation) -- nothing populates it yet;
# listing it now means 187C only has to start setting it, not also touch
# this allowlist.
ALLOWED_EXTRA_FIELDS = frozenset(
    {
        "request_id",
        "trip_id",
        "user_id",
        "owner_id",
        "job_id",
        "job_type",
        "status",
        "stage",
        "provider",
        "error_code",
        "duration_ms",
        # Step 187E (docs/14_backend_architecture.md section 124): one
        # generic label identifying which auth lifecycle event a log line
        # describes -- "signup"/"login"/"logout"/"session_verify" only
        # (see app.auth.service/app.auth.sessions/app.auth.dependencies
        # for the real call sites). Deliberately generic, never an email
        # address or any other per-user identifying value -- `user_id`
        # above already covers "which account," once known.
        "auth_event",
        # Sections 194A/194B (docs/14_backend_architecture.md, following
        # section 144): AI itinerary repair observability. All plain
        # counts/booleans/day-index lists/status strings -- never a
        # prompt, a candidate name/id, a rationale, or a credential.
        "attempt_number",
        "max_attempts",
        "repairable_issue_count",
        "affected_day_count",
        "affected_days",
        "repair_status",
        "loop_back",
        "remaining_repairable_issue_count",
        # Section 195 (docs/14_backend_architecture.md, following section
        # 145): itinerary-narrator observability. Plain counts only --
        # never a prompt, a candidate name, or a validator message.
        "final_plan_item_count",
        "day_count",
        "repair_attempt_count",
        "remaining_validation_issue_count",
        "narrative_reference_count",
    }
)

# Field names (and, via `_is_sensitive_field_name`, name *patterns*) that
# must never be serialized into a log line, regardless of what a future
# call site's `extra=` might try to attach. This is defense in depth,
# not the primary guard -- `JsonFormatter` only ever reads names in
# `ALLOWED_EXTRA_FIELDS` to begin with, and the module-load-time assertion
# below guarantees that allowlist can never itself contain a sensitive
# name. If a later step ever tried to add e.g. "session_cookie" to
# `ALLOWED_EXTRA_FIELDS`, that assertion fails loudly at import time
# rather than silently starting to log it.
SENSITIVE_LOG_FIELD_NAMES = frozenset(
    {
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
    }
)

# Suffix patterns covering every `*_SECRET_KEY`/`*_API_KEY`-shaped
# `Settings` field name (e.g. `session_secret_key`, `groq_api_key`,
# `anthropic_api_key`) without having to enumerate each one here and risk
# missing a future addition.
_SENSITIVE_NAME_SUFFIXES = ("_secret_key", "_api_key")


def _is_sensitive_field_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in SENSITIVE_LOG_FIELD_NAMES:
        return True
    return any(lowered.endswith(suffix) for suffix in _SENSITIVE_NAME_SUFFIXES)


# Load-bearing invariant, checked once at import time: the allowlist and
# the denylist can never overlap. If they ever did, `JsonFormatter` would
# have no way to tell "safe to log" apart from "must never be logged" for
# that name -- fail fast and loudly here instead of silently logging
# something sensitive later.
assert not any(_is_sensitive_field_name(field) for field in ALLOWED_EXTRA_FIELDS), (
    "ALLOWED_EXTRA_FIELDS must never contain a sensitive-looking field name"
)


class JsonFormatter(logging.Formatter):
    """Renders one `logging.LogRecord` as one JSON object per line.

    Always includes the safe default fields (`timestamp`/`level`/
    `logger`/`message`/`module`/`function`/`line`); includes any
    `ALLOWED_EXTRA_FIELDS` name actually present on the record; and
    renders `record.exc_info` (only ever set by a caller that already
    passed `exc_info=True` -- this class never sets it) as a bounded
    string under `exc_info`, never as a separate object that could carry
    unexpected structure. Never touches `record.__dict__` directly, and
    never crashes when every optional field is absent -- a bare
    `logger.warning("message")` with no `extra=` at all still formats
    cleanly.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        for field in ALLOWED_EXTRA_FIELDS:
            if _is_sensitive_field_name(field):
                # Unreachable given the module-load assertion above, but
                # kept as an explicit second guard rather than trusting
                # that invariant silently -- see this module's docstring.
                continue
            if hasattr(record, field):
                payload[field] = getattr(record, field)

        if record.exc_info:
            rendered = "".join(traceback.format_exception(*record.exc_info))
            if len(rendered) > _MAX_EXC_INFO_CHARS:
                rendered = rendered[:_MAX_EXC_INFO_CHARS] + "... [truncated]"
            payload["exc_info"] = rendered

        return json.dumps(payload, default=str)


class RequestIdLogFilter(logging.Filter):
    """Fills in `record.request_id` from the current request context
    (Step 187C, `app.core.request_context.get_current_request_id`) --
    but only when the record doesn't already carry one.

    A call site that explicitly passes `extra={"request_id": ...}`
    itself (none does today; nothing stops a future one from choosing
    to) is never overridden. Outside any request context (a background
    job, a startup-time log, a plain script) `get_current_request_id()`
    returns `None`, and this filter deliberately leaves `record` alone
    in that case -- `JsonFormatter`'s existing `hasattr(record, field)`
    check then simply omits `request_id` from that line, rather than
    inventing or reusing a stale value from some unrelated earlier
    request. `filter()` always returns `True`: this never drops or
    silences a log record, only annotates it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            current = get_current_request_id()
            if current is not None:
                record.request_id = current
        return True


def _resolve_level(raw_level: str) -> int:
    """Normalizes an arbitrary string to a real `logging` level constant.

    Mirrors this file's own project-wide convention (see
    `app/core/config.py`'s `_normalize_persistence_backend`/
    `_normalize_session_cookie_samesite`, etc.): an unrecognized value
    never raises or crashes cryptically -- it falls back to `INFO`, the
    same safe default `Settings.log_level` itself ships.
    """
    normalized = raw_level.strip().upper() if raw_level else "INFO"
    if normalized not in _VALID_LOG_LEVELS:
        normalized = "INFO"
    return getattr(logging, normalized)


def configure_logging(
    *,
    log_level: str | None = None,
    structured_logging_enabled: bool | None = None,
) -> None:
    """Attaches (or reconfigures, never duplicates) one stdout handler on
    the shared `"app"` logger namespace.

    Called once from `app.main` at import time, before the app starts
    handling requests -- but safe to call again (real app startup never
    does, but re-importing `app.main` across a test session, or a test
    calling this directly, safely can): a second call finds this
    module's own previously-attached handler by name and reconfigures
    it in place (level/formatter only) rather than adding a new one, so
    the same log record is never emitted twice.

    With `log_level`/`structured_logging_enabled` omitted, reads
    `Settings.log_level`/`Settings.structured_logging_enabled` (imported
    lazily, inside this function, to avoid a module-import-time cycle
    between `app.core.config` and `app.core.logging_config`). Passing
    either explicitly (as this module's own tests do) never touches
    `Settings`/`.env` at all.

    `structured_logging_enabled=False` falls back to a plain, single-line,
    human-readable formatter instead of JSON -- neither choice changes
    what is logged, what logger is used, or where it goes (always this
    process's own stdout); no external log shipping, no APM, no network
    call exists in either branch.
    """
    if log_level is None or structured_logging_enabled is None:
        from app.core.config import get_settings

        settings = get_settings()
        if log_level is None:
            log_level = settings.log_level
        if structured_logging_enabled is None:
            structured_logging_enabled = settings.structured_logging_enabled

    level = _resolve_level(log_level)

    app_logger = logging.getLogger(APP_LOGGER_NAME)
    app_logger.setLevel(level)
    # Deliberately never propagate to the root logger -- this is what
    # keeps a repeated `configure_logging()` call (or Uvicorn's own,
    # independently-configured "uvicorn"/"uvicorn.error"/"uvicorn.access"
    # loggers, which this module never touches) from ever double-emitting
    # the same "app.*" log record through two different handlers.
    app_logger.propagate = False

    existing = [
        handler for handler in app_logger.handlers if handler.name == _HANDLER_NAME
    ]
    if existing:
        handler = existing[0]
    else:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.name = _HANDLER_NAME
        app_logger.addHandler(handler)

    handler.setLevel(level)
    handler.setFormatter(
        JsonFormatter()
        if structured_logging_enabled
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )

    # Step 187C: idempotent, same reasoning as the handler-reuse check
    # above -- a repeated `configure_logging()` call must never attach a
    # second copy of this filter (which would still be harmless, since
    # `RequestIdLogFilter.filter()` is itself idempotent, but a stray
    # duplicate is unnecessary and this codebase's own convention is to
    # guard against it explicitly rather than rely on that).
    if not any(isinstance(f, RequestIdLogFilter) for f in handler.filters):
        handler.addFilter(RequestIdLogFilter())
