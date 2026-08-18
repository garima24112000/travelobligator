from __future__ import annotations

import ast
import copy
import inspect
import sys
import types
from typing import Any, Callable

import pytest

from app.core.config import Settings
from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalRequest,
    AICandidateProposalStatus,
    AICandidateProposalTask,
)
from app.providers.ai_candidate_proposal import groq_adapter as groq_adapter_module
from app.providers.ai_candidate_proposal.groq_adapter import (
    GroqAICandidateProposalProvider,
    _GroqProposalBatchSchema,
)
from app.services.candidate_grounding_service import CandidateGroundingService


def _no_key_settings() -> Settings:
    """A `Settings` instance that always has no Groq API key, no matter what
    `GROQ_API_KEY` happens to be set to in the ambient environment/local
    `.env` this suite runs in -- bypasses `get_settings()`'s `lru_cache`
    entirely rather than relying on `monkeypatch.delenv`.

    Uses the alias kwarg (`GROQ_API_KEY=`), not the field name
    (`groq_api_key=`): pydantic-settings still reads a local `.env` file
    directly regardless of the current process environment, and when both
    an alias-sourced value (from `.env`) and a field-name-sourced value
    (from an init kwarg) are present, the alias wins -- so
    `Settings(groq_api_key=None)` would silently lose to a developer's
    local `.env` `GROQ_API_KEY=...` line. The alias kwarg has no such
    ambiguity.
    """
    return Settings(GROQ_API_KEY=None)


# Superset checked only against `AICandidateProposal.model_fields` -- a raw
# proposal must never carry a coordinate or provider-identity field, since
# those only ever come from grounding evidence, not the AI's own wording.
_FORBIDDEN_PROPOSAL_FIELD_NAMES = {
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

# Smaller set checked against a full `AICandidateProposalResult` dump --
# deliberately excludes `provider_name`/`model_name`, which are legitimate
# result-level metadata naming which provider/model produced the result,
# not a factual claim about a place.
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
}

# Safety tests for the Groq-backed AI candidate proposal adapter (Step
# 162A, docs/13_llm_reasoning_pipeline.md section 41). Every test here uses
# only in-file fake clients that mimic `client.invoke(prompt)` -- no
# network call is ever made, and no real GROQ_API_KEY is required for this
# suite.


class _FakeGroqClient:
    """Mimics only what the adapter uses: `client.invoke(prompt)`."""

    def __init__(self, handler: Callable[[str], Any]) -> None:
        self._handler = handler

    def invoke(self, prompt: str) -> Any:
        return self._handler(prompt)


def _client_returning(response: Any) -> _FakeGroqClient:
    return _FakeGroqClient(lambda prompt: response)


def _client_raising(exc: BaseException) -> _FakeGroqClient:
    def _raise(prompt: str) -> Any:
        raise exc

    return _FakeGroqClient(_raise)


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


def _valid_output(**overrides: object) -> dict[str, object]:
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
# 1. No api_key and no client -> not_connected, with expected fields.
# ---------------------------------------------------------------------------


def test_no_api_key_and_no_client_returns_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_adapter_module, "get_settings", _no_key_settings)
    provider = GroqAICandidateProposalProvider(api_key=None)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.NOT_CONNECTED
    assert result.proposals == []
    assert result.confidence == 0.0


def test_no_key_result_preserves_request_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_adapter_module, "get_settings", _no_key_settings)
    provider = GroqAICandidateProposalProvider(api_key=None)

    result = provider.propose(_request(task=AICandidateProposalTask.REPLACEMENT_CANDIDATE_DISCOVERY))

    assert result.task == AICandidateProposalTask.REPLACEMENT_CANDIDATE_DISCOVERY


def test_no_key_result_includes_provider_name_and_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_adapter_module, "get_settings", _no_key_settings)
    provider = GroqAICandidateProposalProvider(api_key=None, model="openai/gpt-oss-20b")

    result = provider.propose(_request())

    assert result.provider_name == "groq_ai_candidate_proposal_provider"
    assert result.model_name == "openai/gpt-oss-20b"
    assert result.guardrail_report.passed is False
    assert len(result.guardrail_report.blocked_reasons) > 0


# ---------------------------------------------------------------------------
# 2. No API key path never imports langchain_groq or groq.
# ---------------------------------------------------------------------------


def test_no_api_key_path_does_not_import_langchain_groq_or_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_adapter_module, "get_settings", _no_key_settings)
    monkeypatch.delitem(sys.modules, "langchain_groq", raising=False)
    monkeypatch.delitem(sys.modules, "groq", raising=False)

    provider = GroqAICandidateProposalProvider(api_key=None)
    provider.propose(_request())

    assert "langchain_groq" not in sys.modules
    assert "groq" not in sys.modules


def _imported_names(nodes: list[ast.stmt]) -> list[str]:
    names: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_adapter_module_has_no_disallowed_vendor_imports() -> None:
    source = inspect.getsource(groq_adapter_module)
    tree = ast.parse(source)

    # `langchain_groq`/`groq` are intentionally excluded here -- they are
    # the adapter's own (lazily imported) SDKs. Every other vendor/
    # framework name must never appear anywhere in this module, deferred
    # or not.
    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "gemini",
        "google.generativeai",
        "claude_code",
        "app.providers.places",
        "app.providers.routes",
        "app.providers.transit",
        "app.providers.accommodation",
        "app.providers.weather",
        "app.providers.holidays",
        "app.providers.currency",
        "app.providers.gateway",
        "planning_orchestrator",
        "candidate_grounding_service",
    )

    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]
    for name in all_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


def test_adapter_module_only_imports_langchain_groq_lazily_inside_build_client() -> None:
    """Confirms `import langchain_groq` never appears at module level --
    only inside `_build_client`, so the rest of the app imports cleanly
    even when the `langchain_groq` package is not installed.
    """
    source = inspect.getsource(groq_adapter_module)
    tree = ast.parse(source)

    top_level_names = _imported_names(tree.body)
    assert not any(
        name == "langchain_groq" or name.startswith("langchain_groq.") for name in top_level_names
    )
    assert not any(name == "groq" or name.startswith("groq.") for name in top_level_names)

    build_client = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_build_client"
    )
    nested_names = _imported_names(list(ast.walk(build_client)))
    assert "langchain_groq" in nested_names


# ---------------------------------------------------------------------------
# 3. Provider can be constructed with injected fake client without api_key.
# ---------------------------------------------------------------------------


def test_provider_constructed_with_fake_client_and_no_api_key() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAICandidateProposalProvider(api_key=None, client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.COMPLETED


# ---------------------------------------------------------------------------
# 3-4. Successful fake client responses (dict and structured-schema shape).
# ---------------------------------------------------------------------------


def test_successful_invoke_response_produces_completed_result() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.COMPLETED
    assert result.guardrail_report.passed is True
    assert len(result.proposals) == 1


def test_successful_result_validates_each_proposal() -> None:
    client = _client_returning(
        _valid_output(
            proposals=[
                _valid_proposal_dict(proposal_id="proposal_001", candidate_name="Old Town Waterfront"),
                _valid_proposal_dict(proposal_id="proposal_002", candidate_name="Second Idea"),
            ]
        )
    )
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert len(result.proposals) == 2
    for proposal in result.proposals:
        assert isinstance(proposal, AICandidateProposal)


def test_successful_response_via_structured_output_schema_instance() -> None:
    """The normal `with_structured_output` result is a `_GroqProposalBatchSchema`
    instance, not a plain dict -- `_coerce_output`'s `model_dump()` branch
    must handle it identically to the plain-dict fallback path.
    """
    schema_instance = _GroqProposalBatchSchema(**_valid_output())
    client = _client_returning(schema_instance)
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.COMPLETED
    assert len(result.proposals) == 1


def test_valid_proposals_include_verification_requirements() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    proposal = result.proposals[0]
    assert len(proposal.verification_requirements) > 0


# ---------------------------------------------------------------------------
# 4. Injected fake client path never builds a real client.
# ---------------------------------------------------------------------------


def test_injected_client_path_does_not_build_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(self: GroqAICandidateProposalProvider) -> Any:
        raise AssertionError("_build_client must not be called when a client is injected")

    monkeypatch.setattr(GroqAICandidateProposalProvider, "_build_client", _fail)

    client = _client_returning(_valid_output())
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())  # must not raise

    assert result.status == AICandidateProposalStatus.COMPLETED


# ---------------------------------------------------------------------------
# 5. Fake client returning forbidden factual claim text -> rejected, no
#    proposals.
# ---------------------------------------------------------------------------


def test_forbidden_factual_claim_text_causes_rejected_result() -> None:
    client = _client_returning(
        _valid_output(proposals=[_valid_proposal_dict(candidate_name="This place has a great rating")])
    )
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []
    assert result.guardrail_report.passed is False


# ---------------------------------------------------------------------------
# 6. Fake malformed/non-structured output -> rejected, no proposals.
# ---------------------------------------------------------------------------


def test_non_coercible_response_causes_rejected_result() -> None:
    client = _client_returning("just a plain text response, not structured output")
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []


def test_malformed_response_missing_proposals_key_causes_rejected_result() -> None:
    client = _client_returning({"confidence": 0.5})
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []


def test_empty_proposals_list_causes_rejected_result() -> None:
    client = _client_returning(_valid_output(proposals=[]))
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []


def test_malformed_proposal_entry_causes_rejected_result() -> None:
    client = _client_returning(_valid_output(proposals=["not-a-dict"]))
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []


# ---------------------------------------------------------------------------
# 7. Fake client raising exception -> rejected, no proposals.
# ---------------------------------------------------------------------------


def test_client_raising_exception_causes_rejected_result() -> None:
    client = _client_raising(RuntimeError("simulated API failure"))
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.REJECTED
    assert result.proposals == []
    assert result.guardrail_report.passed is False


# ---------------------------------------------------------------------------
# 8. Explicit api_key is forwarded to the real client builder via a fake
#    `langchain_groq` module -- no network call.
# ---------------------------------------------------------------------------


def test_explicit_api_key_forwarded_to_real_client_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}

    class _FakeChatGroq:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

        def with_structured_output(self, schema: Any) -> "_FakeChatGroq":
            captured_kwargs["structured_output_schema"] = schema
            return self

    fake_module = types.ModuleType("langchain_groq")
    fake_module.ChatGroq = _FakeChatGroq  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_groq", fake_module)

    provider = GroqAICandidateProposalProvider(api_key="fake-explicit-key", model="fake-model")
    client = provider._build_client()

    assert captured_kwargs["api_key"] == "fake-explicit-key"
    assert captured_kwargs["model"] == "fake-model"
    assert captured_kwargs["structured_output_schema"] is _GroqProposalBatchSchema
    assert isinstance(client, _FakeChatGroq)


# ---------------------------------------------------------------------------
# 9. Settings-derived api_key/model used when explicit args are omitted.
# ---------------------------------------------------------------------------


def test_settings_derived_api_key_and_model_used_when_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        groq_adapter_module,
        "get_settings",
        lambda: Settings(GROQ_API_KEY="settings-derived-key", GROQ_MODEL="settings-derived-model"),
    )

    provider = GroqAICandidateProposalProvider()

    assert provider._api_key == "settings-derived-key"
    assert provider._model == "settings-derived-model"


# ---------------------------------------------------------------------------
# 10. No forbidden factual fields anywhere in the result.
# ---------------------------------------------------------------------------


def test_successful_proposals_contain_no_forbidden_factual_fields() -> None:
    proposal_field_names = set(AICandidateProposal.model_fields.keys())
    overlap = proposal_field_names & _FORBIDDEN_PROPOSAL_FIELD_NAMES
    assert overlap == set(), f"AICandidateProposal has forbidden field(s): {overlap}"


def test_result_dump_has_no_forbidden_factual_keys() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAICandidateProposalProvider(client=client)

    result = provider.propose(_request())
    dumped = result.model_dump()

    def _collect_keys(value: object, keys: set[str]) -> None:
        if isinstance(value, dict):
            keys.update(value.keys())
            for nested in value.values():
                _collect_keys(nested, keys)
        elif isinstance(value, list):
            for item in value:
                _collect_keys(item, keys)

    all_keys: set[str] = set()
    _collect_keys(dumped, all_keys)
    overlap = all_keys & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"Result dump has forbidden key(s): {overlap}"


# ---------------------------------------------------------------------------
# 11. Adapter never calls CandidateGroundingService.
# ---------------------------------------------------------------------------


def test_adapter_never_calls_grounding_service(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(self: CandidateGroundingService, request: object) -> None:
        raise AssertionError("CandidateGroundingService.ground must not be called by the adapter")

    monkeypatch.setattr(CandidateGroundingService, "ground", _fail)

    client = _client_returning(_valid_output())
    provider = GroqAICandidateProposalProvider(client=client)
    provider.propose(_request())  # must not raise


# ---------------------------------------------------------------------------
# 12. Adapter does not mutate its input request (it never receives
#     PlanningState at all -- `propose` only ever takes an
#     AICandidateProposalRequest).
# ---------------------------------------------------------------------------


def test_adapter_does_not_mutate_request() -> None:
    client = _client_returning(_valid_output())
    provider = GroqAICandidateProposalProvider(client=client)
    request = _request()
    before = copy.deepcopy(request.model_dump())

    provider.propose(request)

    assert request.model_dump() == before


# ---------------------------------------------------------------------------
# 13. No OpenAI/LangSmith/Gemini/Claude Code CLI imports -- covered by
#     test_adapter_module_has_no_disallowed_vendor_imports above.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Factory support for "groq".
# ---------------------------------------------------------------------------


def test_factory_returns_groq_provider_for_explicit_groq() -> None:
    from app.providers.ai_candidate_proposal import get_ai_candidate_proposal_provider

    provider = get_ai_candidate_proposal_provider("groq")

    assert isinstance(provider, GroqAICandidateProposalProvider)


# ---------------------------------------------------------------------------
# No real GROQ_API_KEY is required for this suite.
# ---------------------------------------------------------------------------


def test_suite_does_not_require_a_real_groq_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_adapter_module, "get_settings", _no_key_settings)
    provider = GroqAICandidateProposalProvider()

    # No network call is made -- this must return instantly, not hang or
    # raise a connection error.
    result = provider.propose(_request())

    assert result.status == AICandidateProposalStatus.NOT_CONNECTED
