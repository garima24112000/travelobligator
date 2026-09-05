from __future__ import annotations

from datetime import datetime, timezone

from app.models.ai_candidate_promotion import AICandidatePromotionReport, PromotedAICandidate
from app.models.candidate_grounding import GroundedCandidate
from app.models.planning_state import PlanningState
from app.services.ai_candidate_review_service import AICandidateReviewService

# AI candidate promotion service (Step 170C, docs/13_llm_reasoning_
# pipeline.md, docs/14_backend_architecture.md). Materializes the
# already-computed Step 170B eligibility verdict into a dedicated,
# durable `AICandidatePromotionReport` -- it never re-derives eligibility
# with new logic, never calls a provider/LLM/LangGraph, and never mutates
# `ExperiencePlan`/`daily_plans`. A promoted candidate is not an itinerary
# stop; it is a provider-grounded, quality-approved candidate that is safe
# for a future scheduling step (170D) to consider.
#
# `build_promotion_report` is pure/read-only: it only reads `planning_state`
# (via `AICandidateReviewService.build_report`, plus the existing
# `ai_candidate_proposal_batch`/`candidate_grounding_batch` fields for
# provider-backed identifiers) and never mutates it. `apply_promotion` is
# the only method that writes -- it sets
# `planning_state.ai_candidate_promotion_report` and bumps
# `metadata.updated_at`, mirroring how other mutation-flavored services
# (e.g. `UserLockService.add_lock`) mutate and return `planning_state`
# without saving it themselves; the caller (the API route) still owns
# persistence via `planning_state_repository.save`.
#
# Idempotent by construction: `PromotedAICandidate.candidate_id` is
# deterministically derived from the original AI candidate id
# (`f"promoted_{original_ai_candidate_id}"`), and the report itself
# *replaces* `planning_state.ai_candidate_promotion_report` on every call
# rather than appending to a list -- calling `apply_promotion` twice in a
# row produces the same promoted candidates, never duplicates.


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AICandidatePromotionService:
    """Builds and applies `AICandidatePromotionReport` from an
    already-computed `AICandidateReviewReport`. Never mutates
    `experience_plan`/`daily_plans`, never calls a provider/AI/LLM, and
    never invents a candidate, coordinate, rating, price, route, or
    booking link.
    """

    def __init__(self, review_service: AICandidateReviewService | None = None) -> None:
        self.review_service = review_service or AICandidateReviewService()

    def build_promotion_report(self, planning_state: PlanningState) -> AICandidatePromotionReport:
        """Pure computation -- reads `planning_state` only, never mutates
        it, and never persists anything.
        """
        review_report = self.review_service.build_report(planning_state)

        if not review_report.items:
            return AICandidatePromotionReport(
                trip_id=planning_state.trip_id,
                status="no_candidates_reviewed",
                generated_at=_utc_now(),
            )

        grounding_batch = planning_state.candidate_grounding_batch
        grounding_result = grounding_batch.result if grounding_batch is not None else None
        grounded_by_id: dict[str, GroundedCandidate] = (
            {candidate.proposal_id: candidate for candidate in grounding_result.grounded_candidates}
            if grounding_result is not None
            else {}
        )

        promoted: list[PromotedAICandidate] = []
        skipped_ids: list[str] = []

        for item in review_report.items:
            if not item.eligible_for_promotion:
                skipped_ids.append(item.candidate_id)
                continue

            grounded = grounded_by_id.get(item.candidate_id)
            if grounded is None:
                # Defensive only: AICandidateReviewItem's own model
                # validator already forbids eligible_for_promotion=True
                # without provider_grounded=True, and build_report only
                # sets provider_grounded=True when a real GroundedCandidate
                # exists -- this branch should be unreachable in practice.
                skipped_ids.append(item.candidate_id)
                continue

            promoted.append(
                PromotedAICandidate(
                    candidate_id=f"promoted_{item.candidate_id}",
                    name=item.name,
                    category=item.category,
                    source="ai_candidate_promotion",
                    provider_place_id=grounded.evidence.provider_place_id,
                    provider_source=grounded.evidence.provider_name,
                    original_ai_candidate_id=item.candidate_id,
                    quality_bucket=item.quality_bucket,
                    grounding_status=item.grounding_status,
                    # Real provider evidence, carried over verbatim -- never
                    # guessed. Required for Step 170D scheduling: without
                    # coordinates, the experience planner has no way to
                    # place this candidate geographically.
                    coordinates=grounded.evidence.coordinates,
                    confidence=grounded.evidence.confidence,
                    data_status=grounded.evidence.data_status.value,
                    promotion_reasons=list(item.eligibility_reasons),
                    warnings=list(item.warnings),
                    promoted=True,
                )
            )

        status = "promoted" if promoted else "no_eligible_candidates"

        return AICandidatePromotionReport(
            trip_id=planning_state.trip_id,
            status=status,
            total_reviewed_candidates=len(review_report.items),
            promoted_count=len(promoted),
            skipped_count=len(skipped_ids),
            promoted_candidates=promoted,
            skipped_candidate_ids=skipped_ids,
            generated_at=_utc_now(),
        )

    def apply_promotion(self, planning_state: PlanningState) -> PlanningState:
        """The only mutating entry point: computes a fresh promotion
        report and stores it on `planning_state.ai_candidate_promotion_report`,
        replacing whatever was there before (never appending) so repeated
        calls stay idempotent. Never touches `experience_plan`/`daily_plans`,
        `destination_context`, `validation_report`, or any scheduling
        field. Does not persist -- the caller (the API route) still owns
        `planning_state_repository.save`.
        """
        report = self.build_promotion_report(planning_state)
        planning_state.ai_candidate_promotion_report = report
        planning_state.touch()
        return planning_state


ai_candidate_promotion_service = AICandidatePromotionService()
