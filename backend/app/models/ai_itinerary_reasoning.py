from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.models.common import DataStatus, GeoPoint

# Section 193A (docs/14_backend_architecture.md section 141): contract-only
# models for a future second LLM stage -- itinerary REASONING (selecting,
# grouping, and coarsely ordering already-verified candidates into days),
# a fundamentally different job from the pre-existing, unrelated
# `app.models.ai_reasoning` module (Step 155A: prose EXPLANATION of
# already-computed plan sections -- traveler_preference_interpretation,
# trip_strategy_explanation, etc. -- never candidate selection/grouping,
# and never wired into any runtime service; confirmed unused outside its
# own tests before writing this module). These are deliberately separate,
# purpose-built models, not an evolution of that one.
#
# No LLM provider, LangGraph, or LangSmith is wired up yet -- these models
# exist purely to define what a future itinerary-reasoning call would be
# allowed to send/receive, and to reject a result if it isn't safe. Nothing
# in this module calls a network service, and nothing elsewhere in the app
# constructs or consumes these models yet (Section 193B wires a real
# Groq/Anthropic adapter; Section 193C translates an accepted result into
# `ExperiencePlan`/routing -- neither exists yet).
#
# Core rule (unchanged from every earlier AI-facing contract in this repo):
# LLM #2 decides which already-verified candidates fit together, on which
# day, in roughly what order, and why -- it never establishes a new travel
# fact. Every `ItineraryCandidateReference` the model may cite is built
# only from data that already passed real provider grounding and the
# existing deterministic `CandidateQualityService` policy (Sections
# 159A/170B/192/192A) -- never from an ungrounded proposal, a rejected
# proposal, a search-limit-skipped attempt, or a provider failure. The
# reasoning result itself may only ever reference a `candidate_id` from
# that pre-approved set; it can never introduce a coordinate, price,
# rating, opening hour, availability, booking link, or route time of its
# own -- every one of those must already exist as a real provider fact
# before this stage runs, or not be there at all.

_FORBIDDEN_TEXT_PATTERNS: tuple[str, ...] = (
    "rating",
    "price",
    "opening_hours",
    "route_time",
    "route_distance",
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
    "09:",
    "12:",
    "17-minute",
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


class CandidateOrigin(str, Enum):
    """Where a `ItineraryCandidateReference` came from -- visible for
    debugging/evaluation only (Task 10). Provider truth and deterministic
    quality remain authoritative either way; nothing reads this field to
    grant preferential treatment to one origin over the other.
    """

    BROAD_PROVIDER_DISCOVERY = "broad_provider_discovery"
    AI_DIRECTED_PROVIDER_DISCOVERY = "ai_directed_provider_discovery"
    # Section 197B (docs/14_backend_architecture.md, following section
    # 148): a place the USER explicitly named in feedback (Section 196's
    # `NewPlaceRequest.query`), grounded by a direct, targeted provider
    # lookup -- never an AI proposal. Task 17's explicit instruction: this
    # must never be mislabeled as `AI_DIRECTED_PROVIDER_DISCOVERY` just
    # because it reuses the same targeted-lookup gateway method. Nothing
    # reads `origin` for behavior (see this enum's own docstring) -- this
    # is provenance/debugging only, same as the other two values.
    USER_REQUESTED = "user_requested"


class ItineraryReasoningCategory(str, Enum):
    """Bounded candidate-domain label -- matches
    `app.models.candidate_quality.CandidateUseCase`'s two schedulable
    domains (must-visit is a targeting concept, not a separate itinerary
    domain, so it is intentionally not repeated here)."""

    ATTRACTION = "attraction"
    RESTAURANT = "restaurant"


def build_candidate_id(provider_name: str, provider_place_id: str) -> str:
    """The one, single-source-of-truth `candidate_id` formula (Task 3:
    deterministic, never an array index). Section 193C's
    `ExperiencePlannerService` reverse-index and this module's own
    `AIItineraryReasoningRequestBuilder`-facing forward construction both
    call this exact function -- never two independently written copies of
    the same format string -- so a candidate_id a reasoning result
    references always resolves to the same real place both places agree
    on (Task 5: "avoid duplicating selection logic").
    """
    return f"{provider_name}:{provider_place_id}"


class ItineraryCandidateReference(BaseModel):
    """One already-verified, provider-backed candidate LLM #2 is allowed
    to cite by `candidate_id`. Every field here is a real fact copied
    verbatim from existing `GroundedCandidate`/`CandidateQualityScore`/
    `PromotedAICandidate`/`destination_context` data -- this model invents
    nothing and is never constructed from AI proposal text alone.

    `candidate_id` is deterministic and stable
    (`f"{provider_name}:{provider_place_id}"`) -- never an array index,
    never regenerated differently between two builds of the same
    candidate (Task 3).
    """

    candidate_id: str
    name: str
    category: ItineraryReasoningCategory
    provider_name: str
    provider_place_id: str
    coordinates: GeoPoint
    data_status: DataStatus
    quality_score: float = Field(ge=0.0, le=1.0)
    quality_tier: str
    origin: CandidateOrigin

    @field_validator("candidate_id", "name", "provider_name", "provider_place_id", "quality_tier")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)


class FactualContextSummary(BaseModel):
    """Coarse, provider-backed status/count summary for trip-structure
    context LLM #2 may find useful -- never the full nested
    `WeatherContext`/`HolidayContext`/etc. object (Task 4: "do not dump
    entire PlanningState"), and never a placeholder value for a source
    that is genuinely absent (Task 11): an unavailable/not_connected
    source is represented by its own honest `*_status` string, never
    silently omitted or replaced with invented data.
    """

    weather_status: str | None = None
    holiday_count_in_range: int | None = None
    holiday_status: str | None = None
    currency_status: str | None = None
    accommodation_inventory_status: str | None = None
    accommodation_offer_count: int = 0
    flight_inventory_status: str | None = None
    flight_offer_count: int = 0


class TripStrategySummary(BaseModel):
    """Coarse passthrough of already-computed `TripStrategy` text fields
    -- never a new strategy fact. Omitted entirely from the request
    (field stays `None`) when no `trip_strategy` has been computed yet.
    """

    recommended_trip_style: str | None = None
    planning_strategy: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)


class TravelerContextSummary(BaseModel):
    """Traveler-facing preference fields only -- never the internal
    heuristic weight dicts (`decision_weights`/`mobility_profile`/
    `budget_profile`/`stay_profile`/`transport_profile`) `TravelerProfile`
    also carries; those are planning-internal, not something a reasoning
    prompt needs restated.
    """

    travelers_count: int = Field(gt=0)
    travel_group_type: str
    # A plain string (the real `TripPace.value`, e.g. "balanced"), not the
    # `app.models.planning_state.TripPace` enum itself -- this module must
    # not import from `planning_state` (that would be circular:
    # `planning_state.py` is the one that will import this module's
    # `AIItineraryReasoningResult`, matching every other AI-contract model
    # file in this repo, e.g. `ai_candidate_proposal.py`/
    # `candidate_grounding.py`, none of which import `planning_state`
    # either).
    pace: str
    interests: list[str] = Field(default_factory=list)
    must_visit: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    budget_min: float | None = None
    budget_max: float | None = None
    budget_currency: str | None = None


# Task 12: fixed, deterministic reasoning instructions every
# `AIItineraryReasoningRequest` carries -- the single source Section 193B's
# future Groq/Anthropic adapter reuses rather than writing its own
# provider-specific prompt copy. Mirrors the exact no-fabrication rules
# every earlier AI-facing contract in this repo already enforces
# structurally (forbidden-text patterns above, candidate-id-only
# references) -- these strings restate them for the model in its own
# input, they do not relax or replace the structural enforcement.
DEFAULT_ITINERARY_REASONING_INSTRUCTIONS: tuple[str, ...] = (
    "Use only the candidate_id values supplied in allowed_candidates. Never invent a "
    "new place, and never reference a place by name only.",
    "Do not infer or state a coordinate, provider id, price, rating, opening hours, "
    "availability, booking link, or route time/distance -- those are not yours to supply.",
    "Group candidates into days and, within a day, into coarse time windows only "
    "(morning/midday/afternoon/evening) -- never an exact clock time or a specific "
    "transfer duration.",
    "Choose combinations that fit the traveler's stated pace, interests, and "
    "constraints, using quality_score/quality_tier as a pre-ranking signal, not the "
    "candidate's own confidence.",
    "Return structured reasoning only: a short strategy summary, per-day candidate "
    "groupings with a brief rationale, and any tradeoffs worth surfacing.",
)


class AIItineraryReasoningRequest(BaseModel):
    """Everything a future itinerary-reasoning call would receive as
    input (Task 4). Bounded and typed -- never a raw `PlanningState` dump.
    """

    model_config = ConfigDict(protected_namespaces=())

    trip_id: str
    destination_name: str
    start_date: str
    end_date: str
    trip_duration_days: int = Field(ge=1)
    traveler_context: TravelerContextSummary
    trip_strategy_summary: TripStrategySummary | None = None
    factual_context: FactualContextSummary = Field(default_factory=FactualContextSummary)
    allowed_candidates: list[ItineraryCandidateReference] = Field(default_factory=list)
    reasoning_instructions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ITINERARY_REASONING_INSTRUCTIONS)
    )

    @field_validator("trip_id", "destination_name")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("allowed_candidates")
    @classmethod
    def validate_unique_candidate_ids(
        cls, value: list[ItineraryCandidateReference]
    ) -> list[ItineraryCandidateReference]:
        seen: set[str] = set()
        for candidate in value:
            if candidate.candidate_id in seen:
                raise ValueError(
                    "AIItineraryReasoningRequest.allowed_candidates has duplicate "
                    f"candidate_id: {candidate.candidate_id!r}."
                )
            seen.add(candidate.candidate_id)
        return value

    def allowed_candidate_ids(self) -> set[str]:
        return {candidate.candidate_id for candidate in self.allowed_candidates}


class ItineraryReasoningTimeWindow(str, Enum):
    MORNING = "morning"
    MIDDAY = "midday"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class ItineraryReasoningCandidatePlacement(BaseModel):
    """One candidate's coarse placement within a day (Task 6) -- a
    time-of-day bucket only, never an exact clock time.
    """

    candidate_id: str
    time_window: ItineraryReasoningTimeWindow

    @field_validator("candidate_id")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "candidate_id")


class ItineraryReasoningDayPlan(BaseModel):
    day_index: int = Field(ge=1)
    candidate_ids: list[str] = Field(min_length=1)
    rationale: str
    tradeoffs: str | None = None
    approximate_structure: list[ItineraryReasoningCandidatePlacement] = Field(default_factory=list)

    @field_validator("candidate_ids")
    @classmethod
    def validate_candidate_ids_not_blank_and_unique_within_day(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for candidate_id in value:
            if not candidate_id.strip():
                raise ValueError("ItineraryReasoningDayPlan.candidate_ids must not contain a blank id.")
            if candidate_id in seen:
                raise ValueError(
                    "ItineraryReasoningDayPlan.candidate_ids has duplicate candidate_id "
                    f"within the same day: {candidate_id!r}."
                )
            seen.add(candidate_id)
        return value

    @field_validator("rationale")
    @classmethod
    def validate_rationale_not_blank_and_safe(cls, value: str) -> str:
        stripped = _require_non_blank(value, "rationale")
        return _check_forbidden(stripped, "rationale")

    @field_validator("tradeoffs")
    @classmethod
    def validate_tradeoffs_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _check_forbidden(value, "tradeoffs")

    @model_validator(mode="after")
    def validate_placements_reference_days_own_candidates(self) -> "ItineraryReasoningDayPlan":
        day_candidate_ids = set(self.candidate_ids)
        for placement in self.approximate_structure:
            if placement.candidate_id not in day_candidate_ids:
                raise ValueError(
                    "ItineraryReasoningDayPlan.approximate_structure references "
                    f"candidate_id {placement.candidate_id!r}, which is not in this "
                    "day's own candidate_ids."
                )
        return self


class ItineraryReasoningStrategy(BaseModel):
    summary: str
    # Plain string for the same reason as TravelerContextSummary.pace above.
    pace: str
    reason: str

    @field_validator("summary", "reason")
    @classmethod
    def validate_not_blank_and_safe(cls, value: str, info: ValidationInfo) -> str:
        stripped = _require_non_blank(value, info.field_name)
        return _check_forbidden(stripped, info.field_name)


class AIItineraryReasoningStatus(str, Enum):
    NOT_CONNECTED = "not_connected"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    REJECTED = "rejected"


class AIItineraryReasoningGuardrailReport(BaseModel):
    passed: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    checked_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_blocked_reasons(self) -> "AIItineraryReasoningGuardrailReport":
        if not self.passed and not self.blocked_reasons:
            raise ValueError("guardrail_report.passed=False requires at least one blocked_reason.")
        return self


class AIItineraryReasoningResult(BaseModel):
    """Base result shape every future itinerary-reasoning provider
    response must validate through before being accepted. Enforces the
    same status-consistency contract every earlier AI-facing result model
    in this repo already uses (`AICandidateProposalResult`,
    `CandidateGroundingResult`, `AIReasoningResult`):

    - `status="completed"` requires at least one day and a passed
      guardrail check.
    - `status="rejected"` requires a failed guardrail check with an
      explicit, non-empty explanation.
    - `status="not_connected"` always carries an empty `days` list and
      `confidence=0.0`.
    - `status="skipped"` always carries an empty `days` list.
    """

    model_config = ConfigDict(protected_namespaces=())

    status: AIItineraryReasoningStatus
    strategy: ItineraryReasoningStrategy | None = None
    days: list[ItineraryReasoningDayPlan] = Field(default_factory=list)
    overall_tradeoffs: list[str] = Field(default_factory=list)
    guardrail_report: AIItineraryReasoningGuardrailReport
    provider_name: str | None = None
    model_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("overall_tradeoffs")
    @classmethod
    def validate_overall_tradeoffs_safe(cls, value: list[str]) -> list[str]:
        for item in value:
            _check_forbidden(item, "overall_tradeoffs")
        return value

    @model_validator(mode="after")
    def validate_status_consistency(self) -> "AIItineraryReasoningResult":
        if self.status == AIItineraryReasoningStatus.COMPLETED:
            if not self.days:
                raise ValueError(
                    "completed AIItineraryReasoningResult must include at least one day."
                )
            if self.strategy is None:
                raise ValueError(
                    "completed AIItineraryReasoningResult must include a strategy."
                )
            if not self.guardrail_report.passed:
                raise ValueError(
                    "completed AIItineraryReasoningResult requires guardrail_report.passed=True."
                )
        if self.status == AIItineraryReasoningStatus.REJECTED:
            if self.guardrail_report.passed:
                raise ValueError(
                    "rejected AIItineraryReasoningResult requires guardrail_report.passed=False."
                )
            if not self.guardrail_report.blocked_reasons:
                raise ValueError(
                    "rejected AIItineraryReasoningResult requires at least one blocked_reason."
                )
        if self.status == AIItineraryReasoningStatus.NOT_CONNECTED:
            if self.days:
                raise ValueError("not_connected AIItineraryReasoningResult must have no days.")
            if self.confidence != 0.0:
                raise ValueError("not_connected AIItineraryReasoningResult must have confidence=0.0.")
        if self.status == AIItineraryReasoningStatus.SKIPPED and self.days:
            raise ValueError("skipped AIItineraryReasoningResult must have no days.")
        return self


def validate_result_against_request(
    request: AIItineraryReasoningRequest, result: AIItineraryReasoningResult
) -> list[str]:
    """Task 7: the deterministic candidate-ID safety check. Pure,
    side-effect-free, and independently callable (not only reachable
    through `AIItineraryReasoningBatch` construction) -- returns every
    violation found, never raises itself, so a caller can log/report all
    problems at once rather than only the first.

    Checks:
    - every `candidate_id` referenced anywhere in `result.days` (both
      `candidate_ids` and `approximate_structure`) exists in
      `request.allowed_candidates` (Task 3/7).
    - no candidate_id appears on more than one day (Task 8's default
      policy: one candidate -> at most one day).
    - every `day_index` is within `1..request.trip_duration_days` (Task 7,
      "candidate assigned to invalid day").
    - no two days share the same `day_index`.
    """
    violations: list[str] = []
    allowed_ids = request.allowed_candidate_ids()

    seen_candidate_ids: dict[str, int] = {}
    seen_day_indexes: set[int] = set()

    for day in result.days:
        if day.day_index > request.trip_duration_days:
            violations.append(
                f"day_index {day.day_index} exceeds trip_duration_days "
                f"({request.trip_duration_days})."
            )
        if day.day_index in seen_day_indexes:
            violations.append(f"day_index {day.day_index} is used by more than one day.")
        seen_day_indexes.add(day.day_index)

        for candidate_id in day.candidate_ids:
            if candidate_id not in allowed_ids:
                violations.append(
                    f"candidate_id {candidate_id!r} on day {day.day_index} is not in "
                    "request.allowed_candidates."
                )
            if candidate_id in seen_candidate_ids:
                violations.append(
                    f"candidate_id {candidate_id!r} appears on both day "
                    f"{seen_candidate_ids[candidate_id]} and day {day.day_index}."
                )
            else:
                seen_candidate_ids[candidate_id] = day.day_index

    return violations


class AIItineraryReasoningBatch(BaseModel):
    """Pairs a request with its (optional, not-yet-produced) result --
    matching the exact `*Batch` convention `AICandidateProposalBatch`/
    `CandidateGroundingBatch` already use in this repo. Enforces
    `validate_result_against_request` (Task 7) whenever a `completed`
    result is present; a `not_connected`/`skipped`/`rejected` result
    carries no days, so there is nothing to validate against the request.
    """

    request: AIItineraryReasoningRequest
    result: AIItineraryReasoningResult | None = None

    @model_validator(mode="after")
    def _validate_candidate_safety(self) -> "AIItineraryReasoningBatch":
        if self.result is None or self.result.status != AIItineraryReasoningStatus.COMPLETED:
            return self
        violations = validate_result_against_request(self.request, self.result)
        if violations:
            raise ValueError(
                "AIItineraryReasoningBatch.result failed candidate-ID safety validation: "
                + "; ".join(violations)
            )
        return self
