from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

# Read-only AI candidate review/report models (Step 170A, extended with
# deterministic promotion-eligibility rules in Step 170B,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md). These
# models exist purely to make the *existing* shadow-mode
# `PlanningState.ai_candidate_proposal_batch`/`candidate_grounding_batch`
# state visible and reviewable -- they never trigger new AI generation,
# never call a provider or LLM, never mutate `PlanningState`, and never add
# a candidate to an itinerary day. Provider grounding and candidate quality
# remain the only things that can ever move a candidate toward usability;
# an AI proposal alone never bypasses either.
#
# `eligible_for_promotion` can be `True` as of Step 170B, but only when
# `AICandidatePromotionEligibilityService`'s deterministic rules all pass
# (enforced structurally below: `provider_grounded` must be `True` and
# `rejection_reasons` must be empty). Being eligible here is not the same
# as being promoted -- Step 170C is the step that would actually add an
# eligible candidate to a day's schedule; nothing in this module does that.


class AICandidateReviewItem(BaseModel):
    """One reviewable AI candidate, built purely from an existing
    `AICandidateProposal` (and, if present, its matching `GroundedCandidate`
    or `RejectedCandidateProposal`) already stored on `PlanningState`, plus
    a deterministic eligibility verdict (Step 170B). Never carries a price,
    rating, opening hour, route time, review count, booking link, or
    safety score -- those fields don't exist here, same as on the
    proposal/grounding models this is built from.
    """

    candidate_id: str
    name: str
    category: str | None = None
    source: str
    ai_proposed: bool = True
    provider_grounded: bool = False
    quality_bucket: str | None = None
    grounding_status: str | None = None
    eligible_for_promotion: bool = False
    eligibility_reasons: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("candidate_id", "name", "source")
    @classmethod
    def _require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate_id, name, and source must not be blank.")
        return value

    @model_validator(mode="after")
    def _validate_eligibility_consistency(self) -> "AICandidateReviewItem":
        """Structural safety net (Step 170B): a candidate can never be
        reported eligible for promotion unless it is provider-grounded and
        carries no rejection reason -- this is enforced here, independent
        of whatever `AICandidatePromotionEligibilityService` computed, so a
        future bug in that service can never silently produce an unsafe
        eligible item.
        """
        if self.eligible_for_promotion and not self.provider_grounded:
            raise ValueError(
                "AICandidateReviewItem.eligible_for_promotion cannot be True when "
                "provider_grounded is False."
            )
        if self.eligible_for_promotion and self.rejection_reasons:
            raise ValueError(
                "AICandidateReviewItem.eligible_for_promotion cannot be True when "
                "rejection_reasons is non-empty."
            )
        return self


class AICandidateReviewReport(BaseModel):
    """Plan-level, read-only rollup of AI candidate discovery/grounding/
    eligibility state for one trip (Step 170A, extended in Step 170B).
    Built purely from `PlanningState.ai_candidate_proposal_batch`/
    `candidate_grounding_batch`/`candidate_quality_report` -- no provider
    call, no AI/LLM call, no invented candidate. If neither batch exists
    yet (the default -- shadow mode is off by default), this reports that
    honestly via `status="no_candidate_data"` and empty `items`, rather
    than fabricating a report.
    """

    trip_id: str
    status: str = "no_candidate_data"
    total_ai_candidates: int = Field(default=0, ge=0)
    grounded_candidates: int = Field(default=0, ge=0)
    ungrounded_candidates: int = Field(default=0, ge=0)
    eligible_for_promotion: int = Field(default=0, ge=0)
    items: list[AICandidateReviewItem] = Field(default_factory=list)
    generated_at: datetime

    @field_validator("trip_id")
    @classmethod
    def _require_non_blank_trip_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trip_id must not be blank.")
        return value

    @model_validator(mode="after")
    def _validate_eligible_count_matches_items(self) -> "AICandidateReviewReport":
        """`eligible_for_promotion` (the count) must always equal the
        number of `items` actually marked eligible -- this is a
        consistency check on the report itself, not a business rule, so a
        future bug in report assembly can never silently report a count
        that disagrees with the items it's summarizing.
        """
        actual_eligible = sum(1 for item in self.items if item.eligible_for_promotion)
        if actual_eligible != self.eligible_for_promotion:
            raise ValueError(
                "AICandidateReviewReport.eligible_for_promotion "
                f"({self.eligible_for_promotion}) does not match the number of "
                f"eligible items ({actual_eligible})."
            )
        return self
