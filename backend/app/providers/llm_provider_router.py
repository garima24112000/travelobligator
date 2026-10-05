"""Provider selection for ONE generation-time model stage (Groq <-> Gemini).

A stage adapter asks a `StageRoute` which provider its next request goes
to, and tells it how each request ended. The route never makes a request
and never retries anything: the attempts, the single recovery slot and the
stage's wall-clock budget all belong to `ai_stage_budget.StageRun`. There
is therefore still exactly one retry layer, and a routed stage makes at
most TWO provider requests in total -- the second being ONE of: a request
to the other provider, or the stage's own same-provider structural retry.
Never both, and two is a maximum, not a quota: after a transport failure
with no other provider eligible there is no second request at all.

Who is in the route (`resolve_chain`):

  * a stage is only ever routed when its OWN selector is already "groq" or
    "gemini" and the stage is enabled -- this module is reached from inside
    those adapters only. A disabled stage, a "not_connected" selector and
    an "anthropic" selector never get here, whatever keys exist;
  * `LLM_FAILOVER_ENABLED=false`: exactly the selected provider;
  * otherwise the configured members of the pair, in
    `LLM_PRIMARY_PROVIDER`, `LLM_SECONDARY_PROVIDER` order. With one
    configured provider that is a single-provider stage, behaving as before.

First request of a routed (two-provider) stage, in order:

  1. the preferred provider, when HEALTHY;
  2. the preferred provider as a HALF_OPEN probe, when the probe slot is
     free (otherwise it would never be tried again while the other is fine);
  3. the other provider, when HEALTHY;
  4. a DRAINING provider (preferred first);
  5. the other provider as a HALF_OPEN probe;
  6. nobody -- no request is made and the stage's deterministic fallback runs.

Second request, after a failure of the first:

  * transport failure (rate limit, 503 / 5xx, timeout, network, rejected
    credentials, unrecognized 4xx): the failure is recorded first (the
    provider's circuit opens), then the other provider takes the request if
    it is HEALTHY, DRAINING or probe-eligible, at once (no backoff, no
    Retry-After wait). If it is not, there is NO second request: the
    provider that just failed is never retried in a routed stage, and the
    deterministic fallback runs. (Only a single-provider stage keeps the
    historical same-provider transport retry.);
  * structural failure (malformed / schema-invalid answer): the other
    provider only if it is HEALTHY; otherwise the stage's own structural
    retry where it has one (anchor, narrator) and nothing where it does not
    (reasoning, repair);
  * an answer that parsed and was then rejected by application validation
    or grounding never reaches this module: it is never a reason to switch.

Identical health and configuration always give the same choice.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from app.core import performance
from app.providers.ai_failure import (
    HEALTH_AUTHENTICATION,
    HEALTH_RATE_LIMIT,
    HEALTH_STRUCTURAL_KINDS,
    AIProviderFailureKind,
    health_failure_kind,
    provider_reset_seconds,
)
from app.providers.ai_stage_budget import StageRun
from app.providers.llm_provider_health import (
    GEMINI,
    GROQ,
    LLMProviderHealthRegistry,
    LLMProviderState,
    get_llm_provider_health,
)
from app.providers.llm_structured_clients import take_groq_quota

logger = logging.getLogger(__name__)

PROVIDER_LABELS = {GROQ: "Groq", GEMINI: "Gemini"}

ATTEMPT_SUCCESS = "success"
ATTEMPT_TRANSPORT_FAILURE = "transport_failure"
ATTEMPT_STRUCTURAL_FAILURE = "structural_failure"
# The request was cut short by the stage's own budget: no verdict on the provider.
ATTEMPT_DEADLINE = "deadline"

STRUCTURAL_VALID = "valid"
STRUCTURAL_INVALID = "invalid"

REASON_ALL_UNAVAILABLE = "all_providers_unavailable"


def configured_pair_providers(settings: Any, *, groq_api_key: str | None = None) -> list[str]:
    """The members of the pair that have what they need to be called.
    `groq_api_key` is the adapter's own (possibly explicitly constructed) key."""
    configured: list[str] = []
    if groq_api_key if groq_api_key is not None else settings.groq_api_key:
        configured.append(GROQ)
    if settings.gemini_api_key and settings.gemini_model:
        configured.append(GEMINI)
    return configured


def resolve_chain(selected: str, settings: Any, *, groq_api_key: str | None = None) -> list[str]:
    """The providers a stage whose selector is `selected` may call, in order."""
    configured = configured_pair_providers(settings, groq_api_key=groq_api_key)
    if not settings.llm_failover_enabled:
        return [selected] if selected in configured else []
    return [
        provider
        for provider in (settings.llm_primary_provider, settings.llm_secondary_provider)
        if provider in configured
    ]


class StageRoute:
    """One stage's provider choices and their sanitized record."""

    def __init__(
        self,
        stage: str,
        chain: list[str],
        *,
        health: LLMProviderHealthRegistry | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stage = stage
        self.chain = list(chain)
        self.routed = len(self.chain) > 1
        self.preferred = self.chain[0] if self.chain else None
        self._health = health or get_llm_provider_health()
        self._clock = clock
        self._probing: set[str] = set()
        self._attempt_started_at: float | None = None
        self.health_before = self._health.states(self.chain)
        self.selected: str | None = None
        self.final: str | None = None
        self.failover_reason: str | None = None
        self.attempts: list[dict[str, Any]] = []

    # -- selection -------------------------------------------------------------------

    def _other(self, provider: str) -> str | None:
        return next((candidate for candidate in self.chain if candidate != provider), None)

    def _probe(self, provider: str) -> bool:
        if self._health.try_acquire_probe(provider):
            self._probing.add(provider)
            return True
        return False

    def first_provider(self) -> str | None:
        """The provider of the stage's first request; None when no provider
        may be called (the deterministic fallback then runs)."""
        if not self.chain:
            return None
        preferred = self.chain[0]
        if not self.routed:
            # A single-provider stage behaves as it always has: its provider
            # is tried, whatever was last observed about it.
            self.selected = preferred
            return preferred
        other = self.chain[1]
        states = {provider: self._health.state(provider) for provider in self.chain}
        choice: str | None = None
        if states[preferred] == LLMProviderState.HEALTHY:
            choice = preferred
        elif states[preferred] == LLMProviderState.HALF_OPEN and self._probe(preferred):
            choice = preferred
        elif states[other] == LLMProviderState.HEALTHY:
            choice = other
        elif states[preferred] == LLMProviderState.DRAINING:
            choice = preferred
        elif states[other] == LLMProviderState.DRAINING:
            choice = other
        elif states[other] == LLMProviderState.HALF_OPEN and self._probe(other):
            choice = other
        self.selected = choice
        if choice is None:
            self.failover_reason = REASON_ALL_UNAVAILABLE
        elif choice != preferred:
            self.failover_reason = (
                f"{preferred}_draining"
                if states[preferred] == LLMProviderState.DRAINING
                else f"{preferred}_circuit_open"
            )
        return choice

    def alternate_for(self, provider: str, *, healthy_only: bool) -> str | None:
        """The other provider, when it may take the stage's second request."""
        other = self._other(provider) if self.routed else None
        if other is None or any(attempt["provider"] == other for attempt in self.attempts):
            return None
        state = self._health.state(other)
        if state == LLMProviderState.HEALTHY:
            return other
        if healthy_only:
            return None
        if state == LLMProviderState.DRAINING:
            return other
        if state == LLMProviderState.HALF_OPEN and self._probe(other):
            return other
        return None

    # -- recording -------------------------------------------------------------------

    def begin_attempt(self, provider: str) -> None:
        self._attempt_started_at = self._clock()
        self._health.note_request(provider)

    def _note(self, provider: str, result: str, kind: str | None, structural: str | None) -> None:
        started_at, self._attempt_started_at = self._attempt_started_at, None
        duration_ms = round((self._clock() - started_at) * 1000.0, 1) if started_at is not None else 0.0
        self.attempts.append(
            {
                "provider": provider,
                "result": result,
                "transport_failure_kind": kind,
                "duration_ms": max(0.0, duration_ms),
                "structural_validation_result": structural,
            }
        )
        self._probing.discard(provider)

    def record_answer(self, provider: str, client: Any, *, structured: bool) -> None:
        """The request completed. `structured` is whether a structured
        answer came back (it may still be rejected by the stage later)."""
        groq_quota = take_groq_quota()
        self._health.record_transport_success(
            provider,
            groq_quota=groq_quota if provider == GROQ else None,
            total_tokens=getattr(client, "total_tokens", None) if provider == GEMINI else None,
        )
        if structured:
            self.final = provider
        self._note(
            provider,
            ATTEMPT_SUCCESS if structured else ATTEMPT_STRUCTURAL_FAILURE,
            None,
            STRUCTURAL_VALID if structured else STRUCTURAL_INVALID,
        )

    def record_exception(
        self, provider: str, exc: BaseException, *, cut_short_by_budget: bool = False, client: Any = None
    ) -> str | None:
        """The request raised. Returns the health kind recorded (None when
        the failure says nothing about the provider). `client` lets a
        structurally invalid Gemini answer still count its reported tokens."""
        take_groq_quota()  # closes the attempt's HTTP client
        if cut_short_by_budget:
            if provider in self._probing:
                self._health.release_probe(provider)
            self._note(provider, ATTEMPT_DEADLINE, None, None)
            return None
        kind = health_failure_kind(exc)
        before = self._health.state(provider)
        structural = kind in HEALTH_STRUCTURAL_KINDS
        if structural:
            # The provider received and processed the request.
            self._health.record_transport_success(
                provider, total_tokens=getattr(client, "total_tokens", None) if provider == GEMINI else None
            )
        else:
            self._health.record_failure(provider, kind, provider_reset_seconds(exc))
        self._note(
            provider,
            ATTEMPT_STRUCTURAL_FAILURE if structural else ATTEMPT_TRANSPORT_FAILURE,
            kind,
            STRUCTURAL_INVALID if structural else None,
        )
        after = self._health.state(provider)
        if after == LLMProviderState.OPEN and before != LLMProviderState.OPEN:
            logger.warning(
                "LLM provider circuit opened.",
                extra={"provider": provider, "stage": self.stage, "status": kind},
            )
        return kind

    def open_reason(self, provider: str) -> str | None:
        return self._health.open_reason(provider)

    def note_switch(self, failed_provider: str, kind: str | None) -> None:
        self.failover_reason = f"{failed_provider}_{kind or 'structural_failure'}"

    def finish(self) -> None:
        """Returns any probe slot taken for a request that was never sent."""
        for provider in list(self._probing):
            self._health.release_probe(provider)
        self._probing.clear()

    # -- diagnostics -----------------------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        """Fixed labels and numbers only: provider names, states, failure
        kinds, durations and quota ratios. Never a key, header set, prompt,
        model answer or URL."""
        attempted = [attempt["provider"] for attempt in self.attempts]
        health_after = self._health.states(self.chain)
        report: dict[str, Any] = {
            "preferred_provider": self.preferred,
            "selected_provider": self.selected,
            "attempted_providers": attempted,
            "final_provider": self.final,
            "failover_used": bool(
                (self.selected is not None and self.selected != self.preferred) or len(set(attempted)) > 1
            ),
            "failover_reason": self.failover_reason,
            "provider_health_before": dict(self.health_before),
            "provider_health_after": health_after,
            "total_provider_requests": len(self.attempts),
            "provider_attempts": [dict(attempt) for attempt in self.attempts],
        }
        for provider in self.chain:
            quota = self._health.quota_report(provider)
            if quota:
                report[f"{provider}_quota"] = quota
        return report


def start_route(
    run: StageRun,
    selected: str,
    settings: Any,
    *,
    groq_api_key: str | None = None,
) -> StageRoute:
    """The route of the stage `run`, for an adapter whose selector is
    `selected`. A two-provider route gives the stage its one failover
    attempt and caps it at two requests in total."""
    route = StageRoute(
        run.stage, resolve_chain(selected, settings, groq_api_key=groq_api_key), clock=run.clock
    )
    if route.routed:
        run.enable_failover()
    return route


def after_transport_failure(run: StageRun, route: StageRoute | None, provider: str, exc: BaseException) -> str | None:
    """A request to `provider` raised a transport failure: the provider of
    the stage's second request, or None when there is none (`run` then says
    whether the deadline was the reason). Records the failure."""
    if route is None:
        return provider if run.allow_transport_retry(exc) else None
    if run.cut_short_by_budget(exc):
        route.record_exception(provider, exc, cut_short_by_budget=True)
        return None
    run.note_transport_failure(exc)
    kind = route.record_exception(provider, exc)
    alternate = route.alternate_for(provider, healthy_only=False)
    if alternate is not None and run.allow_failover():
        route.note_switch(provider, kind)
        return alternate
    if run.deadline_exceeded or kind == HEALTH_AUTHENTICATION:
        return None
    if route.routed:
        # The failure has just been recorded against this provider (its
        # circuit is open) and the other one may not be called: nobody is
        # eligible, so the stage ends here. Two requests is a maximum, never
        # a quota to use up -- the provider that just failed is not retried.
        return None
    # A single-provider stage keeps its historical transport retry.
    return provider if run.allow_transport_retry(exc) else None


def after_structural_failure(
    run: StageRun, route: StageRoute | None, provider: str, *, same_provider_retry: bool
) -> str | None:
    """`provider` answered with malformed / schema-invalid output (already
    recorded on the route): the provider of the second request, or None.
    `same_provider_retry` is whether this stage has its own structural retry."""
    alternate = route.alternate_for(provider, healthy_only=True) if route is not None else None
    if alternate is not None and run.allow_failover():
        assert route is not None
        route.note_switch(provider, route.attempts[-1]["transport_failure_kind"] if route.attempts else None)
        return alternate
    if run.deadline_exceeded:
        return None
    return provider if same_provider_retry and run.allow_structural_retry() else None


def close_route(run: StageRun, route: StageRoute | None, *, completed: bool) -> None:
    """Ends the stage: frees unused probe slots and records what it did."""
    if route is None:
        run.close(completed=completed)
        return
    route.finish()
    if not completed:
        route.final = None
    run.close(completed=completed, providers=route.diagnostics())


def not_configured_message(selected: str) -> str:
    if selected == GEMINI:
        return "Gemini is not configured (GEMINI_API_KEY / GEMINI_MODEL unset)."
    return "Groq API key is not configured (GROQ_API_KEY unset)."


_UNAVAILABLE_MESSAGE = (
    "AI model call skipped: no configured model provider is currently available "
    "(each one recently refused or failed a request)."
)


def unavailable_failure(route: StageRoute) -> tuple[AIProviderFailureKind, str]:
    """The fixed failure of a routed stage that could call nobody. A rate
    limit stays a rate limit for whoever classifies the result."""
    reasons = {route.open_reason(provider) for provider in route.chain}
    kind = AIProviderFailureKind.RATE_LIMITED if HEALTH_RATE_LIMIT in reasons else AIProviderFailureKind.PROVIDER_ERROR
    return kind, _UNAVAILABLE_MESSAGE


def answering_provider(route: StageRoute | None, selected: str = GROQ) -> str:
    """The provider the stage's result is attributed to: the one that got
    its last request, else the one the stage is configured for."""
    if route is not None and route.attempts:
        return route.attempts[-1]["provider"]
    return selected


def gemini_wording(text: str | None) -> str | None:
    """The adapters' fixed sentences name Groq; the same sentence about a
    Gemini request names Gemini."""
    return text.replace("Groq", "Gemini") if isinstance(text, str) else text


def provider_timing_key(stage: str, provider: str) -> str:
    """`groq_anchor` stays the stage's id; a Gemini request of that stage is
    timed as `gemini_anchor`."""
    return stage if provider == GROQ else f"{provider}_{stage.split('_', 1)[-1]}"


def provider_call(stage: str, provider: str) -> Any:
    return performance.provider_call(provider_timing_key(stage, provider))
