from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

# Contract-only models for a future AI reasoning layer (Step 155A,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md
# section 25). No LLM provider, LangGraph, or LangSmith is wired up yet --
# these models exist purely to define what a future AI reasoning response
# would be allowed to return, and to reject it if it isn't. Nothing in this
# module calls a network service, and nothing elsewhere in the app
# constructs or consumes these models yet.
#
# Core rule: AI reasoning may explain, interpret, and summarize existing
# PlanningState data -- it must never invent a travel fact (place,
# restaurant, hotel, price, rating, opening hours, route time, review
# count, booking link, or safety score). `EvidenceRef` can only point back
# at data that already exists elsewhere in PlanningState; it is never a
# vehicle for smuggling a new fact into the system.

# Forbidden claim-shaped substrings/patterns (case-insensitive, except the
# literal "$" character). These deliberately match the exact kinds of
# claims docs/13_llm_reasoning_pipeline.md section 4 forbids the LLM from
# inventing -- ratings, prices, opening hours, booking/reservation links,
# route times, safety scores, currency amounts, and superlative marketing
# language. Checked against `AIReasoningResult.summary` and every item of
# `AIReasoningResult.reasoning`.
_FORBIDDEN_TEXT_PATTERNS: tuple[str, ...] = (
    "rating:",
    "price:",
    "opening_hours:",
    "booking_url:",
    "reservation_url:",
    "route_time:",
    "safety_score:",
    "$",
    "stars",
    "top" + " rated",
    "best hotel",
    "book now",
)


def _find_forbidden_pattern(text: str) -> str | None:
    for pattern in _FORBIDDEN_TEXT_PATTERNS:
        if pattern == "$":
            if "$" in text:
                return pattern
        elif pattern in text.lower():
            return pattern
    return None


def _require_non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank.")
    return value


class AIReasoningTask(str, Enum):
    TRAVELER_PREFERENCE_INTERPRETATION = "traveler_preference_interpretation"
    TRIP_STRATEGY_EXPLANATION = "trip_strategy_explanation"
    STAY_AREA_EXPLANATION = "stay_area_explanation"
    EXPERIENCE_PLAN_EXPLANATION = "experience_plan_explanation"
    VALIDATION_EXPLANATION = "validation_explanation"
    FEEDBACK_INTERPRETATION = "feedback_interpretation"


class AIReasoningStatus(str, Enum):
    NOT_CONNECTED = "not_connected"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    REJECTED = "rejected"


class EvidenceRef(BaseModel):
    """A reference to data that already exists elsewhere in PlanningState.

    Deliberately carries no travel-fact fields of its own (no price,
    rating, opening hours, or booking URL) -- it only points at where a
    claim's evidence lives (`source_section`/`source_field`/`source_id`),
    plus the human-readable `claim` text itself. It can never be used to
    introduce a new fact; the underlying data must already exist at the
    referenced location.
    """

    source_section: str
    source_field: str
    source_id: str | None = None
    claim: str

    @field_validator("source_section", "source_field", "claim")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)


class UnavailableConstraint(BaseModel):
    """A missing field a future AI reasoning call must honor rather than
    guess at (mirrors `app.models.common.UnavailableDataItem`, but scoped
    to this contract so it doesn't depend on the wider PlanningState
    models).
    """

    field: str
    reason: str
    data_status: str
    source: str | None = None

    @field_validator("field", "reason", "data_status")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)


class AIReasoningGuardrailReport(BaseModel):
    """Outcome of running guardrail checks against a candidate AI
    reasoning result. `passed` is the single source of truth for whether
    the result is safe to accept; `blocked_reasons` explains why it wasn't
    when it fails, and `checked_fields` records which fields the guardrail
    actually inspected.
    """

    passed: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    checked_fields: list[str] = Field(default_factory=list)


class AIReasoningRequest(BaseModel):
    """Everything a future AI reasoning call would receive as input.

    Matches the input-boundary rule in docs/13_llm_reasoning_pipeline.md
    section 5: only named PlanningState sections, explicit unavailable
    constraints, and existing evidence references -- never an unrestricted
    dump of PlanningState.
    """

    task: AIReasoningTask
    trip_id: str
    input_sections: list[str] = Field(min_length=1)
    user_prompt: str | None = Field(default=None, max_length=3000)
    unavailable_constraints: list[UnavailableConstraint] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("trip_id")
    @classmethod
    def validate_trip_id(cls, value: str) -> str:
        return _require_non_blank(value, "trip_id")


class AIReasoningResult(BaseModel):
    """Base result shape every future AI reasoning provider response must
    validate through before being accepted (docs/13_llm_reasoning_pipeline.md
    section 7 and section 24). Enforces the core safety invariants:

    - `summary`/`reasoning` can never contain an obviously fabricated
      travel-fact claim (rating, price, opening hours, booking/reservation
      link, route time, safety score, currency amount, or superlative
      marketing language) -- see `_FORBIDDEN_TEXT_PATTERNS`.
    - `status="completed"` requires real evidence (`evidence_refs`
      non-empty) and a passed guardrail check.
    - `status="rejected"` requires an explicit, non-empty explanation
      (`guardrail_report.blocked_reasons`) and a failed guardrail check.
    - `status="not_connected"` always carries `confidence=0.0` -- a
      not-connected result can never claim any confidence.
    """

    model_config = ConfigDict(protected_namespaces=())

    task: AIReasoningTask
    status: AIReasoningStatus
    summary: str
    reasoning: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    unavailable_constraints_respected: list[UnavailableConstraint] = Field(default_factory=list)
    guardrail_report: AIReasoningGuardrailReport
    confidence: float = Field(ge=0.0, le=1.0)
    model_name: str | None = None
    provider_name: str | None = None

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        stripped = _require_non_blank(value, "summary")
        forbidden = _find_forbidden_pattern(stripped)
        if forbidden is not None:
            raise ValueError(f"summary must not contain forbidden pattern: {forbidden!r}")
        return value

    @field_validator("reasoning")
    @classmethod
    def validate_reasoning(cls, value: list[str]) -> list[str]:
        for item in value:
            forbidden = _find_forbidden_pattern(item)
            if forbidden is not None:
                raise ValueError(
                    f"reasoning must not contain forbidden pattern: {forbidden!r}"
                )
        return value

    @model_validator(mode="after")
    def validate_status_consistency(self) -> "AIReasoningResult":
        if self.status == AIReasoningStatus.COMPLETED:
            if not self.evidence_refs:
                raise ValueError(
                    "completed AIReasoningResult must include at least one evidence_ref."
                )
            if not self.guardrail_report.passed:
                raise ValueError(
                    "completed AIReasoningResult requires guardrail_report.passed=True."
                )
        if self.status == AIReasoningStatus.REJECTED:
            if self.guardrail_report.passed:
                raise ValueError(
                    "rejected AIReasoningResult requires guardrail_report.passed=False."
                )
            if not self.guardrail_report.blocked_reasons:
                raise ValueError(
                    "rejected AIReasoningResult requires at least one blocked_reason."
                )
        if self.status == AIReasoningStatus.NOT_CONNECTED and self.confidence != 0.0:
            raise ValueError("not_connected AIReasoningResult must have confidence=0.0.")
        return self


class TravelerPreferenceInterpretationResult(BaseModel):
    """Interpretation-only output for `traveler_preference_interpretation`.
    Restates/clarifies the traveler's own free-text input -- never invents
    a destination, place, or provider-backed fact.
    """

    base: AIReasoningResult
    interpreted_interests: list[str] = Field(default_factory=list)
    interpreted_constraints: list[str] = Field(default_factory=list)
    ambiguity_notes: list[str] = Field(default_factory=list)


class TripStrategyReasoningResult(BaseModel):
    """Explanation-only output for `trip_strategy_explanation`. Explains
    tradeoffs already present in `TripStrategy`; never invents a new
    strategy fact.
    """

    base: AIReasoningResult
    strategy_themes: list[str] = Field(default_factory=list)
    tradeoff_notes: list[str] = Field(default_factory=list)


class StayAreaReasoningResult(BaseModel):
    """Explanation-only output for `stay_area_explanation`. Explains why an
    existing stay-area candidate was suggested and what accommodation data
    is still missing -- never a price, availability, rating, or booking
    claim.
    """

    base: AIReasoningResult
    stay_area_factors: list[str] = Field(default_factory=list)
    missing_accommodation_data: list[str] = Field(default_factory=list)


class ExperiencePlanReasoningResult(BaseModel):
    """Explanation-only output for `experience_plan_explanation`. Explains
    why the existing schedule/sequencing looks the way it does and what
    about it is still unvalidated -- never a new experience, route time, or
    opening-hours claim.
    """

    base: AIReasoningResult
    sequencing_rationale: list[str] = Field(default_factory=list)
    unvalidated_experience_facts: list[str] = Field(default_factory=list)


class ValidationReasoningResult(BaseModel):
    """Explanation-only output for `validation_explanation`. Explains why
    the plan needs review or is blocked, in the user's terms -- never
    changes `ValidationReport.readiness_status` itself.
    """

    base: AIReasoningResult
    user_review_reasons: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


class FeedbackInterpretationResult(BaseModel):
    """Interpretation-only output for `feedback_interpretation`. Mirrors
    the deterministic, rule-based classification `FeedbackService` already
    performs (docs/13_llm_reasoning_pipeline.md section 9.6) -- this is a
    contract for a future AI-assisted version of that same classification,
    never something that applies a change to the plan itself.
    """

    base: AIReasoningResult
    feedback_type: str
    affected_sections: list[str] = Field(min_length=1)
    requires_regeneration: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)

    @field_validator("feedback_type")
    @classmethod
    def validate_feedback_type(cls, value: str) -> str:
        return _require_non_blank(value, "feedback_type")
