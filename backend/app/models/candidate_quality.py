from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

# Deterministic candidate quality models (Step 156A,
# docs/12_provider_architecture.md, docs/14_backend_architecture.md section
# 25, docs/18_candidate_quality.md). These models score and classify
# provider/open-data candidate places already present in
# `DestinationContext` (`candidate_pois`, `candidate_restaurants`,
# `candidate_accommodation_pois`) -- they never create a new place,
# restaurant, or accommodation, and never attach a price, rating, opening
# hour, route time, review count, booking link, or safety score. Scoring is
# a pre-ranking signal only, not a claim of final quality, availability, or
# bookability.


class CandidateUseCase(str, Enum):
    ATTRACTION = "attraction"
    RESTAURANT = "restaurant"
    ACCOMMODATION_POI = "accommodation_poi"
    MUST_VISIT = "must_visit"


class CandidateQualityTier(str, Enum):
    PRIMARY_ANCHOR = "primary_anchor"
    GOOD_CANDIDATE = "good_candidate"
    SECONDARY_CANDIDATE = "secondary_candidate"
    LOW_PRIORITY = "low_priority"
    REJECTED = "rejected"


class CandidateRejectReason(str, Enum):
    MISSING_COORDINATES = "missing_coordinates"
    WEAK_CATEGORY = "weak_category"
    ADMINISTRATIVE_OR_INFRASTRUCTURE = "administrative_or_infrastructure"
    SCHOOL_OR_NON_TOURIST_LOCAL_USE = "school_or_non_tourist_local_use"
    GENERIC_HISTORIC_DISTRICT = "generic_historic_district"
    OUTSIDE_USER_INTERESTS = "outside_user_interests"
    UNSUPPORTED_ACCOMMODATION_INVENTORY = "unsupported_accommodation_inventory"
    DUPLICATE_OR_NEAR_DUPLICATE = "duplicate_or_near_duplicate"
    INSUFFICIENT_PROVIDER_CONFIDENCE = "insufficient_provider_confidence"


_NON_REJECTED_LOW_TIERS = {CandidateQualityTier.LOW_PRIORITY, CandidateQualityTier.REJECTED}


class CandidateQualityScore(BaseModel):
    """One deterministic quality score for a single provider/open-data
    candidate. Never carries a price, rating, opening hour, route time,
    review count, booking link, or safety score -- only a pre-ranking
    signal built from existing candidate fields (name, category, address,
    coordinates, source, data_status, confidence) plus traveler-provided
    interests/must-visit terms.
    """

    candidate_id: str
    candidate_name: str
    use_case: CandidateUseCase
    quality_tier: CandidateQualityTier
    total_score: float = Field(ge=0.0, le=1.0)
    score_components: dict[str, float] = Field(default_factory=dict)
    positive_signals: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)
    reject_reasons: list[CandidateRejectReason] = Field(default_factory=list)
    source: str | None = None
    data_status: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("candidate_id", "candidate_name")
    @classmethod
    def _require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate_id and candidate_name must not be blank.")
        return value

    @field_validator("score_components")
    @classmethod
    def _validate_score_components(cls, value: dict[str, float]) -> dict[str, float]:
        for component_name, component_value in value.items():
            if not (0.0 <= component_value <= 1.0):
                raise ValueError(
                    f"score_components[{component_name!r}] must be between 0 and 1, "
                    f"got {component_value}."
                )
        return value

    @model_validator(mode="after")
    def _validate_tier_reject_reason_consistency(self) -> "CandidateQualityScore":
        if self.quality_tier == CandidateQualityTier.REJECTED and not self.reject_reasons:
            raise ValueError("quality_tier=rejected requires at least one reject_reason.")

        if self.reject_reasons and self.quality_tier not in _NON_REJECTED_LOW_TIERS:
            raise ValueError(
                "A non-empty reject_reasons requires quality_tier to be "
                "low_priority or rejected."
            )

        return self


class CandidateQualityReport(BaseModel):
    """Plan-level rollup of `CandidateQualityScore`s for one destination's
    candidate pools -- built purely from existing `DestinationContext`
    candidates. No provider call, no AI/LLM, no invented place.
    """

    destination_name: str
    generated_at: datetime
    attraction_scores: list[CandidateQualityScore] = Field(default_factory=list)
    restaurant_scores: list[CandidateQualityScore] = Field(default_factory=list)
    accommodation_poi_scores: list[CandidateQualityScore] = Field(default_factory=list)
    # Section 192A (docs/14_backend_architecture.md section 140): scores
    # for candidates that did not come from DestinationContext's own
    # broad candidate collections -- today, exclusively Section 192
    # AI-directed `match_type=targeted_lookup` grounding results. Reuses
    # the exact same `CandidateQualityScore` model as every other score
    # above (no second score shape) and the exact same deterministic
    # scoring rules (`CandidateQualityService.score_provider_backed_candidate`
    # dispatches to the same score_attraction/score_restaurant/
    # score_accommodation_poi methods) -- kept in its own list, not merged
    # into the lists above, purely so a caller can tell "scored from the
    # broad destination_context pool" apart from "scored from a Section
    # 192 targeted lookup" for debugging/evaluation, mirroring how
    # `PlanningState.ai_provider_discovery_result` is already kept
    # separate from `candidate_grounding_batch` for the same reason.
    ai_directed_scores: list[CandidateQualityScore] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)

    @field_validator("destination_name")
    @classmethod
    def _require_non_blank_destination_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("destination_name must not be blank.")
        return value
