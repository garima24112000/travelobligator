from __future__ import annotations

from dataclasses import dataclass, field

from app.models.ai_candidate_proposal import AICandidateProposal
from app.models.candidate_grounding import (
    CandidateGroundingConfidenceTier,
    CandidateGroundingMatchType,
    GroundedCandidate,
    RejectedCandidateProposal,
)
from app.models.candidate_quality import CandidateQualityReport, CandidateQualityScore, CandidateQualityTier
from app.models.common import DataStatus

# Deterministic AI candidate promotion eligibility rules (Step 170B,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md). This
# module only ever reads its inputs -- an `AICandidateProposal`, its
# (optional) matching `GroundedCandidate`/`RejectedCandidateProposal`, and
# an already-computed `CandidateQualityReport` -- and returns a pure,
# side-effect-free `PromotionEligibilityResult`. It never calls a provider,
# LLM, or LangGraph, never calls `CandidateQualityService`/
# `CandidateGroundingService` itself (it only *reads* their prior output),
# and never mutates `PlanningState`.
#
# This step only decides *eligibility* -- it never adds a candidate to an
# itinerary day, never changes `ExperiencePlannerService` scheduling, and
# never mutates `candidate_quality_report`/`candidate_grounding_batch`.
# Actual promotion into the schedule is Step 170C's job, not this module's.
#
# Every rule below is a deterministic check over fields that already exist
# on real project models -- no new heuristic invents a fact, price,
# rating, route, or booking link.

# Rule 4: quality tiers accepted for scheduling -- kept identical to
# `ExperiencePlannerService._ELIGIBLE_SCHEDULING_TIERS` (Step 156E,
# docs/18_candidate_quality.md) so an AI candidate is never held to a
# looser or stricter bar than a real provider candidate competing for the
# same itinerary slot.
_ACCEPTED_QUALITY_TIERS = {
    CandidateQualityTier.PRIMARY_ANCHOR,
    CandidateQualityTier.GOOD_CANDIDATE,
    CandidateQualityTier.SECONDARY_CANDIDATE,
}

# Rule 3: grounding match types that represent a real, non-ambiguous
# provider match. `AMBIGUOUS_MATCH` is excluded even though nothing in the
# `GroundedCandidate` model structurally forbids it -- an ambiguous match
# is never confident enough to promote.
_ACCEPTED_MATCH_TYPES = {
    CandidateGroundingMatchType.EXACT_NAME,
    CandidateGroundingMatchType.NORMALIZED_NAME,
    CandidateGroundingMatchType.PROVIDER_CANDIDATE_REFERENCE,
    CandidateGroundingMatchType.TARGETED_LOOKUP,
}

# Rule 3: a `low` confidence tier (per `CandidateGroundingService`'s own
# scoring -- normalized-name match with low underlying provider
# confidence) is treated as not solid enough to promote.
_REJECTED_CONFIDENCE_TIERS = {CandidateGroundingConfidenceTier.LOW}

# Rule 5: minimum provider-backed confidence for the grounding evidence
# itself, mirroring `CandidateQualityService._MIN_ACCEPTABLE_CONFIDENCE`
# (same value, kept as an independent constant here since that name is
# private to `candidate_quality_service`).
_MIN_GROUNDING_EVIDENCE_CONFIDENCE = 0.15

# Rules 7-8: category keywords that mean "this is not an AI place
# candidate" -- checked only against `GroundedCandidate.evidence.
# matched_category`, the real provider-supplied category string, never
# against the AI's own free-text wording. `AICandidateType` has no
# accommodation/flight/transport member today, so these checks are a
# defensive floor, not something the current enum can trigger on its own.
_ACCOMMODATION_CATEGORY_KEYWORDS = {
    "hotel",
    "hostel",
    "guest_house",
    "guesthouse",
    "motel",
    "apartment",
    "chalet",
    "resort",
    "accommodation",
    "lodging",
}
_TRANSPORT_CATEGORY_KEYWORDS = {
    "flight",
    "airport",
    "airline",
    "transit",
    "transport",
    "train_station",
    "bus_station",
    "ferry_terminal",
}


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _matched_category_keyword_hit(category: str | None, keywords: set[str]) -> str | None:
    if not category:
        return None
    lowered = category.strip().lower()
    for keyword in keywords:
        if keyword in lowered:
            return keyword
    return None


def find_quality_score(
    grounded: GroundedCandidate,
    candidate_quality_report: CandidateQualityReport | None,
) -> CandidateQualityScore | None:
    """Looks up the already-computed `CandidateQualityScore` for the real
    provider place a `GroundedCandidate` matched to (Rule 4). Joins by
    `evidence.provider_place_id` against `CandidateQualityScore.candidate_id`
    first (both are derived from the same underlying candidate's
    `place_id` when one exists), falling back to a normalized-name match
    against `CandidateQualityScore.candidate_name`.

    This never computes a new score and never calls
    `CandidateQualityService` -- it only reads
    `PlanningState.candidate_quality_report`, which is already built
    purely from `destination_context`'s provider candidates and never
    consumes AI candidates itself (docs/13_llm_reasoning_pipeline.md
    section 40's non-consumption guarantee is unaffected by this lookup).
    Returns `None`, honestly, if no matching score can be found -- never a
    guessed or default tier.
    """
    if candidate_quality_report is None:
        return None

    all_scores = (
        list(candidate_quality_report.attraction_scores)
        + list(candidate_quality_report.restaurant_scores)
        + list(candidate_quality_report.accommodation_poi_scores)
    )

    for score in all_scores:
        if score.candidate_id == grounded.evidence.provider_place_id:
            return score

    normalized_matched_name = _normalize_name(grounded.evidence.matched_name)
    for score in all_scores:
        if _normalize_name(score.candidate_name) == normalized_matched_name:
            return score

    return None


@dataclass
class PromotionEligibilityResult:
    """Pure result of evaluating the Step 170B deterministic rules for one
    AI candidate. `eligible` is `True` only when every rule passed;
    `passed_reasons`/`failed_reasons` explain which rules did/didn't,
    always drawn from real field values -- never fabricated.
    """

    eligible: bool
    quality_bucket: str | None
    passed_reasons: list[str] = field(default_factory=list)
    failed_reasons: list[str] = field(default_factory=list)


def evaluate_promotion_eligibility(
    proposal: AICandidateProposal,
    grounded: GroundedCandidate | None,
    rejected: RejectedCandidateProposal | None,
    candidate_quality_report: CandidateQualityReport | None,
) -> PromotionEligibilityResult:
    """Evaluates the 8 deterministic promotion-eligibility rules (Step
    170B) for one AI candidate. Read-only: never mutates any argument,
    never calls a provider/LLM/LangGraph, and never calls
    `CandidateQualityService`/`CandidateGroundingService` itself.
    """
    passed: list[str] = []
    failed: list[str] = []

    # Rule 1: AI-proposed. Every candidate reaching this function came from
    # an `AICandidateProposal`, so this is always true -- recorded
    # explicitly for a transparent, complete rule trail.
    passed.append("Candidate was AI-proposed.")

    # Rule 2: provider-grounded.
    if grounded is None:
        failed.append("Candidate is not provider-grounded.")
        # Rules 3-8 all depend on real provider grounding evidence existing
        # -- without it there is nothing further to honestly evaluate.
        if rejected is not None:
            failed.append(
                f"Candidate was rejected during grounding: {rejected.message}"
            )
        return PromotionEligibilityResult(
            eligible=False, quality_bucket=None, passed_reasons=passed, failed_reasons=failed
        )
    passed.append("Candidate is provider-grounded.")

    # Rule 6: no rejection reason from grounding. Structurally, a batch's
    # own validator already forbids a proposal_id from appearing in both
    # grounded_candidates and rejected_proposals -- this is a defensive
    # restatement of that guarantee, not a new source of truth.
    if rejected is not None:
        failed.append(
            f"Candidate also carries a grounding rejection: {rejected.message}"
        )
    else:
        passed.append("Candidate carries no grounding rejection.")

    # Rule 3: grounding_status represents a successful/accepted match.
    match_type = grounded.evidence.match_type
    if match_type not in _ACCEPTED_MATCH_TYPES:
        failed.append(f"Grounding match type '{match_type.value}' is not accepted for promotion.")
    else:
        passed.append(f"Grounding match type '{match_type.value}' is accepted for promotion.")

    if grounded.confidence_tier in _REJECTED_CONFIDENCE_TIERS:
        failed.append(
            f"Grounding confidence tier '{grounded.confidence_tier.value}' is too low for promotion."
        )
    else:
        passed.append(f"Grounding confidence tier '{grounded.confidence_tier.value}' is acceptable.")

    # Rule 5: enough provider-backed information to be scheduled later.
    if grounded.evidence.confidence < _MIN_GROUNDING_EVIDENCE_CONFIDENCE:
        failed.append("Provider-backed grounding confidence is too low to schedule later.")
    else:
        passed.append("Provider-backed grounding confidence is sufficient to schedule later.")

    # Rule 8: does not rely on AI-only facts -- the grounding evidence
    # itself must be provider-backed, never AI-inferred.
    if grounded.evidence.data_status == DataStatus.AI_INFERRED:
        failed.append("Grounding evidence is AI-inferred rather than provider-backed.")
    else:
        passed.append("Grounding evidence is provider-backed, not AI-inferred.")

    # Rules 7/8: not a hotel/accommodation/flight/transport offer.
    accommodation_hit = _matched_category_keyword_hit(
        grounded.evidence.matched_category, _ACCOMMODATION_CATEGORY_KEYWORDS
    )
    if accommodation_hit is not None:
        failed.append(
            f"Candidate category '{grounded.evidence.matched_category}' resembles "
            "accommodation/lodging, which is not eligible for AI place promotion."
        )
    transport_hit = _matched_category_keyword_hit(
        grounded.evidence.matched_category, _TRANSPORT_CATEGORY_KEYWORDS
    )
    if transport_hit is not None:
        failed.append(
            f"Candidate category '{grounded.evidence.matched_category}' resembles "
            "flight/transport, which is not eligible for AI place promotion."
        )
    if accommodation_hit is None and transport_hit is None:
        passed.append("Candidate category is not accommodation/flight/transport-like.")

    # Rule 4: quality_bucket acceptable for scheduling.
    quality_score = find_quality_score(grounded, candidate_quality_report)
    if quality_score is None:
        failed.append("No candidate quality score is available for this grounded candidate.")
        quality_bucket: str | None = None
    else:
        quality_bucket = quality_score.quality_tier.value
        if quality_score.quality_tier in _ACCEPTED_QUALITY_TIERS:
            passed.append(f"Quality tier '{quality_bucket}' is acceptable for scheduling.")
        else:
            failed.append(f"Quality tier '{quality_bucket}' is not acceptable for scheduling.")

    return PromotionEligibilityResult(
        eligible=not failed,
        quality_bucket=quality_bucket,
        passed_reasons=passed,
        failed_reasons=failed,
    )
