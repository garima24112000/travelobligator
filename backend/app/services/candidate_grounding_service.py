from __future__ import annotations

import re

from app.models.ai_candidate_proposal import AICandidateProposal
from app.models.candidate_grounding import (
    CandidateGroundingConfidenceTier,
    CandidateGroundingEvidence,
    CandidateGroundingGuardrailReport,
    CandidateGroundingMatchType,
    CandidateGroundingRejectReason,
    CandidateGroundingRequest,
    CandidateGroundingResult,
    CandidateGroundingStatus,
    GroundedCandidate,
    ProviderCandidateForGrounding,
    RejectedCandidateProposal,
)

# Service boundary for the candidate grounding/verification stage (Step
# 159A, itinerary-generator-build-spec.md Stage 6, docs/13_llm_reasoning_
# pipeline.md section 32, docs/14_backend_architecture.md section 25). No
# LLM, provider call, LangGraph, or LangSmith dependency is wired up yet.
# `ground` matches Step 157A `AICandidateProposal` ideas only against
# `ProviderCandidateForGrounding` entries explicitly supplied inside the
# request -- it never performs a provider/open-data lookup of its own.
# Nothing in this module calls a network service, and nothing mutates
# `PlanningState`. Nothing elsewhere in the app currently calls this
# service, and it is not wired into `PlanningOrchestrator`.

_LEADING_ARTICLES = ("the ", "a ", "an ")


def _normalize_name(name: str) -> str:
    lowered = name.strip().lower()
    no_punctuation = re.sub(r"[^\w\s]", "", lowered)
    collapsed = re.sub(r"\s+", " ", no_punctuation).strip()
    for article in _LEADING_ARTICLES:
        if collapsed.startswith(article):
            collapsed = collapsed[len(article) :]
            break
    return collapsed


class CandidateGroundingService:
    """`ground` matches each `AICandidateProposal` against the
    `ProviderCandidateForGrounding` entries explicitly supplied on
    `request.provider_candidates`. Matching is deterministic and
    conservative: exact case-insensitive name match, then normalized name
    match (lowercase, punctuation stripped, whitespace collapsed, a single
    leading article removed) -- no fuzzy or substring matching. A proposal
    grounds only if exactly one supplied provider candidate matches it by
    name; zero matches or more than one match both fail to ground it.
    """

    provider_name = "candidate_grounding_service"

    def ground(self, request: CandidateGroundingRequest) -> CandidateGroundingResult:
        if not request.proposals:
            return CandidateGroundingResult(
                status=CandidateGroundingStatus.SKIPPED,
                grounded_candidates=[],
                rejected_proposals=[],
                guardrail_report=CandidateGroundingGuardrailReport(
                    passed=False,
                    blocked_reasons=["No AI candidate proposals were provided for grounding."],
                    checked_fields=["proposals"],
                ),
                provider_name=self.provider_name,
                confidence=0.0,
            )

        if not request.provider_candidates:
            return CandidateGroundingResult(
                status=CandidateGroundingStatus.NOT_CONNECTED,
                grounded_candidates=[],
                rejected_proposals=[],
                guardrail_report=CandidateGroundingGuardrailReport(
                    passed=False,
                    blocked_reasons=["No provider candidates were supplied for grounding."],
                    checked_fields=["provider_candidates"],
                ),
                provider_name=self.provider_name,
                confidence=0.0,
            )

        grounded_candidates: list[GroundedCandidate] = []
        rejected_proposals: list[RejectedCandidateProposal] = []

        for proposal in request.proposals:
            grounded, rejected = self._ground_one(proposal, request.provider_candidates)
            if grounded is not None:
                grounded_candidates.append(grounded)
            else:
                rejected_proposals.append(rejected)

        if grounded_candidates and rejected_proposals:
            status = CandidateGroundingStatus.PARTIAL
        elif grounded_candidates:
            status = CandidateGroundingStatus.COMPLETED
        else:
            status = CandidateGroundingStatus.REJECTED

        if status in (CandidateGroundingStatus.COMPLETED, CandidateGroundingStatus.PARTIAL):
            guardrail_report = CandidateGroundingGuardrailReport(
                passed=True,
                blocked_reasons=[],
                checked_fields=[
                    "proposal_id",
                    "candidate_name",
                    "provider_candidates",
                    "coordinates",
                    "data_status",
                ],
            )
            confidence = sum(candidate.confidence for candidate in grounded_candidates) / len(
                grounded_candidates
            )
        else:
            guardrail_report = CandidateGroundingGuardrailReport(
                passed=False,
                blocked_reasons=[
                    "No AI candidate proposals could be grounded against supplied provider candidates."
                ],
                checked_fields=[
                    "proposal_id",
                    "candidate_name",
                    "provider_candidates",
                    "coordinates",
                    "data_status",
                ],
            )
            confidence = 0.0

        return CandidateGroundingResult(
            status=status,
            grounded_candidates=grounded_candidates,
            rejected_proposals=rejected_proposals,
            guardrail_report=guardrail_report,
            provider_name=self.provider_name,
            confidence=confidence,
        )

    def _ground_one(
        self,
        proposal: AICandidateProposal,
        provider_candidates: list[ProviderCandidateForGrounding],
    ) -> tuple[GroundedCandidate | None, RejectedCandidateProposal | None]:
        # An exact case-insensitive match is always also a normalized match
        # (normalization is a pure function of the string), so normalized
        # matches are the full candidate set for ambiguity purposes -- "more
        # than one provider candidate matches after normalization" already
        # covers the exact-match case too.
        proposal_normalized = _normalize_name(proposal.candidate_name)
        proposal_lower = proposal.candidate_name.strip().lower()

        # ProviderCandidateForGrounding.coordinates is a required GeoPoint,
        # so this can never actually be None -- kept defensive per the
        # build spec's "do not ground if provider candidate has missing
        # coordinates" rule in case that constraint ever loosens.
        matches = [
            candidate
            for candidate in provider_candidates
            if candidate.coordinates is not None
            and _normalize_name(candidate.name) == proposal_normalized
        ]

        if len(matches) == 0:
            return None, RejectedCandidateProposal(
                proposal_id=proposal.proposal_id,
                candidate_name=proposal.candidate_name,
                candidate_type=proposal.candidate_type,
                reject_reason=CandidateGroundingRejectReason.NO_PROVIDER_MATCH,
                message="No supplied provider candidate matched this AI proposal, so it was not grounded.",
            )

        if len(matches) > 1:
            return None, RejectedCandidateProposal(
                proposal_id=proposal.proposal_id,
                candidate_name=proposal.candidate_name,
                candidate_type=proposal.candidate_type,
                reject_reason=CandidateGroundingRejectReason.AMBIGUOUS_MATCH,
                message="Multiple supplied provider candidates matched this AI proposal, so it was not grounded.",
            )

        provider_candidate = matches[0]
        is_exact = provider_candidate.name.strip().lower() == proposal_lower
        match_type = (
            CandidateGroundingMatchType.EXACT_NAME
            if is_exact
            else CandidateGroundingMatchType.NORMALIZED_NAME
        )

        if match_type == CandidateGroundingMatchType.EXACT_NAME and provider_candidate.confidence >= 0.75:
            confidence_tier = CandidateGroundingConfidenceTier.HIGH
        elif match_type == CandidateGroundingMatchType.NORMALIZED_NAME or provider_candidate.confidence >= 0.5:
            confidence_tier = CandidateGroundingConfidenceTier.MEDIUM
        else:
            confidence_tier = CandidateGroundingConfidenceTier.LOW

        evidence = CandidateGroundingEvidence(
            provider_name=provider_candidate.provider_name,
            provider_place_id=provider_candidate.provider_place_id,
            matched_name=provider_candidate.name,
            matched_category=provider_candidate.category,
            match_type=match_type,
            coordinates=provider_candidate.coordinates,
            data_status=provider_candidate.data_status,
            confidence=provider_candidate.confidence,
        )

        # AICandidateProposal.verification_requirements is required to be
        # non-empty (min_length=1), so this always has at least one entry to
        # carry over -- GroundedCandidate.verification_requirements_satisfied
        # has the same non-empty requirement.
        return (
            GroundedCandidate(
                grounding_id=f"grounding_{proposal.proposal_id}",
                proposal_id=proposal.proposal_id,
                candidate_name=proposal.candidate_name,
                candidate_type=proposal.candidate_type,
                matched_name=provider_candidate.name,
                confidence_tier=confidence_tier,
                confidence=min(proposal.confidence, provider_candidate.confidence),
                evidence=evidence,
                verification_requirements_satisfied=list(proposal.verification_requirements),
            ),
            None,
        )
