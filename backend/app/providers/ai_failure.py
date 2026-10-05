from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any

import httpx

# Section 202B.1 (Tasks 16-17): one shared, provider-agnostic taxonomy for
# WHY a call to an AI model provider (Groq/Anthropic) failed, so the rest
# of the app never has to (and never does) parse an exception string to
# decide how to react.
#
# Before this, every adapter formatted `f"<Provider> API call failed:
# {exc}"` into a user-reachable `blocked_reasons`/`message`, which (a)
# leaked the raw provider response body -- including account
# identifiers and truncated model output -- and (b) collapsed a rate limit
# into the same "rejected" bucket as a malformed model answer, so the
# route ended up telling a rate-limited user that "the regeneration
# engine has not been implemented".
#
# Classification only ever looks at structured exception attributes (HTTP
# status, provider error `code`, exception type) -- never at message text.


class AIProviderFailureKind(str, Enum):
    NOT_CONNECTED = "not_connected"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    TIMEOUT_OR_NETWORK = "timeout_or_network"
    MALFORMED_OUTPUT = "malformed_output"
    PROVIDER_ERROR = "provider_error"
    # Section 1C: the stage's own total wall-clock budget ran out before a
    # usable answer arrived (`app/providers/ai_stage_budget.py`). A latency
    # outcome, never a statement about any travel fact.
    DEADLINE_EXCEEDED = "deadline_exceeded"


class LLMStructuredOutputError(Exception):
    """A request the provider completed successfully, whose answer is not
    valid structured output (empty, not JSON, or not matching the stage's
    wire schema). Raised locally by a structured client that validates the
    answer itself; classified as `MALFORMED_OUTPUT`, exactly like a
    provider-reported `json_validate_failed`. The message is a fixed
    sentence -- never the model's text."""


_TIMEOUT_STATUS_CODES = frozenset({408, 504})
_MALFORMED_ERROR_CODES = frozenset({"json_validate_failed"})


def _status_code(exc: BaseException) -> int | None:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        # google-genai's `APIError` carries the HTTP status as an int `code`.
        getattr(exc, "code", None),
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool) and 100 <= candidate <= 599:
            return candidate
    return None


def _is_httpx_transport_error(exc: BaseException) -> bool:
    """google-genai lets httpx's own timeout / connection errors through
    unwrapped (the Groq SDK wraps them in its `APIConnectionError`)."""
    return isinstance(exc, httpx.TransportError)


def _provider_error_code(exc: BaseException) -> str | None:
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return code
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return None


def classify_ai_provider_exception(exc: BaseException) -> AIProviderFailureKind:
    if isinstance(exc, LLMStructuredOutputError):
        return AIProviderFailureKind.MALFORMED_OUTPUT
    status = _status_code(exc)
    type_name = type(exc).__name__

    if status == 429 or type_name == "RateLimitError":
        return AIProviderFailureKind.RATE_LIMITED
    if status in (401, 403) or type_name in ("AuthenticationError", "PermissionDeniedError"):
        return AIProviderFailureKind.AUTHENTICATION
    if (
        status in _TIMEOUT_STATUS_CODES
        or isinstance(exc, (TimeoutError, ConnectionError))
        or "Timeout" in type_name
        or "Connection" in type_name
        or _is_httpx_transport_error(exc)
    ):
        return AIProviderFailureKind.TIMEOUT_OR_NETWORK
    if _provider_error_code(exc) in _MALFORMED_ERROR_CODES:
        return AIProviderFailureKind.MALFORMED_OUTPUT
    return AIProviderFailureKind.PROVIDER_ERROR


_KIND_SENTENCES: dict[AIProviderFailureKind, str] = {
    AIProviderFailureKind.NOT_CONNECTED: "is not configured",
    AIProviderFailureKind.AUTHENTICATION: "rejected the configured credentials",
    AIProviderFailureKind.RATE_LIMITED: "rate-limited the request (HTTP 429)",
    AIProviderFailureKind.TIMEOUT_OR_NETWORK: "could not be reached in time",
    AIProviderFailureKind.MALFORMED_OUTPUT: (
        "returned an output that did not match the required structure"
    ),
    AIProviderFailureKind.PROVIDER_ERROR: "returned an error",
    AIProviderFailureKind.DEADLINE_EXCEEDED: "did not answer within the time allowed for this step",
}


def is_transient_failure(exc: BaseException) -> bool:
    """True for a failure that one more attempt might get past: a rate
    limit, a timeout / connection problem, or a server-side (5xx) error.
    Never true for rejected credentials, a malformed model answer, or any
    other client-side (4xx) rejection -- repeating those cannot help."""
    kind = classify_ai_provider_exception(exc)
    if kind in (AIProviderFailureKind.RATE_LIMITED, AIProviderFailureKind.TIMEOUT_OR_NETWORK):
        return True
    status = _status_code(exc)
    return kind == AIProviderFailureKind.PROVIDER_ERROR and status is not None and status >= 500


# Section 3B: a finer, diagnostic-only label for a failed request -- which
# kind of transport problem it was. Fixed labels only; decided from the
# same structured attributes as the classification above, never from
# message text or a response body.
TRANSPORT_RATE_LIMIT = "rate_limit"
TRANSPORT_TIMEOUT = "timeout"
TRANSPORT_NETWORK = "network"
TRANSPORT_SERVER_ERROR = "server_error"
TRANSPORT_OTHER = "other_transport"
TRANSPORT_FAILURE_SUBTYPES = frozenset(
    {TRANSPORT_RATE_LIMIT, TRANSPORT_TIMEOUT, TRANSPORT_NETWORK, TRANSPORT_SERVER_ERROR, TRANSPORT_OTHER}
)


def transport_failure_subtype(exc: BaseException) -> str | None:
    """Which kind of transport failure `exc` is, or None when it is not a
    transport failure at all (a malformed / schema-rejected model answer is
    an output problem and is never reported, or retried, as transport)."""
    kind = classify_ai_provider_exception(exc)
    if kind == AIProviderFailureKind.MALFORMED_OUTPUT:
        return None
    if kind == AIProviderFailureKind.RATE_LIMITED:
        return TRANSPORT_RATE_LIMIT
    status = _status_code(exc)
    type_name = type(exc).__name__
    if kind == AIProviderFailureKind.TIMEOUT_OR_NETWORK:
        # A timeout is checked first: the SDK's timeout error is a subclass
        # of its connection error.
        timed_out = status in _TIMEOUT_STATUS_CODES or isinstance(exc, TimeoutError) or "Timeout" in type_name
        return TRANSPORT_TIMEOUT if timed_out else TRANSPORT_NETWORK
    if status is None:
        # No HTTP status and not a timeout/connection error: the request
        # never failed in transit (e.g. a local validation rejection).
        return None
    return TRANSPORT_SERVER_ERROR if status >= 500 else TRANSPORT_OTHER


def retry_after_seconds(exc: BaseException) -> float | None:
    """The provider's own Retry-After for a rate-limited request, in
    seconds, read from the response headers the SDK exposes on the
    exception (`retry-after-ms`, or `retry-after` as seconds or an HTTP
    date). None when absent or unreadable. Only the header value is read --
    never the response body."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        milliseconds = headers.get("retry-after-ms")
        if milliseconds is not None:
            return max(0.0, float(milliseconds) / 1000.0)
        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            moment = parsedate_to_datetime(str(value))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())
    except Exception:  # noqa: BLE001 - an unreadable header is simply "no Retry-After"
        return None


# Provider health (`app/providers/llm_provider_health.py`): what a failed
# request says about the PROVIDER, as fixed labels. Finer than the transport
# subtype above (which keeps its labels for the existing diagnostics): a 503
# is temporary unavailability, never quota, and an unrecognized 4xx is named
# as such rather than read as a structural answer.
HEALTH_RATE_LIMIT = "rate_limit"
HEALTH_PROVIDER_UNAVAILABLE = "provider_unavailable"
HEALTH_SERVER_ERROR = "server_error"
HEALTH_TIMEOUT = "timeout"
HEALTH_CONNECTION_ERROR = "connection_error"
HEALTH_AUTHENTICATION = "authentication_error"
# The provider itself reported malformed structured output.
HEALTH_MALFORMED_RESPONSE = "malformed_response"
# The answer arrived and failed the stage's wire schema locally.
HEALTH_SCHEMA_VALIDATION = "schema_validation"
HEALTH_UNKNOWN_TRANSPORT = "unknown_transport"
HEALTH_FAILURE_KINDS = frozenset(
    {
        HEALTH_RATE_LIMIT, HEALTH_PROVIDER_UNAVAILABLE, HEALTH_SERVER_ERROR, HEALTH_TIMEOUT,
        HEALTH_CONNECTION_ERROR, HEALTH_AUTHENTICATION, HEALTH_MALFORMED_RESPONSE,
        HEALTH_SCHEMA_VALIDATION, HEALTH_UNKNOWN_TRANSPORT,
    }
)
# The provider received and processed the request: a statement about the
# model's output, not about the provider's availability or quota.
HEALTH_STRUCTURAL_KINDS = frozenset({HEALTH_MALFORMED_RESPONSE, HEALTH_SCHEMA_VALIDATION})


def health_failure_kind(exc: BaseException) -> str | None:
    """What `exc` says about the provider, or None when it says nothing (an
    exception with no HTTP status that is not a timeout / connection error
    was raised locally, after or outside the request)."""
    kind = classify_ai_provider_exception(exc)
    if kind == AIProviderFailureKind.MALFORMED_OUTPUT:
        return HEALTH_SCHEMA_VALIDATION if isinstance(exc, LLMStructuredOutputError) else HEALTH_MALFORMED_RESPONSE
    if kind == AIProviderFailureKind.RATE_LIMITED:
        return HEALTH_RATE_LIMIT
    if kind == AIProviderFailureKind.AUTHENTICATION:
        return HEALTH_AUTHENTICATION
    if kind == AIProviderFailureKind.TIMEOUT_OR_NETWORK:
        return HEALTH_TIMEOUT if transport_failure_subtype(exc) == TRANSPORT_TIMEOUT else HEALTH_CONNECTION_ERROR
    status = _status_code(exc)
    if status is None:
        return None
    if status == 503 or getattr(exc, "status", None) == "UNAVAILABLE":
        return HEALTH_PROVIDER_UNAVAILABLE
    return HEALTH_SERVER_ERROR if status >= 500 else HEALTH_UNKNOWN_TRANSPORT


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_DURATION_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def parse_duration_seconds(value: object) -> float | None:
    """A provider's reset / retry duration in seconds: a plain number of
    seconds or a Go-style duration (`7.66s`, `2m59.56s`, `1h2m3s`, `250ms`).
    None for anything else -- an unreadable value is never guessed."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        return seconds if seconds >= 0 and seconds != float("inf") else None
    position, total = 0, 0.0
    for match in _DURATION_PART.finditer(text):
        if match.start() != position:
            return None
        total += float(match.group(1)) * _DURATION_UNIT_SECONDS[match.group(2)]
        position = match.end()
    return total if position == len(text) and position > 0 else None


def _retry_info_seconds(details: Any) -> float | None:
    """Gemini's structured `google.rpc.RetryInfo.retryDelay` from an error
    body the SDK already parsed. Only that one field is read."""
    error = details.get("error") if isinstance(details, dict) else None
    items = error.get("details") if isinstance(error, dict) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and "retryDelay" in item:
            return parse_duration_seconds(item["retryDelay"])
    return None


def _exhausted_quota_reset_seconds(headers: Any) -> float | None:
    """The reset of whichever rate-limit dimension the response headers show
    as used up (`x-ratelimit-remaining-* == 0`); the longest when both are."""
    resets: list[float] = []
    for dimension in ("requests", "tokens"):
        try:
            remaining = float(headers.get(f"x-ratelimit-remaining-{dimension}"))
        except (TypeError, ValueError):
            continue
        reset = parse_duration_seconds(headers.get(f"x-ratelimit-reset-{dimension}"))
        if remaining <= 0 and reset is not None:
            resets.append(reset)
    return max(resets) if resets else None


def provider_reset_seconds(exc: BaseException) -> float | None:
    """When a rate-limited provider says it can be called again, in seconds:
    its Retry-After, else its structured retry delay, else the reset of the
    quota dimension its headers show as exhausted. None when it said nothing
    readable. Header values and one structured field only -- never a message."""
    seconds = retry_after_seconds(exc)
    if seconds is not None:
        return seconds
    try:
        seconds = _retry_info_seconds(getattr(exc, "details", None))
        if seconds is not None:
            return seconds
        headers = getattr(getattr(exc, "response", None), "headers", None)
        return _exhausted_quota_reset_seconds(headers) if headers is not None else None
    except Exception:  # noqa: BLE001 - unreadable metadata is simply "no reset known"
        return None


def safe_ai_failure_message(provider_label: str, kind: AIProviderFailureKind) -> str:
    """A fixed, secret-free sentence per failure kind. Never includes the
    raw exception text, provider response body, account identifiers, or
    model output."""
    return f"{provider_label} API call failed: the provider {_KIND_SENTENCES[kind]}."


def classify_and_message(provider_label: str, exc: BaseException) -> tuple[AIProviderFailureKind, str]:
    kind = classify_ai_provider_exception(exc)
    return kind, safe_ai_failure_message(provider_label, kind)
