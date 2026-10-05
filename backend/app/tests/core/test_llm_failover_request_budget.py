from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

import pytest

from app.core import performance
from app.core.config import get_settings
from app.models.ai_candidate_proposal import AICandidateProposalStatus
from app.models.ai_itinerary_reasoning import AIItineraryReasoningStatus
from app.models.ai_itinerary_repair import AIItineraryRepairStatus
from app.models.itinerary_narrative import ItineraryNarrativeStatus
from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider
from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.providers.itinerary_narrator.structural_retry import RETRY_FORMAT_REMINDER
from app.providers.llm_provider_health import GEMINI, GROQ, LLMProviderState, get_llm_provider_health
from app.tests.providers.llm_failover_support import (
    FakeClock,
    ScriptedClient,
    StatusError,
    configure_pair,
    install_clients,
    structural_error,
    use_clock,
)
from app.tests.providers.test_groq_ai_candidate_proposal_provider import (
    _request as _anchor_request,
    _valid_output as _anchor_output,
    _valid_proposal_dict,
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
    _FOREIGN_ID,
    _VALID as _NARRATIVE,
    _request as _narrator_request,
)

# A stage routed over the Groq <-> Gemini pair makes at most TWO provider
# requests in total, under ONE wall-clock budget that is never restarted.
# The second request is the other provider or the stage's own structural
# retry -- never both, and two is a maximum: after a transport failure with
# nobody else eligible there is no second request (the same-provider
# transport retry belongs to single-provider stages only). Driven through
# the four real stage adapters; fake clock.


@dataclass(frozen=True)
class _Stage:
    name: str
    key: str
    provider_cls: Any
    method: str
    groq_method: str
    request: Callable[[], Any]
    valid: Callable[[], Any]
    # an answer that parses but is rejected by the stage's own validation
    rejected: Callable[[], Any]
    completed: Any
    budget: float
    request_timeout: float
    has_structural_retry: bool


def _swap(output: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    return json.loads(json.dumps(output).replace(f'"{old}"', f'"{new}"'))


_STAGES = {
    "anchor": _Stage(
        "anchor", "groq_anchor", GroqAICandidateProposalProvider, "propose", "_build_client", _anchor_request,
        _anchor_output,
        # a named place without a name: dropped by the per-proposal validation
        lambda: _anchor_output(proposals=[_valid_proposal_dict(candidate_name=None)]),
        AICandidateProposalStatus.COMPLETED, 25.0, 30.0, True,
    ),
    "reasoning": _Stage(
        "reasoning", "groq_reasoning", GroqAIItineraryReasoningProvider, "reason", "_build_client", _reasoning_request,
        _reasoning_output,
        lambda: _swap(_reasoning_output(), "c1", "c99"),  # a candidate outside the allowed set
        AIItineraryReasoningStatus.COMPLETED, 20.0, 30.0, False,
    ),
    "repair": _Stage(
        "repair", "groq_repair", GroqAIItineraryReasoningProvider, "repair", "_build_repair_client", _repair_request,
        _repair_output,
        lambda: _swap(_repair_output(), "c2", "c99"),
        AIItineraryRepairStatus.COMPLETED, 20.0, 30.0, False,
    ),
    "narrator": _Stage(
        "narrator", "groq_narrator", GroqItineraryNarratorProvider, "narrate", "_build_client", _narrator_request,
        lambda: _NARRATIVE,
        lambda: _FOREIGN_ID,  # an experience id that is not in that day
        ItineraryNarrativeStatus.SUCCESS, 25.0, 20.0, True,
    ),
}
_ALL = list(_STAGES.values())
_WITH_STRUCTURAL_RETRY = [stage for stage in _ALL if stage.has_structural_retry]
_WITHOUT_STRUCTURAL_RETRY = [stage for stage in _ALL if not stage.has_structural_retry]


def _ids(stages: list[_Stage]) -> list[str]:
    return [stage.name for stage in stages]


@dataclass
class _Outcome:
    result: Any
    completed: bool
    built: list[tuple[str, float | None]]
    figures: dict[str, Any]
    groq: ScriptedClient
    gemini: ScriptedClient

    @property
    def providers(self) -> list[str]:
        return [provider for provider, _ in self.built]


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    use_clock(monkeypatch, fake)
    configure_pair(monkeypatch)
    return fake


def _run(
    stage: _Stage,
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    *,
    groq: list[tuple[float, Any]] | None = None,
    gemini: list[tuple[float, Any]] | None = None,
) -> _Outcome:
    groq_client = ScriptedClient(clock, *(groq or []))
    gemini_client = ScriptedClient(clock, *(gemini or []))
    built = install_clients(
        monkeypatch, stage.provider_cls, groq=groq_client, gemini=gemini_client, groq_method=stage.groq_method
    )
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        result = getattr(stage.provider_cls(), stage.method)(stage.request())
    # every client that was built was called exactly once: no request is made outside the count
    assert len(groq_client.prompts) + len(gemini_client.prompts) == len(built)
    return _Outcome(
        result, result.status == stage.completed, built, recorder.snapshot()["llm_stages"].get(stage.key, {}),
        groq_client, gemini_client,
    )


def _state(provider: str) -> LLMProviderState:
    return get_llm_provider_health().state(provider)


# -- 1: transport failure, then the other provider ---------------------------------------------


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
def test_a_groq_rate_limit_then_a_gemini_success_is_exactly_two_requests(
    stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = _run(
        stage, monkeypatch, clock,
        groq=[(0.1, StatusError(429, headers={"retry-after": "600"}))], gemini=[(1.0, stage.valid())],
    )

    assert outcome.completed and outcome.providers == ["groq", "gemini"]
    assert outcome.figures["attempts"] == outcome.figures["total_provider_requests"] == 2
    assert (outcome.figures["structural_retries"], outcome.figures["transport_retries"]) == (0, 0)
    assert clock.slept == []  # no backoff, no Retry-After wait before the other provider
    assert _state(GROQ) == LLMProviderState.OPEN


# -- 2: structural failure, same-provider structural retry -------------------------------------


@pytest.mark.parametrize("stage", _WITH_STRUCTURAL_RETRY, ids=_ids(_WITH_STRUCTURAL_RETRY))
@pytest.mark.parametrize("gemini_state", ["open", "draining"])
def test_a_groq_schema_failure_with_no_healthy_alternate_uses_the_groq_structural_retry(
    stage: _Stage, gemini_state: str, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    if gemini_state == "open":
        get_llm_provider_health().record_failure(GEMINI, "provider_unavailable")
    else:
        monkeypatch.setenv("GEMINI_RPM_LIMIT", "10")
        get_settings.cache_clear()
        for _ in range(9):
            get_llm_provider_health().note_request(GEMINI)
    assert _state(GEMINI).value == gemini_state

    outcome = _run(stage, monkeypatch, clock, groq=[(0.5, structural_error()), (0.5, stage.valid())])

    assert outcome.completed and outcome.providers == ["groq", "groq"]
    assert (outcome.figures["attempts"], outcome.figures["structural_retries"]) == (2, 1)
    assert outcome.figures["failover_used"] is False


# -- 3: structural failure, then the other provider --------------------------------------------


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
@pytest.mark.parametrize("first_answer", ["provider_reported", "no_structured_output"])
def test_a_groq_schema_failure_then_a_gemini_correction_is_exactly_two_requests(
    stage: _Stage, first_answer: str, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    malformed = structural_error() if first_answer == "provider_reported" else "plain text, not a structured answer"
    outcome = _run(stage, monkeypatch, clock, groq=[(0.5, malformed)], gemini=[(0.5, stage.valid())])

    assert outcome.completed and outcome.providers == ["groq", "gemini"]
    assert outcome.figures["attempts"] == 2 and outcome.figures["final_provider"] == "gemini"
    # cross-provider recovery, not a same-provider structural retry
    assert outcome.figures["structural_retries"] == 0 and outcome.figures["failover_used"] is True
    first = outcome.figures["provider_attempts"][0]
    assert (first["result"], first["structural_validation_result"]) == ("structural_failure", "invalid")
    # the provider answered: a structural failure never opens its circuit
    assert _state(GROQ) == LLMProviderState.HEALTHY


def test_the_alternate_gets_the_same_corrective_input_a_structural_retry_would(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    narrator = _run(
        _STAGES["narrator"], monkeypatch, clock, groq=[(0.5, structural_error())], gemini=[(0.5, _NARRATIVE)]
    )
    assert RETRY_FORMAT_REMINDER not in narrator.groq.prompts[0]
    assert narrator.gemini.prompts[0] == f"{narrator.groq.prompts[0]}\n\n{RETRY_FORMAT_REMINDER}"

    anchor = _run(
        _STAGES["anchor"], monkeypatch, clock, groq=[(0.5, structural_error())], gemini=[(0.5, _anchor_output())]
    )
    assert "Maximum candidates to propose: 15" in anchor.groq.prompts[0]
    assert "Maximum candidates to propose: 7" in anchor.gemini.prompts[0]  # the smaller batch

    # reasoning has no corrective variant: the very same prompt goes to the other provider
    reasoning = _run(
        _STAGES["reasoning"], monkeypatch, clock, groq=[(0.5, structural_error())], gemini=[(0.5, _reasoning_output())]
    )
    assert reasoning.gemini.prompts == reasoning.groq.prompts


@pytest.mark.parametrize("stage", _WITHOUT_STRUCTURAL_RETRY, ids=_ids(_WITHOUT_STRUCTURAL_RETRY))
def test_reasoning_and_repair_get_no_same_provider_structural_retry(
    stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_llm_provider_health().record_failure(GEMINI, "rate_limit", 3600.0)
    outcome = _run(stage, monkeypatch, clock, groq=[(0.5, structural_error())])

    # one request, then the stage falls back exactly as it does today
    assert not outcome.completed and outcome.providers == ["groq"]
    assert outcome.result.failure_kind == "malformed_output"
    assert (outcome.figures["attempts"], outcome.figures["structural_retries"]) == (1, 0)


# -- application validation is never a reason to switch ----------------------------------------


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
def test_an_answer_rejected_by_application_validation_never_triggers_a_second_request(
    stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = _run(stage, monkeypatch, clock, groq=[(0.5, stage.rejected())], gemini=[(0.5, stage.valid())])

    assert not outcome.completed
    assert outcome.providers == ["groq"] and outcome.gemini.prompts == []
    assert outcome.figures["attempts"] == 1 and outcome.figures["failover_used"] is False
    assert outcome.figures["final_provider"] is None  # no accepted answer
    assert _state(GROQ) == LLMProviderState.HEALTHY and _state(GEMINI) == LLMProviderState.HEALTHY


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
def test_a_gemini_answer_rejected_by_application_validation_does_not_go_back_to_groq(
    stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_llm_provider_health().record_failure(GROQ, "provider_unavailable")
    outcome = _run(stage, monkeypatch, clock, groq=[(0.5, stage.valid())], gemini=[(0.5, stage.rejected())])

    assert not outcome.completed and outcome.providers == ["gemini"] and outcome.groq.prompts == []


# -- 4: both providers fail --------------------------------------------------------------------


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
def test_a_groq_503_then_a_gemini_503_is_exactly_two_requests_then_the_fallback(
    stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = _run(
        stage, monkeypatch, clock,
        groq=[(0.2, StatusError(503)), (0.2, stage.valid())],  # a third request would succeed -- it is never made
        gemini=[(0.2, StatusError(503)), (0.2, stage.valid())],
    )

    assert not outcome.completed and outcome.providers == ["groq", "gemini"]
    assert outcome.figures["attempts"] == 2 and outcome.figures["result"] == "failed"
    assert outcome.figures["final_provider"] is None
    assert _state(GROQ) == _state(GEMINI) == LLMProviderState.OPEN
    assert "SECRET" not in json.dumps(outcome.result.model_dump(mode="json"))


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
def test_a_routed_stage_never_makes_a_third_request_even_with_two_transport_retries_configured(
    stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_MAX_RETRIES", "2")
    get_settings.cache_clear()
    outcome = _run(
        stage, monkeypatch, clock,
        groq=[(0.2, StatusError(500)), (0.2, stage.valid())], gemini=[(0.2, TimeoutError("t")), (0.2, stage.valid())],
    )
    assert not outcome.completed and len(outcome.built) == 2


@pytest.mark.parametrize("stage", _WITH_STRUCTURAL_RETRY, ids=_ids(_WITH_STRUCTURAL_RETRY))
def test_recovery_kinds_never_stack(stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    # failover used the one recovery slot: a malformed answer from the alternate is not retried
    outcome = _run(
        stage, monkeypatch, clock,
        groq=[(0.2, StatusError(429)), (0.2, stage.valid())],
        gemini=[(0.2, structural_error()), (0.2, stage.valid())],
    )
    assert not outcome.completed and outcome.providers == ["groq", "gemini"]
    assert (outcome.figures["attempts"], outcome.figures["structural_retries"], outcome.figures["transport_retries"]) == (2, 0, 0)

    # a structural hand-over used it too: a transport failure of the alternate is not retried
    get_llm_provider_health().reset()
    outcome = _run(
        stage, monkeypatch, clock,
        groq=[(0.2, structural_error()), (0.2, stage.valid())],
        gemini=[(0.2, StatusError(503)), (0.2, stage.valid())],
    )
    assert not outcome.completed and outcome.providers == ["groq", "gemini"]


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
@pytest.mark.parametrize("failed, other", [(GROQ, GEMINI), (GEMINI, GROQ)], ids=["groq_fails", "gemini_fails"])
def test_two_requests_is_a_maximum_a_failed_provider_is_never_retried_when_nobody_else_is_eligible(
    stage: _Stage, failed: str, other: str, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_llm_provider_health().record_failure(other, "rate_limit", 3600.0)
    steps = [(0.2, StatusError(503)), (0.2, stage.valid())]  # a retry would succeed -- it is never made
    outcome = _run(stage, monkeypatch, clock, **{failed: steps})

    assert not outcome.completed and outcome.providers == [failed]
    assert (outcome.figures["attempts"], outcome.figures["transport_retries"]) == (1, 0)
    assert clock.slept == [] and outcome.figures["deadline_exceeded"] is False
    assert _state(GROQ) == _state(GEMINI) == LLMProviderState.OPEN


# -- 7-8: one wall-clock budget, never restarted -----------------------------------------------


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
def test_the_stage_budget_does_not_reset_when_the_provider_changes(
    stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    spent = stage.budget - 6.0
    outcome = _run(stage, monkeypatch, clock, groq=[(spent, StatusError(429))], gemini=[(2.0, stage.valid())])

    assert outcome.completed
    first_timeout = min(stage.request_timeout, stage.budget)
    # Gemini gets only what is left of the SAME budget -- not a fresh one
    assert outcome.built == [("groq", first_timeout), ("gemini", pytest.approx(6.0))]
    assert outcome.figures["deadline_exceeded"] is False


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
def test_the_alternate_is_not_called_without_a_meaningful_part_of_the_budget_left(
    stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    spent = stage.budget - 2.0  # under the 3 s a request is worth starting with
    outcome = _run(stage, monkeypatch, clock, groq=[(spent, StatusError(503))], gemini=[(0.5, stage.valid())])

    assert not outcome.completed and outcome.providers == ["groq"] and outcome.gemini.prompts == []
    assert outcome.result.failure_kind == "deadline_exceeded"
    assert outcome.figures["deadline_exceeded"] is True and outcome.figures["result"] == "failed"
    # the provider failure itself was still recorded for later stages
    assert _state(GROQ) == LLMProviderState.OPEN


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
def test_a_timeout_caused_by_the_stages_own_budget_says_nothing_about_the_provider(
    stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # make the request timeout larger than the budget, so the attempt is clipped to the budget
    monkeypatch.setenv("ITINERARY_NARRATOR_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("GROQ_REQUEST_TIMEOUT_SECONDS", "60")
    get_settings.cache_clear()
    outcome = _run(
        stage, monkeypatch, clock, groq=[(stage.budget, TimeoutError("read timeout"))], gemini=[(0.5, stage.valid())]
    )

    assert not outcome.completed and outcome.providers == ["groq"]
    assert outcome.result.failure_kind == "deadline_exceeded"
    assert outcome.figures["provider_attempts"][0]["result"] == "deadline"
    assert _state(GROQ) == LLMProviderState.HEALTHY  # our deadline, not the provider's failure


def test_the_whole_routed_stage_never_outlasts_its_budget(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _STAGES["reasoning"]
    started = clock.now
    outcome = _run(stage, monkeypatch, clock, groq=[(14.0, StatusError(503))], gemini=[(6.0, TimeoutError("t"))])

    assert not outcome.completed and outcome.providers == ["groq", "gemini"]
    assert outcome.built[1] == ("gemini", pytest.approx(6.0))
    assert clock.now - started <= stage.budget


# -- the single-provider path keeps its own rules ----------------------------------------------


@pytest.mark.parametrize("stage", _ALL, ids=_ids(_ALL))
def test_with_only_groq_configured_the_existing_transport_retry_is_unchanged(
    stage: _Stage, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_pair(monkeypatch, gemini=False)
    outcome = _run(stage, monkeypatch, clock, groq=[(0.5, StatusError(503)), (0.5, stage.valid())])

    assert outcome.completed and outcome.providers == ["groq", "groq"]
    assert clock.slept == [1.0]  # the ordinary backoff
    assert (outcome.figures["transport_retries"], outcome.figures["failover_used"]) == (1, False)
