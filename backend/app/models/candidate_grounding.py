from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.common import DataStatus, GeoPoint

# Contract-only models for a future candidate grounding/verification layer
# (Step 158A, itinerary-generator-build-spec.md Stage 6 "Grounding &
# Verification (the trust firewall)", docs/13_llm_reasoning_pipeline.md
# section 30, docs/14_backend_architecture.md section 25). No LLM, provider
# call, LangGraph, or LangSmith dependency is wired up yet -- these models
# exist purely to define what a future grounding step would be allowed to
# return, and to reject it if it isn't. Nothing in this module calls a
# network service or mutates `PlanningState`, and nothing elsewhere in the
# app constructs or consumes these models yet.
#
# Core rule (build spec Stage 0/Stage 6): an `AICandidateProposal` (Step
# 157A) is only an idea. It becomes a `GroundedCandidate` -- schedulable in
# principle -- only once real provider/open-data evidence
# (`CandidateGroundingEvidence`) confirms it exists, where it is, and what
# it is. A proposal that cannot be matched becomes a
# `RejectedCandidateProposal` instead, never silently dropped, so it can
# later feed the "what the AI suggested but we didn't use" trust UI.

# Forbidden claim-shaped substrings/patterns (case-insensitive). Same list
# used by `app.models.ai_candidate_proposal` and `app.models.ai_reasoning`
# -- these deliberately match the exact kinds of factual claims neither an
# AI proposal nor its grounding step is allowed to assert on its own words;
# real values still flow through typed provider-backed fields
# (`CandidateGroundingEvidence.coordinates`, `.data_status`, etc.), never
# through free text.
_FORBIDDEN_TEXT_PATTERNS: tuple[str, ...] = (
    "rating",
    "price",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
    "reservation",
    "book now",
    "open until",
    "highly rated",
    "cheap",
    "guaran" + "teed",
    "exact travel time",
)


def _find_forbidden_pattern(text: str) -> str | None:
    lowered = text.lower()
    for pattern in _FORBIDDEN_TEXT_PATTERNS:
        if pattern in lowered:
            return pattern
    return None


def _require_non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank.")
    return value


def _check_forbidden(value: str, field_name: str) -> str:
    forbidden = _find_forbidden_pattern(value)
    if forbidden is not None:
        raise ValueError(f"{field_name} must not contain forbidden pattern: {forbidden!r}")
    return value


class CandidateGroundingStatus(str, Enum):
    NOT_CONNECTED = "not_connected"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    PARTIAL = "partial"
    REJECTED = "rejected"


class CandidateGroundingMatchType(str, Enum):
    EXACT_NAME = "exact_name"
    NORMALIZED_NAME = "normalized_name"
    FUZZY_NAME = "fuzzy_name"
    PROVIDER_CANDIDATE_REFERENCE = "provider_candidate_reference"
    TARGETED_LOOKUP = "targeted_lookup"
    AMBIGUOUS_MATCH = "ambiguous_match"


class CandidateGroundingConfidenceTier(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CandidateGroundingRejectReason(str, Enum):
    NO_PROVIDER_MATCH = "no_provider_match"
    OUTSIDE_DESTINATION_BOUNDS = "outside_destination_bounds"
    AMBIGUOUS_MATCH = "ambiguous_match"
    CATEGORY_MISMATCH = "category_mismatch"
    MISSING_COORDINATES = "missing_coordinates"
    PROVIDER_NOT_CONNECTED = "provider_not_connected"
    UNSAFE_AI_CLAIM = "unsafe_ai_claim"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    UNSUPPORTED_CANDIDATE_TYPE = "unsupported_candidate_type"


class CandidateGroundingEvidence(BaseModel):
    """The real provider/open-data fact that grounds one
    `AICandidateProposal` (build spec Stage 6). `coordinates` and
    `data_status` are required, typed, provider-backed fields -- never
    something an AI proposal's free text can assert on its own.
    """

    provider_name: str
    provider_place_id: str
    matched_name: str
    matched_category: str | None = None
    match_type: CandidateGroundingMatchType
    coordinates: GeoPoint
    data_status: DataStatus
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("provider_name", "provider_place_id", "matched_name")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("provider_name", "provider_place_id", "matched_name", "matched_category")
    @classmethod
    def validate_no_forbidden_claims(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return value
        return _check_forbidden(value, info.field_name)


class GroundedCandidate(BaseModel):
    """A previously-proposed AI candidate idea that has been independently
    verified against real provider/open-data evidence (build spec Stage 6).
    Unlike `AICandidateProposal`, this may carry coordinates -- but only
    because they come from `evidence`, a real provider/open-data fact,
    never from the AI's own wording. This is still not necessarily
    schedulable by itself; future scheduling logic (Stage 7-8) decides
    that, informed by `confidence_tier`/`confidence`.
    """

    grounding_id: str
    proposal_id: str
    candidate_name: str
    candidate_type: AICandidateType
    matched_name: str
    confidence_tier: CandidateGroundingConfidenceTier
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: CandidateGroundingEvidence
    verification_requirements_satisfied: list[AICandidateVerificationRequirement] = Field(
        min_length=1
    )
    source: str = "candidate_grounding"

    @field_validator("grounding_id", "proposal_id", "candidate_name", "matched_name")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("candidate_name", "matched_name")
    @classmethod
    def validate_no_forbidden_claims(cls, value: str, info: ValidationInfo) -> str:
        return _check_forbidden(value, info.field_name)


class RejectedCandidateProposal(BaseModel):
    """An `AICandidateProposal` that could not be grounded (build spec
    Stage 6): "if a user's explicit must-visit can't be grounded, don't
    drop it silently." Deliberately carries no coordinate or provider
    field -- a rejected proposal was never confirmed real, so it must not
    look like a real place. Feeds the future "what the AI suggested but we
    didn't use" trust UI.
    """

    proposal_id: str
    candidate_name: str
    candidate_type: AICandidateType
    reject_reason: CandidateGroundingRejectReason
    message: str
    source: str = "candidate_grounding"

    @field_validator("proposal_id", "candidate_name", "message")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("candidate_name", "message")
    @classmethod
    def validate_no_forbidden_claims(cls, value: str, info: ValidationInfo) -> str:
        return _check_forbidden(value, info.field_name)


class CandidateGroundingGuardrailReport(BaseModel):
    """Outcome of running guardrail checks against a candidate grounding
    result. `passed` is the single source of truth for whether the result
    is safe to accept; `blocked_reasons` explains why it wasn't when it
    fails, and `checked_fields` records which fields the guardrail actually
    inspected.
    """

    passed: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    checked_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_blocked_reasons(self) -> "CandidateGroundingGuardrailReport":
        if not self.passed and not self.blocked_reasons:
            raise ValueError("guardrail_report.passed=False requires at least one blocked_reason.")
        return self


class CandidateGroundingRequest(BaseModel):
    """Everything a future grounding step would receive as input (build
    spec Stage 6). `proposals` are the Step 157A `AICandidateProposal`
    ideas to verify; empty is valid for `not_connected`/`skipped` cases
    where grounding never ran. `provider_candidate_summary` is a coverage
    summary only (e.g. counts by category), matching the same field on
    `AICandidateProposalRequest`.
    """

    trip_id: str
    destination_name: str
    proposals: list[AICandidateProposal] = Field(default_factory=list)
    provider_candidate_summary: dict[str, int] = Field(default_factory=dict)
    unavailable_data: list[str] = Field(default_factory=list)

    @field_validator("trip_id", "destination_name")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("provider_candidate_summary")
    @classmethod
    def validate_provider_candidate_summary(cls, value: dict[str, int]) -> dict[str, int]:
        for key, count in value.items():
            if count < 0:
                raise ValueError(f"provider_candidate_summary[{key!r}] must be >= 0, got {count}.")
        return value


class CandidateGroundingResult(BaseModel):
    """Base result shape every future candidate grounding step's output
    must validate through before being accepted. Enforces the core safety
    invariants:

    - `status="completed"` requires a passed guardrail check and at least
      one grounded candidate.
    - `status="partial"` requires a passed guardrail check and at least
      one grounded candidate *and* at least one rejected proposal (some
      proposals grounded, some not).
    - `status="rejected"` requires a failed guardrail check with an
      explicit, non-empty explanation.
    - `status="not_connected"` always carries empty grounded/rejected
      lists and `confidence=0.0`.
    - `status="skipped"` always carries an empty grounded list and
      `confidence=0.0`.
    """

    model_config = ConfigDict(protected_namespaces=())

    status: CandidateGroundingStatus
    grounded_candidates: list[GroundedCandidate] = Field(default_factory=list)
    rejected_proposals: list[RejectedCandidateProposal] = Field(default_factory=list)
    guardrail_report: CandidateGroundingGuardrailReport
    provider_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_status_consistency(self) -> "CandidateGroundingResult":
        if self.status == CandidateGroundingStatus.COMPLETED:
            if not self.guardrail_report.passed:
                raise ValueError(
                    "completed CandidateGroundingResult requires guardrail_report.passed=True."
                )
            if not self.grounded_candidates:
                raise ValueError(
                    "completed CandidateGroundingResult must include at least one grounded candidate."
                )
        if self.status == CandidateGroundingStatus.PARTIAL:
            if not self.guardrail_report.passed:
                raise ValueError(
                    "partial CandidateGroundingResult requires guardrail_report.passed=True."
                )
            if not self.grounded_candidates:
                raise ValueError(
                    "partial CandidateGroundingResult must include at least one grounded candidate."
                )
            if not self.rejected_proposals:
                raise ValueError(
                    "partial CandidateGroundingResult must include at least one rejected proposal."
                )
        if self.status == CandidateGroundingStatus.REJECTED:
            if self.guardrail_report.passed:
                raise ValueError(
                    "rejected CandidateGroundingResult requires guardrail_report.passed=False."
                )
            if not self.guardrail_report.blocked_reasons:
                raise ValueError(
                    "rejected CandidateGroundingResult requires at least one blocked_reason."
                )
        if self.status == CandidateGroundingStatus.NOT_CONNECTED:
            if self.grounded_candidates or self.rejected_proposals:
                raise ValueError(
                    "not_connected CandidateGroundingResult must have no grounded/rejected candidates."
                )
            if self.confidence != 0.0:
                raise ValueError("not_connected CandidateGroundingResult must have confidence=0.0.")
        if self.status == CandidateGroundingStatus.SKIPPED:
            if self.grounded_candidates:
                raise ValueError("skipped CandidateGroundingResult must have no grounded candidates.")
            if self.confidence != 0.0:
                raise ValueError("skipped CandidateGroundingResult must have confidence=0.0.")
        return self


class CandidateGroundingBatch(BaseModel):
    """Pairs a request with its (optional, not-yet-produced) result. Not
    wired into any runtime pipeline -- a contract for how a future
    request/result pair would travel together, and for keeping grounding
    output honestly tied to the proposals it was actually run against.
    """

    request: CandidateGroundingRequest
    result: CandidateGroundingResult | None = None

    @model_validator(mode="after")
    def validate_result_against_request(self) -> "CandidateGroundingBatch":
        if self.result is None:
            return self

        grounded_ids = [candidate.proposal_id for candidate in self.result.grounded_candidates]
        rejected_ids = [proposal.proposal_id for proposal in self.result.rejected_proposals]

        if len(grounded_ids) != len(set(grounded_ids)):
            raise ValueError("CandidateGroundingBatch.result.grounded_candidates has duplicate proposal_id values.")
        if len(rejected_ids) != len(set(rejected_ids)):
            raise ValueError("CandidateGroundingBatch.result.rejected_proposals has duplicate proposal_id values.")
        overlap = set(grounded_ids) & set(rejected_ids)
        if overlap:
            raise ValueError(
                f"CandidateGroundingBatch.result has proposal_id(s) both grounded and rejected: {sorted(overlap)}."
            )

        if self.request.proposals:
            request_ids = {proposal.proposal_id for proposal in self.request.proposals}
            unknown_ids = (set(grounded_ids) | set(rejected_ids)) - request_ids
            if unknown_ids:
                raise ValueError(
                    "CandidateGroundingBatch.result references proposal_id(s) not present in "
                    f"request.proposals: {sorted(unknown_ids)}."
                )

            if self.result.status in (
                CandidateGroundingStatus.COMPLETED,
                CandidateGroundingStatus.PARTIAL,
            ):
                accounted_ids = set(grounded_ids) | set(rejected_ids)
                missing_ids = request_ids - accounted_ids
                if missing_ids:
                    raise ValueError(
                        "CandidateGroundingBatch.result does not account for every "
                        f"request.proposals proposal_id: {sorted(missing_ids)}."
                    )

        return self
