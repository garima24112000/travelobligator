from __future__ import annotations

import threading
import time
from typing import Any, Callable

import httpx
import pytest

from app.core import performance, provider_usage
from app.core.bounded_concurrency import run_bounded
from app.core.config import Settings, get_settings
from app.core.provider_usage import ProviderUsageTracker, process_request_slots
from app.models.ai_candidate_proposal import AICandidateProposalFailureKind, AICandidateProposalStatus
from app.models.ai_itinerary_reasoning import AIItineraryReasoningStatus
from app.models.ai_itinerary_repair import AIItineraryRepairResult, AIItineraryRepairStatus
from app.models.generation_performance import GenerationPerformanceReport, LLMStagePerformance
from app.models.itinerary_narrative import ItineraryNarrativeReport, ItineraryNarrativeStatus
from app.providers import ai_stage_budget, geoapify_client
from app.providers.ai_candidate_proposal import groq_adapter as anchor_module
from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider
from app.providers.ai_failure import AIProviderFailureKind, is_transient_failure, safe_ai_failure_message
from app.providers.ai_itinerary_reasoning import groq_adapter as reasoning_module
from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider
from app.providers.ai_stage_budget import MIN_ATTEMPT_SECONDS, StageRun
from app.providers.errors import ProviderRequestError
from app.providers.itinerary_narrator import groq_adapter as narrator_module
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.providers.itinerary_narrator.structural_retry import RETRY_FORMAT_REMINDER
from app.services.itinerary_narrative_service import ItineraryNarrativeService
from app.tests.core.test_generation_performance_1a import (  # noqa: F401 - `production_like_pipeline` is a fixture
    _canary_args,
    _load_canary,
    production_like_pipeline,
)
from app.tests.providers.test_groq_ai_candidate_proposal_provider import (
    _request as _anchor_request,
    _valid_output as _anchor_output,
)
from app.tests.providers.test_groq_ai_itinerary_reasoning_provider import (
    _request as _reasoning_request,
    _valid_output as _reasoning_output,
)
from app.tests.providers.test_groq_ai_itinerary_repair_provider import (
    _request as _repair_request,
    _valid_output as _repair_output,
)
from app.tests.providers.test_narrator_structural_retry_202c1d import (
    _VALID as _NARRATIVE,
    _request as _narrator_request,
    _state as _narrator_state,
)

# Section 1C: every generation-time Groq stage answers to ONE total
# wall-clock budget and makes at most one recovery attempt; Geoapify
# requests are bounded per generation AND per process. No network and no
# real waiting here: a fake clock that the scripted model clients advance.

_KEY = "SENTINEL_1C_KEY_0001"


# The provider record of the Groq <-> Gemini pair sits beside the stage's own
# figures in the report (it has its own tests); these tests compare the
# figures exactly, without it.
_PROVIDER_RECORD_KEYS = frozenset(
    {
        "preferred_provider", "selected_provider", "attempted_providers", "final_provider", "failover_used",
        "failover_reason", "provider_health_before", "provider_health_after", "total_provider_requests",
        "provider_attempts", "groq_quota", "gemini_quota",
    }
)


def _figures(stage: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in stage.items() if key not in _PROVIDER_RECORD_KEYS}


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _StatusError(Exception):
    def __init__(self, status_code: int, code: str | None = None) -> None:
        super().__init__("RAW PROVIDER BODY org_SECRET must never surface")
        self.status_code = status_code
        self.code = code


def _structural() -> Exception:
    return _StatusError(400, "json_validate_failed")


class _TimedClient:
    """`invoke` takes `seconds` of fake time, then returns / raises the step."""

    def __init__(self, clock: _Clock, *steps: tuple[float, Any]) -> None:
        self._clock = clock
        self._steps = list(steps)
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        seconds, outcome = self._steps.pop(0)
        self._clock.now += seconds
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Every stage of the three Groq adapters runs on one fake clock, with
    the real (1 s) transport backoff -- taken from the fake clock, not slept."""
    fake = _Clock()
    monkeypatch.setattr(ai_stage_budget, "TRANSPORT_RETRY_BACKOFF_SECONDS", 1.0)
    for module in (anchor_module, reasoning_module, narrator_module):
        monkeypatch.setattr(
            module, "StageRun", lambda *args, **kwargs: StageRun(*args, clock=fake, sleep=fake.sleep, **kwargs)
        )
    return fake


def _built_with(monkeypatch: pytest.MonkeyPatch, provider_cls: Any, method: str, client: Any) -> list[float | None]:
    """Makes `provider_cls` build `client`, recording each attempt's timeout."""
    timeouts: list[float | None] = []

    def build(self: Any, *args: Any, timeout: float | None = None, **kwargs: Any) -> Any:
        timeouts.append(timeout)
        return client

    monkeypatch.setattr(provider_cls, method, build)
    return timeouts


# -- the budget itself -----------------------------------------------------------------------------------------------


def test_the_total_stage_budget_runs_on_the_monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    assert StageRun.__init__.__kwdefaults__["clock"] is time.monotonic
    # a wall-clock jump (NTP step, DST) cannot shorten or lengthen a budget
    monkeypatch.setattr(time, "time", lambda: 9_999_999_999.0)
    clock = _Clock()
    run = StageRun("groq_narrator", total_budget_seconds=25, request_timeout_seconds=20, clock=clock)
    assert run.remaining() == 25.0
    clock.now += 7.5
    assert run.remaining() == 17.5
    clock.now += 100
    assert run.remaining() == 0.0  # never negative


def test_an_attempt_gets_the_smaller_of_the_request_timeout_and_the_budget_left() -> None:
    clock = _Clock()
    run = StageRun("groq_narrator", total_budget_seconds=25, request_timeout_seconds=20, structural_retries=1, clock=clock)
    assert run.next_attempt_timeout() == 20  # the first attempt: the full per-request timeout
    clock.now += 18
    assert run.allow_structural_retry() is True
    assert run.next_attempt_timeout() == 7  # the retry: only what is left
    clock.now += 7
    assert run.next_attempt_timeout() is None and run.deadline_exceeded is True


def test_a_request_timeout_larger_than_the_budget_is_clipped_to_the_budget() -> None:
    run = StageRun("groq_reasoning", total_budget_seconds=20, request_timeout_seconds=30, clock=_Clock())
    assert run.next_attempt_timeout() == 20


def test_a_retry_is_not_started_without_a_meaningful_window() -> None:
    clock = _Clock()
    run = StageRun("groq_narrator", total_budget_seconds=25, request_timeout_seconds=20, structural_retries=1, clock=clock)
    run.next_attempt_timeout()
    clock.now += 25 - MIN_ATTEMPT_SECONDS + 0.5  # less than the minimum window is left
    assert run.allow_structural_retry() is False and run.deadline_exceeded is True
    assert run.attempts == 1 and run.structural_retries == 0


def test_the_transport_backoff_is_part_of_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_stage_budget, "TRANSPORT_RETRY_BACKOFF_SECONDS", 1.0)  # the production value
    clock = _Clock()
    run = StageRun("groq_anchor", total_budget_seconds=25, request_timeout_seconds=30, clock=clock, sleep=clock.sleep)
    run.next_attempt_timeout()
    clock.now += 21.5  # 3.5 s left: a 1 s pause would leave less than the minimum window
    assert run.allow_transport_retry(_StatusError(503)) is False
    assert run.deadline_exceeded is True and clock.slept == []


def test_recovery_attempts_never_multiply() -> None:
    clock = _Clock()
    # one structural retry and one transport retry allowed -> still 2 requests at most
    run = StageRun(
        "groq_anchor", total_budget_seconds=0, request_timeout_seconds=30, structural_retries=1,
        transport_retries=1, clock=clock, sleep=clock.sleep,
    )
    assert run.max_attempts == 2 and run.remaining() is None  # budget 0 = no deadline, the cap still holds
    run.next_attempt_timeout()
    assert run.allow_transport_retry(_StatusError(429)) is True
    run.next_attempt_timeout()
    assert run.allow_structural_retry() is False and run.allow_transport_retry(_StatusError(429)) is False
    assert (run.attempts, run.transport_retries, run.structural_retries, run.deadline_exceeded) == (2, 1, 0, False)


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (_StatusError(429), True), (_StatusError(408), True), (_StatusError(500), True), (_StatusError(503), True),
        (TimeoutError("t"), True), (ConnectionError("c"), True),
        (_StatusError(400, "json_validate_failed"), False),  # schema-invalid model output
        (_StatusError(400), False), (_StatusError(401), False), (_StatusError(403), False),
        (ValueError("deterministic validation rejection"), False),
    ],
)
def test_only_transient_transport_failures_are_retryable(exc: Exception, transient: bool) -> None:
    assert is_transient_failure(exc) is transient
    clock = _Clock()
    run = StageRun("groq_anchor", total_budget_seconds=25, request_timeout_seconds=30, clock=clock, sleep=clock.sleep)
    run.next_attempt_timeout()
    assert run.allow_transport_retry(exc) is transient


# -- narrator ----------------------------------------------------------------------------------------------------------


def test_a_fast_first_success_is_untouched(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _TimedClient(clock, (1.5, _NARRATIVE))
    timeouts = _built_with(monkeypatch, GroqItineraryNarratorProvider, "_build_client", client)
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        report = GroqItineraryNarratorProvider(api_key="k").narrate(_narrator_request())

    assert report.status == ItineraryNarrativeStatus.SUCCESS and report.failure_kind is None
    assert len(client.prompts) == 1 and timeouts == [20.0] and clock.slept == []
    assert {stage: _figures(entry) for stage, entry in recorder.snapshot()["llm_stages"].items()} == {
        "groq_narrator": {
            "attempts": 1, "structural_retries": 0, "transport_retries": 0, "deadline_exceeded": False,
            "result": "success",
        }
    }


def test_the_structural_retry_gets_only_the_remaining_budget(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _TimedClient(clock, (16.0, _structural()), (2.0, _NARRATIVE))
    timeouts = _built_with(monkeypatch, GroqItineraryNarratorProvider, "_build_client", client)
    report = GroqItineraryNarratorProvider(api_key="k").narrate(_narrator_request())

    assert report.status == ItineraryNarrativeStatus.SUCCESS
    assert timeouts == [20.0, 9.0]  # 25 s budget - 16 s already spent
    assert client.prompts[1] == f"{client.prompts[0]}\n\n{RETRY_FORMAT_REMINDER}"


def test_a_late_malformed_answer_goes_straight_to_the_fallback_without_a_retry(
    clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the live Lisbon case: a malformed first answer after most of the budget
    client = _TimedClient(clock, (23.0, _structural()), (2.0, _NARRATIVE))
    timeouts = _built_with(monkeypatch, GroqItineraryNarratorProvider, "_build_client", client)
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        report = GroqItineraryNarratorProvider(api_key="k").narrate(_narrator_request())

    assert len(client.prompts) == 1 and timeouts == [20.0]  # no second 20-second window
    assert report.status == ItineraryNarrativeStatus.FAILED
    assert report.failure_kind == "deadline_exceeded"
    assert report.message == safe_ai_failure_message("Groq", AIProviderFailureKind.DEADLINE_EXCEEDED)
    assert report.summary is None and report.daily_narratives == []  # nothing fabricated
    stage = recorder.snapshot()["llm_stages"]["groq_narrator"]
    assert _figures(stage) == {
        "attempts": 1, "structural_retries": 0, "transport_retries": 0, "deadline_exceeded": True, "result": "failed",
    }


def test_a_timed_out_first_attempt_gets_one_short_retry_inside_the_budget(
    clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _TimedClient(clock, (20.0, TimeoutError("read timeout")), (1.5, _NARRATIVE))
    timeouts = _built_with(monkeypatch, GroqItineraryNarratorProvider, "_build_client", client)
    report = GroqItineraryNarratorProvider(api_key="k").narrate(_narrator_request())

    assert report.status == ItineraryNarrativeStatus.SUCCESS
    assert clock.slept == [1.0] and timeouts == [20.0, 4.0]  # 25 - 20 - 1 s backoff
    assert client.prompts[0] == client.prompts[1]  # a transport retry repeats the same prompt


def test_the_whole_stage_never_outlasts_its_budget(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    started = clock.now
    # every attempt uses its full timeout
    client = _TimedClient(clock, (20.0, TimeoutError("t")), (4.0, TimeoutError("t")), (1.0, _NARRATIVE))
    _built_with(monkeypatch, GroqItineraryNarratorProvider, "_build_client", client)
    report = GroqItineraryNarratorProvider(api_key="k").narrate(_narrator_request())

    assert report.status == ItineraryNarrativeStatus.FAILED and report.failure_kind == "deadline_exceeded"
    assert len(client.prompts) == 2
    assert clock.now - started <= get_settings().groq_narrator_total_budget_seconds == 25.0


def test_the_narrator_service_falls_back_cleanly_when_the_budget_is_exhausted(
    clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "itinerary_narrator_enabled", True, raising=False)
    client = _TimedClient(clock, (24.0, "not structured"), (1.0, _NARRATIVE))
    provider = GroqItineraryNarratorProvider(client=client)
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        state = ItineraryNarrativeService(provider=provider).generate(_narrator_state())

    report = state.itinerary_narrative_report
    assert len(client.prompts) == 1
    assert report.status == ItineraryNarrativeStatus.FAILED and report.failure_kind == "deadline_exceeded"
    assert report.narrative_source == "deterministic_fallback"
    assert report.daily_narratives and "Belem Tower" in report.daily_narratives[0].narrative  # fact-only text
    assert "SECRET" not in report.model_dump_json() and "did not answer within the time allowed" in report.message
    assert recorder.snapshot()["llm_stages"]["groq_narrator"]["deadline_exceeded"] is True


def test_a_grounding_rejection_after_a_model_answer_is_recorded_as_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "itinerary_narrator_enabled", True, raising=False)
    invented = {
        **_NARRATIVE,
        "daily_narratives": [{**_NARRATIVE["daily_narratives"][0], "narrative": "Start at Belem Tower, then Sintra Palace."}],
    }

    class _Client:
        def invoke(self, prompt: str) -> Any:
            return invented

    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        state = ItineraryNarrativeService(provider=GroqItineraryNarratorProvider(client=_Client())).generate(
            _narrator_state()
        )
    assert state.itinerary_narrative_report.narrative_source == "deterministic_fallback"
    stage = recorder.snapshot()["llm_stages"]["groq_narrator"]
    assert (stage["attempts"], stage["deadline_exceeded"], stage["result"]) == (1, False, "fallback")


# -- anchor proposal ----------------------------------------------------------------------------------------------------


def test_anchor_budget_exhaustion_is_a_degraded_factual_outcome(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    # the live Jaipur case: a slow malformed first answer; the retry would have doubled the stage
    client = _TimedClient(clock, (23.5, _structural()), (9.0, _anchor_output()))
    timeouts = _built_with(monkeypatch, GroqAICandidateProposalProvider, "_build_client", client)
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        result = GroqAICandidateProposalProvider(api_key="k").propose(_anchor_request())

    assert len(client.prompts) == 1 and timeouts == [25.0]  # min(30 s request timeout, 25 s budget)
    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.failure_kind == AICandidateProposalFailureKind.DEADLINE_EXCEEDED
    assert result.failure_kind.value == "deadline_exceeded"
    # never a fabricated anchor: the broad provider pool stands on its own
    assert result.proposals == [] and result.rejected_raw_items == []
    reasons = " ".join(result.guardrail_report.blocked_reasons)
    assert "did not answer within the time allowed" in reasons and "SECRET" not in reasons
    assert _figures(recorder.snapshot()["llm_stages"]["groq_anchor"]) == {
        "attempts": 1, "structural_retries": 0, "transport_retries": 0, "deadline_exceeded": True, "result": "failed",
    }


def test_the_anchor_structural_retry_obeys_the_budget_and_still_recovers_when_there_is_time(
    clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _TimedClient(clock, (4.0, _structural()), (3.0, _anchor_output()))
    timeouts = _built_with(monkeypatch, GroqAICandidateProposalProvider, "_build_client", client)
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        result = GroqAICandidateProposalProvider(api_key="k").propose(_anchor_request())

    assert result.status == AICandidateProposalStatus.COMPLETED and result.failure_kind is None
    assert timeouts == [25.0, 21.0]
    assert _figures(recorder.snapshot()["llm_stages"]["groq_anchor"]) == {
        "attempts": 2, "structural_retries": 1, "transport_retries": 0, "deadline_exceeded": False, "result": "success",
    }


# -- reasoning / repair ---------------------------------------------------------------------------------------------------


def test_reasoning_budget_exhaustion_leaves_the_deterministic_planner_in_charge(
    clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _TimedClient(clock, (20.0, TimeoutError("read timeout")), (1.0, _reasoning_output()))
    timeouts = _built_with(monkeypatch, GroqAIItineraryReasoningProvider, "_build_client", client)
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        result = GroqAIItineraryReasoningProvider(api_key="k").reason(_reasoning_request())

    assert len(client.prompts) == 1 and timeouts == [20.0]  # the 20 s budget clips the 30 s request timeout
    # `rejected` with no days: the experience planner only ever consumes a
    # `completed` result, so its own deterministic scheduling runs
    assert result.status == AIItineraryReasoningStatus.REJECTED and result.days == []
    assert result.failure_kind == "deadline_exceeded"
    assert "SECRET" not in " ".join(result.guardrail_report.blocked_reasons)
    assert recorder.snapshot()["llm_stages"]["groq_reasoning"]["deadline_exceeded"] is True


def test_reasoning_gets_one_transport_retry_and_a_fast_success_is_untouched(
    clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _TimedClient(clock, (0.4, _StatusError(429)), (1.2, _reasoning_output()))
    timeouts = _built_with(monkeypatch, GroqAIItineraryReasoningProvider, "_build_client", client)
    result = GroqAIItineraryReasoningProvider(api_key="k").reason(_reasoning_request())
    assert result.status == AIItineraryReasoningStatus.COMPLETED and result.failure_kind is None
    assert clock.slept == [1.0] and timeouts == [20.0, pytest.approx(18.6)]  # 20 - 0.4 - 1 s backoff

    fast = _TimedClient(clock, (1.0, _reasoning_output()))
    assert GroqAIItineraryReasoningProvider(client=fast).reason(_reasoning_request()).status == (
        AIItineraryReasoningStatus.COMPLETED
    )
    assert len(fast.prompts) == 1


def test_repair_budget_exhaustion_leaves_the_existing_top_up_path(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _TimedClient(clock, (19.0, _StatusError(503)), (1.0, _repair_output()))
    timeouts = _built_with(monkeypatch, GroqAIItineraryReasoningProvider, "_build_repair_client", client)
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        result = GroqAIItineraryReasoningProvider(api_key="k").repair(_repair_request())

    assert len(client.prompts) == 1 and timeouts == [20.0]
    # not `completed`: the graph never loops back, and the deterministic top-up / needs-review path runs
    assert result.status == AIItineraryRepairStatus.REJECTED and result.repaired_days == []
    assert result.failure_kind == "deadline_exceeded"
    stage = recorder.snapshot()["llm_stages"]["groq_repair"]
    assert _figures(stage) == {
        "attempts": 1, "structural_retries": 0, "transport_retries": 0, "deadline_exceeded": True, "result": "failed",
        "transport_failure": "server_error",  # Section 3B: the kind of the failed request
    }
    # the provider record sits beside those figures (single provider: nothing to fail over to)
    assert (stage["preferred_provider"], stage["attempted_providers"], stage["failover_used"]) == ("groq", ["groq"], False)


# -- the retry stack ----------------------------------------------------------------------------------------------------


def test_no_stage_can_make_more_than_two_requests_however_it_fails(clock: _Clock) -> None:
    transient, malformed = _StatusError(503), _structural()
    for failures in ((transient,) * 6, (malformed,) * 6, (transient, malformed) * 3, (malformed, transient) * 3):
        steps = [(0.1, failure) for failure in failures]
        anchor = _TimedClient(clock, *steps)
        assert GroqAICandidateProposalProvider(client=anchor).propose(_anchor_request()).status == (
            AICandidateProposalStatus.REJECTED
        )
        narrator = _TimedClient(clock, *steps)
        assert GroqItineraryNarratorProvider(client=narrator).narrate(_narrator_request()).status == (
            ItineraryNarrativeStatus.FAILED
        )
        reasoning = _TimedClient(clock, *steps)
        GroqAIItineraryReasoningProvider(client=reasoning).reason(_reasoning_request())
        repair = _TimedClient(clock, *steps)
        GroqAIItineraryReasoningProvider(client=repair).repair(_repair_request())

        assert len(anchor.prompts) <= 2 and len(narrator.prompts) <= 2
        assert len(reasoning.prompts) <= 2 and len(repair.prompts) <= 2
    # reasoning and repair have no structural retry at all
    once = _TimedClient(clock, (0.1, malformed), (0.1, _reasoning_output()))
    GroqAIItineraryReasoningProvider(client=once).reason(_reasoning_request())
    assert len(once.prompts) == 1


def test_transport_retries_can_be_switched_off(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_MAX_RETRIES", "0")
    get_settings.cache_clear()
    reasoning = _TimedClient(clock, (0.1, _StatusError(503)), (0.1, _reasoning_output()))
    GroqAIItineraryReasoningProvider(client=reasoning).reason(_reasoning_request())
    assert len(reasoning.prompts) == 1


# -- Geoapify: per-generation AND per-process limits -----------------------------------------------------------------------


class _SlowGeoapify:
    def __init__(self, seconds: float = 0.02, fail: Callable[[httpx.Request], bool] = lambda request: False) -> None:
        self._seconds, self._fail = seconds, fail
        self._lock = threading.Lock()
        self._now = 0
        self.peak = 0
        self.count = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self._now += 1
            self.count += 1
            self.peak = max(self.peak, self._now)
        try:
            time.sleep(self._seconds)
            if self._fail(request):
                raise httpx.ReadTimeout("simulated timeout")
            return httpx.Response(200, json={"results": []})
        finally:
            with self._lock:
                self._now -= 1


def _geocode(client: httpx.Client, tracker: ProviderUsageTracker, text: str) -> None:
    geoapify_client.geoapify_get(
        client, base_url="https://geo.test", path="/v1/geocode/search", params={"text": text},
        api_key=_KEY, timeout=5.0, api="geocoding", usage=tracker,
    )


def _generation(client: httpx.Client, tracker: ProviderUsageTracker, name: str, requests: int = 12) -> list[Any]:
    """One 'generation': a batch that wants 8 requests in flight at once."""
    return run_bounded("places", [lambda i=i: _geocode(client, tracker, f"{name}-{i}") for i in range(requests)], 8)


@pytest.fixture()
def process_limit(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    def set_limit(limit: int) -> None:
        monkeypatch.setenv("GEOAPIFY_PROCESS_MAX_CONCURRENT_REQUESTS", str(limit))
        get_settings.cache_clear()
        monkeypatch.setattr(provider_usage, "_process_slots", None)

    return set_limit


def test_two_simultaneous_generations_respect_the_process_limit_and_their_own(
    process_limit: Callable[[int], None],
) -> None:
    assert get_settings().geoapify_process_max_concurrent_requests == 6
    process_limit(6)
    network = _SlowGeoapify()
    first, second = ProviderUsageTracker(100), ProviderUsageTracker(100)
    assert first.request_limiter.limit == second.request_limiter.limit == 4

    with httpx.Client(transport=httpx.MockTransport(network)) as client:
        threads = [
            threading.Thread(target=_generation, args=(client, tracker, name))
            for tracker, name in ((first, "a"), (second, "b"))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(thread.is_alive() for thread in threads)

    assert network.count == 24
    assert network.peak == 6  # 4 + 4 wanted, the process allows 6
    assert first.request_limiter.peak <= 4 and second.request_limiter.peak <= 4  # each generation's own limit
    assert process_request_slots().peak == 6


def test_one_generation_alone_is_still_bounded_by_its_own_limit(process_limit: Callable[[int], None]) -> None:
    process_limit(6)
    network = _SlowGeoapify()
    tracker = ProviderUsageTracker(100)
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder), httpx.Client(transport=httpx.MockTransport(network)) as client:
        _generation(client, tracker, "solo")
    report = recorder.snapshot(tracker.snapshot())
    assert network.peak == tracker.request_limiter.peak == 4
    assert report["peak_geoapify_concurrency"] == 4 and report["process_peak_geoapify_concurrency"] == 4


def test_the_process_limit_wins_when_it_is_the_smaller_one(process_limit: Callable[[int], None]) -> None:
    process_limit(2)
    network = _SlowGeoapify()
    tracker = ProviderUsageTracker(100)
    with httpx.Client(transport=httpx.MockTransport(network)) as client:
        _generation(client, tracker, "solo")
    assert network.peak == 2 and network.count == 12


def test_both_slots_are_released_after_a_timeout_or_an_exception(process_limit: Callable[[int], None]) -> None:
    process_limit(1)
    network = _SlowGeoapify(seconds=0.0, fail=lambda request: "bad" in request.url.params["text"])
    tracker = ProviderUsageTracker(100, max_concurrent_requests=1)
    with httpx.Client(transport=httpx.MockTransport(network)) as client:
        failed = run_bounded("places", [lambda i=i: _geocode(client, tracker, f"bad-{i}") for i in range(5)], 4)
        assert [outcome.error.kind for outcome in failed] == ["timeout"] * 5
        assert all(isinstance(outcome.error, ProviderRequestError) for outcome in failed)
        # with ONE slot at each level, a single leaked slot would block this forever
        done = threading.Event()
        worker = threading.Thread(target=lambda: (_geocode(client, tracker, "good"), done.set()))
        worker.start()
        worker.join(timeout=10)
        assert done.is_set()
    assert tracker.request_limiter._generation._in_flight == 0 and process_request_slots()._in_flight == 0


def test_overlapping_generations_never_deadlock(process_limit: Callable[[int], None]) -> None:
    process_limit(1)  # the harshest case: every generation queues behind one process slot
    network = _SlowGeoapify(seconds=0.002, fail=lambda request: request.url.params["text"].endswith("-3"))
    trackers = [ProviderUsageTracker(100) for _ in range(6)]
    with httpx.Client(transport=httpx.MockTransport(network)) as client:
        threads = [
            threading.Thread(target=_generation, args=(client, tracker, f"g{index}", 8))
            for index, tracker in enumerate(trackers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(thread.is_alive() for thread in threads), "generations deadlocked"
    assert network.count == 48 and network.peak == 1
    assert process_request_slots()._in_flight == 0
    assert all(tracker.request_limiter._generation._in_flight == 0 for tracker in trackers)


def test_the_per_generation_limit_cannot_be_configured_above_four() -> None:
    assert Settings(_env_file=None).geoapify_max_concurrent_requests == 4
    with pytest.raises(ValueError):
        Settings(_env_file=None, GEOAPIFY_MAX_CONCURRENT_REQUESTS=5)


# -- compatibility -------------------------------------------------------------------------------------------------------


def test_old_reports_states_and_configuration_stay_compatible() -> None:
    # a performance report stored by 1A/1B (no LLM stages, no process peak)
    old = GenerationPerformanceReport.model_validate(
        {"total_ms": 82_100.0, "stage_ms": {"narrator": 22_000.0}, "peak_geoapify_concurrency": 4, "batch_sizes": {"places": [6]}}
    )
    assert old.llm_stages == {} and old.process_peak_geoapify_concurrency == 0
    assert {
        "attempts": 0, "structural_retries": 0, "transport_retries": 0, "deadline_exceeded": False, "result": None,
        "transport_failure": None, "retry_after": None,  # Section 3B diagnostics, optional
        # the provider record of the Groq <-> Gemini pair, optional
        "preferred_provider": None, "final_provider": None, "failover_used": False, "total_provider_requests": 0,
    }.items() <= LLMStagePerformance().model_dump().items()
    # results stored before `failure_kind` existed
    assert ItineraryNarrativeReport.model_validate({"status": "failed", "message": "x"}).failure_kind is None
    assert AIItineraryRepairResult.model_validate(
        {"status": "rejected", "guardrail_report": {"passed": False, "blocked_reasons": ["x"], "checked_fields": []}}
    ).failure_kind is None

    settings = Settings(_env_file=None)
    assert (
        settings.groq_anchor_total_budget_seconds, settings.groq_reasoning_total_budget_seconds,
        settings.groq_repair_total_budget_seconds, settings.groq_narrator_total_budget_seconds,
    ) == (25.0, 20.0, 20.0, 25.0)
    assert (settings.groq_request_timeout_seconds, settings.groq_max_retries) == (30.0, 1)
    assert settings.geoapify_process_max_concurrent_requests == 6 and settings.provider_io_concurrency_enabled is True
    # a budget of 0 switches the deadline off for that stage (the attempt cap still applies)
    disabled = StageRun(
        "groq_narrator", total_budget_seconds=Settings(_env_file=None, GROQ_NARRATOR_TOTAL_BUDGET_SECONDS=0).groq_narrator_total_budget_seconds,
        request_timeout_seconds=20, clock=_Clock(),
    )
    assert disabled.remaining() is None and disabled.next_attempt_timeout() == 20


def test_the_canary_reports_llm_stages_and_both_concurrency_peaks_without_judging_them(
    production_like_pipeline: dict[str, list[httpx.Request]]
) -> None:
    canary = _load_canary()
    report = canary._run(_canary_args(), {})
    section = report["performance"]
    assert section["llm_stages"] == []  # no model provider is connected in this fixture
    assert 1 <= section["concurrency"]["peak_geoapify_concurrency"] <= 4
    assert section["concurrency"]["process_peak_geoapify_concurrency"] >= section["concurrency"]["peak_geoapify_concurrency"]

    section["llm_stages"] = [
        {"label": "LLM narrator", "attempts": 1, "structural_retries": 0, "transport_retries": 0,
         "deadline_exceeded": True, "result": "failed", "seconds": 23.0}
    ]
    text = canary._render(report)
    assert "LLM STAGES" in text
    # the semantic stage is provider-neutral: its attempts are never labelled as one provider's
    assert (
        "- LLM narrator: 1 attempt(s) in 23.0 s | structural retries: 0 | transport retries: 0"
        " | deadline exceeded: YES | result: failed"
    ) in text
    assert report["acceptance"]["outcome"] == "PASS"
    assert not any("deadline" in check["check"].lower() or "llm" in check["check"].lower() for check in report["acceptance"]["checks"])
