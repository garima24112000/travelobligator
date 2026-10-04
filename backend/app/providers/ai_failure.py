from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum

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


_TIMEOUT_STATUS_CODES = frozenset({408, 504})
_MALFORMED_ERROR_CODES = frozenset({"json_validate_failed"})


def _status_code(exc: BaseException) -> int | None:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int):
            return candidate
    return None


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


def safe_ai_failure_message(provider_label: str, kind: AIProviderFailureKind) -> str:
    """A fixed, secret-free sentence per failure kind. Never includes the
    raw exception text, provider response body, account identifiers, or
    model output."""
    return f"{provider_label} API call failed: the provider {_KIND_SENTENCES[kind]}."


def classify_and_message(provider_label: str, exc: BaseException) -> tuple[AIProviderFailureKind, str]:
    kind = classify_ai_provider_exception(exc)
    return kind, safe_ai_failure_message(provider_label, kind)
