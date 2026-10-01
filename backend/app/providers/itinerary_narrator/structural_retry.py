"""Section 202C.1D: one bounded retry for a STRUCTURAL narrator output failure.

202C.1C saw the Groq narrator fail on all three pipeline runs with the same
recoverable error (HTTP 400 `json_validate_failed`: the generated text did not
satisfy the structured-output schema), while an identical prompt succeeded on
a direct call. With no retry, one transient miss sent the traveler the
deterministic fallback text every time.

Contract (shared by the Groq and Anthropic narrator adapters):

  * at most `MAX_NARRATOR_ATTEMPTS` (2) provider calls per narration event:
    one initial attempt + one retry -- never a loop;
  * the retry happens ONLY for a structural output failure: the provider
    reported malformed structured output, or no structured output came back;
  * never retried: rate limits/quota, authentication, timeouts/network,
    other provider errors, and every result that DID parse but was then
    rejected by the adapter's own checks (schema/domain validation, an
    experience id outside its day) or by the service's grounding check;
  * the retry sends the same factual input plus `RETRY_FORMAT_REMINDER`
    (format only -- no new facts, no style change);
  * whatever the retry returns goes through exactly the same checks as a
    first attempt; if it fails too, the adapter returns the same honest
    `failed` report as before and the service uses the deterministic,
    grounded fallback.

Logging uses allowlisted fields only (stage/provider/status/attempt_number/
max_attempts/retry_count) -- never the prompt, the model output or user text.
"""

from __future__ import annotations

import logging

from app.providers.ai_failure import AIProviderFailureKind, classify_and_message

logger = logging.getLogger(__name__)

MAX_NARRATOR_ATTEMPTS = 2

RETRY_FORMAT_REMINDER = (
    "Format reminder: your previous answer was not valid structured output. Return ONLY output "
    "that matches the required schema exactly, with every required key present (use an empty "
    "list where there is nothing to report). Use only the facts given above; do not add any "
    "fact that is not in the input."
)

_STAGE = "itinerary_narrator"


def structural_failure_message(provider_label: str, exc: BaseException) -> tuple[bool, str]:
    """`(is_structural, safe_message)` for an exception raised by a provider call."""
    kind, message = classify_and_message(provider_label, exc)
    return kind == AIProviderFailureKind.MALFORMED_OUTPUT, message


def log_retrying(provider_name: str) -> None:
    logger.warning(
        "Narrator structured output failed; retrying once.",
        extra={
            "stage": _STAGE,
            "provider": provider_name,
            "status": "retrying",
            "attempt_number": 1,
            "max_attempts": MAX_NARRATOR_ATTEMPTS,
        },
    )


def log_retry_outcome(provider_name: str, *, recovered: bool) -> None:
    fields = {
        "stage": _STAGE,
        "provider": provider_name,
        "status": "retry_succeeded" if recovered else "retry_failed",
        "attempt_number": MAX_NARRATOR_ATTEMPTS,
        "max_attempts": MAX_NARRATOR_ATTEMPTS,
        "retry_count": 1,
    }
    if recovered:
        logger.info("Narrator structural retry returned structured output.", extra=fields)
    else:
        logger.warning("Narrator structural retry also failed; the deterministic fallback will be used.", extra=fields)
