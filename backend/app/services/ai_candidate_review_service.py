from __future__ import annotations

from datetime import datetime, timezone

from app.models.ai_candidate_review import AICandidateReviewItem, AICandidateReviewReport
from app.models.candidate_grounding import CandidateGroundingResult, GroundedCandidate, RejectedCandidateProposal
from app.models.planning_state import PlanningState
from app.services.ai_candidate_promotion_eligibility_service import evaluate_promotion_eligibility

# Read-only AI candidate review/report service (Step 170A, extended with
# deterministic promotion-eligibility rules in Step 170B,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md). This
# service only ever *reads* `planning_state.ai_candidate_proposal_batch`/
# `candidate_grounding_batch`/`candidate_quality_report` (already
# populated, or not, by the existing Step 161B shadow stage and the
# existing destination-context stage) -- it never calls a provider, LLM,
# or LangGraph, never triggers new AI candidate discovery, never calls
# `CandidateQualityService`/`CandidateGroundingService`/
# `AICandidateDiscoveryService` itself, and never mutates `planning_state`.
#
# `build_report` is safe to call for any trip at any time, including one
# where shadow mode has never run: with both batch fields `None` (today's
# default), it returns an honest empty report rather than fabricating one.
#
# As of Step 170B, `eligible_for_promotion` can be `True` when
# `AICandidatePromotionEligibilityService`'s deterministic rules all pass
# for a candidate. This is still not promotion: no candidate is ever added
# to `experience_plan`/`daily_plans` by this service -- Step 170C is the
# step that would actually act on eligibility.

_GROUNDING_NOT_RUN_WARNING = (
    "Grounding has not been run for this candidate yet, so it cannot be "
    "considered usable."
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _grounded_by_proposal_id(
    grounding_result: CandidateGroundingResult | None,
) -> dict[str, GroundedCandidate]:
    if grounding_result is None:
        return {}
    return {candidate.proposal_id: candidate for candidate in grounding_result.grounded_candidates}


def _rejected_by_proposal_id(
    grounding_result: CandidateGroundingResult | None,
) -> dict[str, RejectedCandidateProposal]:
    if grounding_result is None:
        return {}
    return {proposal.proposal_id: proposal for proposal in grounding_result.rejected_proposals}


class AICandidateReviewService:
    """Builds `AICandidateReviewReport` purely from already-stored
    `PlanningState` fields (docs/14_backend_architecture.md). Never mutates
    `planning_state`, never calls a provider/AI/LLM, never invents a
    candidate, and never adds a candidate to `experience_plan`.
    """

    def build_report(self, planning_state: PlanningState) -> AICandidateReviewReport:
        proposal_batch = planning_state.ai_candidate_proposal_batch
        grounding_batch = planning_state.candidate_grounding_batch
        candidate_quality_report = planning_state.candidate_quality_report

        proposal_result = proposal_batch.result if proposal_batch is not None else None
        grounding_result = grounding_batch.result if grounding_batch is not None else None

        if proposal_result is None or not proposal_result.proposals:
            status = "no_candidate_data" if proposal_result is None else proposal_result.status.value
            return AICandidateReviewReport(
                trip_id=planning_state.trip_id,
                status=status,
                generated_at=_utc_now(),
            )

        grounded_by_id = _grounded_by_proposal_id(grounding_result)
        rejected_by_id = _rejected_by_proposal_id(grounding_result)

        items: list[AICandidateReviewItem] = []
        for proposal in proposal_result.proposals:
            grounded = grounded_by_id.get(proposal.proposal_id)
            rejected = rejected_by_id.get(proposal.proposal_id)
            provider_grounded = grounded is not None

            eligibility = evaluate_promotion_eligibility(
                proposal, grounded, rejected, candidate_quality_report
            )

            warnings: list[str] = []
            if grounded is not None:
                grounding_status: str | None = grounded.evidence.match_type.value
            elif rejected is not None:
                grounding_status = rejected.reject_reason.value
                warnings.append(rejected.message)
            else:
                grounding_status = None
                warnings.append(_GROUNDING_NOT_RUN_WARNING)

            items.append(
                AICandidateReviewItem(
                    candidate_id=proposal.proposal_id,
                    name=proposal.candidate_name,
                    category=proposal.candidate_type.value,
                    source=proposal.source,
                    ai_proposed=True,
                    provider_grounded=provider_grounded,
                    quality_bucket=eligibility.quality_bucket,
                    grounding_status=grounding_status,
                    eligible_for_promotion=eligibility.eligible,
                    eligibility_reasons=eligibility.passed_reasons,
                    rejection_reasons=eligibility.failed_reasons,
                    warnings=warnings,
                )
            )

        grounded_count = sum(1 for item in items if item.provider_grounded)
        ungrounded_count = len(items) - grounded_count
        eligible_count = sum(1 for item in items if item.eligible_for_promotion)

        return AICandidateReviewReport(
            trip_id=planning_state.trip_id,
            status="reviewed",
            total_ai_candidates=len(items),
            grounded_candidates=grounded_count,
            ungrounded_candidates=ungrounded_count,
            eligible_for_promotion=eligible_count,
            items=items,
            generated_at=_utc_now(),
        )


ai_candidate_review_service = AICandidateReviewService()
