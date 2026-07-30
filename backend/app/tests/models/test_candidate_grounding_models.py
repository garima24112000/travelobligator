from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.candidate_grounding import (
    CandidateGroundingBatch,
    CandidateGroundingConfidenceTier,
    CandidateGroundingEvidence,
    CandidateGroundingGuardrailReport,
    CandidateGroundingMatchType,
    CandidateGroundingRejectReason,
    CandidateGroundingRequest,
    CandidateGroundingResult,
    CandidateGroundingStatus,
    GroundedCandidate,
    RejectedCandidateProposal,
)
from app.models.common import DataStatus, GeoPoint

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


def _coordinates() -> GeoPoint:
    return GeoPoint(lat=38.7223, lng=-9.1393)


def _evidence(**overrides: object) -> CandidateGroundingEvidence:
    fields: dict[str, object] = {
        "provider_name": "openstreetmap",
        "provider_place_id": "way/12345",
        "matched_name": "Old Town Waterfront",
        "matched_category": "neighborhood",
        "match_type": CandidateGroundingMatchType.EXACT_NAME,
        "coordinates": _coordinates(),
        "data_status": DataStatus.LIVE,
        "confidence": 0.8,
    }
    fields.update(overrides)
    return CandidateGroundingEvidence(**fields)


def _verification_requirements() -> list[AICandidateVerificationRequirement]:
    return [AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION]


def _grounded_candidate(**overrides: object) -> GroundedCandidate:
    fields: dict[str, object] = {
        "grounding_id": "grounding_001",
        "proposal_id": "proposal_001",
        "candidate_name": "Old Town Waterfront",
        "candidate_type": AICandidateType.NEIGHBORHOOD,
        "matched_name": "Old Town Waterfront",
        "confidence_tier": CandidateGroundingConfidenceTier.HIGH,
        "confidence": 0.8,
        "evidence": _evidence(),
        "verification_requirements_satisfied": _verification_requirements(),
    }
    fields.update(overrides)
    return GroundedCandidate(**fields)


def _rejected_proposal(**overrides: object) -> RejectedCandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": "proposal_002",
        "candidate_name": "Mystery Garden",
        "candidate_type": AICandidateType.PARK,
        "reject_reason": CandidateGroundingRejectReason.NO_PROVIDER_MATCH,
        "message": "No provider result matched this name within the destination bounds.",
    }
    fields.update(overrides)
    return RejectedCandidateProposal(**fields)


def _guardrail(passed: bool, blocked_reasons: list[str] | None = None) -> CandidateGroundingGuardrailReport:
    return CandidateGroundingGuardrailReport(
        passed=passed, blocked_reasons=blocked_reasons or ([] if passed else ["blocked"])
    )


def _proposal(**overrides: object) -> AICandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": "proposal_001",
        "candidate_name": "Old Town Waterfront",
        "candidate_type": AICandidateType.NEIGHBORHOOD,
        "why_consider": "Locally known for evening walks, may be under-tagged in provider data.",
        "verification_requirements": [AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION],
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
# 1. Valid CandidateGroundingEvidence with provider-backed coordinates is
#    accepted.
# ---------------------------------------------------------------------------


def test_valid_evidence_with_coordinates_is_accepted() -> None:
    evidence = _evidence()
    assert evidence.coordinates.lat == pytest.approx(38.7223)
    assert evidence.match_type == CandidateGroundingMatchType.EXACT_NAME


# ---------------------------------------------------------------------------
# 2. Evidence rejects blank provider_name/provider_place_id/matched_name.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank_field", ["provider_name", "provider_place_id", "matched_name"])
def test_evidence_rejects_blank_required_fields(blank_field: str) -> None:
    with pytest.raises(ValidationError):
        _evidence(**{blank_field: "   "})


# ---------------------------------------------------------------------------
# 3. Evidence requires coordinates.
# ---------------------------------------------------------------------------


def test_evidence_requires_coordinates() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingEvidence(
            provider_name="openstreetmap",
            provider_place_id="way/12345",
            matched_name="Old Town Waterfront",
            match_type=CandidateGroundingMatchType.EXACT_NAME,
            data_status=DataStatus.LIVE,
            confidence=0.8,
        )


# ---------------------------------------------------------------------------
# 4. Valid GroundedCandidate is accepted.
# ---------------------------------------------------------------------------


def test_valid_grounded_candidate_is_accepted() -> None:
    candidate = _grounded_candidate()
    assert candidate.source == "candidate_grounding"
    assert candidate.evidence.provider_name == "openstreetmap"


# ---------------------------------------------------------------------------
# 5. GroundedCandidate rejects blank grounding_id/proposal_id/
#    candidate_name/matched_name.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blank_field", ["grounding_id", "proposal_id", "candidate_name", "matched_name"]
)
def test_grounded_candidate_rejects_blank_required_fields(blank_field: str) -> None:
    with pytest.raises(ValidationError):
        _grounded_candidate(**{blank_field: "   "})


# ---------------------------------------------------------------------------
# 6. GroundedCandidate requires verification_requirements_satisfied.
# ---------------------------------------------------------------------------


def test_grounded_candidate_requires_verification_requirements_satisfied() -> None:
    with pytest.raises(ValidationError):
        _grounded_candidate(verification_requirements_satisfied=[])


# ---------------------------------------------------------------------------
# 7. Valid RejectedCandidateProposal is accepted.
# ---------------------------------------------------------------------------


def test_valid_rejected_candidate_proposal_is_accepted() -> None:
    rejected = _rejected_proposal()
    assert rejected.source == "candidate_grounding"
    assert rejected.reject_reason == CandidateGroundingRejectReason.NO_PROVIDER_MATCH


def test_rejected_candidate_proposal_has_no_coordinate_or_provider_fields() -> None:
    field_names = set(RejectedCandidateProposal.model_fields.keys())
    assert "coordinates" not in field_names
    assert "evidence" not in field_names
    assert "provider_name" not in field_names


# ---------------------------------------------------------------------------
# 8. RejectedCandidateProposal rejects blank proposal_id/candidate_name/
#    message.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank_field", ["proposal_id", "candidate_name", "message"])
def test_rejected_proposal_rejects_blank_required_fields(blank_field: str) -> None:
    with pytest.raises(ValidationError):
        _rejected_proposal(**{blank_field: "   "})


# ---------------------------------------------------------------------------
# 9. CandidateGroundingRequest rejects blank trip_id/destination_name.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank_field", ["trip_id", "destination_name"])
def test_request_rejects_blank_required_fields(blank_field: str) -> None:
    with pytest.raises(ValidationError):
        _request(**{blank_field: "   "})


def test_request_allows_empty_proposals() -> None:
    request = _request(proposals=[])
    assert request.proposals == []


# ---------------------------------------------------------------------------
# 10. CandidateGroundingRequest rejects negative provider_candidate_summary
#     counts.
# ---------------------------------------------------------------------------


def test_request_rejects_negative_provider_candidate_summary_counts() -> None:
    with pytest.raises(ValidationError):
        _request(provider_candidate_summary={"attraction": -1})


def test_request_accepts_zero_provider_candidate_summary_counts() -> None:
    request = _request(provider_candidate_summary={"attraction": 0})
    assert request.provider_candidate_summary == {"attraction": 0}


# ---------------------------------------------------------------------------
# 11. Guardrail report failed requires blocked reasons.
# ---------------------------------------------------------------------------


def test_guardrail_report_failed_requires_blocked_reasons() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingGuardrailReport(passed=False, blocked_reasons=[])


def test_guardrail_report_passed_does_not_require_blocked_reasons() -> None:
    report = CandidateGroundingGuardrailReport(passed=True)
    assert report.blocked_reasons == []


# ---------------------------------------------------------------------------
# 12. completed result requires passed guardrails and at least one grounded
#     candidate.
# ---------------------------------------------------------------------------


def test_completed_result_requires_passed_guardrail() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.COMPLETED,
            grounded_candidates=[_grounded_candidate()],
            guardrail_report=_guardrail(False, ["blocked"]),
            confidence=0.5,
        )


def test_completed_result_requires_grounded_candidate() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.COMPLETED,
            grounded_candidates=[],
            guardrail_report=_guardrail(True),
            confidence=0.5,
        )


def test_completed_result_with_grounded_candidate_is_valid() -> None:
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.COMPLETED,
        grounded_candidates=[_grounded_candidate()],
        guardrail_report=_guardrail(True),
        confidence=0.5,
    )
    assert result.status == CandidateGroundingStatus.COMPLETED


# ---------------------------------------------------------------------------
# 13. partial result requires at least one grounded candidate and one
#     rejected proposal.
# ---------------------------------------------------------------------------


def test_partial_result_requires_grounded_and_rejected() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.PARTIAL,
            grounded_candidates=[_grounded_candidate()],
            rejected_proposals=[],
            guardrail_report=_guardrail(True),
            confidence=0.5,
        )
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.PARTIAL,
            grounded_candidates=[],
            rejected_proposals=[_rejected_proposal()],
            guardrail_report=_guardrail(True),
            confidence=0.5,
        )


def test_partial_result_with_grounded_and_rejected_is_valid() -> None:
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.PARTIAL,
        grounded_candidates=[_grounded_candidate()],
        rejected_proposals=[_rejected_proposal()],
        guardrail_report=_guardrail(True),
        confidence=0.5,
    )
    assert result.status == CandidateGroundingStatus.PARTIAL


# ---------------------------------------------------------------------------
# 14. rejected result requires failed guardrails and blocked reasons.
# ---------------------------------------------------------------------------


def test_rejected_result_requires_failed_guardrail() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.REJECTED,
            guardrail_report=_guardrail(True),
            confidence=0.0,
        )


def test_rejected_result_requires_blocked_reasons() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.REJECTED,
            guardrail_report=CandidateGroundingGuardrailReport.model_construct(
                passed=False, blocked_reasons=[], checked_fields=[]
            ),
            confidence=0.0,
        )


def test_rejected_result_with_blocked_reasons_is_valid() -> None:
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.REJECTED,
        guardrail_report=_guardrail(False, ["Output contained an unsafe claim."]),
        confidence=0.0,
    )
    assert result.status == CandidateGroundingStatus.REJECTED


# ---------------------------------------------------------------------------
# 15. not_connected result requires confidence 0 and no grounded/rejected
#     candidates.
# ---------------------------------------------------------------------------


def test_not_connected_result_requires_zero_confidence() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.NOT_CONNECTED,
            guardrail_report=_guardrail(False, ["No provider connected."]),
            confidence=0.4,
        )


def test_not_connected_result_requires_no_grounded_or_rejected() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.NOT_CONNECTED,
            grounded_candidates=[_grounded_candidate()],
            guardrail_report=_guardrail(False, ["No provider connected."]),
            confidence=0.0,
        )
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.NOT_CONNECTED,
            rejected_proposals=[_rejected_proposal()],
            guardrail_report=_guardrail(False, ["No provider connected."]),
            confidence=0.0,
        )


def test_not_connected_result_is_valid() -> None:
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.NOT_CONNECTED,
        guardrail_report=_guardrail(False, ["No candidate grounding provider is connected yet."]),
        confidence=0.0,
    )
    assert result.grounded_candidates == []
    assert result.rejected_proposals == []


# ---------------------------------------------------------------------------
# 16. skipped result requires confidence 0 and no grounded candidates.
# ---------------------------------------------------------------------------


def test_skipped_result_requires_zero_confidence() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.SKIPPED,
            guardrail_report=_guardrail(False, ["skipped"]),
            confidence=0.2,
        )


def test_skipped_result_requires_no_grounded_candidates() -> None:
    with pytest.raises(ValidationError):
        CandidateGroundingResult(
            status=CandidateGroundingStatus.SKIPPED,
            grounded_candidates=[_grounded_candidate()],
            guardrail_report=_guardrail(False, ["skipped"]),
            confidence=0.0,
        )


def test_skipped_result_is_valid() -> None:
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.SKIPPED,
        guardrail_report=_guardrail(False, ["skipped"]),
        confidence=0.0,
    )
    assert result.status == CandidateGroundingStatus.SKIPPED


# ---------------------------------------------------------------------------
# 17. Batch rejects grounded/rejected proposal IDs that were not in the
#     request.
# ---------------------------------------------------------------------------


def test_batch_rejects_unknown_grounded_proposal_id() -> None:
    request = _request(proposals=[_proposal(proposal_id="proposal_001")])
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.COMPLETED,
        grounded_candidates=[_grounded_candidate(proposal_id="proposal_999")],
        guardrail_report=_guardrail(True),
        confidence=0.5,
    )
    with pytest.raises(ValidationError):
        CandidateGroundingBatch(request=request, result=result)


def test_batch_rejects_unknown_rejected_proposal_id() -> None:
    request = _request(
        proposals=[
            _proposal(proposal_id="proposal_001"),
        ]
    )
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.PARTIAL,
        grounded_candidates=[_grounded_candidate(proposal_id="proposal_001")],
        rejected_proposals=[_rejected_proposal(proposal_id="proposal_999")],
        guardrail_report=_guardrail(True),
        confidence=0.5,
    )
    with pytest.raises(ValidationError):
        CandidateGroundingBatch(request=request, result=result)


# ---------------------------------------------------------------------------
# 18. Batch rejects duplicate proposal IDs across grounded/rejected output.
# ---------------------------------------------------------------------------


def test_batch_rejects_duplicate_proposal_id_within_grounded() -> None:
    request = _request(proposals=[_proposal(proposal_id="proposal_001")])
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.COMPLETED,
        grounded_candidates=[
            _grounded_candidate(grounding_id="grounding_001", proposal_id="proposal_001"),
            _grounded_candidate(grounding_id="grounding_002", proposal_id="proposal_001"),
        ],
        guardrail_report=_guardrail(True),
        confidence=0.5,
    )
    with pytest.raises(ValidationError):
        CandidateGroundingBatch(request=request, result=result)


def test_batch_rejects_proposal_id_both_grounded_and_rejected() -> None:
    request = _request(proposals=[_proposal(proposal_id="proposal_001")])
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.PARTIAL,
        grounded_candidates=[_grounded_candidate(proposal_id="proposal_001")],
        rejected_proposals=[_rejected_proposal(proposal_id="proposal_001")],
        guardrail_report=_guardrail(True),
        confidence=0.5,
    )
    with pytest.raises(ValidationError):
        CandidateGroundingBatch(request=request, result=result)


# ---------------------------------------------------------------------------
# 19. Batch accepts completed result when all request proposals are
#     grounded.
# ---------------------------------------------------------------------------


def test_batch_accepts_completed_result_when_all_proposals_grounded() -> None:
    request = _request(
        proposals=[
            _proposal(proposal_id="proposal_001"),
            _proposal(proposal_id="proposal_002", candidate_name="Second Idea"),
        ]
    )
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.COMPLETED,
        grounded_candidates=[
            _grounded_candidate(grounding_id="grounding_001", proposal_id="proposal_001"),
            _grounded_candidate(grounding_id="grounding_002", proposal_id="proposal_002"),
        ],
        guardrail_report=_guardrail(True),
        confidence=0.6,
    )
    batch = CandidateGroundingBatch(request=request, result=result)
    assert batch.result is not None


def test_batch_rejects_completed_result_missing_a_proposal() -> None:
    request = _request(
        proposals=[
            _proposal(proposal_id="proposal_001"),
            _proposal(proposal_id="proposal_002", candidate_name="Second Idea"),
        ]
    )
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.COMPLETED,
        grounded_candidates=[_grounded_candidate(proposal_id="proposal_001")],
        guardrail_report=_guardrail(True),
        confidence=0.6,
    )
    with pytest.raises(ValidationError):
        CandidateGroundingBatch(request=request, result=result)


# ---------------------------------------------------------------------------
# 20. Batch accepts partial result when request proposals are split between
#     grounded and rejected.
# ---------------------------------------------------------------------------


def test_batch_accepts_partial_result_split_between_grounded_and_rejected() -> None:
    request = _request(
        proposals=[
            _proposal(proposal_id="proposal_001"),
            _proposal(proposal_id="proposal_002", candidate_name="Second Idea"),
        ]
    )
    result = CandidateGroundingResult(
        status=CandidateGroundingStatus.PARTIAL,
        grounded_candidates=[_grounded_candidate(proposal_id="proposal_001")],
        rejected_proposals=[_rejected_proposal(proposal_id="proposal_002")],
        guardrail_report=_guardrail(True),
        confidence=0.5,
    )
    batch = CandidateGroundingBatch(request=request, result=result)
    assert batch.result is not None


def test_batch_without_result_is_valid() -> None:
    batch = CandidateGroundingBatch(request=_request())
    assert batch.result is None


# ---------------------------------------------------------------------------
# 21. Forbidden factual claim patterns are rejected in evidence, grounded
#     candidates, and rejected proposal messages.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden_text", _FORBIDDEN_TEXT_CASES)
def test_evidence_rejects_forbidden_text_in_matched_name(forbidden_text: str) -> None:
    with pytest.raises(ValidationError):
        _evidence(matched_name=forbidden_text)


@pytest.mark.parametrize("forbidden_text", _FORBIDDEN_TEXT_CASES)
def test_grounded_candidate_rejects_forbidden_text_in_candidate_name(forbidden_text: str) -> None:
    with pytest.raises(ValidationError):
        _grounded_candidate(candidate_name=forbidden_text)


@pytest.mark.parametrize("forbidden_text", _FORBIDDEN_TEXT_CASES)
def test_rejected_proposal_rejects_forbidden_text_in_message(forbidden_text: str) -> None:
    with pytest.raises(ValidationError):
        _rejected_proposal(message=forbidden_text)


# ---------------------------------------------------------------------------
# 22. Model dumps contain no forbidden factual field names.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_cls",
    [
        CandidateGroundingEvidence,
        GroundedCandidate,
        RejectedCandidateProposal,
        CandidateGroundingRequest,
        CandidateGroundingResult,
        CandidateGroundingBatch,
    ],
)
def test_models_never_contain_forbidden_field_names(model_cls) -> None:
    field_names = set(model_cls.model_fields.keys())
    overlap = field_names & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"{model_cls.__name__} has forbidden field(s): {overlap}"


# ---------------------------------------------------------------------------
# 23. No imports of provider adapters, LLM clients, LangGraph, LangSmith,
#     httpx, requests, OpenAI, Anthropic, Gemini, or services.
# ---------------------------------------------------------------------------


def test_module_has_no_disallowed_imports() -> None:
    import ast
    import inspect

    import app.models.candidate_grounding as module

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
