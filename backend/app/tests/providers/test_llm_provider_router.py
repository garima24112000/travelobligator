from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core import performance
from app.core.config import Settings, get_settings
from app.models.ai_itinerary_reasoning import AIItineraryReasoningStatus
from app.models.itinerary_narrative import ItineraryNarrativeStatus
from app.providers import llm_structured_clients
from app.providers.ai_candidate_proposal import get_ai_candidate_proposal_provider
from app.providers.ai_candidate_proposal.anthropic_adapter import AnthropicAICandidateProposalProvider
from app.providers.ai_candidate_proposal.not_connected_adapter import NotConnectedAICandidateProposalProvider
from app.providers.ai_itinerary_reasoning import get_ai_itinerary_reasoning_provider
from app.providers.ai_itinerary_reasoning.anthropic_adapter import AnthropicAIItineraryReasoningProvider
from app.providers.ai_itinerary_reasoning.gemini_adapter import GeminiAIItineraryReasoningProvider
from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider
from app.providers.ai_itinerary_reasoning.not_connected_adapter import NotConnectedAIItineraryReasoningProvider
from app.providers.ai_stage_budget import StageRun
from app.providers.itinerary_narrator import get_itinerary_narrator_provider
from app.providers.itinerary_narrator.anthropic_adapter import AnthropicItineraryNarratorProvider
from app.providers.itinerary_narrator.gemini_adapter import GeminiItineraryNarratorProvider
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.providers.itinerary_narrator.not_connected_adapter import NotConnectedItineraryNarratorProvider
from app.providers.llm_provider_health import GEMINI, GROQ, LLMProviderHealthRegistry, LLMProviderState, get_llm_provider_health
from app.providers.llm_provider_router import StageRoute, resolve_chain, start_route
from app.services.itinerary_narrative_service import ItineraryNarrativeService
from app.tests.providers.llm_failover_support import (
    GEMINI_KEY,
    GEMINI_MODEL,
    GROQ_KEY,
    FakeClock,
    ScriptedClient,
    StatusError,
    configure_pair,
    install_clients,
    json_response,
    structural_error,
    use_clock,
)
from app.tests.providers.test_groq_ai_itinerary_reasoning_provider import (
    _request as _reasoning_request,
    _valid_output as _reasoning_output,
)
from app.tests.providers.test_narrator_structural_retry_202c1d import (
    _VALID as _NARRATIVE,
    _request as _narrator_request,
    _state as _narrator_state,
)

# Provider selection and failover of the Groq <-> Gemini pair, driven through
# the real stage adapters with scripted clients (no network, fake clock).

HEALTHY, DRAINING, OPEN, HALF_OPEN = (
    LLMProviderState.HEALTHY, LLMProviderState.DRAINING, LLMProviderState.OPEN, LLMProviderState.HALF_OPEN,
)
SUCCESS, FAILED = ItineraryNarrativeStatus.SUCCESS, ItineraryNarrativeStatus.FAILED


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    use_clock(monkeypatch, fake)
    return fake


@pytest.fixture()
def pair(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_pair(monkeypatch)


def _health() -> LLMProviderHealthRegistry:
    return get_llm_provider_health()


def _narrate(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, *, groq: list[Any] | None = None, gemini: list[Any] | None = None
) -> tuple[Any, list[str], dict[str, Any]]:
    """One narrator stage. Each step is an answer to return or an exception
    to raise. Returns the report, the providers requested (in order) and the
    stage's recorded figures."""
    groq_client = ScriptedClient(clock, *((0.1, step) for step in groq or []))
    gemini_client = ScriptedClient(clock, *((0.1, step) for step in gemini or []))
    built = install_clients(monkeypatch, GroqItineraryNarratorProvider, groq=groq_client, gemini=gemini_client)
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        report = get_itinerary_narrator_provider("groq").narrate(_narrator_request())
    assert len(groq_client.prompts) + len(gemini_client.prompts) == len(built)
    return report, [provider for provider, _ in built], recorder.snapshot()["llm_stages"].get("groq_narrator", {})


def _reason(monkeypatch: pytest.MonkeyPatch, clock: FakeClock, *, groq: list[Any] | None = None, gemini: list[Any] | None = None) -> tuple[Any, list[str]]:
    groq_client = ScriptedClient(clock, *((0.1, step) for step in groq or []))
    gemini_client = ScriptedClient(clock, *((0.1, step) for step in gemini or []))
    built = install_clients(monkeypatch, GroqAIItineraryReasoningProvider, groq=groq_client, gemini=gemini_client)
    result = get_ai_itinerary_reasoning_provider("groq").reason(_reasoning_request())
    return result, [provider for provider, _ in built]


# -- 1-2: the healthy path -------------------------------------------------------------------


def test_with_both_healthy_groq_is_selected_and_gemini_is_not_called(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, requested, stage = _narrate(monkeypatch, clock, groq=[_NARRATIVE])

    assert report.status == SUCCESS and report.provider == "groq_itinerary_narrator_provider"
    assert requested == ["groq"]
    assert (stage["preferred_provider"], stage["selected_provider"], stage["final_provider"]) == ("groq", "groq", "groq")
    assert stage["attempted_providers"] == ["groq"] and stage["failover_used"] is False
    assert stage["failover_reason"] is None and stage["total_provider_requests"] == 1
    assert stage["provider_health_before"] == stage["provider_health_after"] == {"groq": "healthy", "gemini": "healthy"}


# -- 3-5: transport failures fail over -------------------------------------------------------


def test_a_groq_rate_limit_opens_groq_and_gemini_serves_the_second_request(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, requested, stage = _narrate(
        monkeypatch, clock, groq=[StatusError(429, headers={"retry-after": "900"})], gemini=[_NARRATIVE]
    )

    assert report.status == SUCCESS
    assert requested == ["groq", "gemini"]
    # the answer is attributed to the provider that gave it -- metadata only
    assert (report.provider, report.model) == ("gemini_itinerary_narrator_provider", GEMINI_MODEL)
    assert _health().state(GROQ) == OPEN and _health().open_reason(GROQ) == "rate_limit"
    # a long quota window is never slept through inside the stage
    assert clock.slept == []
    assert (stage["attempts"], stage["result"], stage["final_provider"]) == (2, "success", "gemini")
    assert (stage["failover_used"], stage["failover_reason"]) == (True, "groq_rate_limit")
    assert stage["provider_health_after"] == {"groq": "open", "gemini": "healthy"}
    assert [(a["provider"], a["result"], a["transport_failure_kind"]) for a in stage["provider_attempts"]] == [
        ("groq", "transport_failure", "rate_limit"), ("gemini", "success", None),
    ]
    clock.now += 898.0
    assert _health().state(GROQ) == OPEN
    clock.now += 2.0
    assert _health().state(GROQ) == HALF_OPEN


def test_a_groq_503_opens_groq_for_the_short_cooldown_and_gemini_serves(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, requested, stage = _narrate(monkeypatch, clock, groq=[StatusError(503)], gemini=[_NARRATIVE])

    assert report.status == SUCCESS and requested == ["groq", "gemini"] and clock.slept == []
    assert stage["failover_reason"] == "groq_provider_unavailable"
    assert _health().open_reason(GROQ) == "provider_unavailable"
    clock.now += 58.0
    assert _health().state(GROQ) == OPEN
    clock.now += 2.0
    assert _health().state(GROQ) == HALF_OPEN  # short cooldown, never a quota wait


def test_a_groq_timeout_fails_over_to_gemini(pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    report, requested, stage = _narrate(monkeypatch, clock, groq=[TimeoutError("read timeout")], gemini=[_NARRATIVE])

    assert report.status == SUCCESS and requested == ["groq", "gemini"]
    assert stage["failover_reason"] == "groq_timeout" and _health().state(GROQ) == OPEN


# -- 6-8: proactive draining from Groq's own rate-limit headers --------------------------------

_NARRATIVE_WIRE = {**_NARRATIVE, "getting_around_profile": "", "getting_around_advisory": ""}


def _groq_http(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[httpx.Request]:
    """The REAL langchain-groq / Groq SDK path over a mock HTTP transport."""
    requests: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        llm_structured_clients,
        "_new_http_client",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(recording), **kwargs),
    )
    return requests


def _groq_completion(headers: dict[str, str]) -> httpx.Response:
    return json_response(
        {
            "id": "x", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(_NARRATIVE_WIRE)}}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
        headers=headers,
    )


@pytest.mark.parametrize(
    ("headers", "reason_key"),
    [
        (
            {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "80", "x-ratelimit-reset-requests": "10m0s",
             "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "7000", "x-ratelimit-reset-tokens": "5s"},
            "remaining_request_ratio",
        ),
        (
            {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "900", "x-ratelimit-reset-requests": "10m0s",
             "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "400", "x-ratelimit-reset-tokens": "40s"},
            "remaining_token_ratio",
        ),
    ],
)
def test_groq_near_a_quota_keeps_its_answer_and_the_next_stage_prefers_gemini(
    pair: None, monkeypatch: pytest.MonkeyPatch, headers: dict[str, str], reason_key: str
) -> None:
    requests = _groq_http(monkeypatch, lambda request: _groq_completion(headers))
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        report = get_itinerary_narrator_provider("groq").narrate(_narrator_request())

    # the request that revealed the low quota is a normal success
    assert report.status == SUCCESS and report.provider == "groq_itinerary_narrator_provider"
    assert len(requests) == 1
    assert _health().state(GROQ) == DRAINING
    stage = recorder.snapshot()["llm_stages"]["groq_narrator"]
    assert stage["provider_health_after"]["groq"] == "draining"
    assert stage["groq_quota"][reason_key] <= 0.10
    # only numbers were kept from the headers
    assert set(stage["groq_quota"]) == {"remaining_request_ratio", "remaining_token_ratio", "reset_seconds"}

    # the next stage goes to Gemini first, without a failed Groq request
    gemini = ScriptedClient(None, (0.0, _reasoning_output()))
    built = install_clients(monkeypatch, GroqAIItineraryReasoningProvider, groq=ScriptedClient(None), gemini=gemini)
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        result = get_ai_itinerary_reasoning_provider("groq").reason(_reasoning_request())
    assert result.status == AIItineraryReasoningStatus.COMPLETED
    assert [provider for provider, _ in built] == ["gemini"]
    stage = recorder.snapshot()["llm_stages"]["groq_reasoning"]
    assert (stage["selected_provider"], stage["failover_used"], stage["failover_reason"]) == ("gemini", True, "groq_draining")


def test_groq_with_headroom_stays_healthy(pair: None, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "900",
               "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "7000"}
    _groq_http(monkeypatch, lambda request: _groq_completion(headers))
    assert get_itinerary_narrator_provider("groq").narrate(_narrator_request()).status == SUCCESS
    assert _health().state(GROQ) == HEALTHY


def test_groq_without_quota_headers_is_never_guessed_to_be_draining(pair: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _groq_http(monkeypatch, lambda request: _groq_completion({}))
    assert get_itinerary_narrator_provider("groq").narrate(_narrator_request()).status == SUCCESS
    assert _health().state(GROQ) == HEALTHY and _health().quota_report(GROQ) == {}


def test_the_groq_sdk_makes_no_hidden_retries(pair: None, monkeypatch: pytest.MonkeyPatch, clock: FakeClock) -> None:
    """One invocation = one HTTP request: a 429 is not repeated by the SDK,
    and the stage's second request goes to Gemini."""
    requests = _groq_http(
        monkeypatch,
        lambda request: json_response({"error": {"message": "x", "code": "rate_limit_exceeded"}}, 429, {"retry-after": "120"}),
    )
    gemini = ScriptedClient(clock, (0.1, _NARRATIVE))
    monkeypatch.setattr(GroqItineraryNarratorProvider, "_build_gemini_client", lambda self, timeout: gemini)

    report = get_itinerary_narrator_provider("groq").narrate(_narrator_request())

    assert report.status == SUCCESS and len(requests) == 1 and len(gemini.prompts) == 1
    assert _health().state(GROQ) == OPEN
    # the request carried the key only as a header, never in the URL
    assert GROQ_KEY not in str(requests[0].url)


# -- 9, 14: an OPEN provider is not called by later stages ------------------------------------


def test_an_open_groq_is_skipped_by_every_later_stage(pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    _narrate(monkeypatch, clock, groq=[StatusError(429, headers={"retry-after": "3600"})], gemini=[_NARRATIVE])

    result, requested = _reason(monkeypatch, clock, gemini=[_reasoning_output()])
    assert result.status == AIItineraryReasoningStatus.COMPLETED and requested == ["gemini"]
    assert result.provider_name == "gemini_ai_itinerary_reasoning_provider" and result.model_name == GEMINI_MODEL

    report, requested, stage = _narrate(monkeypatch, clock, gemini=[_NARRATIVE])
    assert report.status == SUCCESS and requested == ["gemini"]
    assert (stage["selected_provider"], stage["failover_used"], stage["failover_reason"]) == (
        "gemini", True, "groq_circuit_open",
    )
    assert stage["provider_health_before"] == {"groq": "open", "gemini": "healthy"}


# -- 10-13: HALF_OPEN ------------------------------------------------------------------------


def test_after_the_cooldown_one_stage_probes_groq_and_a_success_makes_it_healthy(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _narrate(monkeypatch, clock, groq=[StatusError(429, headers={"retry-after": "30"})], gemini=[_NARRATIVE])
    clock.now += 30.0
    assert _health().state(GROQ) == HALF_OPEN

    report, requested, stage = _narrate(monkeypatch, clock, groq=[_NARRATIVE])

    assert report.status == SUCCESS and requested == ["groq"]
    assert stage["provider_health_before"]["groq"] == "half_open" and stage["provider_health_after"]["groq"] == "healthy"
    assert _health().state(GROQ) == HEALTHY


def test_a_failed_probe_reopens_groq_and_gemini_takes_the_second_request(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _narrate(monkeypatch, clock, groq=[StatusError(429, headers={"retry-after": "30"})], gemini=[_NARRATIVE])
    clock.now += 30.0

    report, requested, stage = _narrate(
        monkeypatch, clock, groq=[StatusError(429, headers={"retry-after": "600"})], gemini=[_NARRATIVE]
    )

    assert report.status == SUCCESS and requested == ["groq", "gemini"]  # still two requests at most
    assert _health().state(GROQ) == OPEN
    clock.now += 590.0
    assert _health().state(GROQ) == OPEN  # reopened with the NEW reset, not the old one


def test_a_probe_answered_with_malformed_output_makes_groq_healthy_and_gemini_corrects(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _narrate(monkeypatch, clock, groq=[StatusError(503)], gemini=[_NARRATIVE])
    clock.now += 60.0

    report, requested, stage = _narrate(monkeypatch, clock, groq=[structural_error()], gemini=[_NARRATIVE])

    assert report.status == SUCCESS and requested == ["groq", "gemini"]
    # the provider processed the request: healthy, and the circuit is not reopened
    assert _health().state(GROQ) == HEALTHY
    assert stage["provider_attempts"][0]["transport_failure_kind"] == "malformed_response"
    assert stage["provider_attempts"][0]["structural_validation_result"] == "invalid"


def test_while_one_stage_probes_groq_a_concurrent_stage_uses_gemini(pair: None, clock: FakeClock) -> None:
    health = _health()
    health.record_failure(GROQ, "provider_unavailable")
    clock.now += 60.0
    settings = get_settings()

    first = StageRoute("groq_narrator", resolve_chain(GROQ, settings))
    second = StageRoute("groq_reasoning", resolve_chain(GROQ, settings))
    assert first.first_provider() == GROQ  # the single probe
    assert second.first_provider() == GEMINI  # no probe storm
    assert second.failover_reason == "groq_circuit_open"

    # a probe that was never sent gives its slot back
    first.finish()
    assert StageRoute("groq_repair", resolve_chain(GROQ, settings)).first_provider() == GROQ


def test_selection_is_deterministic_for_identical_state(pair: None, clock: FakeClock) -> None:
    settings = get_settings()
    _health().record_failure(GROQ, "rate_limit", 600.0)
    choices = {StageRoute("groq_anchor", resolve_chain(GROQ, settings)).first_provider() for _ in range(20)}
    assert choices == {GEMINI}


# -- 15-17, section 19: Gemini's own failures --------------------------------------------------


def test_a_gemini_rate_limit_opens_gemini_and_groq_serves_when_gemini_is_primary(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_pair(monkeypatch, LLM_PRIMARY_PROVIDER="gemini", LLM_SECONDARY_PROVIDER="groq")
    report, requested, stage = _narrate(
        monkeypatch, clock, gemini=[StatusError(429, headers={"retry-after": "45"})], groq=[_NARRATIVE]
    )

    assert report.status == SUCCESS and requested == ["gemini", "groq"]
    assert report.provider == "groq_itinerary_narrator_provider"
    assert _health().state(GEMINI) == OPEN and _health().open_reason(GEMINI) == "rate_limit"
    assert (stage["preferred_provider"], stage["final_provider"], stage["failover_reason"]) == (
        "gemini", "groq", "gemini_rate_limit",
    )
    clock.now += 44.0
    assert _health().state(GEMINI) == OPEN
    clock.now += 2.0
    assert _health().state(GEMINI) == HALF_OPEN


class _GeminiUnavailable(Exception):
    """google-genai's `ServerError` for the observed high-demand response:
    HTTP 503, status `UNAVAILABLE`."""

    def __init__(self) -> None:
        super().__init__("503 UNAVAILABLE. This model is currently experiencing high demand")
        self.code, self.status = 503, "UNAVAILABLE"


def test_gemini_high_demand_503_is_temporary_unavailability_not_quota(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Groq is draining, so Gemini is tried first; Gemini answers 503 UNAVAILABLE.
    from app.providers.llm_provider_health import GroqQuotaSnapshot

    _health().record_transport_success(GROQ, groq_quota=GroqQuotaSnapshot(remaining_request_ratio=0.01, reset_requests_seconds=3600.0))
    report, requested, stage = _narrate(monkeypatch, clock, gemini=[_GeminiUnavailable()], groq=[_NARRATIVE])

    # Groq (draining, but callable) serves the alternate attempt
    assert report.status == SUCCESS and requested == ["gemini", "groq"]
    attempt = stage["provider_attempts"][0]
    assert (attempt["provider"], attempt["transport_failure_kind"]) == ("gemini", "provider_unavailable")
    assert _health().open_reason(GEMINI) == "provider_unavailable"  # not `rate_limit`
    assert "high demand" not in json.dumps(stage)
    # a SHORT cooldown, after which Gemini is probe-eligible again
    clock.now += 59.0
    assert _health().state(GEMINI) == OPEN
    clock.now += 1.0
    assert _health().state(GEMINI) == HALF_OPEN and _health().try_acquire_probe(GEMINI) is True


def test_gemini_503_with_groq_also_unavailable_ends_on_the_deterministic_fallback(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _health().record_failure(GROQ, "rate_limit", 3600.0)
    # Gemini's failure opens its circuit at once; Groq is already open, so
    # nobody is eligible: no second request (a second answer is scripted --
    # it is never asked for) and no backoff.
    report, requested, stage = _narrate(monkeypatch, clock, gemini=[_GeminiUnavailable(), _NARRATIVE])

    assert report.status == FAILED and report.summary is None  # nothing fabricated
    assert requested == ["gemini"] and stage["final_provider"] is None
    assert (stage["attempts"], stage["transport_retries"], stage["total_provider_requests"]) == (1, 0, 1)
    assert clock.slept == [] and stage["failover_reason"] == "groq_circuit_open"
    assert stage["provider_health_after"] == {"groq": "open", "gemini": "open"}
    assert _health().open_reason(GEMINI) == "provider_unavailable"


# -- a routed stage never retries the provider that just failed ---------------------------------


def test_groq_open_and_a_gemini_503_is_one_gemini_call_then_the_fallback(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _health().record_failure(GROQ, "provider_unavailable")
    report, requested, stage = _narrate(monkeypatch, clock, gemini=[StatusError(503), _NARRATIVE])

    assert report.status == FAILED and requested == ["gemini"] and clock.slept == []
    assert stage["total_provider_requests"] == 1 and stage["result"] == "failed"
    assert _health().state(GEMINI) == OPEN
    clock.now += 60.0
    assert _health().state(GEMINI) == HALF_OPEN  # the short cooldown


def test_groq_open_and_a_gemini_429_is_one_gemini_call_then_the_fallback(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _health().record_failure(GROQ, "rate_limit", 3600.0)
    # a short Retry-After that WOULD fit the budget is still not waited for
    report, requested, stage = _narrate(
        monkeypatch, clock, gemini=[StatusError(429, headers={"retry-after": "2"}), _NARRATIVE]
    )

    assert report.status == FAILED and report.failure_kind == "rate_limited"
    assert requested == ["gemini"] and clock.slept == [] and stage["total_provider_requests"] == 1
    assert _health().state(GEMINI) == OPEN and _health().open_reason(GEMINI) == "rate_limit"  # quota semantics
    clock.now += 2.0
    assert _health().state(GEMINI) == HALF_OPEN


def test_gemini_open_and_a_groq_503_is_one_groq_call_then_the_fallback(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _health().record_failure(GEMINI, "rate_limit", 3600.0)
    report, requested, stage = _narrate(monkeypatch, clock, groq=[StatusError(503), _NARRATIVE])

    assert report.status == FAILED and requested == ["groq"] and clock.slept == []
    assert (stage["attempts"], stage["transport_retries"], stage["failover_used"]) == (1, 0, False)
    assert stage["provider_health_after"] == {"groq": "open", "gemini": "open"}


@pytest.mark.parametrize(
    "failure",
    [StatusError(429), StatusError(503), StatusError(500), TimeoutError("read timeout"), ConnectionError("reset"), StatusError(401)],
    ids=["429", "503", "5xx", "timeout", "network", "authentication"],
)
def test_no_kind_of_transport_failure_is_retried_on_the_same_provider_in_a_routed_stage(
    failure: Exception, pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _health().record_failure(GEMINI, "provider_unavailable")
    report, requested, stage = _narrate(monkeypatch, clock, groq=[failure, _NARRATIVE])

    assert report.status == FAILED and requested == ["groq"] and clock.slept == []
    assert stage["total_provider_requests"] == 1 and _health().state(GROQ) == OPEN


def test_a_groq_503_with_gemini_healthy_is_groq_once_then_gemini_once(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, requested, _ = _narrate(monkeypatch, clock, groq=[StatusError(503)], gemini=[_NARRATIVE])
    assert report.status == SUCCESS and requested == ["groq", "gemini"]

    get_llm_provider_health().reset()
    # ...and when Gemini fails too, that is the end: Groq is not asked again
    report, requested, stage = _narrate(
        monkeypatch, clock, groq=[StatusError(503), _NARRATIVE], gemini=[StatusError(503), _NARRATIVE]
    )
    assert report.status == FAILED and requested == ["groq", "gemini"] and stage["total_provider_requests"] == 2


def test_the_failover_disabled_groq_only_path_keeps_its_same_provider_retry(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_pair(monkeypatch, LLM_FAILOVER_ENABLED="false")
    report, requested, stage = _narrate(monkeypatch, clock, groq=[StatusError(503), _NARRATIVE])
    assert report.status == SUCCESS and requested == ["groq", "groq"] and clock.slept == [1.0]
    assert stage["transport_retries"] == 1

    # and with only Groq configured (failover on, nobody to fail over to)
    get_llm_provider_health().reset()
    configure_pair(monkeypatch, gemini=False)
    clock.slept.clear()
    report, requested, _ = _narrate(monkeypatch, clock, groq=[TimeoutError("read timeout"), _NARRATIVE])
    assert report.status == SUCCESS and requested == ["groq", "groq"] and clock.slept == [1.0]


# -- 18: nobody may be called ------------------------------------------------------------------


def test_with_both_providers_open_no_request_is_made_and_the_fallback_runs(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _health().record_failure(GROQ, "rate_limit", 3600.0)
    _health().record_failure(GEMINI, "provider_unavailable")

    report, requested, stage = _narrate(monkeypatch, clock)

    assert report.status == FAILED and report.failure_kind == "rate_limited" and requested == []
    assert (stage["attempts"], stage["result"], stage["total_provider_requests"]) == (0, "failed", 0)
    assert (stage["selected_provider"], stage["final_provider"], stage["failover_reason"]) == (
        None, None, "all_providers_unavailable",
    )

    # the service narrates deterministically, exactly as for any other model failure
    monkeypatch.setenv("ITINERARY_NARRATOR_ENABLED", "true")
    monkeypatch.setenv("ITINERARY_NARRATOR_PROVIDER", "groq")
    get_settings.cache_clear()
    built = install_clients(monkeypatch, GroqItineraryNarratorProvider)
    state = ItineraryNarrativeService().generate(_narrator_state())
    assert built == [] and state.itinerary_narrative_report.narrative_source == "deterministic_fallback"


# -- 19: credentials ---------------------------------------------------------------------------


def test_rejected_credentials_are_never_retried_and_the_other_provider_still_runs(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, requested, stage = _narrate(monkeypatch, clock, groq=[StatusError(401)], gemini=[_NARRATIVE])
    assert report.status == SUCCESS and requested == ["groq", "gemini"]
    assert stage["failover_reason"] == "groq_authentication_error"

    clock.now += 7 * 86_400.0  # no cooldown ever re-admits it
    for _ in range(3):
        _, requested, _ = _narrate(monkeypatch, clock, gemini=[_NARRATIVE])
        assert requested == ["gemini"]


def test_rejected_credentials_with_no_alternate_end_the_stage_after_one_request(
    pair: None, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _health().record_failure(GEMINI, "provider_unavailable")
    report, requested, _ = _narrate(monkeypatch, clock, groq=[StatusError(401)])
    assert report.status == FAILED and report.failure_kind == "authentication" and requested == ["groq"]


# -- 20 and the configuration matrix: who is in the route --------------------------------------


def test_with_failover_disabled_the_stage_uses_exactly_its_selected_provider(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_pair(monkeypatch, LLM_FAILOVER_ENABLED="false")
    # the existing same-provider transport retry (and its Retry-After wait) is what happens
    report, requested, stage = _narrate(
        monkeypatch, clock, groq=[StatusError(429, headers={"retry-after": "2"}), _NARRATIVE]
    )

    assert report.status == SUCCESS and requested == ["groq", "groq"] and clock.slept == [2.0]
    assert stage["transport_retries"] == 1 and stage["failover_used"] is False
    assert stage["provider_health_before"] == {"groq": "healthy"}  # Gemini is not part of the stage


def test_a_single_provider_stage_is_tried_whatever_was_last_observed(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_pair(monkeypatch, gemini=False)
    _health().record_failure(GROQ, "rate_limit", 3600.0)
    report, requested, _ = _narrate(monkeypatch, clock, groq=[_NARRATIVE])
    assert report.status == SUCCESS and requested == ["groq"]  # existing Groq-only behaviour


def _settings(**values: Any) -> Settings:
    base = {"GROQ_API_KEY": GROQ_KEY, "GEMINI_API_KEY": GEMINI_KEY, "GEMINI_MODEL": GEMINI_MODEL}
    return Settings(_env_file=None, **{**base, **values})


@pytest.mark.parametrize(
    ("values", "selected", "chain"),
    [
        ({}, GROQ, [GROQ, GEMINI]),
        ({}, GEMINI, [GROQ, GEMINI]),  # the selector opts in; the global order decides who goes first
        ({"LLM_PRIMARY_PROVIDER": "gemini", "LLM_SECONDARY_PROVIDER": "groq"}, GROQ, [GEMINI, GROQ]),
        ({"GEMINI_API_KEY": ""}, GROQ, [GROQ]),  # A: Groq only
        ({"GEMINI_MODEL": ""}, GROQ, [GROQ]),  # a key without a model name is not a configured Gemini
        ({"GROQ_API_KEY": ""}, GROQ, [GEMINI]),  # B: Gemini only
        ({"GROQ_API_KEY": "", "GEMINI_API_KEY": ""}, GROQ, []),  # D: neither
        ({"LLM_FAILOVER_ENABLED": "false"}, GROQ, [GROQ]),  # E
        ({"LLM_FAILOVER_ENABLED": "false"}, GEMINI, [GEMINI]),
        ({"LLM_FAILOVER_ENABLED": "false", "GROQ_API_KEY": ""}, GROQ, []),  # never silently the other one
    ],
)
def test_the_route_is_the_configured_members_of_the_pair(values: dict[str, str], selected: str, chain: list[str]) -> None:
    assert resolve_chain(selected, _settings(**values)) == chain


def test_only_a_two_provider_route_enables_failover_and_it_caps_the_stage_at_two_requests() -> None:
    def run() -> StageRun:
        return StageRun("groq_reasoning", total_budget_seconds=20, request_timeout_seconds=30, transport_retries=2)

    legacy = run()
    start_route(legacy, GROQ, _settings(GEMINI_API_KEY=""))
    assert legacy.max_attempts == 3  # the historical GROQ_MAX_RETRIES=2 behaviour is untouched

    routed = run()
    start_route(routed, GROQ, _settings())
    assert routed.max_attempts == 2


def test_gemini_only_configuration_serves_a_groq_selected_stage(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_pair(monkeypatch, groq=False)
    report, requested, stage = _narrate(monkeypatch, clock, gemini=[_NARRATIVE])
    assert report.status == SUCCESS and requested == ["gemini"]
    assert stage["preferred_provider"] == "gemini" and stage["failover_used"] is False


def test_neither_provider_configured_is_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_pair(monkeypatch, groq=False, gemini=False)
    report = get_itinerary_narrator_provider("groq").narrate(_narrator_request())
    assert report.status == ItineraryNarrativeStatus.NOT_CONNECTED and "GROQ_API_KEY" in (report.message or "")
    report = get_itinerary_narrator_provider("gemini").narrate(_narrator_request())
    assert report.status == ItineraryNarrativeStatus.NOT_CONNECTED and "GEMINI_API_KEY" in (report.message or "")
    assert report.provider == "gemini_itinerary_narrator_provider"


# -- existing gates stay authoritative ---------------------------------------------------------


def test_a_disabled_stage_makes_no_groq_and_no_gemini_call(pair: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITINERARY_NARRATOR_ENABLED", "false")
    monkeypatch.setenv("ITINERARY_NARRATOR_PROVIDER", "groq")
    monkeypatch.setenv("AI_ITINERARY_REASONING_ENABLED", "false")
    monkeypatch.setenv("AI_ITINERARY_REASONING_PROVIDER", "groq")
    get_settings.cache_clear()
    narrator_built = install_clients(monkeypatch, GroqItineraryNarratorProvider)
    reasoning_built = install_clients(monkeypatch, GroqAIItineraryReasoningProvider)

    state = ItineraryNarrativeService().generate(_narrator_state())
    assert state.itinerary_narrative_report.status == ItineraryNarrativeStatus.NOT_CONNECTED

    from app.services.ai_itinerary_reasoning_service import AIItineraryReasoningService

    result = AIItineraryReasoningService().reason(_narrator_state())
    assert result.status != AIItineraryReasoningStatus.COMPLETED
    assert narrator_built == [] and reasoning_built == []


def test_a_not_connected_selector_stays_not_connected_with_both_keys_present(pair: None) -> None:
    # the defaults: every stage selector is "not_connected"
    assert isinstance(get_ai_candidate_proposal_provider(), NotConnectedAICandidateProposalProvider)
    assert isinstance(get_ai_itinerary_reasoning_provider(), NotConnectedAIItineraryReasoningProvider)
    assert isinstance(get_itinerary_narrator_provider(), NotConnectedItineraryNarratorProvider)
    # and an unrecognized selector never becomes a member of the pair
    assert isinstance(get_itinerary_narrator_provider("openai"), NotConnectedItineraryNarratorProvider)
    report = get_itinerary_narrator_provider().narrate(_narrator_request())
    assert report.status == ItineraryNarrativeStatus.NOT_CONNECTED


def test_an_anthropic_selector_keeps_the_anthropic_adapter_and_is_never_routed(
    pair: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = install_clients(monkeypatch, GroqItineraryNarratorProvider)
    providers = (
        get_ai_candidate_proposal_provider("anthropic"),
        get_ai_itinerary_reasoning_provider("anthropic"),
        get_itinerary_narrator_provider("anthropic"),
    )
    assert isinstance(providers[0], AnthropicAICandidateProposalProvider)
    assert isinstance(providers[1], AnthropicAIItineraryReasoningProvider)
    assert isinstance(providers[2], AnthropicItineraryNarratorProvider)
    assert not any(hasattr(provider, "_selected_provider") for provider in providers)
    # no Anthropic key in this environment: its own honest not_connected, no Groq / Gemini request
    report = providers[2].narrate(_narrator_request())
    assert report.status == ItineraryNarrativeStatus.NOT_CONNECTED and built == []
    assert _health().states([GROQ, GEMINI]) == {"groq": "healthy", "gemini": "healthy"}


def test_the_gemini_selector_is_the_same_stage_adapter() -> None:
    assert isinstance(get_itinerary_narrator_provider("gemini"), GeminiItineraryNarratorProvider)
    assert issubclass(GeminiItineraryNarratorProvider, GroqItineraryNarratorProvider)
    assert isinstance(get_ai_itinerary_reasoning_provider("gemini"), GeminiAIItineraryReasoningProvider)
    # nothing but the pair member differs: no method is overridden
    for cls in (GeminiItineraryNarratorProvider, GeminiAIItineraryReasoningProvider):
        assert [name for name, value in vars(cls).items() if callable(value)] == []
        assert cls._selected_provider == GEMINI


def test_an_injected_client_is_never_routed(pair: None, clock: FakeClock) -> None:
    client = ScriptedClient(clock, (0.1, StatusError(429)), (0.1, _NARRATIVE))
    report = GroqItineraryNarratorProvider(client=client).narrate(_narrator_request())
    assert report.status == SUCCESS and len(client.prompts) == 2
    assert _health().state(GROQ) == HEALTHY  # nothing was selected, nothing was recorded
