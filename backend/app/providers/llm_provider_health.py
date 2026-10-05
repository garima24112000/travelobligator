"""In-process health of the Groq <-> Gemini resilience group.

One small, lock-guarded record per provider, shared by every model stage
and every generation in this process, so a provider that just refused a
request is not called again by the next stage (or the next city of a
benchmark run):

  * HEALTHY   -- selectable normally;
  * DRAINING  -- still works, but REAL quota evidence says it is close to a
                 limit: later stages prefer a HEALTHY alternative. The
                 request that revealed it stays successful;
  * OPEN      -- not called until `open_until` (monotonic clock);
  * HALF_OPEN -- the cooldown is over: ONE probe request may go through.

What moves the state (see `record_failure` / `record_transport_success`):

  * a completed transport/API request -> HEALTHY, whether or not the answer
    then passes the stage's schema (a structural failure is a statement
    about the model's output, handled by the stage, never about the
    provider);
  * 429 -> OPEN until the provider's own reset / Retry-After, else for
    `LLM_PROVIDER_PROBE_SECONDS`;
  * 503 / other 5xx / timeout / network / an unrecognized 4xx -> OPEN for
    `LLM_PROVIDER_SHORT_COOLDOWN_SECONDS` (never a quota wait);
  * rejected credentials -> OPEN until the process restarts, never probed.

DRAINING is only ever set from quota evidence: Groq's own rate-limit
response headers, or -- for Gemini, which documents no such headers --
optional configured limits against this process's own request / token
counters. Latency, a single failure, or the time of day never drain a
provider.

Scope and limits: process memory only (one Uvicorn worker per container, so
this is one container's view). Never Redis, never PostgreSQL, and nothing
here is needed for correctness -- with no state at all every stage simply
tries its preferred provider. The lock is never held across a network
request. Nothing here reads or stores a prompt, a model answer, a key, a
URL or a raw header set.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from app.core.config import get_settings
from app.providers.ai_failure import (
    HEALTH_AUTHENTICATION,
    HEALTH_RATE_LIMIT,
    HEALTH_STRUCTURAL_KINDS,
    parse_duration_seconds,
)

GROQ = "groq"
GEMINI = "gemini"
LLM_PAIR_PROVIDERS = (GROQ, GEMINI)

# A rate limit's own reset is never treated as shorter than this.
MIN_OPEN_SECONDS = 1.0
_ROLLING_WINDOW_SECONDS = 60.0
# Dev-only reason label of `force_open` (benchmark scripts).
REASON_FORCED = "forced_dev"


class LLMProviderState(str, Enum):
    HEALTHY = "healthy"
    DRAINING = "draining"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class GroqQuotaSnapshot:
    """Numbers taken from one Groq response's rate-limit headers. A ratio is
    only present when BOTH its limit and its remaining value were readable."""

    remaining_request_ratio: float | None = None
    remaining_token_ratio: float | None = None
    reset_requests_seconds: float | None = None
    reset_tokens_seconds: float | None = None


def _ratio(headers: Any, dimension: str) -> float | None:
    try:
        limit = float(headers.get(f"x-ratelimit-limit-{dimension}"))
        remaining = float(headers.get(f"x-ratelimit-remaining-{dimension}"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(limit) and math.isfinite(remaining)) or limit <= 0 or remaining < 0:
        return None
    return min(1.0, remaining / limit)


def parse_groq_quota_headers(headers: Any) -> GroqQuotaSnapshot | None:
    """The six `x-ratelimit-*` values of a Groq response as plain numbers,
    or None when none of them is readable. Missing or malformed values are
    ignored one by one; no other header is looked at."""
    if headers is None:
        return None
    try:
        snapshot = GroqQuotaSnapshot(
            remaining_request_ratio=_ratio(headers, "requests"),
            remaining_token_ratio=_ratio(headers, "tokens"),
            reset_requests_seconds=parse_duration_seconds(headers.get("x-ratelimit-reset-requests")),
            reset_tokens_seconds=parse_duration_seconds(headers.get("x-ratelimit-reset-tokens")),
        )
    except Exception:  # noqa: BLE001 - unreadable headers are simply "no quota evidence"
        return None
    return None if snapshot == GroqQuotaSnapshot() else snapshot


def _pacific_day() -> str:
    """The calendar day Gemini's per-day quota counts against (Pacific time)."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:  # noqa: BLE001 - no time-zone data installed: UTC days
        return datetime.now(timezone.utc).date().isoformat()


class _Entry:
    def __init__(self) -> None:
        self.open_until: float | None = None
        self.open_reason: str | None = None
        self.probe_in_flight = False
        self.draining_until: float | None = None
        self.groq_quota: GroqQuotaSnapshot | None = None
        # Gemini's advisory counters (used only when a limit is configured).
        self.request_times: deque[float] = deque()
        self.token_events: deque[tuple[float, int]] = deque()
        self.day: str | None = None
        self.day_requests = 0


class LLMProviderHealthRegistry:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] = _pacific_day,
    ) -> None:
        self._clock = clock
        self._today = today
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    # -- reading ---------------------------------------------------------------------

    def state(self, provider: str) -> LLMProviderState:
        with self._lock:
            return self._state(provider)

    def states(self, providers: tuple[str, ...] | list[str]) -> dict[str, str]:
        with self._lock:
            return {provider: self._state(provider).value for provider in providers}

    def open_reason(self, provider: str) -> str | None:
        with self._lock:
            return self._entry(provider).open_reason

    def quota_report(self, provider: str) -> dict[str, Any]:
        """Sanitized quota figures for diagnostics (numbers only)."""
        with self._lock:
            entry = self._entry(provider)
            if provider == GROQ:
                quota = entry.groq_quota
                if quota is None:
                    return {}
                resets = [
                    value for value in (quota.reset_requests_seconds, quota.reset_tokens_seconds) if value is not None
                ]
                return {
                    "remaining_request_ratio": quota.remaining_request_ratio,
                    "remaining_token_ratio": quota.remaining_token_ratio,
                    "reset_seconds": min(resets) if resets else None,
                }
            if provider == GEMINI:
                settings = get_settings()
                limits = {
                    "configured_rpm": settings.gemini_rpm_limit,
                    "configured_tpm": settings.gemini_tpm_limit,
                    "configured_rpd": settings.gemini_rpd_limit,
                }
                if not any(limits.values()):
                    return {}
                return {**limits, "advisory_remaining_ratio": self._gemini_remaining_ratio(entry)}
            return {}

    # -- probes ----------------------------------------------------------------------

    def try_acquire_probe(self, provider: str) -> bool:
        """True for exactly one caller while `provider` is HALF_OPEN."""
        with self._lock:
            entry = self._entry(provider)
            if self._state(provider) != LLMProviderState.HALF_OPEN or entry.probe_in_flight:
                return False
            entry.probe_in_flight = True
            return True

    def release_probe(self, provider: str) -> None:
        """Gives the probe slot back without a verdict (the probe was never
        sent, or was cut short by the caller's own deadline)."""
        with self._lock:
            self._entry(provider).probe_in_flight = False

    # -- recording -------------------------------------------------------------------

    def note_request(self, provider: str) -> None:
        """A request to `provider` is about to be sent."""
        if provider != GEMINI:
            return
        with self._lock:
            entry = self._entry(provider)
            now = self._clock()
            entry.request_times.append(now)
            self._roll_day(entry)
            entry.day_requests += 1

    def record_transport_success(
        self,
        provider: str,
        *,
        groq_quota: GroqQuotaSnapshot | None = None,
        total_tokens: int | None = None,
    ) -> None:
        """The transport/API request completed. Says nothing about whether
        the answer is usable -- only that the provider answered."""
        with self._lock:
            entry = self._entry(provider)
            entry.open_until = None
            entry.open_reason = None
            entry.probe_in_flight = False
            now = self._clock()
            if provider == GROQ and groq_quota is not None:
                entry.groq_quota = groq_quota
                entry.draining_until = self._groq_draining_until(groq_quota, now)
            if provider == GEMINI and isinstance(total_tokens, int) and total_tokens > 0:
                entry.token_events.append((now, total_tokens))

    def record_failure(self, provider: str, kind: str | None, reset_seconds: float | None = None) -> None:
        """A request to `provider` failed with health kind `kind`
        (`app.providers.ai_failure.health_failure_kind`)."""
        if kind is None:
            # No verdict about the provider: only a held probe slot is returned.
            self.release_probe(provider)
            return
        if kind in HEALTH_STRUCTURAL_KINDS:
            self.record_transport_success(provider)
            return
        settings = get_settings()
        if kind == HEALTH_AUTHENTICATION:
            seconds = math.inf
        elif kind == HEALTH_RATE_LIMIT:
            seconds = (
                max(MIN_OPEN_SECONDS, reset_seconds)
                if reset_seconds is not None and math.isfinite(reset_seconds)
                else settings.llm_provider_probe_seconds
            )
        else:
            seconds = settings.llm_provider_short_cooldown_seconds
        self._open(provider, seconds, kind)

    def force_open(self, provider: str) -> None:
        """Development / benchmark scripts only: treat `provider` as OPEN for
        the rest of this process. Never called by the application."""
        self._open(provider, math.inf, REASON_FORCED)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()

    def _open(self, provider: str, seconds: float, reason: str) -> None:
        with self._lock:
            entry = self._entry(provider)
            entry.open_until = self._clock() + seconds
            entry.open_reason = reason
            entry.probe_in_flight = False

    # -- internals (lock held) -------------------------------------------------------

    def _entry(self, provider: str) -> _Entry:
        entry = self._entries.get(provider)
        if entry is None:
            entry = self._entries[provider] = _Entry()
        return entry

    def _state(self, provider: str) -> LLMProviderState:
        entry = self._entry(provider)
        now = self._clock()
        if entry.open_until is not None:
            return LLMProviderState.OPEN if now < entry.open_until else LLMProviderState.HALF_OPEN
        if provider == GROQ:
            if entry.draining_until is not None and now < entry.draining_until:
                return LLMProviderState.DRAINING
            entry.draining_until = None
        elif provider == GEMINI:
            ratio = self._gemini_remaining_ratio(entry)
            if ratio is not None and ratio <= get_settings().llm_quota_reserve_ratio:
                return LLMProviderState.DRAINING
        return LLMProviderState.HEALTHY

    def _groq_draining_until(self, quota: GroqQuotaSnapshot, now: float) -> float | None:
        """When Groq stops DRAINING, from the dimensions at or under the
        reserve; None when it has headroom. A dimension without a readable
        reset drains for the probe interval."""
        settings = get_settings()
        waits = [
            reset if reset is not None else settings.llm_provider_probe_seconds
            for ratio, reset in (
                (quota.remaining_request_ratio, quota.reset_requests_seconds),
                (quota.remaining_token_ratio, quota.reset_tokens_seconds),
            )
            if ratio is not None and ratio <= settings.llm_quota_reserve_ratio
        ]
        return now + max(waits) if waits else None

    def _roll_day(self, entry: _Entry) -> None:
        today = self._today()
        if entry.day != today:
            entry.day, entry.day_requests = today, 0

    def _gemini_remaining_ratio(self, entry: _Entry) -> float | None:
        """The smallest remaining share over the CONFIGURED limits, from this
        process's own counters; None when no limit is configured."""
        settings = get_settings()
        limits = (settings.gemini_rpm_limit, settings.gemini_tpm_limit, settings.gemini_rpd_limit)
        if not any(limits):
            return None
        horizon = self._clock() - _ROLLING_WINDOW_SECONDS
        while entry.request_times and entry.request_times[0] <= horizon:
            entry.request_times.popleft()
        while entry.token_events and entry.token_events[0][0] <= horizon:
            entry.token_events.popleft()
        self._roll_day(entry)
        used = (
            len(entry.request_times),
            sum(tokens for _, tokens in entry.token_events),
            entry.day_requests,
        )
        ratios = [max(0.0, (limit - count) / limit) for limit, count in zip(limits, used) if limit]
        return min(ratios) if ratios else None


_REGISTRY = LLMProviderHealthRegistry()


def get_llm_provider_health() -> LLMProviderHealthRegistry:
    return _REGISTRY
