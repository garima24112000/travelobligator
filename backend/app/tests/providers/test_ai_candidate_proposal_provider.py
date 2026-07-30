from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.models.ai_candidate_proposal import (
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidateProposalTask,
)
from app.providers.ai_candidate_proposal import (
    AICandidateProposalProvider,
    NotConnectedAICandidateProposalProvider,
)

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
# 1. AICandidateProposalProvider cannot be instantiated directly.
# ---------------------------------------------------------------------------


def test_provider_interface_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        AICandidateProposalProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# 2. NotConnectedAICandidateProposalProvider returns status not_connected.
# ---------------------------------------------------------------------------


def test_not_connected_adapter_returns_not_connected_status() -> None:
    provider = NotConnectedAICandidateProposalProvider()
    result = provider.propose(_request())
    assert result.status == AICandidateProposalStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# 3. Returned result has confidence 0.0.
# ---------------------------------------------------------------------------


def test_not_connected_adapter_returns_zero_confidence() -> None:
    provider = NotConnectedAICandidateProposalProvider()
    result = provider.propose(_request())
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# 4. Returned result has no proposals and no rejected_raw_items.
# ---------------------------------------------------------------------------


def test_not_connected_adapter_returns_no_proposals_or_rejected_items() -> None:
    provider = NotConnectedAICandidateProposalProvider()
    result = provider.propose(_request())
    assert result.proposals == []
    assert result.rejected_raw_items == []


# ---------------------------------------------------------------------------
# 5. Returned result preserves request.task.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task", list(AICandidateProposalTask))
def test_not_connected_adapter_preserves_request_task(task: AICandidateProposalTask) -> None:
    provider = NotConnectedAICandidateProposalProvider()
    result = provider.propose(_request(task=task))
    assert result.task == task


# ---------------------------------------------------------------------------
# 6. Returned result has provider_name "ai_candidate_proposal_provider".
# ---------------------------------------------------------------------------


def test_not_connected_adapter_returns_expected_provider_name() -> None:
    provider = NotConnectedAICandidateProposalProvider()
    result = provider.propose(_request())
    assert result.provider_name == "ai_candidate_proposal_provider"
    assert provider.provider_name == "ai_candidate_proposal_provider"


# ---------------------------------------------------------------------------
# 7. Guardrail report passed is false and blocked_reasons is non-empty.
# ---------------------------------------------------------------------------


def test_not_connected_adapter_guardrail_report_is_failed_with_reasons() -> None:
    provider = NotConnectedAICandidateProposalProvider()
    result = provider.propose(_request())
    assert result.guardrail_report.passed is False
    assert len(result.guardrail_report.blocked_reasons) > 0


# ---------------------------------------------------------------------------
# 8. Repeated calls are deterministic.
# ---------------------------------------------------------------------------


def test_repeated_calls_are_deterministic() -> None:
    provider = NotConnectedAICandidateProposalProvider()
    request = _request()
    first = provider.propose(request)
    second = provider.propose(request)
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# 9. Provider does not mutate the request.
# ---------------------------------------------------------------------------


def test_provider_does_not_mutate_request() -> None:
    provider = NotConnectedAICandidateProposalProvider()
    request = _request()
    before = copy.deepcopy(request.model_dump())
    provider.propose(request)
    after = request.model_dump()
    assert before == after


# ---------------------------------------------------------------------------
# 10. Provider output passes AICandidateProposalResult validation.
# ---------------------------------------------------------------------------


def test_provider_output_is_a_valid_result_instance() -> None:
    provider = NotConnectedAICandidateProposalProvider()
    result = provider.propose(_request())
    assert isinstance(result, AICandidateProposalResult)
    # Round-tripping through validation must not raise.
    AICandidateProposalResult.model_validate(result.model_dump())


def test_provider_output_would_fail_validation_if_status_mismatched() -> None:
    provider = NotConnectedAICandidateProposalProvider()
    result = provider.propose(_request())
    dumped = result.model_dump()
    dumped["confidence"] = 0.5
    with pytest.raises(ValidationError):
        AICandidateProposalResult.model_validate(dumped)


# ---------------------------------------------------------------------------
# 11. Provider module imports no LLM clients, LangGraph, LangSmith, httpx,
#     requests, OpenAI, Anthropic, Gemini, or provider adapters.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "app.providers.ai_candidate_proposal.base",
        "app.providers.ai_candidate_proposal.not_connected_adapter",
    ],
)
def test_provider_modules_have_no_disallowed_imports(module_name: str) -> None:
    import ast
    import importlib
    import inspect

    module = importlib.import_module(module_name)
    source = inspect.getsource(module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "gemini",
        "google.generativeai",
        "app.providers.places",
        "app.providers.holidays",
        "app.providers.currency",
        "app.providers.weather",
        "app.providers.gateway",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"{module_name}: disallowed import found: {name}"


# ---------------------------------------------------------------------------
# 12. No output contains forbidden factual fields.
# ---------------------------------------------------------------------------


def test_result_model_has_no_forbidden_factual_field_names() -> None:
    field_names = set(AICandidateProposalResult.model_fields.keys())
    overlap = field_names & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"AICandidateProposalResult has forbidden field(s): {overlap}"


def test_result_dump_has_no_forbidden_factual_keys() -> None:
    provider = NotConnectedAICandidateProposalProvider()
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
