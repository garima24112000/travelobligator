from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalBatch,
    AICandidateProposalGuardrailReport,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidateProposalTask,
    AICandidatePriorityHint,
    AICandidateType,
    AICandidateVerificationRequirement,
)

_FORBIDDEN_MODEL_FIELD_NAMES = {
    "rating",
    "price",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
}

_FORBIDDEN_TEXT_CASES = [
    "This place has a great rating",
    "The price is low",
    "opening_hours: 9am-5pm",
    "route_time is short",
    "booking_url available soon",
    "review_count is high",
    "ticket_price is affordable",
    "availability looks good",
    "safety_score is high",
    "reservation recommended",
    "book now before it fills up",
    "open until late",
    "highly rated by locals",
    "a cheap place to visit",
    "guaran" + "teed to be a great time",
    "exact travel time is 10 minutes",
]


def _valid_verification_requirements() -> list[AICandidateVerificationRequirement]:
    return [
        AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION,
        AICandidateVerificationRequirement.MUST_REJECT_IF_NOT_FOUND,
    ]


def _valid_proposal(**overrides: object) -> AICandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": "proposal_001",
        "candidate_name": "Old Town Waterfront",
        "candidate_type": AICandidateType.NEIGHBORHOOD,
        "priority_hint": AICandidatePriorityHint.MEDIUM,
        "suggested_area": "Old Town",
        "why_consider": "Locally known for evening walks, may be under-tagged in provider data.",
        "fit_with_user_preferences": ["matches interest in walking tours"],
        "verification_requirements": _valid_verification_requirements(),
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


def _guardrail(passed: bool, blocked_reasons: list[str] | None = None) -> AICandidateProposalGuardrailReport:
    return AICandidateProposalGuardrailReport(
        passed=passed, blocked_reasons=blocked_reasons or ([] if passed else ["blocked"])
    )


def _valid_request(**overrides: object) -> AICandidateProposalRequest:
    fields: dict[str, object] = {
        "task": AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        "trip_id": "trip_001",
        "destination_name": "Lisbon",
        "trip_duration_days": 4,
    }
    fields.update(overrides)
    return AICandidateProposalRequest(**fields)


# ---------------------------------------------------------------------------
# 1. Valid proposal with safe text is accepted.
# ---------------------------------------------------------------------------


def test_valid_proposal_with_safe_text_is_accepted() -> None:
    proposal = _valid_proposal()
    assert proposal.candidate_name == "Old Town Waterfront"
    assert proposal.source == "ai_candidate_proposal"


# ---------------------------------------------------------------------------
# 2. Blank proposal_id/candidate_name/why_consider rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank_field", ["proposal_id", "candidate_name", "why_consider"])
def test_proposal_rejects_blank_required_text_fields(blank_field: str) -> None:
    with pytest.raises(ValidationError):
        _valid_proposal(**{blank_field: "   "})


# ---------------------------------------------------------------------------
# 3. Empty verification_requirements rejected.
# ---------------------------------------------------------------------------


def test_proposal_rejects_empty_verification_requirements() -> None:
    with pytest.raises(ValidationError):
        _valid_proposal(verification_requirements=[])


# ---------------------------------------------------------------------------
# 4. Forbidden factual claims are rejected in candidate_name, suggested_area,
#    why_consider, and fit_with_user_preferences.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden_text", _FORBIDDEN_TEXT_CASES)
def test_proposal_rejects_forbidden_text_in_candidate_name(forbidden_text: str) -> None:
    with pytest.raises(ValidationError):
        _valid_proposal(candidate_name=forbidden_text)


@pytest.mark.parametrize("forbidden_text", _FORBIDDEN_TEXT_CASES)
def test_proposal_rejects_forbidden_text_in_suggested_area(forbidden_text: str) -> None:
    with pytest.raises(ValidationError):
        _valid_proposal(suggested_area=forbidden_text)


@pytest.mark.parametrize("forbidden_text", _FORBIDDEN_TEXT_CASES)
def test_proposal_rejects_forbidden_text_in_why_consider(forbidden_text: str) -> None:
    with pytest.raises(ValidationError):
        _valid_proposal(why_consider=forbidden_text)


@pytest.mark.parametrize("forbidden_text", _FORBIDDEN_TEXT_CASES)
def test_proposal_rejects_forbidden_text_in_fit_with_user_preferences(forbidden_text: str) -> None:
    with pytest.raises(ValidationError):
        _valid_proposal(fit_with_user_preferences=[forbidden_text])


# ---------------------------------------------------------------------------
# 5. Request rejects blank trip_id/destination_name.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank_field", ["trip_id", "destination_name"])
def test_request_rejects_blank_required_fields(blank_field: str) -> None:
    with pytest.raises(ValidationError):
        _valid_request(**{blank_field: "   "})


# ---------------------------------------------------------------------------
# 6. Request rejects trip_duration_days < 1.
# ---------------------------------------------------------------------------


def test_request_rejects_trip_duration_days_below_one() -> None:
    with pytest.raises(ValidationError):
        _valid_request(trip_duration_days=0)


# ---------------------------------------------------------------------------
# 7. Request rejects max_candidates > 25.
# ---------------------------------------------------------------------------


def test_request_rejects_max_candidates_above_cap() -> None:
    with pytest.raises(ValidationError):
        _valid_request(max_candidates=26)


def test_request_default_max_candidates_is_fifteen() -> None:
    request = _valid_request()
    assert request.max_candidates == 15


# ---------------------------------------------------------------------------
# 8. completed result requires proposals.
# ---------------------------------------------------------------------------


def test_completed_result_requires_proposals() -> None:
    with pytest.raises(ValidationError):
        AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=[],
            guardrail_report=_guardrail(True),
            confidence=0.5,
        )


def test_completed_result_with_proposals_is_valid() -> None:
    result = AICandidateProposalResult(
        task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        status=AICandidateProposalStatus.COMPLETED,
        proposals=[_valid_proposal()],
        guardrail_report=_guardrail(True),
        confidence=0.5,
    )
    assert result.status == AICandidateProposalStatus.COMPLETED


# ---------------------------------------------------------------------------
# 9. completed result requires passed guardrails.
# ---------------------------------------------------------------------------


def test_completed_result_requires_guardrail_passed_true() -> None:
    with pytest.raises(ValidationError):
        AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=[_valid_proposal()],
            guardrail_report=_guardrail(False, ["blocked"]),
            confidence=0.5,
        )


# ---------------------------------------------------------------------------
# 10. rejected result requires failed guardrails and blocked reasons.
# ---------------------------------------------------------------------------


def test_rejected_result_requires_guardrail_passed_false() -> None:
    with pytest.raises(ValidationError):
        AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.REJECTED,
            proposals=[],
            guardrail_report=_guardrail(True),
            confidence=0.0,
        )


def test_rejected_result_requires_blocked_reasons() -> None:
    with pytest.raises(ValidationError):
        AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.REJECTED,
            proposals=[],
            guardrail_report=AICandidateProposalGuardrailReport.model_construct(
                passed=False, blocked_reasons=[], checked_fields=[]
            ),
            confidence=0.0,
        )


def test_guardrail_report_itself_requires_blocked_reasons_when_failed() -> None:
    with pytest.raises(ValidationError):
        AICandidateProposalGuardrailReport(passed=False, blocked_reasons=[])


# ---------------------------------------------------------------------------
# 11. not_connected requires confidence 0 and no proposals.
# ---------------------------------------------------------------------------


def test_not_connected_result_requires_zero_confidence() -> None:
    with pytest.raises(ValidationError):
        AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.NOT_CONNECTED,
            proposals=[],
            guardrail_report=_guardrail(False, ["No AI candidate proposal provider is connected yet."]),
            confidence=0.4,
        )


def test_not_connected_result_requires_no_proposals() -> None:
    with pytest.raises(ValidationError):
        AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.NOT_CONNECTED,
            proposals=[_valid_proposal()],
            guardrail_report=_guardrail(False, ["No AI candidate proposal provider is connected yet."]),
            confidence=0.0,
        )


def test_not_connected_result_is_valid() -> None:
    result = AICandidateProposalResult(
        task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        status=AICandidateProposalStatus.NOT_CONNECTED,
        proposals=[],
        guardrail_report=_guardrail(False, ["No AI candidate proposal provider is connected yet."]),
        confidence=0.0,
    )
    assert result.confidence == 0.0
    assert result.proposals == []


# ---------------------------------------------------------------------------
# 12. skipped requires no proposals.
# ---------------------------------------------------------------------------


def test_skipped_result_requires_no_proposals() -> None:
    with pytest.raises(ValidationError):
        AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.SKIPPED,
            proposals=[_valid_proposal()],
            guardrail_report=_guardrail(False, ["skipped"]),
            confidence=0.0,
        )


def test_skipped_result_is_valid() -> None:
    result = AICandidateProposalResult(
        task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        status=AICandidateProposalStatus.SKIPPED,
        proposals=[],
        guardrail_report=_guardrail(False, ["skipped"]),
        confidence=0.0,
    )
    assert result.status == AICandidateProposalStatus.SKIPPED


# ---------------------------------------------------------------------------
# 13. Batch rejects task mismatch.
# ---------------------------------------------------------------------------


def test_batch_rejects_task_mismatch() -> None:
    with pytest.raises(ValidationError):
        AICandidateProposalBatch(
            request=_valid_request(task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY),
            result=AICandidateProposalResult(
                task=AICandidateProposalTask.FEEDBACK_CANDIDATE_DISCOVERY,
                status=AICandidateProposalStatus.NOT_CONNECTED,
                proposals=[],
                guardrail_report=_guardrail(False, ["No provider connected."]),
                confidence=0.0,
            ),
        )


def test_batch_with_matching_task_is_valid() -> None:
    batch = AICandidateProposalBatch(
        request=_valid_request(task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY),
        result=AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.NOT_CONNECTED,
            proposals=[],
            guardrail_report=_guardrail(False, ["No provider connected."]),
            confidence=0.0,
        ),
    )
    assert batch.result is not None


def test_batch_without_result_is_valid() -> None:
    batch = AICandidateProposalBatch(request=_valid_request())
    assert batch.result is None


# ---------------------------------------------------------------------------
# 14. Model dumps contain no forbidden factual field names.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_cls",
    [AICandidateProposal, AICandidateProposalRequest, AICandidateProposalResult, AICandidateProposalBatch],
)
def test_models_never_contain_forbidden_field_names(model_cls) -> None:
    field_names = set(model_cls.model_fields.keys())
    overlap = field_names & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"{model_cls.__name__} has forbidden field(s): {overlap}"


# ---------------------------------------------------------------------------
# 15. No imports of provider adapters, LLM clients, LangGraph, LangSmith,
#     httpx, requests, OpenAI, Anthropic, or Gemini.
# ---------------------------------------------------------------------------


def test_module_has_no_disallowed_imports() -> None:
    import ast
    import inspect

    import app.models.ai_candidate_proposal as module

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
        "app.services",
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
