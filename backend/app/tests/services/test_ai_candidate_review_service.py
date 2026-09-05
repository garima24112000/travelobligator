from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

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
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.ai_candidate_review import AICandidateReviewItem
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
from app.models.candidate_quality import (
    CandidateQualityReport,
    CandidateQualityScore,
    CandidateQualityTier,
    CandidateRejectReason,
    CandidateUseCase,
)
from app.models.common import DataStatus, GeoPoint
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.services import ai_candidate_review_service as ai_candidate_review_service_module
from app.services.ai_candidate_review_service import AICandidateReviewService

# Tests for the read-only AI candidate review/report service (Step 170A,
# extended with deterministic promotion-eligibility rules in Step 170B).
# `AICandidateReviewService.build_report` only ever reads
# `PlanningState.ai_candidate_proposal_batch`/`candidate_grounding_batch`/
# `candidate_quality_report` -- these tests build those fields directly
# (never running the real shadow stage / a real provider) to keep the
# fixture deterministic and fast.

_FORBIDDEN_FACTUAL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "safety_score",
}

_DEFAULT_PROVIDER_PLACE_ID = "test/attraction/1"


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "New York",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _planning_state() -> PlanningState:
    return PlanningState(trip_request=_trip_request())


def _proposal(proposal_id: str, name: str, **overrides: Any) -> AICandidateProposal:
    fields: dict[str, Any] = {
        "proposal_id": proposal_id,
        "candidate_name": name,
        "candidate_type": AICandidateType.ATTRACTION,
        "why_consider": "Locally known landmark that may be under-tagged in provider data.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


def _grounded_candidate(
    proposal: AICandidateProposal,
    *,
    provider_place_id: str = _DEFAULT_PROVIDER_PLACE_ID,
    match_type: CandidateGroundingMatchType = CandidateGroundingMatchType.EXACT_NAME,
    confidence_tier: CandidateGroundingConfidenceTier = CandidateGroundingConfidenceTier.HIGH,
    evidence_confidence: float = 0.8,
    matched_category: str | None = "attraction",
    data_status: DataStatus = DataStatus.LIVE,
) -> GroundedCandidate:
    return GroundedCandidate(
        grounding_id=f"grounding_{proposal.proposal_id}",
        proposal_id=proposal.proposal_id,
        candidate_name=proposal.candidate_name,
        candidate_type=proposal.candidate_type,
        matched_name=proposal.candidate_name,
        confidence_tier=confidence_tier,
        confidence=0.8,
        evidence=CandidateGroundingEvidence(
            provider_name="openstreetmap_places",
            provider_place_id=provider_place_id,
            matched_name=proposal.candidate_name,
            matched_category=matched_category,
            match_type=match_type,
            coordinates=GeoPoint(lat=0.0, lng=0.0),
            data_status=data_status,
            confidence=evidence_confidence,
        ),
        verification_requirements_satisfied=list(proposal.verification_requirements),
    )


def _rejected_proposal(proposal: AICandidateProposal) -> RejectedCandidateProposal:
    return RejectedCandidateProposal(
        proposal_id=proposal.proposal_id,
        candidate_name=proposal.candidate_name,
        candidate_type=proposal.candidate_type,
        reject_reason=CandidateGroundingRejectReason.NO_PROVIDER_MATCH,
        message="No supplied provider candidate matched this AI proposal, so it was not grounded.",
    )


def _quality_score(
    candidate_id: str,
    candidate_name: str,
    tier: CandidateQualityTier,
    *,
    use_case: CandidateUseCase = CandidateUseCase.ATTRACTION,
) -> CandidateQualityScore:
    reject_reasons = (
        [CandidateRejectReason.WEAK_CATEGORY]
        if tier in (CandidateQualityTier.LOW_PRIORITY, CandidateQualityTier.REJECTED)
        else []
    )
    return CandidateQualityScore(
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        use_case=use_case,
        quality_tier=tier,
        total_score={
            CandidateQualityTier.PRIMARY_ANCHOR: 0.9,
            CandidateQualityTier.GOOD_CANDIDATE: 0.6,
            CandidateQualityTier.SECONDARY_CANDIDATE: 0.4,
            CandidateQualityTier.LOW_PRIORITY: 0.25,
            CandidateQualityTier.REJECTED: 0.05,
        }[tier],
        reject_reasons=reject_reasons,
    )


def _quality_report(*scores: CandidateQualityScore) -> CandidateQualityReport:
    return CandidateQualityReport(
        destination_name="New York",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=list(scores),
    )


def _batches_with(
    proposals: list[AICandidateProposal],
    *,
    grounded: list[GroundedCandidate] | None = None,
    rejected: list[RejectedCandidateProposal] | None = None,
) -> tuple[AICandidateProposalBatch, CandidateGroundingBatch]:
    grounded = grounded or []
    rejected = rejected or []

    proposal_status = (
        AICandidateProposalStatus.COMPLETED if proposals else AICandidateProposalStatus.NOT_CONNECTED
    )
    proposal_batch = AICandidateProposalBatch(
        request=AICandidateProposalRequest(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            trip_id="trip_test",
            destination_name="New York",
            trip_duration_days=2,
        ),
        result=AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=proposal_status,
            proposals=proposals,
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            confidence=0.6 if proposals else 0.0,
        ),
    )

    if grounded and rejected:
        grounding_status = CandidateGroundingStatus.PARTIAL
    elif grounded:
        grounding_status = CandidateGroundingStatus.COMPLETED
    elif rejected:
        grounding_status = CandidateGroundingStatus.REJECTED
    else:
        grounding_status = CandidateGroundingStatus.SKIPPED

    guardrail_passed = grounding_status in (
        CandidateGroundingStatus.COMPLETED,
        CandidateGroundingStatus.PARTIAL,
    )
    grounding_batch = CandidateGroundingBatch(
        request=CandidateGroundingRequest(
            trip_id="trip_test",
            destination_name="New York",
            proposals=proposals,
        ),
        result=CandidateGroundingResult(
            status=grounding_status,
            grounded_candidates=grounded,
            rejected_proposals=rejected,
            guardrail_report=CandidateGroundingGuardrailReport(
                passed=guardrail_passed,
                blocked_reasons=[] if guardrail_passed else ["No candidates could be grounded."],
            ),
            confidence=0.6 if grounded else 0.0,
        ),
    )
    return proposal_batch, grounding_batch


def _state_with(
    proposals: list[AICandidateProposal],
    *,
    grounded: list[GroundedCandidate] | None = None,
    rejected: list[RejectedCandidateProposal] | None = None,
    quality_report: CandidateQualityReport | None = None,
) -> PlanningState:
    planning_state = _planning_state()
    proposal_batch, grounding_batch = _batches_with(proposals, grounded=grounded, rejected=rejected)
    planning_state.ai_candidate_proposal_batch = proposal_batch
    planning_state.candidate_grounding_batch = grounding_batch
    planning_state.candidate_quality_report = quality_report
    return planning_state


# ---------------------------------------------------------------------------
# Empty trip / no AI candidate data returns an honest empty report.
# ---------------------------------------------------------------------------


def test_no_batches_returns_empty_report_honestly() -> None:
    planning_state = _planning_state()
    report = AICandidateReviewService().build_report(planning_state)

    assert report.status == "no_candidate_data"
    assert report.total_ai_candidates == 0
    assert report.grounded_candidates == 0
    assert report.ungrounded_candidates == 0
    assert report.eligible_for_promotion == 0
    assert report.items == []
    assert report.trip_id == planning_state.trip_id


def test_not_connected_proposal_batch_returns_empty_report() -> None:
    planning_state = _planning_state()
    proposal_batch, grounding_batch = _batches_with([])
    planning_state.ai_candidate_proposal_batch = proposal_batch
    planning_state.candidate_grounding_batch = grounding_batch

    report = AICandidateReviewService().build_report(planning_state)

    assert report.status == "not_connected"
    assert report.items == []


# ---------------------------------------------------------------------------
# build_report never mutates PlanningState.
# ---------------------------------------------------------------------------


def test_build_report_does_not_mutate_planning_state() -> None:
    proposal = _proposal("proposal_1", "Test Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Test Landmark", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )
    before = planning_state.model_copy(deep=True)

    AICandidateReviewService().build_report(planning_state)

    assert planning_state == before


# ---------------------------------------------------------------------------
# No LLM/provider calls: source-level guarantees.
# ---------------------------------------------------------------------------


def test_service_module_has_no_llm_or_provider_imports() -> None:
    import ast

    source = inspect.getsource(ai_candidate_review_service_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = (
        "anthropic",
        "groq",
        "openai",
        "langgraph",
        "langsmith",
        "ai_candidate_discovery_service",
        "ai_candidate_proposal_provider",
        "app.providers",
        "app.providers.gateway",
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"

    # No live network/HTTP client usage anywhere in the module body either.
    for disallowed_call in ("requests.", "httpx."):
        assert disallowed_call not in source


def test_eligibility_service_module_has_no_llm_or_provider_imports() -> None:
    import ast

    import app.services.ai_candidate_promotion_eligibility_service as eligibility_module

    source = inspect.getsource(eligibility_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = (
        "anthropic",
        "groq",
        "openai",
        "langgraph",
        "langsmith",
        "ai_candidate_discovery_service",
        "candidate_quality_service",
        "candidate_grounding_service",
        "app.providers",
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"
    for disallowed_call in ("requests.", "httpx."):
        assert disallowed_call not in source


# ---------------------------------------------------------------------------
# Candidate counts reflect stored state.
# ---------------------------------------------------------------------------


def test_report_includes_correct_candidate_counts() -> None:
    grounded_proposal = _proposal("proposal_grounded", "Grounded Landmark")
    rejected_proposal_source = _proposal("proposal_rejected", "Rejected Landmark")
    planning_state = _state_with(
        [grounded_proposal, rejected_proposal_source],
        grounded=[_grounded_candidate(grounded_proposal)],
        rejected=[_rejected_proposal(rejected_proposal_source)],
    )

    report = AICandidateReviewService().build_report(planning_state)

    assert report.status == "reviewed"
    assert report.total_ai_candidates == 2
    assert report.grounded_candidates == 1
    assert report.ungrounded_candidates == 1
    assert len(report.items) == 2


# ---------------------------------------------------------------------------
# 1. Grounded candidate with acceptable quality becomes eligible.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tier",
    [
        CandidateQualityTier.PRIMARY_ANCHOR,
        CandidateQualityTier.GOOD_CANDIDATE,
        CandidateQualityTier.SECONDARY_CANDIDATE,
    ],
)
def test_grounded_candidate_with_acceptable_quality_is_eligible(
    tier: CandidateQualityTier,
) -> None:
    proposal = _proposal("proposal_grounded", "Grounded Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Grounded Landmark", tier)
    )
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )

    report = AICandidateReviewService().build_report(planning_state)
    item = report.items[0]

    assert item.eligible_for_promotion is True
    assert item.quality_bucket == tier.value
    assert item.rejection_reasons == []
    assert item.eligibility_reasons


# ---------------------------------------------------------------------------
# 2. Report eligible_for_promotion count increments correctly.
# ---------------------------------------------------------------------------


def test_report_eligible_count_increments_correctly() -> None:
    eligible_proposal = _proposal("proposal_eligible", "Eligible Landmark")
    ungrounded_proposal = _proposal("proposal_ungrounded", "Unmatched Landmark")
    low_quality_proposal = _proposal("proposal_low_quality", "Low Quality Landmark")

    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Eligible Landmark", CandidateQualityTier.GOOD_CANDIDATE),
        _quality_score("place/low", "Low Quality Landmark", CandidateQualityTier.LOW_PRIORITY),
    )
    planning_state = _state_with(
        [eligible_proposal, ungrounded_proposal, low_quality_proposal],
        grounded=[
            _grounded_candidate(eligible_proposal, provider_place_id=_DEFAULT_PROVIDER_PLACE_ID),
            _grounded_candidate(low_quality_proposal, provider_place_id="place/low"),
        ],
        rejected=[_rejected_proposal(ungrounded_proposal)],
        quality_report=quality_report,
    )

    report = AICandidateReviewService().build_report(planning_state)

    assert report.eligible_for_promotion == 1
    eligible_ids = {item.candidate_id for item in report.items if item.eligible_for_promotion}
    assert eligible_ids == {"proposal_eligible"}


# ---------------------------------------------------------------------------
# 3. Ungrounded candidate is not eligible.
# ---------------------------------------------------------------------------


def test_ungrounded_candidate_is_not_eligible() -> None:
    proposal = _proposal("proposal_rejected", "Rejected Landmark")
    planning_state = _state_with([proposal], rejected=[_rejected_proposal(proposal)])

    report = AICandidateReviewService().build_report(planning_state)
    item = report.items[0]

    assert item.provider_grounded is False
    assert item.eligible_for_promotion is False
    assert "Candidate is not provider-grounded." in item.rejection_reasons
    assert item.grounding_status == CandidateGroundingRejectReason.NO_PROVIDER_MATCH.value


# ---------------------------------------------------------------------------
# 4. Grounded but low_priority/rejected quality candidate is not eligible.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", [CandidateQualityTier.LOW_PRIORITY, CandidateQualityTier.REJECTED])
def test_grounded_but_low_quality_candidate_is_not_eligible(tier: CandidateQualityTier) -> None:
    proposal = _proposal("proposal_grounded", "Grounded Landmark")
    quality_report = _quality_report(_quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Grounded Landmark", tier))
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )

    report = AICandidateReviewService().build_report(planning_state)
    item = report.items[0]

    assert item.provider_grounded is True
    assert item.eligible_for_promotion is False
    assert item.quality_bucket == tier.value
    assert any("not acceptable for scheduling" in reason for reason in item.rejection_reasons)


# ---------------------------------------------------------------------------
# 5. Grounded but missing quality candidate is not eligible.
# ---------------------------------------------------------------------------


def test_grounded_but_missing_quality_candidate_is_not_eligible() -> None:
    proposal = _proposal("proposal_grounded", "Grounded Landmark")
    planning_state = _state_with([proposal], grounded=[_grounded_candidate(proposal)])

    report = AICandidateReviewService().build_report(planning_state)
    item = report.items[0]

    assert item.provider_grounded is True
    assert item.eligible_for_promotion is False
    assert item.quality_bucket is None
    assert "No candidate quality score is available for this grounded candidate." in item.rejection_reasons


# ---------------------------------------------------------------------------
# 6. Candidate with rejected grounding message is not eligible.
# ---------------------------------------------------------------------------


def test_candidate_with_rejected_grounding_message_is_not_eligible() -> None:
    proposal = _proposal("proposal_rejected", "Rejected Landmark")
    rejected = _rejected_proposal(proposal)
    planning_state = _state_with([proposal], rejected=[rejected])

    report = AICandidateReviewService().build_report(planning_state)
    item = report.items[0]

    assert item.eligible_for_promotion is False
    assert rejected.message in item.warnings


# ---------------------------------------------------------------------------
# 7. AI-only candidate without provider grounding is not eligible.
# ---------------------------------------------------------------------------


def test_ai_only_candidate_without_grounding_is_not_eligible() -> None:
    proposal = _proposal("proposal_no_grounding", "Ungrounded Landmark")
    planning_state = _state_with([proposal])

    report = AICandidateReviewService().build_report(planning_state)
    item = report.items[0]

    assert item.provider_grounded is False
    assert item.eligible_for_promotion is False


# ---------------------------------------------------------------------------
# 8. Accommodation/hotel candidate is not eligible for AI place promotion.
# ---------------------------------------------------------------------------


def test_accommodation_like_candidate_is_not_eligible() -> None:
    proposal = _proposal("proposal_hotel", "Fictional Hotel")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Fictional Hotel", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal],
        grounded=[_grounded_candidate(proposal, matched_category="hotel")],
        quality_report=quality_report,
    )

    report = AICandidateReviewService().build_report(planning_state)
    item = report.items[0]

    assert item.eligible_for_promotion is False
    assert any("accommodation/lodging" in reason for reason in item.rejection_reasons)


# ---------------------------------------------------------------------------
# 9. Flight/transport candidate is not eligible for AI place promotion.
# ---------------------------------------------------------------------------


def test_flight_transport_like_candidate_is_not_eligible() -> None:
    proposal = _proposal("proposal_airport", "Fictional Airport Lounge")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Fictional Airport Lounge", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal],
        grounded=[_grounded_candidate(proposal, matched_category="airport")],
        quality_report=quality_report,
    )

    report = AICandidateReviewService().build_report(planning_state)
    item = report.items[0]

    assert item.eligible_for_promotion is False
    assert any("flight/transport" in reason for reason in item.rejection_reasons)


# ---------------------------------------------------------------------------
# 10. Model validator rejects eligible_for_promotion=True when
#     provider_grounded=False.
# ---------------------------------------------------------------------------


def test_model_validator_rejects_eligible_without_grounding() -> None:
    with pytest.raises(ValidationError):
        AICandidateReviewItem(
            candidate_id="c1",
            name="Test",
            source="ai_candidate_proposal",
            provider_grounded=False,
            eligible_for_promotion=True,
        )


# ---------------------------------------------------------------------------
# 11. Model validator rejects eligible_for_promotion=True when
#     rejection_reasons is non-empty.
# ---------------------------------------------------------------------------


def test_model_validator_rejects_eligible_with_rejection_reasons() -> None:
    with pytest.raises(ValidationError):
        AICandidateReviewItem(
            candidate_id="c1",
            name="Test",
            source="ai_candidate_proposal",
            provider_grounded=True,
            eligible_for_promotion=True,
            rejection_reasons=["Quality tier 'low_priority' is not acceptable for scheduling."],
        )


def test_report_validator_rejects_mismatched_eligible_count() -> None:
    from app.models.ai_candidate_review import AICandidateReviewReport

    eligible_item = AICandidateReviewItem(
        candidate_id="c1",
        name="Test",
        source="ai_candidate_proposal",
        provider_grounded=True,
        eligible_for_promotion=True,
    )
    with pytest.raises(ValidationError):
        AICandidateReviewReport(
            trip_id="trip_1",
            items=[eligible_item],
            eligible_for_promotion=0,
            generated_at=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# No candidate is added to itinerary days -- this service never schedules
# anything, and ExperiencePlannerService never references this subsystem.
# ---------------------------------------------------------------------------


def test_build_report_never_touches_experience_plan() -> None:
    proposal = _proposal("proposal_grounded", "Grounded Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Grounded Landmark", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )

    AICandidateReviewService().build_report(planning_state)

    assert planning_state.experience_plan is None

    import app.services.experience_planner_service as experience_planner_module

    experience_planner_source = inspect.getsource(experience_planner_module)
    for disallowed in (
        "AICandidateReviewItem",
        "AICandidateReviewReport",
        "ai_candidate_review_service",
        "ai_candidate_promotion_eligibility_service",
        "evaluate_promotion_eligibility",
    ):
        assert disallowed not in experience_planner_source


# ---------------------------------------------------------------------------
# No forbidden factual fields anywhere on the report.
# ---------------------------------------------------------------------------


def test_report_has_no_forbidden_factual_fields() -> None:
    proposal = _proposal("proposal_grounded", "Grounded Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Grounded Landmark", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )

    report = AICandidateReviewService().build_report(planning_state)
    dumped_keys: set[str] = set()

    def _collect(value: Any) -> None:
        if isinstance(value, dict):
            dumped_keys.update(value.keys())
            for nested in value.values():
                _collect(nested)
        elif isinstance(value, list):
            for entry in value:
                _collect(entry)

    _collect(report.model_dump(mode="json"))
    assert dumped_keys & _FORBIDDEN_FACTUAL_FIELD_NAMES == set()
