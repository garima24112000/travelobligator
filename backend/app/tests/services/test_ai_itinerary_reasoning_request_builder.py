from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalType,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.ai_itinerary_reasoning import CandidateOrigin, ItineraryReasoningCategory
from app.models.candidate_grounding import (
    CandidateGroundingBatch,
    CandidateGroundingConfidenceTier,
    CandidateGroundingEvidence,
    CandidateGroundingMatchType,
    CandidateGroundingRequest,
    CandidateGroundingResult,
    CandidateGroundingStatus,
    GroundedCandidate,
)
from app.models.candidate_quality import (
    CandidateQualityReport,
    CandidateQualityScore,
    CandidateQualityTier,
    CandidateRejectReason,
    CandidateUseCase,
)
from app.models.common import DataStatus, GeoPoint
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
from app.services.ai_candidate_promotion_service import AICandidatePromotionService
from app.services.ai_itinerary_reasoning_request_builder import AIItineraryReasoningRequestBuilder

# Tests for the Section 193A request builder (docs/14_backend_architecture.md
# section 141). No provider/LLM call is made anywhere here -- every
# PlanningState fixture is constructed directly, deterministically.


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Lisbon, Portugal",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
        "interests": ["food", "history"],
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _planning_state(**trip_request_overrides: Any) -> PlanningState:
    return PlanningState(trip_request=_trip_request(**trip_request_overrides))


def _poi(
    place_id: str,
    name: str,
    *,
    category: str = "attraction",
    lat: float = 38.7,
    lng: float = -9.1,
    source: str = "openstreetmap_places",
    data_status: str = "live",
    confidence: float = 0.8,
) -> dict[str, Any]:
    return {
        "place_id": place_id,
        "name": name,
        "category": category,
        "coordinates": {"lat": lat, "lng": lng},
        "source": source,
        "data_status": data_status,
        "confidence": confidence,
    }


def _quality_score(
    candidate_id: str,
    candidate_name: str,
    tier: CandidateQualityTier,
    *,
    use_case: CandidateUseCase = CandidateUseCase.ATTRACTION,
    total_score: float | None = None,
) -> CandidateQualityScore:
    default_scores = {
        CandidateQualityTier.PRIMARY_ANCHOR: 0.9,
        CandidateQualityTier.GOOD_CANDIDATE: 0.6,
        CandidateQualityTier.SECONDARY_CANDIDATE: 0.4,
        CandidateQualityTier.LOW_PRIORITY: 0.25,
        CandidateQualityTier.REJECTED: 0.05,
    }
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
        total_score=total_score if total_score is not None else default_scores[tier],
        reject_reasons=reject_reasons,
    )


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
    provider_place_id: str,
    provider_name: str = "openstreetmap_places",
    match_type: CandidateGroundingMatchType = CandidateGroundingMatchType.TARGETED_LOOKUP,
    matched_category: str | None = "attraction",
) -> GroundedCandidate:
    return GroundedCandidate(
        grounding_id=f"grounding_{proposal.proposal_id}",
        proposal_id=proposal.proposal_id,
        candidate_name=proposal.candidate_name or proposal.search_query,
        candidate_type=proposal.candidate_type,
        matched_name=proposal.candidate_name or proposal.search_query,
        confidence_tier=CandidateGroundingConfidenceTier.MEDIUM,
        confidence=0.5,
        evidence=CandidateGroundingEvidence(
            provider_name=provider_name,
            provider_place_id=provider_place_id,
            matched_name=proposal.candidate_name or proposal.search_query,
            matched_category=matched_category,
            match_type=match_type,
            coordinates=GeoPoint(lat=38.69, lng=-9.21),
            data_status=DataStatus.LIVE,
            confidence=0.5,
        ),
        verification_requirements_satisfied=list(proposal.verification_requirements),
    )


def _apply_ai_directed_promotion(
    planning_state: PlanningState,
    proposal: AICandidateProposal,
    *,
    provider_place_id: str,
    tier: CandidateQualityTier,
    total_score: float | None = None,
) -> None:
    """Builds a real, promoted Section 192/192A candidate onto
    `planning_state` -- proposal -> grounded (targeted_lookup) -> quality
    score (ai_directed_scores) -> AICandidatePromotionService.apply_promotion
    -- so these tests exercise the real integration rather than
    hand-constructing a PromotedAICandidate directly.
    """
    grounded = _grounded_candidate(proposal, provider_place_id=provider_place_id)

    planning_state.ai_candidate_proposal_batch = None  # not needed by AICandidateReviewService beyond result
    from app.models.ai_candidate_proposal import (
        AICandidateProposalBatch,
        AICandidateProposalGuardrailReport,
        AICandidateProposalRequest,
        AICandidateProposalResult,
        AICandidateProposalStatus,
        AICandidateProposalTask,
    )
    from app.models.candidate_grounding import CandidateGroundingGuardrailReport

    planning_state.ai_candidate_proposal_batch = AICandidateProposalBatch(
        request=AICandidateProposalRequest(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            trip_id=planning_state.trip_id,
            destination_name=planning_state.trip_request.primary_destination,
            trip_duration_days=3,
        ),
        result=AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=[proposal],
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            confidence=0.6,
        ),
    )
    planning_state.candidate_grounding_batch = CandidateGroundingBatch(
        request=CandidateGroundingRequest(
            trip_id=planning_state.trip_id,
            destination_name=planning_state.trip_request.primary_destination,
            proposals=[proposal],
        ),
        result=CandidateGroundingResult(
            status=CandidateGroundingStatus.COMPLETED,
            grounded_candidates=[grounded],
            guardrail_report=CandidateGroundingGuardrailReport(passed=True),
            confidence=0.5,
        ),
    )

    score = _quality_score(provider_place_id, grounded.candidate_name, tier, total_score=total_score)
    existing_report = planning_state.candidate_quality_report
    if existing_report is not None:
        planning_state.candidate_quality_report = existing_report.model_copy(
            update={"ai_directed_scores": [*existing_report.ai_directed_scores, score]}
        )
    else:
        planning_state.candidate_quality_report = CandidateQualityReport(
            destination_name=planning_state.trip_request.primary_destination,
            generated_at=datetime.now(timezone.utc),
            ai_directed_scores=[score],
        )

    AICandidatePromotionService().apply_promotion(planning_state)


# ---------------------------------------------------------------------------
# 1. Backward compatibility -- an empty/minimal PlanningState still builds.
# ---------------------------------------------------------------------------


def test_backward_compatible_with_minimal_planning_state() -> None:
    planning_state = _planning_state()
    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)
    assert request.allowed_candidates == []
    assert request.trip_duration_days == 3
    assert request.trip_strategy_summary is None


# ---------------------------------------------------------------------------
# 2. Broad-pool candidates: accepted tier included, low/rejected excluded.
# ---------------------------------------------------------------------------


def test_broad_pool_accepted_tier_candidate_is_included() -> None:
    planning_state = _planning_state()
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_pois=[_poi("way/1", "Torre de Belem")],
    )
    planning_state.candidate_quality_report = CandidateQualityReport(
        destination_name="Lisbon, Portugal",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[
            _quality_score("way/1", "Torre de Belem", CandidateQualityTier.GOOD_CANDIDATE)
        ],
    )

    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)

    assert len(request.allowed_candidates) == 1
    candidate = request.allowed_candidates[0]
    assert candidate.candidate_id == "openstreetmap_places:way/1"
    assert candidate.name == "Torre de Belem"
    assert candidate.category == ItineraryReasoningCategory.ATTRACTION
    assert candidate.origin == CandidateOrigin.BROAD_PROVIDER_DISCOVERY
    assert candidate.provider_place_id == "way/1"


def test_broad_pool_low_and_rejected_tier_candidates_are_excluded() -> None:
    planning_state = _planning_state()
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_pois=[_poi("way/1", "Weak Candidate"), _poi("way/2", "Rejected Candidate")],
    )
    planning_state.candidate_quality_report = CandidateQualityReport(
        destination_name="Lisbon, Portugal",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[
            _quality_score("way/1", "Weak Candidate", CandidateQualityTier.LOW_PRIORITY),
            _quality_score("way/2", "Rejected Candidate", CandidateQualityTier.REJECTED),
        ],
    )

    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)

    assert request.allowed_candidates == []


def test_restaurant_scores_map_to_restaurant_category() -> None:
    planning_state = _planning_state()
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_restaurants=[_poi("way/9", "Time Out Market", category="restaurant")],
    )
    planning_state.candidate_quality_report = CandidateQualityReport(
        destination_name="Lisbon, Portugal",
        generated_at=datetime.now(timezone.utc),
        restaurant_scores=[
            _quality_score(
                "way/9", "Time Out Market", CandidateQualityTier.GOOD_CANDIDATE, use_case=CandidateUseCase.RESTAURANT
            )
        ],
    )

    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)

    assert request.allowed_candidates[0].category == ItineraryReasoningCategory.RESTAURANT


def test_accommodation_candidates_are_never_included_as_day_candidates() -> None:
    planning_state = _planning_state()
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_accommodation_pois=[_poi("way/5", "Some Guesthouse", category="guest_house")],
    )
    planning_state.candidate_quality_report = CandidateQualityReport(
        destination_name="Lisbon, Portugal",
        generated_at=datetime.now(timezone.utc),
        accommodation_poi_scores=[
            _quality_score(
                "way/5", "Some Guesthouse", CandidateQualityTier.GOOD_CANDIDATE,
                use_case=CandidateUseCase.ACCOMMODATION_POI,
            )
        ],
    )

    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)

    assert request.allowed_candidates == []


# ---------------------------------------------------------------------------
# 3. AI-directed promoted candidates included; ungrounded proposals excluded.
# ---------------------------------------------------------------------------


def test_ai_directed_promoted_candidate_is_included() -> None:
    planning_state = _planning_state()
    proposal = _proposal("proposal_001", "Torre de Belem")
    _apply_ai_directed_promotion(
        planning_state, proposal, provider_place_id="way/24341353", tier=CandidateQualityTier.GOOD_CANDIDATE
    )

    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)

    assert len(request.allowed_candidates) == 1
    candidate = request.allowed_candidates[0]
    assert candidate.provider_place_id == "way/24341353"
    assert candidate.provider_name == "openstreetmap_places"
    assert candidate.origin == CandidateOrigin.AI_DIRECTED_PROVIDER_DISCOVERY
    assert candidate.quality_tier == CandidateQualityTier.GOOD_CANDIDATE.value


def test_ungrounded_ai_proposal_never_appears_as_a_candidate() -> None:
    """A discovery_query with no provider match, or any proposal that
    never reached promotion, must never appear in allowed_candidates."""
    planning_state = _planning_state()
    # No ai_candidate_proposal_batch/candidate_grounding_batch/promotion
    # report populated at all -- mirrors an ungrounded/rejected proposal
    # that never got anywhere near promotion.
    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)
    assert request.allowed_candidates == []


def test_discovery_query_with_no_provider_match_is_excluded_even_with_a_promotion_report() -> None:
    from app.models.ai_candidate_promotion import AICandidatePromotionReport

    planning_state = _planning_state()
    # An honest, empty promotion report (nothing promoted) -- as
    # apply_promotion would produce for an all-ungrounded batch.
    planning_state.ai_candidate_promotion_report = AICandidatePromotionReport(
        trip_id=planning_state.trip_id,
        status="no_eligible_candidates",
        generated_at=datetime.now(timezone.utc),
    )

    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)

    assert request.allowed_candidates == []


# ---------------------------------------------------------------------------
# 4. Provider identity preserved.
# ---------------------------------------------------------------------------


def test_candidate_reference_provider_identity_matches_real_grounding_evidence() -> None:
    planning_state = _planning_state()
    proposal = _proposal("proposal_001", "Torre de Belem")
    _apply_ai_directed_promotion(
        planning_state, proposal, provider_place_id="way/24341353", tier=CandidateQualityTier.GOOD_CANDIDATE
    )

    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)
    candidate = request.allowed_candidates[0]

    assert candidate.candidate_id == "openstreetmap_places:way/24341353"
    assert candidate.coordinates == GeoPoint(lat=38.69, lng=-9.21)


# ---------------------------------------------------------------------------
# 5. AI proposal confidence never controls selection/score.
# ---------------------------------------------------------------------------


def test_ai_proposal_confidence_does_not_affect_candidate_quality_score() -> None:
    scores: list[float] = []
    for confidence in (0.01, 0.99):
        planning_state = _planning_state()
        proposal = _proposal("proposal_001", "Torre de Belem", confidence=confidence)
        _apply_ai_directed_promotion(
            planning_state,
            proposal,
            provider_place_id="way/24341353",
            tier=CandidateQualityTier.GOOD_CANDIDATE,
            total_score=0.61,
        )
        request = AIItineraryReasoningRequestBuilder().build_request(planning_state)
        scores.append(request.allowed_candidates[0].quality_score)

    assert scores[0] == scores[1] == 0.61


# ---------------------------------------------------------------------------
# 6. Duplicate identity: broad pool wins over an AI-directed duplicate.
# ---------------------------------------------------------------------------


def test_duplicate_provider_identity_broad_pool_wins_over_ai_directed() -> None:
    planning_state = _planning_state()
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_pois=[_poi("way/24341353", "Torre de Belem")],
    )
    planning_state.candidate_quality_report = CandidateQualityReport(
        destination_name="Lisbon, Portugal",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[
            _quality_score("way/24341353", "Torre de Belem", CandidateQualityTier.PRIMARY_ANCHOR)
        ],
    )
    proposal = _proposal("proposal_001", "Torre de Belem")
    _apply_ai_directed_promotion(
        planning_state, proposal, provider_place_id="way/24341353", tier=CandidateQualityTier.GOOD_CANDIDATE
    )

    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)

    assert len(request.allowed_candidates) == 1
    assert request.allowed_candidates[0].origin == CandidateOrigin.BROAD_PROVIDER_DISCOVERY
    assert request.allowed_candidates[0].quality_tier == CandidateQualityTier.PRIMARY_ANCHOR.value


# ---------------------------------------------------------------------------
# 7. Bounded request size, deterministic selection order.
# ---------------------------------------------------------------------------


def test_candidate_universe_is_bounded_and_selected_by_tier_then_score(monkeypatch) -> None:
    from app.core.config import Settings

    import app.services.ai_itinerary_reasoning_request_builder as builder_module

    monkeypatch.setattr(
        builder_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_MAX_CANDIDATES=2)
    )

    planning_state = _planning_state()
    pois = [_poi(f"way/{i}", f"Place {i}") for i in range(5)]
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal", candidate_pois=pois
    )
    planning_state.candidate_quality_report = CandidateQualityReport(
        destination_name="Lisbon, Portugal",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[
            _quality_score("way/0", "Place 0", CandidateQualityTier.SECONDARY_CANDIDATE, total_score=0.4),
            _quality_score("way/1", "Place 1", CandidateQualityTier.GOOD_CANDIDATE, total_score=0.6),
            _quality_score("way/2", "Place 2", CandidateQualityTier.PRIMARY_ANCHOR, total_score=0.9),
            _quality_score("way/3", "Place 3", CandidateQualityTier.PRIMARY_ANCHOR, total_score=0.95),
            _quality_score("way/4", "Place 4", CandidateQualityTier.SECONDARY_CANDIDATE, total_score=0.35),
        ],
    )

    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)

    assert len(request.allowed_candidates) == 2
    selected_ids = {c.provider_place_id for c in request.allowed_candidates}
    # Both PRIMARY_ANCHOR candidates (way/2, way/3) beat every
    # SECONDARY_CANDIDATE/GOOD_CANDIDATE one, regardless of score.
    assert selected_ids == {"way/2", "way/3"}


# ---------------------------------------------------------------------------
# 8. Factual context: no fabricated placeholders for unavailable sources.
# ---------------------------------------------------------------------------


def test_factual_context_reports_unavailable_sources_honestly_not_with_placeholders() -> None:
    planning_state = _planning_state()
    request = AIItineraryReasoningRequestBuilder().build_request(planning_state)

    assert request.factual_context.weather_status is None
    assert request.factual_context.holiday_status is None
    assert request.factual_context.accommodation_inventory_status is None
    assert request.factual_context.accommodation_offer_count == 0
    assert request.factual_context.flight_inventory_status is None
    assert request.factual_context.flight_offer_count == 0


# ---------------------------------------------------------------------------
# 9. Section 193A is contract-only -- nothing in the runtime pipeline calls
#    it yet, so normal /generate behavior is unaffected (Task 13/15).
# ---------------------------------------------------------------------------


def _imported_module_names(module: object) -> list[str]:
    import ast
    import inspect

    source = inspect.getsource(module)  # type: ignore[arg-type]
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
            imported_names.extend(alias.name for alias in node.names)
    return imported_names


def test_planning_orchestrator_does_not_import_itinerary_reasoning_builder() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    imported_names = _imported_module_names(orchestrator_module)
    assert not any("ai_itinerary_reasoning" in name for name in imported_names)


def test_langgraph_nodes_do_not_import_itinerary_reasoning_builder() -> None:
    import app.graphs.planning_graph_nodes as nodes_module

    imported_names = _imported_module_names(nodes_module)
    assert not any("ai_itinerary_reasoning" in name for name in imported_names)


def test_experience_planner_does_not_import_itinerary_reasoning_builder() -> None:
    import app.services.experience_planner_service as module

    imported_names = _imported_module_names(module)
    assert not any("ai_itinerary_reasoning" in name for name in imported_names)


def test_api_routes_do_not_import_itinerary_reasoning_builder() -> None:
    import app.api.routes.trips as trips_routes_module

    imported_names = _imported_module_names(trips_routes_module)
    assert not any("ai_itinerary_reasoning" in name for name in imported_names)


def test_request_builder_module_calls_no_provider_or_llm() -> None:
    import app.services.ai_itinerary_reasoning_request_builder as builder_module

    imported_names = _imported_module_names(builder_module)
    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "gemini",
        "app.providers",
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"
