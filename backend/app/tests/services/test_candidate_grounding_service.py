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
    CandidateGroundingConfidenceTier,
    CandidateGroundingMatchType,
    CandidateGroundingRejectReason,
    CandidateGroundingRequest,
    CandidateGroundingResult,
    CandidateGroundingStatus,
    ProviderCandidateForGrounding,
)
from app.models.common import DataStatus, GeoPoint
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


def _coordinates() -> GeoPoint:
    return GeoPoint(lat=38.7223, lng=-9.1393)


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


def _provider_candidate(**overrides: object) -> ProviderCandidateForGrounding:
    fields: dict[str, object] = {
        "provider_name": "openstreetmap",
        "provider_place_id": "way/12345",
        "name": "Old Town Waterfront",
        "category": "neighborhood",
        "coordinates": _coordinates(),
        "data_status": DataStatus.LIVE,
        "confidence": 0.8,
    }
    fields.update(overrides)
    return ProviderCandidateForGrounding(**fields)


def _request(**overrides: object) -> CandidateGroundingRequest:
    fields: dict[str, object] = {
        "trip_id": "trip_001",
        "destination_name": "Lisbon",
    }
    fields.update(overrides)
    return CandidateGroundingRequest(**fields)


# ---------------------------------------------------------------------------
# 1. Empty proposals returns skipped.
# ---------------------------------------------------------------------------


def test_ground_returns_skipped_when_no_proposals() -> None:
    service = CandidateGroundingService()
    result = service.ground(_request())
    assert result.status == CandidateGroundingStatus.SKIPPED
    assert result.grounded_candidates == []
    assert result.rejected_proposals == []
    assert result.confidence == 0.0
    assert result.guardrail_report.passed is False
    assert result.guardrail_report.blocked_reasons == [
        "No AI candidate proposals were provided for grounding."
    ]
    assert result.guardrail_report.checked_fields == ["proposals"]
    assert result.provider_name == "candidate_grounding_service"


# ---------------------------------------------------------------------------
# 2. Proposals present but no provider_candidates returns not_connected.
# ---------------------------------------------------------------------------


def test_ground_returns_not_connected_when_no_provider_candidates() -> None:
    service = CandidateGroundingService()
    request = _request(proposals=[_proposal()])
    result = service.ground(request)
    assert result.status == CandidateGroundingStatus.NOT_CONNECTED
    assert result.grounded_candidates == []
    assert result.rejected_proposals == []
    assert result.confidence == 0.0
    assert result.guardrail_report.passed is False
    assert result.guardrail_report.blocked_reasons == [
        "No provider candidates were supplied for grounding."
    ]
    assert result.guardrail_report.checked_fields == ["provider_candidates"]


# ---------------------------------------------------------------------------
# 3. Exact case-insensitive match produces completed result with one
#    GroundedCandidate.
# ---------------------------------------------------------------------------


def test_ground_exact_case_insensitive_match_produces_completed_result() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[_proposal(candidate_name="old town waterfront")],
        provider_candidates=[_provider_candidate(name="Old Town Waterfront")],
    )
    result = service.ground(request)
    assert result.status == CandidateGroundingStatus.COMPLETED
    assert len(result.grounded_candidates) == 1
    grounded = result.grounded_candidates[0]
    assert grounded.grounding_id == "grounding_proposal_001"
    assert grounded.evidence.match_type == CandidateGroundingMatchType.EXACT_NAME
    assert grounded.confidence_tier == CandidateGroundingConfidenceTier.HIGH


# ---------------------------------------------------------------------------
# 4. Normalized match with punctuation/articles produces completed result.
# ---------------------------------------------------------------------------


def test_ground_normalized_match_with_punctuation_and_article_produces_completed_result() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[_proposal(candidate_name="The Old Town Waterfront!")],
        provider_candidates=[_provider_candidate(name="Old Town, Waterfront")],
    )
    result = service.ground(request)
    assert result.status == CandidateGroundingStatus.COMPLETED
    grounded = result.grounded_candidates[0]
    assert grounded.evidence.match_type == CandidateGroundingMatchType.NORMALIZED_NAME
    assert grounded.confidence_tier == CandidateGroundingConfidenceTier.MEDIUM


# ---------------------------------------------------------------------------
# 5. Grounded candidate evidence carries provider_name, provider_place_id,
#    coordinates, data_status, matched category.
# ---------------------------------------------------------------------------


def test_grounded_candidate_evidence_carries_provider_fields() -> None:
    service = CandidateGroundingService()
    provider_candidate = _provider_candidate(
        provider_name="openstreetmap",
        provider_place_id="way/98765",
        category="neighborhood",
        data_status=DataStatus.LIVE,
    )
    request = _request(
        proposals=[_proposal()],
        provider_candidates=[provider_candidate],
    )
    result = service.ground(request)
    evidence = result.grounded_candidates[0].evidence
    assert evidence.provider_name == "openstreetmap"
    assert evidence.provider_place_id == "way/98765"
    assert evidence.matched_category == "neighborhood"
    assert evidence.coordinates == provider_candidate.coordinates
    assert evidence.data_status == DataStatus.LIVE


# ---------------------------------------------------------------------------
# 6. No match produces rejected result with RejectedCandidateProposal and
#    NO_PROVIDER_MATCH.
# ---------------------------------------------------------------------------


def test_ground_no_match_produces_rejected_result() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[_proposal(candidate_name="Mystery Garden")],
        provider_candidates=[_provider_candidate(name="Old Town Waterfront")],
    )
    result = service.ground(request)
    assert result.status == CandidateGroundingStatus.REJECTED
    assert result.grounded_candidates == []
    assert len(result.rejected_proposals) == 1
    rejected = result.rejected_proposals[0]
    assert rejected.reject_reason == CandidateGroundingRejectReason.NO_PROVIDER_MATCH
    assert result.confidence == 0.0
    assert result.guardrail_report.passed is False


# ---------------------------------------------------------------------------
# 7. Ambiguous match produces rejected proposal with AMBIGUOUS_MATCH.
# ---------------------------------------------------------------------------


def test_ground_ambiguous_match_produces_rejected_proposal() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[_proposal(candidate_name="Old Town Waterfront")],
        provider_candidates=[
            _provider_candidate(provider_place_id="way/1", name="Old Town Waterfront"),
            _provider_candidate(provider_place_id="way/2", name="old town waterfront"),
        ],
    )
    result = service.ground(request)
    assert result.status == CandidateGroundingStatus.REJECTED
    assert result.grounded_candidates == []
    rejected = result.rejected_proposals[0]
    assert rejected.reject_reason == CandidateGroundingRejectReason.AMBIGUOUS_MATCH


# ---------------------------------------------------------------------------
# 8. Mixed grounded and rejected proposals produce partial result.
# ---------------------------------------------------------------------------


def test_ground_mixed_grounded_and_rejected_produces_partial_result() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[
            _proposal(proposal_id="proposal_001", candidate_name="Old Town Waterfront"),
            _proposal(proposal_id="proposal_002", candidate_name="Mystery Garden"),
        ],
        provider_candidates=[_provider_candidate(name="Old Town Waterfront")],
    )
    result = service.ground(request)
    assert result.status == CandidateGroundingStatus.PARTIAL
    assert len(result.grounded_candidates) == 1
    assert len(result.rejected_proposals) == 1
    assert result.guardrail_report.passed is True


# ---------------------------------------------------------------------------
# 9. All grounded proposals produce completed result.
# ---------------------------------------------------------------------------


def test_ground_all_grounded_produces_completed_result() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[
            _proposal(proposal_id="proposal_001", candidate_name="Old Town Waterfront"),
            _proposal(proposal_id="proposal_002", candidate_name="Second Idea"),
        ],
        provider_candidates=[
            _provider_candidate(provider_place_id="way/1", name="Old Town Waterfront"),
            _provider_candidate(provider_place_id="way/2", name="Second Idea"),
        ],
    )
    result = service.ground(request)
    assert result.status == CandidateGroundingStatus.COMPLETED
    assert len(result.grounded_candidates) == 2
    assert result.rejected_proposals == []


# ---------------------------------------------------------------------------
# 10. No grounded proposals but rejected proposals produce rejected result.
# ---------------------------------------------------------------------------


def test_ground_no_grounded_but_rejected_produces_rejected_result() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[
            _proposal(proposal_id="proposal_001", candidate_name="Mystery Garden"),
            _proposal(proposal_id="proposal_002", candidate_name="Another Ghost"),
        ],
        provider_candidates=[_provider_candidate(name="Old Town Waterfront")],
    )
    result = service.ground(request)
    assert result.status == CandidateGroundingStatus.REJECTED
    assert result.grounded_candidates == []
    assert len(result.rejected_proposals) == 2


# ---------------------------------------------------------------------------
# 11. Result confidence uses grounded candidate confidence only.
# ---------------------------------------------------------------------------


def test_ground_result_confidence_uses_grounded_candidates_only() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[
            _proposal(proposal_id="proposal_001", candidate_name="Old Town Waterfront", confidence=0.4),
            _proposal(proposal_id="proposal_002", candidate_name="Mystery Garden", confidence=0.9),
        ],
        provider_candidates=[
            _provider_candidate(name="Old Town Waterfront", confidence=0.8),
        ],
    )
    result = service.ground(request)
    assert result.status == CandidateGroundingStatus.PARTIAL
    expected_confidence = min(0.4, 0.8)
    assert result.confidence == pytest.approx(expected_confidence)


# ---------------------------------------------------------------------------
# 12. Repeated calls are deterministic.
# ---------------------------------------------------------------------------


def test_repeated_calls_are_deterministic() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[
            _proposal(proposal_id="proposal_001", candidate_name="Old Town Waterfront"),
            _proposal(proposal_id="proposal_002", candidate_name="Mystery Garden"),
        ],
        provider_candidates=[_provider_candidate(name="Old Town Waterfront")],
    )
    first = service.ground(request)
    second = service.ground(request)
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# 13. Service does not mutate request.
# ---------------------------------------------------------------------------


def test_service_does_not_mutate_request() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[_proposal()],
        provider_candidates=[_provider_candidate()],
    )
    before = copy.deepcopy(request.model_dump())
    service.ground(request)
    after = request.model_dump()
    assert before == after


# ---------------------------------------------------------------------------
# 14. Service imports no provider adapters, LLM clients, LangGraph,
#     LangSmith, httpx, requests, OpenAI, Anthropic, Gemini,
#     PlanningOrchestrator, or PlanningState.
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
        "planning_state",
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
# 15. Service does not import PlanningState.
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
# 16. Result dump contains no forbidden factual fields.
# ---------------------------------------------------------------------------


def test_result_model_has_no_forbidden_factual_field_names() -> None:
    field_names = set(CandidateGroundingResult.model_fields.keys())
    overlap = field_names & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"CandidateGroundingResult has forbidden field(s): {overlap}"


def test_result_dump_has_no_forbidden_factual_keys() -> None:
    service = CandidateGroundingService()
    request = _request(
        proposals=[_proposal()],
        provider_candidates=[_provider_candidate()],
    )
    result = service.ground(request)
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
# 17. Service never creates coordinates not present in supplied
#     provider_candidates.
# ---------------------------------------------------------------------------


def test_grounded_candidate_coordinates_always_come_from_supplied_provider_candidate() -> None:
    service = CandidateGroundingService()
    provider_candidate = _provider_candidate(coordinates=GeoPoint(lat=10.0, lng=20.0))
    request = _request(
        proposals=[_proposal()],
        provider_candidates=[provider_candidate],
    )
    result = service.ground(request)
    grounded = result.grounded_candidates[0]
    assert grounded.evidence.coordinates == provider_candidate.coordinates
    assert grounded.evidence.coordinates.lat == pytest.approx(10.0)
    assert grounded.evidence.coordinates.lng == pytest.approx(20.0)


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
