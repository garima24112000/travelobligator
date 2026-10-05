from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from app.core.config import get_settings
from app.models.ai_candidate_proposal import AICandidateProposalFailureKind, AICandidateProposalStatus
from app.models.ai_itinerary_reasoning import AIItineraryReasoningStatus
from app.models.ai_itinerary_repair import AIItineraryRepairStatus
from app.models.itinerary_narrative import ItineraryNarrativeStatus
from app.providers.ai_candidate_proposal import get_ai_candidate_proposal_provider
from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider, _GroqProposalBatchSchema
from app.providers.ai_itinerary_reasoning import get_ai_itinerary_reasoning_provider
from app.providers.ai_itinerary_reasoning.groq_adapter import (
    GroqAIItineraryReasoningProvider,
    _GroqItineraryReasoningSchema,
    _GroqRepairSchema,
)
from app.providers.itinerary_narrator import get_itinerary_narrator_provider
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider, _NarratorBatchSchema
from app.services.itinerary_narrative_service import ItineraryNarrativeService
from app.tests.providers.llm_failover_support import (
    GEMINI_MODEL,
    configure_pair,
    gemini_body,
    json_response,
    real_gemini_client,
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
    _VALID,
    _request as _narrator_request,
    _state as _narrator_state,
)

# Adapter parity, not a second copy of the business-rule tests: the SAME
# model answer, arriving once through Groq and once through the real Gemini
# client (over a mock HTTP transport), must produce the same domain result --
# and everything the existing checks reject for Groq is rejected for Gemini.

_NARRATIVE = {**_VALID, "getting_around_profile": "", "getting_around_advisory": ""}
_FOREIGN_NARRATIVE = {**_FOREIGN_ID, "getting_around_profile": "", "getting_around_advisory": ""}


def _swap(output: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    return json.loads(json.dumps(output).replace(f'"{old}"', f'"{new}"'))


class _Stage:
    def __init__(
        self,
        name: str,
        provider_cls: Any,
        method: str,
        schema: Any,
        request: Callable[[], Any],
        valid: Callable[[], dict[str, Any]],
        completed: Any,
        gemini_provider: Callable[[], Any],
        gemini_name: str,
    ) -> None:
        self.name, self.provider_cls, self.method, self.schema = name, provider_cls, method, schema
        self.request, self.valid, self.completed = request, valid, completed
        self.gemini_provider, self.gemini_name = gemini_provider, gemini_name


_STAGES = [
    _Stage(
        "anchor", GroqAICandidateProposalProvider, "propose", _GroqProposalBatchSchema, _anchor_request,
        _anchor_output, AICandidateProposalStatus.COMPLETED,
        lambda: get_ai_candidate_proposal_provider("gemini"), "gemini_ai_candidate_proposal_provider",
    ),
    _Stage(
        "reasoning", GroqAIItineraryReasoningProvider, "reason", _GroqItineraryReasoningSchema, _reasoning_request,
        _reasoning_output, AIItineraryReasoningStatus.COMPLETED,
        lambda: get_ai_itinerary_reasoning_provider("gemini"), "gemini_ai_itinerary_reasoning_provider",
    ),
    _Stage(
        "repair", GroqAIItineraryReasoningProvider, "repair", _GroqRepairSchema, _repair_request,
        _repair_output, AIItineraryRepairStatus.COMPLETED,
        lambda: get_ai_itinerary_reasoning_provider("gemini"), "gemini_ai_itinerary_reasoning_provider",
    ),
    _Stage(
        "narrator", GroqItineraryNarratorProvider, "narrate", _NarratorBatchSchema, _narrator_request,
        lambda: _NARRATIVE, ItineraryNarrativeStatus.SUCCESS,
        lambda: get_itinerary_narrator_provider("gemini"), "gemini_itinerary_narrator_provider",
    ),
]
_BY_NAME = {stage.name: stage for stage in _STAGES}
_IDS = [stage.name for stage in _STAGES]
# provider identity (and the narrator's timestamp) are the only fields allowed to differ
_IDENTITY_FIELDS = {"provider_name", "model_name", "provider", "model", "generated_at"}


@pytest.fixture(autouse=True)
def gemini_only(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_pair(monkeypatch, groq=False)


class _Injected:
    def __init__(self, answer: Any) -> None:
        self._answer = answer

    def invoke(self, prompt: str) -> Any:
        return self._answer


def _through_groq(stage: _Stage, answer: dict[str, Any]) -> Any:
    return getattr(stage.provider_cls(client=_Injected(answer)), stage.method)(stage.request())


def _through_gemini(stage: _Stage, monkeypatch: pytest.MonkeyPatch, *texts: str) -> tuple[Any, list[httpx.Request]]:
    """The stage on Gemini: the real client, each HTTP request answered with
    the next of `texts` (the model's raw text)."""
    remaining, requests = list(texts), []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response(gemini_body(remaining.pop(0)))

    def build(self: Any, *args: Any, **kwargs: Any) -> Any:
        return real_gemini_client(stage.schema, handler)

    monkeypatch.setattr(stage.provider_cls, "_build_gemini_client", build)
    return getattr(stage.gemini_provider(), stage.method)(stage.request()), requests


def _domain(result: Any) -> dict[str, Any]:
    return {key: value for key, value in result.model_dump(mode="json").items() if key not in _IDENTITY_FIELDS}


@pytest.mark.parametrize("stage", _STAGES, ids=_IDS)
def test_the_same_answer_gives_the_same_domain_result_through_either_provider(
    stage: _Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = stage.valid()
    via_groq = _through_groq(stage, answer)
    via_gemini, requests = _through_gemini(stage, monkeypatch, json.dumps(answer))

    assert via_groq.status == via_gemini.status == stage.completed
    assert type(via_gemini) is type(via_groq)  # the existing domain model, not a Gemini one
    assert _domain(via_gemini) == _domain(via_groq)
    assert len(requests) == 1
    # only the attribution differs
    dumped = via_gemini.model_dump(mode="json")
    assert stage.gemini_name in (dumped.get("provider_name"), dumped.get("provider"))
    assert GEMINI_MODEL in (dumped.get("model_name"), dumped.get("model"))


@pytest.mark.parametrize("stage", _STAGES, ids=_IDS)
def test_the_prompt_sent_to_gemini_is_the_one_groq_would_get(stage: _Stage, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class _Capturing(_Injected):
        def invoke(self, prompt: str) -> Any:
            seen.append(prompt)
            return self._answer

    getattr(stage.provider_cls(client=_Capturing(stage.valid())), stage.method)(stage.request())
    _, requests = _through_gemini(stage, monkeypatch, json.dumps(stage.valid()))

    body = json.loads(requests[0].content)
    assert body["contents"][0]["parts"][0]["text"] == seen[0]
    assert body["generationConfig"]["responseJsonSchema"] == stage.schema.model_json_schema()
    assert "tools" not in body


@pytest.mark.parametrize("stage", _STAGES, ids=_IDS)
@pytest.mark.parametrize("text", ["this is not json", '{"unexpected": true}'], ids=["malformed_json", "wrong_structure"])
def test_malformed_or_structurally_invalid_gemini_output_is_rejected(
    stage: _Stage, text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a second (equally bad) answer is there for the stages that have a structural retry
    result, requests = _through_gemini(stage, monkeypatch, text, text)

    assert result.status != stage.completed
    assert result.failure_kind in ("malformed_output", AICandidateProposalFailureKind.SCHEMA_VALIDATION)
    assert len(requests) == (2 if stage.name in ("anchor", "narrator") else 1)
    dumped = json.dumps(result.model_dump(mode="json"))
    assert "unexpected" not in dumped and "this is not json" not in dumped  # no model text in the result
    assert "Gemini" in dumped and "Groq" not in dumped  # the failure names the provider that gave it


def test_a_forbidden_factual_claim_in_a_gemini_proposal_is_rejected_like_a_groq_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = _BY_NAME["anchor"]
    answer = _anchor_output(proposals=[_valid_proposal_dict(why_consider="Has a 4.8 rating and a low price.")])

    via_groq = _through_groq(stage, answer)
    via_gemini, _ = _through_gemini(stage, monkeypatch, json.dumps(answer))

    assert via_groq.status == via_gemini.status == AICandidateProposalStatus.REJECTED
    assert via_gemini.failure_kind == via_groq.failure_kind == AICandidateProposalFailureKind.CANDIDATE_VALIDATION
    assert via_gemini.proposals == [] and via_gemini.dropped_proposal_count == via_groq.dropped_proposal_count == 1


def test_one_invalid_gemini_proposal_is_dropped_and_the_valid_ones_kept_exactly_as_for_groq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = _BY_NAME["anchor"]
    answer = _anchor_output(
        proposals=[
            _valid_proposal_dict(),
            _valid_proposal_dict(proposal_id="proposal_002", candidate_name="Riverside Market", search_query="Riverside Market",
                                 why_consider="Tickets cost a low price."),
        ]
    )
    via_groq = _through_groq(stage, answer)
    via_gemini, _ = _through_gemini(stage, monkeypatch, json.dumps(answer))

    assert _domain(via_gemini) == _domain(via_groq)
    assert [proposal.candidate_name for proposal in via_gemini.proposals] == ["Old Town Waterfront"]


def test_gemini_cannot_supply_a_factual_identity_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coordinates, a provider place id, a price or a rating in a Gemini
    proposal have nowhere to go: the wire schema has no such field, so the
    proposal still has to be grounded by a provider before it can be used."""
    stage = _BY_NAME["anchor"]
    proposal = {
        **_valid_proposal_dict(), "latitude": 38.69, "longitude": -9.21, "provider_place_id": "gem:123",
        "provider_source": "gemini", "price": "12 EUR", "rating": 4.8, "opening_hours": "9-17", "booking_url": "x",
    }
    result, _ = _through_gemini(stage, monkeypatch, json.dumps(_anchor_output(proposals=[proposal])))

    assert result.status == AICandidateProposalStatus.COMPLETED
    dumped = json.dumps(result.model_dump(mode="json"))
    for forbidden in ("38.69", "gem:123", "12 EUR", "4.8", "9-17", "booking_url", "provider_place_id", "latitude"):
        assert forbidden not in dumped
    assert _domain(result) == _domain(_through_groq(stage, _anchor_output()))


@pytest.mark.parametrize("name", ["reasoning", "repair"])
def test_a_gemini_candidate_reference_outside_the_allowed_set_rejects_the_output(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _BY_NAME[name]
    answer = _swap(stage.valid(), "c1" if name == "reasoning" else "c2", "c99")

    via_groq = _through_groq(stage, answer)
    via_gemini, requests = _through_gemini(stage, monkeypatch, json.dumps(answer))

    assert via_groq.status == via_gemini.status
    assert via_gemini.status != stage.completed and len(requests) == 1  # never retried, never hopped
    assert _domain(via_gemini)["guardrail_report"]["passed"] is False


@pytest.mark.parametrize("name", ["reasoning", "repair"])
def test_gemini_cannot_introduce_a_place_by_its_real_identifier(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The model only ever sees short references; a raw provider id (even a
    real-looking one) in its answer is not a reference and is rejected."""
    stage = _BY_NAME[name]
    answer = _swap(stage.valid(), "c1" if name == "reasoning" else "c2", "openstreetmap_places:way/999")
    result, _ = _through_gemini(stage, monkeypatch, json.dumps(answer))
    assert result.status != stage.completed


def test_a_gemini_narrative_about_a_place_outside_its_day_is_rejected_like_a_groq_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = _BY_NAME["narrator"]
    via_groq = _through_groq(stage, _FOREIGN_NARRATIVE)
    via_gemini, requests = _through_gemini(stage, monkeypatch, json.dumps(_FOREIGN_NARRATIVE))

    assert via_groq.status == via_gemini.status == ItineraryNarrativeStatus.FAILED
    assert "outside that day's real scheduled items" in (via_gemini.message or "")
    assert via_gemini.summary is None and len(requests) == 1


def _narrate_through_service(monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any]) -> tuple[Any, list[httpx.Request]]:
    monkeypatch.setenv("ITINERARY_NARRATOR_ENABLED", "true")
    monkeypatch.setenv("ITINERARY_NARRATOR_PROVIDER", "gemini")
    get_settings.cache_clear()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response(gemini_body(json.dumps(answer)))

    monkeypatch.setattr(
        GroqItineraryNarratorProvider, "_build_gemini_client",
        lambda self, timeout: real_gemini_client(_NarratorBatchSchema, handler),
    )
    return ItineraryNarrativeService().generate(_narrator_state()).itinerary_narrative_report, requests


def test_an_unsupported_factual_claim_in_a_gemini_narrative_is_rejected_by_the_existing_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = {
        **_NARRATIVE,
        "daily_narratives": [
            {**_NARRATIVE["daily_narratives"][0], "narrative": "Belem Tower opens at 9am, costs $12 and is rated 4.8 stars."}
        ],
    }
    report, requests = _narrate_through_service(monkeypatch, claim)

    assert report.narrative_source == "deterministic_fallback"
    assert len(requests) == 1  # a grounding rejection is never retried and never hops
    assert "$12" not in json.dumps(report.model_dump(mode="json"))


def test_a_grounded_gemini_narrative_reaches_the_traveller_through_the_same_service_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, requests = _narrate_through_service(monkeypatch, _NARRATIVE)

    assert report.status == ItineraryNarrativeStatus.SUCCESS and report.narrative_source == "ai"
    assert (report.provider, report.model) == ("gemini_itinerary_narrator_provider", GEMINI_MODEL)
    assert len(requests) == 1


def test_the_reported_tokens_of_an_unusable_gemini_answer_still_count_against_the_advisory_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.llm_provider_health import GEMINI, LLMProviderState, get_llm_provider_health

    monkeypatch.setenv("GEMINI_TPM_LIMIT", "1000")
    get_settings.cache_clear()
    _through_gemini(_BY_NAME["reasoning"], monkeypatch, "this is not json")  # the mock reports 12 tokens

    health = get_llm_provider_health()
    assert health.quota_report(GEMINI)["advisory_remaining_ratio"] == pytest.approx(0.988)
    assert health.state(GEMINI) == LLMProviderState.HEALTHY  # an unusable answer never opens the circuit


def test_an_unsanitized_gemini_advisory_never_reaches_the_state(monkeypatch: pytest.MonkeyPatch) -> None:
    advisory = {**_NARRATIVE, "getting_around_profile": "transit_walk",
                "getting_around_advisory": "The metro costs 2 EUR and runs every 4 minutes until 1am."}
    report, _ = _narrate_through_service(monkeypatch, advisory)
    assert "2 EUR" not in json.dumps(report.model_dump(mode="json"))
