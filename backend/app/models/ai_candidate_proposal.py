from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

# Contract-only models for a future AI-assisted candidate discovery layer
# (Step 157A, itinerary-generator-build-spec.md Stage 5,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md section
# 25). No LLM provider, LangGraph, or LangSmith is wired up yet -- these
# models exist purely to define what a future AI candidate proposal
# response would be allowed to return, and to reject it if it isn't.
# Nothing in this module calls a network service, and nothing elsewhere in
# the app constructs or consumes these models yet.
#
# Core rule (build spec Stage 0/Stage 5-6): the LLM is a proposer, never a
# source of truth. A proposal may name a candidate place/area idea and give
# a one-line rationale -- it must never carry a coordinate, provider
# source, rating, price, opening hours, booking/availability claim, or
# route/timing value. Every proposal must later be grounded/verified
# against provider/open data (Stage 6) before it can be scheduled; nothing
# here is schedulable on its own.

# Forbidden claim-shaped substrings/patterns (case-insensitive). These
# deliberately match the exact kinds of factual claims a candidate proposal
# must never make -- ratings, prices, opening hours, booking/reservation
# links, route times, review counts, ticket prices, availability, safety
# scores, and superlative/marketing language implying a verified fact.
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


class AICandidateProposalTask(str, Enum):
    DESTINATION_CANDIDATE_DISCOVERY = "destination_candidate_discovery"
    REPLACEMENT_CANDIDATE_DISCOVERY = "replacement_candidate_discovery"
    FEEDBACK_CANDIDATE_DISCOVERY = "feedback_candidate_discovery"


class AICandidateProposalStatus(str, Enum):
    NOT_CONNECTED = "not_connected"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    REJECTED = "rejected"


class AICandidateType(str, Enum):
    ATTRACTION = "attraction"
    NEIGHBORHOOD = "neighborhood"
    VIEWPOINT = "viewpoint"
    MUSEUM = "museum"
    PARK = "park"
    FOOD_AREA = "food_area"
    FERRY_OR_WATERFRONT = "ferry_or_waterfront"
    CULTURAL_AREA = "cultural_area"
    SHOPPING_AREA = "shopping_area"
    DAY_CLUSTER_IDEA = "day_cluster_idea"


class AICandidatePriorityHint(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class AICandidateVerificationRequirement(str, Enum):
    MUST_GROUND_BY_NAME_AND_LOCATION = "must_ground_by_name_and_location"
    MUST_GROUND_BY_DESTINATION_CONTAINMENT = "must_ground_by_destination_containment"
    MUST_CHECK_AMBIGUITY = "must_check_ambiguity"
    MUST_REJECT_IF_NOT_FOUND = "must_reject_if_not_found"
    MUST_NOT_USE_WITHOUT_PROVIDER_MATCH = "must_not_use_without_provider_match"


class AICandidateProposal(BaseModel):
    """One AI-proposed candidate idea (build spec Stage 5). Deliberately
    carries no coordinate, provider source, rating, price, opening-hours,
    booking/availability, or route/timing field -- it is a name and a
    one-line rationale only, never a grounded or schedulable place. It must
    be independently verified against provider/open data (Stage 6) before
    it can be scheduled.
    """

    proposal_id: str
    candidate_name: str
    candidate_type: AICandidateType
    priority_hint: AICandidatePriorityHint = AICandidatePriorityHint.UNKNOWN
    suggested_area: str | None = None
    why_consider: str
    fit_with_user_preferences: list[str] = Field(default_factory=list)
    verification_requirements: list[AICandidateVerificationRequirement] = Field(min_length=1)
    source: str = "ai_candidate_proposal"
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("proposal_id", "candidate_name", "why_consider")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("candidate_name", "suggested_area", "why_consider")
    @classmethod
    def validate_no_forbidden_claims(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return value
        return _check_forbidden(value, info.field_name)

    @field_validator("fit_with_user_preferences")
    @classmethod
    def validate_fit_with_user_preferences(cls, value: list[str]) -> list[str]:
        for item in value:
            _check_forbidden(item, "fit_with_user_preferences")
        return value


class AICandidateProposalRequest(BaseModel):
    """Everything a future AI candidate proposal call would receive as
    input (build spec Stage 5). `provider_candidate_summary` is a coverage
    summary only (e.g. counts by category), never a place list -- the LLM
    is told what's already covered, not fed raw provider data to restate.
    """

    task: AICandidateProposalTask
    trip_id: str
    destination_name: str
    trip_duration_days: int = Field(ge=1)
    interests: list[str] = Field(default_factory=list)
    must_visit: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    provider_candidate_summary: dict[str, int] = Field(default_factory=dict)
    unavailable_data: list[str] = Field(default_factory=list)
    max_candidates: int = Field(default=15, ge=1, le=25)

    @field_validator("trip_id", "destination_name")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)


class AICandidateProposalGuardrailReport(BaseModel):
    """Outcome of running guardrail checks against a candidate AI proposal
    result. `passed` is the single source of truth for whether the result
    is safe to accept; `blocked_reasons` explains why it wasn't when it
    fails, and `checked_fields` records which fields the guardrail actually
    inspected.
    """

    passed: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    checked_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_blocked_reasons(self) -> "AICandidateProposalGuardrailReport":
        if not self.passed and not self.blocked_reasons:
            raise ValueError("guardrail_report.passed=False requires at least one blocked_reason.")
        return self


class AICandidateProposalResult(BaseModel):
    """Base result shape every future AI candidate proposal provider
    response must validate through before being accepted. Enforces the core
    safety invariants:

    - `status="completed"` requires at least one proposal and a passed
      guardrail check.
    - `status="rejected"` requires a failed guardrail check with an
      explicit, non-empty explanation.
    - `status="not_connected"` always carries an empty proposal list and
      `confidence=0.0`.
    - `status="skipped"` always carries an empty proposal list.
    """

    model_config = ConfigDict(protected_namespaces=())

    task: AICandidateProposalTask
    status: AICandidateProposalStatus
    proposals: list[AICandidateProposal] = Field(default_factory=list)
    rejected_raw_items: list[str] = Field(default_factory=list)
    guardrail_report: AICandidateProposalGuardrailReport
    provider_name: str | None = None
    model_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_status_consistency(self) -> "AICandidateProposalResult":
        if self.status == AICandidateProposalStatus.COMPLETED:
            if not self.proposals:
                raise ValueError(
                    "completed AICandidateProposalResult must include at least one proposal."
                )
            if not self.guardrail_report.passed:
                raise ValueError(
                    "completed AICandidateProposalResult requires guardrail_report.passed=True."
                )
        if self.status == AICandidateProposalStatus.REJECTED:
            if self.guardrail_report.passed:
                raise ValueError(
                    "rejected AICandidateProposalResult requires guardrail_report.passed=False."
                )
            if not self.guardrail_report.blocked_reasons:
                raise ValueError(
                    "rejected AICandidateProposalResult requires at least one blocked_reason."
                )
        if self.status == AICandidateProposalStatus.NOT_CONNECTED:
            if self.proposals:
                raise ValueError("not_connected AICandidateProposalResult must have no proposals.")
            if self.confidence != 0.0:
                raise ValueError("not_connected AICandidateProposalResult must have confidence=0.0.")
        if self.status == AICandidateProposalStatus.SKIPPED and self.proposals:
            raise ValueError("skipped AICandidateProposalResult must have no proposals.")
        return self


class AICandidateProposalBatch(BaseModel):
    """Pairs a request with its (optional, not-yet-produced) result. Not
    wired into any runtime pipeline -- a contract for how a future request/
    result pair would travel together.
    """

    request: AICandidateProposalRequest
    result: AICandidateProposalResult | None = None

    @model_validator(mode="after")
    def validate_task_match(self) -> "AICandidateProposalBatch":
        if self.result is not None and self.result.task != self.request.task:
            raise ValueError("AICandidateProposalBatch.result.task must match request.task.")
        return self
