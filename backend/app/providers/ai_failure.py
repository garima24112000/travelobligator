from __future__ import annotations

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
}


def safe_ai_failure_message(provider_label: str, kind: AIProviderFailureKind) -> str:
    """A fixed, secret-free sentence per failure kind. Never includes the
    raw exception text, provider response body, account identifiers, or
    model output."""
    return f"{provider_label} API call failed: the provider {_KIND_SENTENCES[kind]}."


def classify_and_message(provider_label: str, exc: BaseException) -> tuple[AIProviderFailureKind, str]:
    kind = classify_ai_provider_exception(exc)
    return kind, safe_ai_failure_message(provider_label, kind)
