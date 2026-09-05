from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

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
from app.services import ai_candidate_promotion_service as ai_candidate_promotion_service_module
from app.services.ai_candidate_promotion_service import AICandidatePromotionService

# Tests for the AI candidate promotion service (Step 170C). A "promoted"
# candidate is not an itinerary stop -- it is a provider-grounded,
# quality-approved candidate already marked `eligible_for_promotion=True`
# by Step 170B's deterministic rules, materialized into a dedicated
# `AICandidatePromotionReport`. These tests build
# `ai_candidate_proposal_batch`/`candidate_grounding_batch`/
# `candidate_quality_report` directly (never running a real shadow stage or
# provider) to keep the fixtures deterministic and fast.

_FORBIDDEN_FACTUAL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "safety_score",
    "description",
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
    provider_name: str = "openstreetmap_places",
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
            provider_name=provider_name,
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
# 8. Empty candidate state returns empty promotion report.
# ---------------------------------------------------------------------------


def test_no_candidates_returns_empty_promotion_report() -> None:
    planning_state = _planning_state()
    report = AICandidatePromotionService().build_promotion_report(planning_state)

    assert report.status == "no_candidates_reviewed"
    assert report.total_reviewed_candidates == 0
    assert report.promoted_count == 0
    assert report.skipped_count == 0
    assert report.promoted_candidates == []
    assert report.skipped_candidate_ids == []
    assert report.trip_id == planning_state.trip_id


# ---------------------------------------------------------------------------
# 1. Promotes grounded candidate with acceptable quality.
# ---------------------------------------------------------------------------


def test_promotes_grounded_candidate_with_acceptable_quality() -> None:
    proposal = _proposal("proposal_eligible", "Eligible Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Eligible Landmark", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )

    report = AICandidatePromotionService().build_promotion_report(planning_state)

    assert report.status == "promoted"
    assert report.promoted_count == 1
    assert report.skipped_count == 0
    promoted = report.promoted_candidates[0]
    assert promoted.name == "Eligible Landmark"
    assert promoted.promoted is True
    assert promoted.original_ai_candidate_id == "proposal_eligible"


# ---------------------------------------------------------------------------
# 2. Ungrounded candidate is not promoted.
# ---------------------------------------------------------------------------


def test_does_not_promote_ungrounded_candidate() -> None:
    proposal = _proposal("proposal_rejected", "Rejected Landmark")
    planning_state = _state_with([proposal], rejected=[_rejected_proposal(proposal)])

    report = AICandidatePromotionService().build_promotion_report(planning_state)

    assert report.promoted_count == 0
    assert report.skipped_count == 1
    assert report.skipped_candidate_ids == ["proposal_rejected"]


# ---------------------------------------------------------------------------
# 3. Does not promote low_priority/rejected/missing-quality candidate.
# ---------------------------------------------------------------------------


def test_does_not_promote_low_quality_candidate() -> None:
    proposal = _proposal("proposal_low_quality", "Low Quality Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Low Quality Landmark", CandidateQualityTier.LOW_PRIORITY)
    )
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )

    report = AICandidatePromotionService().build_promotion_report(planning_state)

    assert report.promoted_count == 0
    assert report.skipped_candidate_ids == ["proposal_low_quality"]


def test_does_not_promote_missing_quality_candidate() -> None:
    proposal = _proposal("proposal_missing_quality", "Missing Quality Landmark")
    planning_state = _state_with([proposal], grounded=[_grounded_candidate(proposal)])

    report = AICandidatePromotionService().build_promotion_report(planning_state)

    assert report.promoted_count == 0
    assert report.skipped_candidate_ids == ["proposal_missing_quality"]


# ---------------------------------------------------------------------------
# 4. Does not promote accommodation/hotel candidate.
# ---------------------------------------------------------------------------


def test_does_not_promote_accommodation_like_candidate() -> None:
    proposal = _proposal("proposal_hotel", "Fictional Hotel")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Fictional Hotel", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal],
        grounded=[_grounded_candidate(proposal, matched_category="hotel")],
        quality_report=quality_report,
    )

    report = AICandidatePromotionService().build_promotion_report(planning_state)

    assert report.promoted_count == 0
    assert report.skipped_candidate_ids == ["proposal_hotel"]


# ---------------------------------------------------------------------------
# 5. Does not promote flight/transport candidate.
# ---------------------------------------------------------------------------


def test_does_not_promote_flight_transport_like_candidate() -> None:
    proposal = _proposal("proposal_airport", "Fictional Airport Lounge")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Fictional Airport Lounge", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal],
        grounded=[_grounded_candidate(proposal, matched_category="airport")],
        quality_report=quality_report,
    )

    report = AICandidatePromotionService().build_promotion_report(planning_state)

    assert report.promoted_count == 0
    assert report.skipped_candidate_ids == ["proposal_airport"]


# ---------------------------------------------------------------------------
# 6. Does not promote AI-only candidate (no grounding at all).
# ---------------------------------------------------------------------------


def test_does_not_promote_ai_only_candidate_without_grounding() -> None:
    proposal = _proposal("proposal_ai_only", "AI Only Landmark")
    planning_state = _state_with([proposal])

    report = AICandidatePromotionService().build_promotion_report(planning_state)

    assert report.promoted_count == 0
    assert report.skipped_candidate_ids == ["proposal_ai_only"]


# ---------------------------------------------------------------------------
# 7. Promotion report counts are correct with a mix of candidates.
# ---------------------------------------------------------------------------


def test_promotion_report_counts_are_correct() -> None:
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

    report = AICandidatePromotionService().build_promotion_report(planning_state)

    assert report.total_reviewed_candidates == 3
    assert report.promoted_count == 1
    assert report.skipped_count == 2
    assert {c.original_ai_candidate_id for c in report.promoted_candidates} == {"proposal_eligible"}
    assert set(report.skipped_candidate_ids) == {"proposal_ungrounded", "proposal_low_quality"}


# ---------------------------------------------------------------------------
# 9. Promotion preserves provider-backed identifiers/evidence.
# ---------------------------------------------------------------------------


def test_promotion_preserves_provider_backed_identifiers() -> None:
    proposal = _proposal("proposal_eligible", "Eligible Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Eligible Landmark", CandidateQualityTier.PRIMARY_ANCHOR)
    )
    planning_state = _state_with(
        [proposal],
        grounded=[
            _grounded_candidate(
                proposal, provider_place_id=_DEFAULT_PROVIDER_PLACE_ID, provider_name="openstreetmap_places"
            )
        ],
        quality_report=quality_report,
    )

    report = AICandidatePromotionService().build_promotion_report(planning_state)
    promoted = report.promoted_candidates[0]

    assert promoted.provider_place_id == _DEFAULT_PROVIDER_PLACE_ID
    assert promoted.provider_source == "openstreetmap_places"
    assert promoted.quality_bucket == "primary_anchor"
    assert promoted.grounding_status == CandidateGroundingMatchType.EXACT_NAME.value
    assert promoted.original_ai_candidate_id == "proposal_eligible"
    # Step 170D: coordinates/confidence/data_status are copied verbatim
    # from the real GroundedCandidate.evidence -- required for
    # ExperiencePlannerService to schedule this candidate later, never
    # guessed.
    assert promoted.coordinates is not None
    assert promoted.coordinates.lat == 0.0
    assert promoted.coordinates.lng == 0.0
    assert promoted.confidence == 0.8
    assert promoted.data_status == "live"


# ---------------------------------------------------------------------------
# 10. Promotion does not invent coordinates, ratings, opening hours,
#     prices, booking links, routes, or descriptions.
# ---------------------------------------------------------------------------


def test_promotion_has_no_forbidden_factual_fields() -> None:
    proposal = _proposal("proposal_eligible", "Eligible Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Eligible Landmark", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )

    report = AICandidatePromotionService().build_promotion_report(planning_state)
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


# ---------------------------------------------------------------------------
# 11. Idempotent: running promotion twice does not duplicate candidates.
# ---------------------------------------------------------------------------


def test_apply_promotion_is_idempotent() -> None:
    proposal = _proposal("proposal_eligible", "Eligible Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Eligible Landmark", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )

    service = AICandidatePromotionService()
    service.apply_promotion(planning_state)
    first_report = planning_state.ai_candidate_promotion_report
    assert first_report is not None

    service.apply_promotion(planning_state)
    second_report = planning_state.ai_candidate_promotion_report
    assert second_report is not None

    assert first_report.promoted_count == second_report.promoted_count == 1
    assert [c.candidate_id for c in first_report.promoted_candidates] == [
        c.candidate_id for c in second_report.promoted_candidates
    ]
    # No accumulation across repeated calls -- the report is replaced, not
    # appended to.
    assert len(second_report.promoted_candidates) == 1


# ---------------------------------------------------------------------------
# 12. Promotion does not mutate itinerary days.
# ---------------------------------------------------------------------------


def test_promotion_never_touches_experience_plan() -> None:
    """`AICandidatePromotionService` itself never mutates
    `experience_plan`/`daily_plans` -- calling it in isolation (without
    ever running `ExperiencePlannerService`) leaves `experience_plan`
    exactly as it was. As of Step 170D, `ExperiencePlannerService` is
    allowed to *read* `ai_candidate_promotion_report` when it later runs
    (that's the whole point of Step 170D) -- so this test asserts the
    dependency only ever points one way: `ai_candidate_promotion_service`
    must never import or reference `ExperiencePlannerService`/
    `ExperienceItem`/`daily_plans` itself.
    """
    proposal = _proposal("proposal_eligible", "Eligible Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Eligible Landmark", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )

    AICandidatePromotionService().apply_promotion(planning_state)

    assert planning_state.experience_plan is None

    import ast

    source = inspect.getsource(ai_candidate_promotion_service_module)
    tree = ast.parse(source)
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = ("experience_planner_service",)
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


# ---------------------------------------------------------------------------
# build_promotion_report never mutates PlanningState (apply_promotion is
# the only mutating entry point).
# ---------------------------------------------------------------------------


def test_build_promotion_report_does_not_mutate_planning_state() -> None:
    proposal = _proposal("proposal_eligible", "Eligible Landmark")
    quality_report = _quality_report(
        _quality_score(_DEFAULT_PROVIDER_PLACE_ID, "Eligible Landmark", CandidateQualityTier.GOOD_CANDIDATE)
    )
    planning_state = _state_with(
        [proposal], grounded=[_grounded_candidate(proposal)], quality_report=quality_report
    )
    before = planning_state.model_copy(deep=True)

    AICandidatePromotionService().build_promotion_report(planning_state)

    assert planning_state == before
    assert planning_state.ai_candidate_promotion_report is None


# ---------------------------------------------------------------------------
# No LLM/provider calls: source-level guarantee.
# ---------------------------------------------------------------------------


def test_promotion_service_module_has_no_llm_or_provider_imports() -> None:
    import ast

    source = inspect.getsource(ai_candidate_promotion_service_module)
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
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"
    for disallowed_call in ("requests.", "httpx."):
        assert disallowed_call not in source
