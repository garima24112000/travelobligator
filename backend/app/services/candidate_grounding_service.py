from __future__ import annotations

import re

from app.models.ai_candidate_proposal import AICandidateProposal, AICandidateProposalType
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


def find_broad_pool_name_matches(
    candidate_name: str, provider_candidates: list[ProviderCandidateForGrounding]
) -> list[ProviderCandidateForGrounding]:
    """Exact-or-normalized-name matches for `candidate_name` within
    `provider_candidates` -- the exact same deterministic predicate
    `_ground_one` uses for a `named_place` proposal, extracted so
    `AIDirectedProviderDiscoveryService` (Section 192) can reuse it to
    decide whether a proposal already has a clean broad-pool match before
    spending a real targeted provider lookup on it. Never fuzzy/substring
    matching -- identical semantics to the grounding path itself, kept as
    one implementation rather than two that could silently drift apart.
    """
    normalized = _normalize_name(candidate_name)
    return [
        candidate
        for candidate in provider_candidates
        if candidate.coordinates is not None and _normalize_name(candidate.name) == normalized
    ]


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

        # Section 192: `ai_directed_matches` is its own, separate source of
        # provider evidence (identity-keyed by proposal_id, never a name-
        # text match) -- a request can carry zero broad-pool
        # `provider_candidates` and still have real evidence to ground
        # against via `ai_directed_matches`, so this only reports
        # `not_connected` when *neither* source of provider evidence is
        # present.
        if not request.provider_candidates and not request.ai_directed_matches:
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
            grounded, rejected = self._ground_one(
                proposal, request.provider_candidates, request.ai_directed_matches
            )
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
        ai_directed_matches: dict[str, ProviderCandidateForGrounding],
    ) -> tuple[GroundedCandidate | None, RejectedCandidateProposal | None]:
        directed_match = ai_directed_matches.get(proposal.proposal_id)

        # Step 191B/Section 192: a `discovery_query` proposal is a search
        # intent, not a factual place name -- there is no broad-pool name
        # to match by at all. It can only ever ground via a Section 192
        # `ai_directed_matches` entry: real evidence
        # `AIDirectedProviderDiscoveryService` already resolved for this
        # exact `proposal_id` through a targeted provider search. Matching
        # its free-text `search_query` against the broad pool by name would
        # be exactly the "pretend the search phrase is a factual place"
        # behavior this architecture exists to avoid, so that path is never
        # attempted here.
        if proposal.proposal_type == AICandidateProposalType.DISCOVERY_QUERY:
            if directed_match is not None:
                return self._build_grounded_candidate(
                    proposal, directed_match, CandidateGroundingMatchType.TARGETED_LOOKUP
                ), None
            return None, RejectedCandidateProposal(
                proposal_id=proposal.proposal_id,
                candidate_name=proposal.candidate_name or proposal.search_query,
                candidate_type=proposal.candidate_type,
                reject_reason=CandidateGroundingRejectReason.DISCOVERY_QUERY_AWAITING_PROVIDER_SEARCH,
                message=(
                    "This is a discovery-query proposal (a search intent, not a named "
                    "place); it stays ungrounded until a provider search resolves it."
                ),
            )

        # A named_place proposal is structurally guaranteed (by
        # AICandidateProposal's own model validator) to carry a non-blank
        # candidate_name -- safe to match by name exactly as before Step
        # 191B.
        assert proposal.candidate_name is not None

        matches = find_broad_pool_name_matches(proposal.candidate_name, provider_candidates)

        if len(matches) == 1:
            provider_candidate = matches[0]
            is_exact = provider_candidate.name.strip().lower() == proposal.candidate_name.strip().lower()
            match_type = (
                CandidateGroundingMatchType.EXACT_NAME
                if is_exact
                else CandidateGroundingMatchType.NORMALIZED_NAME
            )
            return self._build_grounded_candidate(proposal, provider_candidate, match_type), None

        # Section 192: broad-pool matching failed (zero or ambiguous
        # matches) -- fall back to a Section 192 targeted provider lookup
        # result, if `AIDirectedProviderDiscoveryService` already resolved
        # one for this proposal_id. This never overrides a clean broad-pool
        # match (handled above); it only rescues a proposal broad matching
        # alone could not.
        if directed_match is not None:
            return self._build_grounded_candidate(
                proposal, directed_match, CandidateGroundingMatchType.TARGETED_LOOKUP
            ), None

        if len(matches) == 0:
            return None, RejectedCandidateProposal(
                proposal_id=proposal.proposal_id,
                candidate_name=proposal.candidate_name,
                candidate_type=proposal.candidate_type,
                reject_reason=CandidateGroundingRejectReason.NO_PROVIDER_MATCH,
                message="No supplied provider candidate matched this AI proposal, so it was not grounded.",
            )

        return None, RejectedCandidateProposal(
            proposal_id=proposal.proposal_id,
            candidate_name=proposal.candidate_name,
            candidate_type=proposal.candidate_type,
            reject_reason=CandidateGroundingRejectReason.AMBIGUOUS_MATCH,
            message="Multiple supplied provider candidates matched this AI proposal, so it was not grounded.",
        )

    def _build_grounded_candidate(
        self,
        proposal: AICandidateProposal,
        provider_candidate: ProviderCandidateForGrounding,
        match_type: CandidateGroundingMatchType,
    ) -> GroundedCandidate:
        if match_type == CandidateGroundingMatchType.EXACT_NAME and provider_candidate.confidence >= 0.75:
            confidence_tier = CandidateGroundingConfidenceTier.HIGH
        elif (
            match_type
            in (CandidateGroundingMatchType.NORMALIZED_NAME, CandidateGroundingMatchType.TARGETED_LOOKUP)
            or provider_candidate.confidence >= 0.5
        ):
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
        # has the same non-empty requirement. candidate_name falls back to
        # search_query for a discovery_query proposal (Step 191B), which
        # never has a candidate_name of its own.
        return GroundedCandidate(
            grounding_id=f"grounding_{proposal.proposal_id}",
            proposal_id=proposal.proposal_id,
            candidate_name=proposal.candidate_name or proposal.search_query,
            candidate_type=proposal.candidate_type,
            matched_name=provider_candidate.name,
            confidence_tier=confidence_tier,
            confidence=min(proposal.confidence, provider_candidate.confidence),
            evidence=evidence,
            verification_requirements_satisfied=list(proposal.verification_requirements),
        )
