from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.common import GeoPoint

# AI candidate promotion report models (Step 170C, extended with the
# coordinate/confidence/data_status evidence fields needed for safe
# scheduling in Step 170D, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md).
#
# A "promoted" AI candidate is NOT automatically an itinerary stop. It is a
# provider-grounded, quality-approved candidate that Step 170B's
# deterministic eligibility rules already marked `eligible_for_promotion`
# on the read-only `AICandidateReviewReport` -- this module only
# materializes that same verdict into a durable, dedicated report. As of
# Step 170D, `ExperiencePlannerService` may consider a `PromotedAICandidate`
# as an additional schedulable place -- but only using the exact same
# geographic/quality/pace rules every other candidate is subject to, never
# a special case.
#
# `PromotedAICandidate` carries only fields that already exist on real
# project models (`AICandidateProposal`, `GroundedCandidate.evidence`,
# `CandidateQualityScore`) -- no rating, opening hour, price, route, or
# booking link is invented here, mirroring every upstream model in this
# subsystem. `coordinates`/`confidence`/`data_status` are copied verbatim
# from `GroundedCandidate.evidence` (real provider evidence, required
# there) -- never guessed, never defaulted to a fabricated value.


class PromotedAICandidate(BaseModel):
    """One AI candidate that cleared Step 170B's deterministic eligibility
    rules, materialized for future scheduling consideration. `promoted` is
    always `True` for an entry in this list -- an ineligible candidate
    never becomes a `PromotedAICandidate` at all; it is recorded only by
    id in `AICandidatePromotionReport.skipped_candidate_ids`.

    `coordinates` is the one field a candidate absolutely needs to be
    schedulable (`ExperiencePlannerService` requires it, same as any other
    candidate) -- it is `None` only if the underlying `GroundedCandidate`
    somehow lacked it (structurally not possible today, since
    `CandidateGroundingEvidence.coordinates` is a required field, but kept
    optional here defensively rather than assumed). A promoted candidate
    with no coordinates is skipped at scheduling time with an honest
    warning, never scheduled with a guessed location.
    """

    candidate_id: str
    name: str
    category: str | None = None
    source: str = "ai_candidate_promotion"
    provider_place_id: str | None = None
    provider_source: str | None = None
    original_ai_candidate_id: str | None = None
    quality_bucket: str | None = None
    grounding_status: str | None = None
    coordinates: GeoPoint | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    data_status: str | None = None
    promotion_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    promoted: bool = True

    @field_validator("candidate_id", "name", "source")
    @classmethod
    def _require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate_id, name, and source must not be blank.")
        return value

    @field_validator("promoted")
    @classmethod
    def _require_promoted_true(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "PromotedAICandidate.promoted must be True -- an ineligible candidate "
                "must never appear as a PromotedAICandidate at all."
            )
        return value


class AICandidatePromotionReport(BaseModel):
    """Plan-level, deterministic promotion report (Step 170C). Built purely
    from an already-computed `AICandidateReviewReport` (Step 170A/170B) --
    no provider call, no AI/LLM call, no invented candidate, and no
    itinerary mutation. Stored on `PlanningState.ai_candidate_promotion_report`
    only when a caller explicitly applies promotion
    (`POST /trips/{trip_id}/ai-candidate-promotions`); it is never set as a
    side effect of the read-only review endpoint.
    """

    trip_id: str
    status: str = "no_candidates_reviewed"
    total_reviewed_candidates: int = Field(default=0, ge=0)
    promoted_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    promoted_candidates: list[PromotedAICandidate] = Field(default_factory=list)
    skipped_candidate_ids: list[str] = Field(default_factory=list)
    generated_at: datetime

    @field_validator("trip_id")
    @classmethod
    def _require_non_blank_trip_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trip_id must not be blank.")
        return value

    @model_validator(mode="after")
    def _validate_counts_match_lists(self) -> "AICandidatePromotionReport":
        """Consistency checks only -- never a business rule. A future bug
        in report assembly can never silently report counts that disagree
        with the lists they summarize, and a promoted candidate can never
        also appear in `skipped_candidate_ids`.
        """
        if self.promoted_count != len(self.promoted_candidates):
            raise ValueError(
                "AICandidatePromotionReport.promoted_count "
                f"({self.promoted_count}) does not match len(promoted_candidates) "
                f"({len(self.promoted_candidates)})."
            )
        if self.skipped_count != len(self.skipped_candidate_ids):
            raise ValueError(
                "AICandidatePromotionReport.skipped_count "
                f"({self.skipped_count}) does not match len(skipped_candidate_ids) "
                f"({len(self.skipped_candidate_ids)})."
            )
        if self.total_reviewed_candidates != self.promoted_count + self.skipped_count:
            raise ValueError(
                "AICandidatePromotionReport.total_reviewed_candidates "
                f"({self.total_reviewed_candidates}) does not equal promoted_count + "
                f"skipped_count ({self.promoted_count + self.skipped_count})."
            )
        promoted_original_ids = {
            candidate.original_ai_candidate_id
            for candidate in self.promoted_candidates
            if candidate.original_ai_candidate_id is not None
        }
        overlap = promoted_original_ids & set(self.skipped_candidate_ids)
        if overlap:
            raise ValueError(
                "AICandidatePromotionReport has candidate id(s) in both "
                f"promoted_candidates and skipped_candidate_ids: {sorted(overlap)}."
            )
        return self
