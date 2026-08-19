from __future__ import annotations

import ast
import copy
import inspect
from typing import Any, Callable

import pytest

from app.core.config import Settings
from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalRequest,
    AICandidateProposalStatus,
    AICandidateProposalTask,
)
from app.providers.ai_candidate_proposal import anthropic_adapter as anthropic_adapter_module
from app.providers.ai_candidate_proposal.anthropic_adapter import (
    AnthropicAICandidateProposalProvider,
)
from app.services.candidate_grounding_service import CandidateGroundingService


def _no_key_settings() -> Settings:
    """A `Settings` instance that always has no Anthropic API key, no
    matter what `ANTHROPIC_API_KEY` happens to be set to in the ambient
    environment this suite runs in -- bypasses `get_settings()`'s
    `lru_cache` entirely rather than relying on `monkeypatch.delenv`.
    """
    return Settings(anthropic_api_key=None)

_FORBIDDEN_MODEL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
    "coordinates",
    "provider_name",
    "provider_place_id",
    "provider_id",
}

# Safety tests for the Anthropic-backed AI candidate proposal adapter (Step
# 161A, docs/13_llm_reasoning_pipeline.md section 39). Every test here uses
# only in-file fake clients that mimic `client.messages.create(...)` -- no
# network call is ever made, and no real ANTHROPIC_API_KEY is required for
# this suite.


class _FakeContentBlock:
    """Mimics an Anthropic SDK content block closely enough for the
    adapter's `getattr`-based extraction: a `.type`, plus whatever other
    attributes (`.name`, `.input`, `.text`) the block needs.
    """

    def __init__(self, type_: str, **kwargs: Any) -> None:
        self.type = type_
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeResponse:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessagesResource:
    def __init__(self, handler: Callable[..., Any]) -> None:
        self._handler = handler

    def create(self, **kwargs: Any) -> Any:
        return self._handler(**kwargs)


class _FakeAnthropicClient:
    """Mimics only what the adapter uses: `client.messages.create(...)`."""

    def __init__(self, handler: Callable[..., Any]) -> None:
        self.messages = _FakeMessagesResource(handler)


def _client_returning(response: Any) -> _FakeAnthropicClient:
    return _FakeAnthropicClient(lambda **kwargs: response)


def _client_raising(exc: BaseException) -> _FakeAnthropicClient:
    def _raise(**kwargs: Any) -> Any:
        raise exc

    return _FakeAnthropicClient(_raise)


def _tool_use_response(tool_input: Any, tool_name: str = "submit_ai_candidate_proposals") -> _FakeResponse:
    block = _FakeContentBlock("tool_use", name=tool_name, input=tool_input)
    return _FakeResponse([block])


def _valid_proposal_dict(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "proposal_id": "proposal_001",
        "candidate_name": "Old Town Waterfront",
        "candidate_type": "neighborhood",
        "why_consider": "Locally known for evening walks, may be under-tagged in provider data.",
        "verification_requirements": ["must_ground_by_name_and_location"],
        "confidence": 0.6,
    }
    fields.update(overrides)
    return fields


def _valid_tool_input(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "proposals": [_valid_proposal_dict()],
        "rejected_raw_items": [],
        "confidence": 0.7,
    }
    fields.update(overrides)
    return fields


def _request(**overrides: object) -> AICandidateProposalRequest:
    fields: dict[str, object] = {
        "task": AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        "trip_id": "trip_001",
        "destination_name": "Lisbon",
        "trip_duration_days": 4,
    }
    fields.update(overrides)
    return AICandidateProposalRequest(**fields)


# ---------------------------------------------------------------------------
# 1-4. No api_key and no client -> not_connected, with expected fields.
# ---------------------------------------------------------------------------


def test_no_api_key_and_no_client_returns_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_adapter_module, "get_settings", _no_key_settings)
    provider = AnthropicAICandidateProposalProvider(api_key=None)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.NOT_CONNECTED


def test_no_key_result_has_empty_proposals_and_zero_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(anthropic_adapter_module, "get_settings", _no_key_settings)
    provider = AnthropicAICandidateProposalProvider(api_key=None)

    result = provider.propose(_request())

    assert result.proposals == []
    assert result.confidence == 0.0


def test_no_key_result_preserves_request_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_adapter_module, "get_settings", _no_key_settings)
    provider = AnthropicAICandidateProposalProvider(api_key=None)

    result = provider.propose(_request(task=AICandidateProposalTask.REPLACEMENT_CANDIDATE_DISCOVERY))

    assert result.task == AICandidateProposalTask.REPLACEMENT_CANDIDATE_DISCOVERY


def test_no_key_result_includes_provider_name_and_model_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(anthropic_adapter_module, "get_settings", _no_key_settings)
    provider = AnthropicAICandidateProposalProvider(api_key=None, model="claude-sonnet-4-20250514")

    result = provider.propose(_request())

    assert result.provider_name == "anthropic_ai_candidate_proposal_provider"
    assert result.model_name == "claude-sonnet-4-20250514"
    assert result.guardrail_report.passed is False
    assert len(result.guardrail_report.blocked_reasons) > 0


# ---------------------------------------------------------------------------
# 5. Provider can be constructed with injected fake client without api_key.
# ---------------------------------------------------------------------------


def test_provider_constructed_with_fake_client_and_no_api_key() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input()))
    provider = AnthropicAICandidateProposalProvider(api_key=None, client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.COMPLETED


# ---------------------------------------------------------------------------
# 6-9. Successful fake client tool_use response.
# ---------------------------------------------------------------------------


def test_successful_tool_use_response_produces_completed_result() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input()))
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.COMPLETED
    assert result.guardrail_report.passed is True
    assert len(result.proposals) == 1


def test_successful_result_validates_each_proposal() -> None:
    client = _client_returning(
        _tool_use_response(
            _valid_tool_input(
                proposals=[
                    _valid_proposal_dict(proposal_id="proposal_001", candidate_name="Old Town Waterfront"),
                    _valid_proposal_dict(proposal_id="proposal_002", candidate_name="Second Idea"),
                ]
            )
        )
    )
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert len(result.proposals) == 2
    for proposal in result.proposals:
        assert isinstance(proposal, AICandidateProposal)


def test_successful_proposals_contain_no_forbidden_factual_fields() -> None:
    proposal_field_names = set(AICandidateProposal.model_fields.keys())
    overlap = proposal_field_names & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"AICandidateProposal has forbidden field(s): {overlap}"


def test_valid_proposals_include_verification_requirements() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input()))
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    proposal = result.proposals[0]
    assert len(proposal.verification_requirements) > 0


# ---------------------------------------------------------------------------
# 10. Fake client returning forbidden factual claim text -> rejected, no
#     proposals.
# ---------------------------------------------------------------------------


def test_forbidden_factual_claim_text_causes_rejected_result() -> None:
    client = _client_returning(
        _tool_use_response(
            _valid_tool_input(
                proposals=[_valid_proposal_dict(candidate_name="This place has a great rating")]
            )
        )
    )
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []
    assert result.guardrail_report.passed is False


# ---------------------------------------------------------------------------
# 11. Fake client returning malformed/non-tool/no structured output ->
#     rejected, no proposals.
# ---------------------------------------------------------------------------


def test_text_only_response_causes_rejected_result() -> None:
    client = _client_returning(_FakeResponse([_FakeContentBlock("text", text="Sure, here are some ideas...")]))
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []


def test_empty_content_response_causes_rejected_result() -> None:
    client = _client_returning(_FakeResponse([]))
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []


def test_tool_input_missing_proposals_key_causes_rejected_result() -> None:
    client = _client_returning(_tool_use_response({"confidence": 0.5}))
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []


def test_tool_input_empty_proposals_list_causes_rejected_result() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input(proposals=[])))
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []


def test_malformed_proposal_entry_causes_rejected_result() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input(proposals=["not-a-dict"])))
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []


def test_wrong_tool_name_causes_rejected_result() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input(), tool_name="some_other_tool"))
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []


# ---------------------------------------------------------------------------
# 12. Fake client raising exception -> rejected, no proposals.
# ---------------------------------------------------------------------------


def test_client_raising_exception_causes_rejected_result() -> None:
    client = _client_raising(RuntimeError("simulated API failure"))
    provider = AnthropicAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []
    assert result.guardrail_report.passed is False


# ---------------------------------------------------------------------------
# 13. Adapter does not call CandidateGroundingService.
# ---------------------------------------------------------------------------


def test_adapter_never_calls_grounding_service(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(self: CandidateGroundingService, request: object) -> None:
        raise AssertionError("CandidateGroundingService.ground must not be called by the adapter")

    monkeypatch.setattr(CandidateGroundingService, "ground", _fail)

    client = _client_returning(_tool_use_response(_valid_tool_input()))
    provider = AnthropicAICandidateProposalProvider(client=client)
    provider.propose(_request())  # must not raise


# ---------------------------------------------------------------------------
# 14. Adapter does not mutate request.
# ---------------------------------------------------------------------------


def test_adapter_does_not_mutate_request() -> None:
    client = _client_returning(_tool_use_response(_valid_tool_input()))
    provider = AnthropicAICandidateProposalProvider(client=client)
    request = _request()
    before = copy.deepcopy(request.model_dump())

    provider.propose(request)

    assert request.model_dump() == before


# ---------------------------------------------------------------------------
# 15. Adapter does not import OpenAI/LangGraph/LangSmith/Gemini/provider
#     adapters/PlanningOrchestrator. `anthropic` itself is only ever
#     imported lazily, inside `_build_client`, never at module level.
# ---------------------------------------------------------------------------


def _imported_names(nodes: list[ast.stmt]) -> list[str]:
    names: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_adapter_module_has_no_disallowed_vendor_imports() -> None:
    import app.providers.ai_candidate_proposal.anthropic_adapter as adapter_module

    source = inspect.getsource(adapter_module)
    tree = ast.parse(source)

    # `anthropic` itself is intentionally excluded here -- it is the
    # adapter's own (lazily imported) SDK. Every other vendor/framework
    # name must never appear anywhere in this module, deferred or not.
    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "openai",
        "gemini",
        "google.generativeai",
        "app.providers.places",
        "app.providers.routes",
        "app.providers.transit",
        "app.providers.accommodation",
        "app.providers.weather",
        "app.providers.holidays",
        "app.providers.currency",
        "app.providers.gateway",
        "planning_orchestrator",
    )

    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]
    for name in all_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


def test_adapter_module_only_imports_anthropic_lazily_inside_build_client() -> None:
    """Confirms `import anthropic` never appears at module level -- only
    inside `_build_client`, so the rest of the app imports cleanly even
    when the `anthropic` package is not installed.
    """
    import app.providers.ai_candidate_proposal.anthropic_adapter as adapter_module

    source = inspect.getsource(adapter_module)
    tree = ast.parse(source)

    top_level_names = _imported_names(tree.body)
    assert not any(name == "anthropic" or name.startswith("anthropic.") for name in top_level_names)

    build_client = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_build_client"
    )
    nested_names = _imported_names(list(ast.walk(build_client)))
    assert "anthropic" in nested_names


# ---------------------------------------------------------------------------
# 16-18. Factory support for "anthropic".
# ---------------------------------------------------------------------------


def test_factory_returns_anthropic_provider_for_explicit_anthropic() -> None:
    from app.providers.ai_candidate_proposal import get_ai_candidate_proposal_provider

    provider = get_ai_candidate_proposal_provider("anthropic")

    assert isinstance(provider, AnthropicAICandidateProposalProvider)


def test_factory_default_still_returns_not_connected_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.ai_candidate_proposal import factory as factory_module
    from app.providers.ai_candidate_proposal import (
        NotConnectedAICandidateProposalProvider,
        get_ai_candidate_proposal_provider,
    )

    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings())

    provider = get_ai_candidate_proposal_provider()

    assert isinstance(provider, NotConnectedAICandidateProposalProvider)


def test_factory_unsupported_value_still_returns_not_connected_provider() -> None:
    from app.providers.ai_candidate_proposal import (
        NotConnectedAICandidateProposalProvider,
        get_ai_candidate_proposal_provider,
    )

    provider = get_ai_candidate_proposal_provider("some_unrecognized_provider")

    assert isinstance(provider, NotConnectedAICandidateProposalProvider)


# ---------------------------------------------------------------------------
# 20. AICandidateDiscoveryService with injected Anthropic fake client can
#     produce a completed proposal_result and a completed grounding_result
#     when PlanningState has a matching destination_context candidate.
# ---------------------------------------------------------------------------


def test_discovery_service_with_anthropic_fake_client_grounds_end_to_end() -> None:
    from app.models.candidate_grounding import CandidateGroundingStatus
    from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
    from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService

    client = _client_returning(
        _tool_use_response(
            _valid_tool_input(
                proposals=[_valid_proposal_dict(candidate_name="Old Town Waterfront")]
            )
        )
    )
    provider = AnthropicAICandidateProposalProvider(client=client)
    service = AICandidateDiscoveryService(proposal_provider=provider)

    planning_state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Lisbon, Portugal",
            start_date="2026-08-10",
            end_date="2026-08-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        )
    )
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_pois=[
            {
                "name": "Old Town Waterfront",
                "category": "neighborhood",
                "coordinates": {"lat": 38.7223, "lng": -9.1393},
                "source": "openstreetmap_places",
                "data_status": "live",
                "confidence": 0.8,
                "place_id": "way/12345",
            }
        ],
    )

    result = service.dry_run(planning_state)

    assert result.proposal_result.status == AICandidateProposalStatus.COMPLETED
    assert result.grounding_result.status == CandidateGroundingStatus.COMPLETED
    assert len(result.grounding_result.grounded_candidates) == 1


# ---------------------------------------------------------------------------
# 20 (default path). AICandidateDiscoveryService default behavior remains
#     not_connected/skipped.
# ---------------------------------------------------------------------------


def test_discovery_service_default_behavior_remains_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.candidate_grounding import CandidateGroundingStatus
    from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
    from app.providers.ai_candidate_proposal import factory as factory_module
    from app.services.ai_candidate_discovery_service import AICandidateDiscoveryService

    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings())

    service = AICandidateDiscoveryService()
    planning_state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Lisbon, Portugal",
            start_date="2026-08-10",
            end_date="2026-08-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        )
    )

    result = service.dry_run(planning_state)

    assert result.proposal_result.status == AICandidateProposalStatus.NOT_CONNECTED
    assert result.proposal_result.proposals == []
    assert result.grounding_result.status == CandidateGroundingStatus.SKIPPED


# ---------------------------------------------------------------------------
# 22. No real ANTHROPIC_API_KEY is required for this suite. (Sanity check:
#     every test above either omits api_key/client entirely, injects a
#     fake client, or explicitly clears the env var.)
# ---------------------------------------------------------------------------


def test_suite_does_not_require_a_real_anthropic_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_adapter_module, "get_settings", _no_key_settings)
    provider = AnthropicAICandidateProposalProvider()

    # No network call is made -- this must return instantly, not hang or
    # raise a connection error.
    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.NOT_CONNECTED
