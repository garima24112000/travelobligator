from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.candidate_grounding import (
    CandidateGroundingRequest,
    CandidateGroundingResult,
    CandidateGroundingStatus,
)
from app.services.candidate_grounding_service import CandidateGroundingService

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


def _proposal(**overrides: object) -> AICandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": "proposal_001",
        "candidate_name": "Old Town Waterfront",
        "candidate_type": AICandidateType.NEIGHBORHOOD,
        "why_consider": "Locally known for evening walks, may be under-tagged in provider data.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


def _request(**overrides: object) -> CandidateGroundingRequest:
    fields: dict[str, object] = {
        "trip_id": "trip_001",
        "destination_name": "Lisbon",
    }
    fields.update(overrides)
    return CandidateGroundingRequest(**fields)


# ---------------------------------------------------------------------------
# 1. CandidateGroundingService.ground returns status not_connected.
# ---------------------------------------------------------------------------


def test_ground_returns_not_connected_status() -> None:
    service = CandidateGroundingService()
    result = service.ground(_request())
    assert result.status == CandidateGroundingStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# 2. Result has confidence 0.0.
# ---------------------------------------------------------------------------


def test_ground_returns_zero_confidence() -> None:
    service = CandidateGroundingService()
    result = service.ground(_request())
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# 3. Result has empty grounded_candidates and rejected_proposals.
# ---------------------------------------------------------------------------


def test_ground_returns_empty_grounded_and_rejected() -> None:
    service = CandidateGroundingService()
    result = service.ground(_request())
    assert result.grounded_candidates == []
    assert result.rejected_proposals == []


# ---------------------------------------------------------------------------
# 4. Result has provider_name "candidate_grounding_service".
# ---------------------------------------------------------------------------


def test_ground_returns_expected_provider_name() -> None:
    service = CandidateGroundingService()
    result = service.ground(_request())
    assert result.provider_name == "candidate_grounding_service"
    assert service.provider_name == "candidate_grounding_service"


# ---------------------------------------------------------------------------
# 5. Guardrail report passed is false and blocked_reasons is non-empty.
# ---------------------------------------------------------------------------


def test_ground_guardrail_report_is_failed_with_reasons() -> None:
    service = CandidateGroundingService()
    result = service.ground(_request())
    assert result.guardrail_report.passed is False
    assert len(result.guardrail_report.blocked_reasons) > 0


# ---------------------------------------------------------------------------
# 6. Guardrail checked_fields includes "grounding_connection".
# ---------------------------------------------------------------------------


def test_ground_guardrail_checked_fields_includes_grounding_connection() -> None:
    service = CandidateGroundingService()
    result = service.ground(_request())
    assert "grounding_connection" in result.guardrail_report.checked_fields


# ---------------------------------------------------------------------------
# 7. Output passes CandidateGroundingResult validation round-trip.
# ---------------------------------------------------------------------------


def test_ground_output_is_a_valid_result_instance() -> None:
    service = CandidateGroundingService()
    result = service.ground(_request())
    assert isinstance(result, CandidateGroundingResult)
    CandidateGroundingResult.model_validate(result.model_dump())


def test_ground_output_would_fail_validation_if_confidence_mismatched() -> None:
    service = CandidateGroundingService()
    result = service.ground(_request())
    dumped = result.model_dump()
    dumped["confidence"] = 0.5
    with pytest.raises(ValidationError):
        CandidateGroundingResult.model_validate(dumped)


# ---------------------------------------------------------------------------
# 8. Repeated calls are deterministic.
# ---------------------------------------------------------------------------


def test_repeated_calls_are_deterministic() -> None:
    service = CandidateGroundingService()
    request = _request()
    first = service.ground(request)
    second = service.ground(request)
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# 9. Service does not mutate the request.
# ---------------------------------------------------------------------------


def test_service_does_not_mutate_request() -> None:
    service = CandidateGroundingService()
    request = _request(proposals=[_proposal()])
    before = copy.deepcopy(request.model_dump())
    service.ground(request)
    after = request.model_dump()
    assert before == after


# ---------------------------------------------------------------------------
# 10. Request with proposals still returns no grounded/rejected output.
# ---------------------------------------------------------------------------


def test_request_with_proposals_still_returns_no_grounded_or_rejected() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[
            _proposal(proposal_id="proposal_001"),
            _proposal(proposal_id="proposal_002", candidate_name="Second Idea"),
        ]
    )
    result = service.ground(request)
    assert result.grounded_candidates == []
    assert result.rejected_proposals == []
    assert result.status == CandidateGroundingStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# 11. Service module imports no provider adapters, LLM clients, LangGraph,
#     LangSmith, httpx, requests, OpenAI, Anthropic, Gemini, or
#     PlanningOrchestrator.
# ---------------------------------------------------------------------------


def test_service_module_has_no_disallowed_imports() -> None:
    import ast
    import inspect

    import app.services.candidate_grounding_service as module

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
        "app.providers",
        "planning_orchestrator",
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
            assert disallowed not in lowered, f"Disallowed import found: {name}"


# ---------------------------------------------------------------------------
# 12. Service does not import PlanningState.
# ---------------------------------------------------------------------------


def test_service_module_does_not_import_planning_state() -> None:
    import ast
    import inspect

    import app.services.candidate_grounding_service as module

    source = inspect.getsource(module)
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.models.planning_state":
            pytest.fail("candidate_grounding_service must not import app.models.planning_state")
        if isinstance(node, ast.ImportFrom) and node.names:
            for alias in node.names:
                assert alias.name != "PlanningState"


# ---------------------------------------------------------------------------
# 13. Result dump contains no forbidden factual fields.
# ---------------------------------------------------------------------------


def test_result_model_has_no_forbidden_factual_field_names() -> None:
    field_names = set(CandidateGroundingResult.model_fields.keys())
    overlap = field_names & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"CandidateGroundingResult has forbidden field(s): {overlap}"


def test_result_dump_has_no_forbidden_factual_keys() -> None:
    service = CandidateGroundingService()
    result = service.ground(_request())
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
