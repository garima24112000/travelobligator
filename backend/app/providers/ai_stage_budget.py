"""Total wall-clock budget for ONE generation-time model stage (Section 1C).

A per-request timeout bounds one HTTP attempt, not a stage: before this,
a stage's own structural retry sat on top of the SDK's hidden transport
retries (2 application attempts x 3 SDK attempts = up to 6 requests, each
with its own full timeout, plus backoff). One slow or malformed answer
could therefore hold a generation for minutes.

`StageRun` makes every attempt of a stage answer to one deadline:

  * the SDK's own transport retries are switched OFF (`max_retries=0` on
    the client), so there is exactly one retry layer -- this one;
  * a stage gets at most ONE recovery attempt, for either a structural
    output failure (where the stage already had such a retry) or a
    transient transport failure (rate limit, timeout / network, 5xx). The
    two never multiply: `max_attempts = 1 + max(structural, transport)`;
  * every attempt's request timeout is
    `min(per-request timeout, budget remaining)`;
  * a retry is not started unless a meaningful window
    (`MIN_ATTEMPT_SECONDS`) remains after its backoff; otherwise the stage
    ends as `deadline_exceeded` and the pipeline's existing deterministic
    fallback takes over, exactly as for any other model failure.

The clock is monotonic. Nothing here reads a prompt or a model answer,
and a fast first success is never touched. A budget of 0 disables the
deadline (the attempt cap still applies).
"""

from __future__ import annotations

import time
from typing import Callable

from app.core import performance
from app.providers.ai_failure import (
    TRANSPORT_RATE_LIMIT,
    AIProviderFailureKind,
    classify_ai_provider_exception,
    is_transient_failure,
    retry_after_seconds,
    safe_ai_failure_message,
    transport_failure_subtype,
)

# A retry is only worth starting with at least this much budget left.
MIN_ATTEMPT_SECONDS = 3.0
# Fixed pause before the single transport retry (counted against the budget).
TRANSPORT_RETRY_BACKOFF_SECONDS = 1.0
# A budget with less than this left is spent.
_SPENT_SECONDS = 0.25
# Section 3B: a rate limit's Retry-After is honoured only when the wait fits
# inside what is left of the stage budget (leaving room for the attempt). A
# stage whose budget is disabled never waits longer than this.
MAX_RETRY_AFTER_SECONDS_WITHOUT_BUDGET = 20.0
RETRY_AFTER_OBEYED = "obeyed"  # waited exactly as long as the provider asked
RETRY_AFTER_CLAMPED = "clamped"  # asked for less than the ordinary backoff: waited the backoff
RETRY_AFTER_SKIPPED = "skipped"  # did not fit: no wait, no retry, fallback

RESULT_SUCCESS = "success"
# The model answered, but the answer was rejected (structure / validation /
# grounding): the deterministic fallback is used.
RESULT_FALLBACK = "fallback"
# No usable answer at all (transport failure, timeout, deadline): the
# deterministic fallback is used.
RESULT_FAILED = "failed"


class StageRun:
    """The attempts of one model stage, under one total budget."""

    def __init__(
        self,
        stage: str,
        *,
        total_budget_seconds: float,
        request_timeout_seconds: float,
        structural_retries: int = 0,
        transport_retries: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.stage = stage
        self._budget = total_budget_seconds if total_budget_seconds and total_budget_seconds > 0 else None
        self._request_timeout = request_timeout_seconds
        self._structural_allowed = max(0, structural_retries)
        self._transport_allowed = max(0, transport_retries)
        self._clock = clock
        self._sleep = sleep
        self._started_at = clock()
        self._last_timeout_clipped = False
        self.attempts = 0
        self.structural_retries = 0
        self.transport_retries = 0
        self.deadline_exceeded = False
        # True once a request came back with ANY answer (usable or not).
        self.answered = False
        # Section 3B diagnostics: the subtype of the last transport failure,
        # and what was done with a rate limit's Retry-After (fixed labels).
        self.transport_failure: str | None = None
        self.retry_after: str | None = None

    @property
    def max_attempts(self) -> int:
        """One attempt plus at most ONE kind of recovery -- never a product."""
        return 1 + max(self._structural_allowed, self._transport_allowed)

    def remaining(self) -> float | None:
        """Seconds of budget left (never negative); None with no budget."""
        if self._budget is None:
            return None
        return max(0.0, self._budget - (self._clock() - self._started_at))

    def _room_for_retry(self, after_pause: float = 0.0) -> bool:
        remaining = self.remaining()
        return remaining is None or remaining - after_pause >= MIN_ATTEMPT_SECONDS

    def next_attempt_timeout(self) -> float | None:
        """The request timeout for the attempt about to start, or None when
        the budget no longer allows one (the stage is then
        `deadline_exceeded`). The first attempt always runs."""
        remaining = self.remaining()
        if self.attempts > 0 and remaining is not None and remaining < MIN_ATTEMPT_SECONDS:
            self.deadline_exceeded = True
            return None
        self.attempts += 1
        if remaining is None or remaining >= self._request_timeout:
            self._last_timeout_clipped = False
            return self._request_timeout
        self._last_timeout_clipped = True
        return max(remaining, 0.001)

    def allow_structural_retry(self) -> bool:
        """After a STRUCTURAL output failure: True when the one recovery
        attempt may start now."""
        if self.structural_retries >= self._structural_allowed or self.attempts >= self.max_attempts:
            return False
        if not self._room_for_retry():
            self.deadline_exceeded = True
            return False
        self.structural_retries += 1
        return True

    def allow_transport_retry(self, exc: BaseException) -> bool:
        """After a request raised `exc`: True (after the backoff) when the
        one recovery attempt may start. Never for a failure that repeating
        cannot fix."""
        timed_out = classify_ai_provider_exception(exc) == AIProviderFailureKind.TIMEOUT_OR_NETWORK
        remaining = self.remaining()
        if timed_out and self._last_timeout_clipped and remaining is not None and remaining < _SPENT_SECONDS:
            # The request was cut short by the stage budget itself.
            self.deadline_exceeded = True
            return False
        # Section 3B: which kind of transport failure this was (the last one
        # of the stage is what is reported). Diagnostic label only.
        self.transport_failure = transport_failure_subtype(exc) or self.transport_failure
        if not is_transient_failure(exc):
            return False
        if self.transport_retries >= self._transport_allowed or self.attempts >= self.max_attempts:
            return False

        pause = TRANSPORT_RETRY_BACKOFF_SECONDS
        retry_after = (
            retry_after_seconds(exc) if self.transport_failure == TRANSPORT_RATE_LIMIT else None
        )
        if retry_after is not None:
            # The provider said when to come back. Retrying sooner would only
            # be refused again, so the wait is never shorter than asked (and
            # never shorter than the ordinary backoff); a wait that does not
            # fit -- inside the stage budget, or under the fixed ceiling when
            # the stage has none -- is not started at all: the stage ends
            # now and the deterministic fallback takes over.
            pause = max(retry_after, TRANSPORT_RETRY_BACKOFF_SECONDS)
            fits = self._room_for_retry(after_pause=pause) and (
                self._budget is not None or pause <= MAX_RETRY_AFTER_SECONDS_WITHOUT_BUDGET
            )
            if not fits:
                self.retry_after = RETRY_AFTER_SKIPPED
                return False
            self.retry_after = RETRY_AFTER_OBEYED if pause == retry_after else RETRY_AFTER_CLAMPED
        elif not self._room_for_retry(after_pause=pause):
            self.deadline_exceeded = True
            return False
        self._sleep(pause)
        self.transport_retries += 1
        return True

    def deadline_message(self, provider_label: str) -> str:
        return safe_ai_failure_message(provider_label, AIProviderFailureKind.DEADLINE_EXCEEDED)

    def close(self, completed: bool) -> None:
        """Records the stage's attempt counts and outcome for the
        generation being profiled (numbers and fixed labels only). Nothing
        is recorded for a stage that never made a request."""
        if self.attempts == 0:
            return
        result = (
            RESULT_SUCCESS
            if completed
            else RESULT_FALLBACK
            if self.answered and not self.deadline_exceeded
            else RESULT_FAILED
        )
        performance.note_llm_stage(
            self.stage,
            attempts=self.attempts,
            structural_retries=self.structural_retries,
            transport_retries=self.transport_retries,
            deadline_exceeded=self.deadline_exceeded,
            result=result,
            transport_failure=self.transport_failure,
            retry_after=self.retry_after,
        )
