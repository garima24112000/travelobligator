from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningRequest,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    FactualContextSummary,
    ItineraryCandidateReference,
    ItineraryReasoningDayPlan,
    TravelerContextSummary,
    TripStrategySummary,
    validate_result_against_request,
)
from app.models.common import ValidationSeverity

# Same tiny forbidden-text-pattern/non-blank helpers
# `app.models.ai_itinerary_reasoning` defines for itself -- kept as this
# module's own local copies rather than importing that module's
# underscore-prefixed (module-private) names. The pattern list mirrors
# that module's `_FORBIDDEN_TEXT_PATTERNS` exactly, since a repair
# summary/rationale must never smuggle in a factual claim the original
# reasoning contract already forbids.
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

# Section 194A (docs/14_backend_architecture.md, following section 143):
# contract-only models for repairing an already-completed LLM #2
# (AIItineraryReasoningResult) in response to deterministic
# routing/validation findings -- a third stage in the same reasoning
# subsystem as Sections 193A-C, never a replacement for it.
#
# No LangGraph node, PlanningOrchestrator stage, or /generate call site
# constructs or consumes these models yet (that is Section 194B's job).
# This module exists purely to define what a repair call would be allowed
# to send/receive, and to reject a result if it isn't safe.
#
# Core rule (unchanged from every earlier AI-facing contract in this
# repo, restated for repair specifically): the repair LLM may only choose
# a better arrangement of candidates that already passed real provider
# grounding and deterministic quality scoring -- the exact same
# `allowed_candidates` universe the original `AIItineraryReasoningRequest`
# used, never a newly discovered candidate. It may edit only the days a
# deterministic classification names as affected; every other day must
# come back byte-for-byte unchanged. It is never allowed to override a
# validator finding, invent a route/travel-time, invent a candidate,
# invent a place, invent a coordinate, invent an hour/price/rating/
# availability claim, or remove factual provider provenance.


class RepairableIssueType(str, Enum):
    """This app's own, narrow vocabulary for "a real, structured finding
    that itinerary reasoning could plausibly address by rearranging
    candidates already in allowed_candidates" (Section 194A Task 2) --
    not a validator category itself, and not a 1:1 mirror of
    `ValidationIssue.category`. Deliberately small: several examples
    named as "possibly repairable" in the 194A spec (day-count/pace
    overload, duplicate candidate) have no corresponding validator/report
    finding anywhere in this codebase today -- `ExperiencePlannerService`
    already prevents them at scheduling time, so there is nothing for a
    repair stage to observe or classify. Only the three members below are
    ever produced, each backed by a real, inspectable field on an
    existing report:

    - `GEOGRAPHIC_SPREAD`: `PlanValidatorService`'s own
      `category="geographic_spread"` `ValidationIssue`, whose
      `affected_section` names a specific day
      (`experience_plan.daily_plans[N]`) whose consecutive coordinate-backed
      experiences are unusually spread out in straight-line distance.
    - `ROUTE_NEEDS_REVIEW`: a `RouteLegFeasibility` on
      `planning_state.route_feasibility_report.legs` whose
      `feasibility_status == RouteFeasibilityStatus.NEEDS_REVIEW` AND
      whose `status == ProviderStatus.FAILED` -- a routing call that was
      genuinely attempted and genuinely failed for a specific pair of
      consecutive same-day experiences. `NEEDS_REVIEW` alone is *not*
      sufficient (Section 194B correction): `RouteFeasibilityService`
      also reports it whenever no routing provider is connected at all
      (this app's own documented default), which is an absence of data,
      not a detected problem, and would otherwise fire on nearly every
      multi-stop day.
    - `INSUFFICIENT_TRAVEL_BUFFER`: a `TravelTimeBuffer` on
      `planning_state.travel_time_buffer_report.buffers` whose
      `buffer_status == BufferSufficiencyStatus.INSUFFICIENT` -- a real,
      provider-backed route duration exceeds a real, known schedule gap
      for a specific pair of consecutive same-day experiences.
    """

    GEOGRAPHIC_SPREAD = "geographic_spread"
    ROUTE_NEEDS_REVIEW = "route_needs_review"
    INSUFFICIENT_TRAVEL_BUFFER = "insufficient_travel_buffer"


class AIItineraryRepairIssue(BaseModel):
    """One deterministically classified, structurally-sourced repairable
    finding for a single day (Task 2/3). `source_category` restates the
    real `ValidationIssue.category`/report this came from, for
    traceability -- never invented, never guessed from message prose.
    """

    issue_type: RepairableIssueType
    day_index: int = Field(ge=1)
    source_category: str
    message: str
    severity: ValidationSeverity

    @field_validator("source_category", "message")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)


# Task 7: fixed, deterministic repair instructions every
# `AIItineraryRepairRequest` carries -- mirrors
# `DEFAULT_ITINERARY_REASONING_INSTRUCTIONS`'s role exactly, restated for
# the narrower repair job.
DEFAULT_ITINERARY_REPAIR_INSTRUCTIONS: tuple[str, ...] = (
    "You are repairing an existing itinerary in response to deterministic validation "
    "findings. The validator is authoritative about what is wrong; you are not being "
    "asked to judge feasibility yourself.",
    "Use only the candidate_id values supplied in allowed_candidates -- the same "
    "candidate universe the original itinerary reasoning used. Never invent a new place "
    "or a new candidate_id.",
    "Do not contradict factual provider data, and do not claim an exact route duration, "
    "price, rating, opening hours, availability, or booking information.",
    "Modify only the day_index values listed in affected_days. Every other day must stay "
    "exactly as given in original_days -- do not restate or alter an unaffected day.",
    "Prefer the smallest change that resolves the supplied issues -- moving or "
    "re-ordering the fewest candidates necessary, not a full re-plan.",
    "Respect the traveler's stated pace, interests, and constraints exactly as the "
    "original reasoning did.",
    "Return structured repair output only: the revised day(s), a short repair summary, "
    "and which issue types you addressed.",
)


class AIItineraryRepairRequest(BaseModel):
    """Everything a repair call receives as input (Task 3). Bounded and
    typed -- never a raw `PlanningState` dump. Shares the same
    trip-context/traveler-context/candidate-universe shape as
    `AIItineraryReasoningRequest` (Task 4: same candidate universe,
    deliberately, since `AIItineraryRepairRequestBuilder` builds both from
    the same unchanged `PlanningState` data) plus the repair-specific
    fields below.
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

    original_days: list[ItineraryReasoningDayPlan] = Field(min_length=1)
    affected_days: list[int] = Field(min_length=1)
    issues: list[AIItineraryRepairIssue] = Field(min_length=1)
    attempt_number: int = Field(default=1, ge=1)
    repair_instructions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ITINERARY_REPAIR_INSTRUCTIONS)
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
                    "AIItineraryRepairRequest.allowed_candidates has duplicate "
                    f"candidate_id: {candidate.candidate_id!r}."
                )
            seen.add(candidate.candidate_id)
        return value

    @field_validator("affected_days")
    @classmethod
    def validate_affected_days_unique(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("AIItineraryRepairRequest.affected_days must not repeat a day_index.")
        return value

    @model_validator(mode="after")
    def validate_structural_consistency(self) -> "AIItineraryRepairRequest":
        allowed_ids = self.allowed_candidate_ids()
        affected_day_set = set(self.affected_days)

        for day_index in self.affected_days:
            if day_index > self.trip_duration_days:
                raise ValueError(
                    f"AIItineraryRepairRequest.affected_days contains {day_index}, which "
                    f"exceeds trip_duration_days ({self.trip_duration_days})."
                )

        seen_original_day_indexes: set[int] = set()
        for day in self.original_days:
            if day.day_index in seen_original_day_indexes:
                raise ValueError(
                    "AIItineraryRepairRequest.original_days has duplicate day_index: "
                    f"{day.day_index!r}."
                )
            seen_original_day_indexes.add(day.day_index)
            for candidate_id in day.candidate_ids:
                if candidate_id not in allowed_ids:
                    raise ValueError(
                        f"AIItineraryRepairRequest.original_days references candidate_id "
                        f"{candidate_id!r} on day {day.day_index}, which is not in "
                        "allowed_candidates."
                    )

        for issue in self.issues:
            if issue.day_index not in affected_day_set:
                raise ValueError(
                    f"AIItineraryRepairRequest.issues names day_index {issue.day_index}, "
                    "which is not in affected_days."
                )

        return self

    def allowed_candidate_ids(self) -> set[str]:
        return {candidate.candidate_id for candidate in self.allowed_candidates}

    def to_reasoning_request(self) -> AIItineraryReasoningRequest:
        """Reconstructs an `AIItineraryReasoningRequest` carrying the
        exact same trip context/candidate universe this repair request
        used -- lets `merge_repair_into_reasoning_result` reuse
        `validate_result_against_request` (Task 16's "full result
        validates under the standard reasoning contract") rather than
        writing a second copy of that same safety check.
        """
        return AIItineraryReasoningRequest(
            trip_id=self.trip_id,
            destination_name=self.destination_name,
            start_date=self.start_date,
            end_date=self.end_date,
            trip_duration_days=self.trip_duration_days,
            traveler_context=self.traveler_context,
            trip_strategy_summary=self.trip_strategy_summary,
            factual_context=self.factual_context,
            allowed_candidates=self.allowed_candidates,
        )


class AIItineraryRepairStatus(str, Enum):
    NOT_CONNECTED = "not_connected"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    REJECTED = "rejected"


class AIItineraryRepairResult(BaseModel):
    """Base result shape every repair provider response must validate
    through before being accepted (Task 5). Deliberately the smallest
    design compatible with the existing reasoning architecture: rather
    than a full revised `AIItineraryReasoningResult`, `repaired_days`
    holds only the day(s) the repair call actually changed -- unaffected
    days are never restated here, and
    `merge_repair_into_reasoning_result` is the one deterministic place
    that combines this with the original result (Task 6).

    Status consistency (mirrors `AIItineraryReasoningResult` exactly):

    - `status="completed"` requires at least one repaired day, a
      non-blank/safe `repair_summary`, and a passed guardrail check.
    - `status="rejected"` requires a failed guardrail check with an
      explicit, non-empty explanation.
    - `status="not_connected"` (provider disabled/unreachable) and
      `status="skipped"` (Task 13/26: no repairable issue existed, so the
      provider was never even called) both always carry an empty
      `repaired_days`.
    """

    model_config = ConfigDict(protected_namespaces=())

    status: AIItineraryRepairStatus
    repaired_days: list[ItineraryReasoningDayPlan] = Field(default_factory=list)
    repair_summary: str | None = None
    addressed_issue_types: list[RepairableIssueType] = Field(default_factory=list)
    guardrail_report: AIItineraryReasoningGuardrailReport
    provider_name: str | None = None
    model_name: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    attempt_number: int = Field(default=1, ge=1)

    @field_validator("repair_summary")
    @classmethod
    def validate_repair_summary_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _check_forbidden(value, "repair_summary")

    @model_validator(mode="after")
    def validate_status_consistency(self) -> "AIItineraryRepairResult":
        if self.status == AIItineraryRepairStatus.COMPLETED:
            if not self.repaired_days:
                raise ValueError(
                    "completed AIItineraryRepairResult must include at least one repaired day."
                )
            if not self.repair_summary or not self.repair_summary.strip():
                raise ValueError("completed AIItineraryRepairResult must include a repair_summary.")
            if not self.guardrail_report.passed:
                raise ValueError(
                    "completed AIItineraryRepairResult requires guardrail_report.passed=True."
                )
        if self.status == AIItineraryRepairStatus.REJECTED:
            if self.guardrail_report.passed:
                raise ValueError(
                    "rejected AIItineraryRepairResult requires guardrail_report.passed=False."
                )
            if not self.guardrail_report.blocked_reasons:
                raise ValueError(
                    "rejected AIItineraryRepairResult requires at least one blocked_reason."
                )
        if self.status == AIItineraryRepairStatus.NOT_CONNECTED and self.repaired_days:
            raise ValueError("not_connected AIItineraryRepairResult must have no repaired_days.")
        if self.status == AIItineraryRepairStatus.SKIPPED and self.repaired_days:
            raise ValueError("skipped AIItineraryRepairResult must have no repaired_days.")
        return self


def validate_repair_result_against_request(
    request: AIItineraryRepairRequest, result: AIItineraryRepairResult
) -> list[str]:
    """The deterministic repair-safety check (Task 15), independently
    callable and pure -- returns every violation found, never raises.

    Checks:
    - every `day_index` in `result.repaired_days` is in
      `request.affected_days` (Task 23: an unaffected day can never be
      "repaired").
    - no two repaired days share a `day_index`.
    - every `candidate_id` referenced (in both `candidate_ids` and
      `approximate_structure`) is in `request.allowed_candidates`
      (Task 4/24: no new candidate, ever).
    - no `candidate_id` appears on more than one repaired day.
    - no `candidate_id` used in a repaired day collides with a
      candidate_id already used by an *unaffected* original day -- that
      day is guaranteed to survive the merge unchanged (Task 6), so
      reusing its candidate elsewhere would create a cross-day duplicate
      the moment the merge combines both.
    """
    violations: list[str] = []
    allowed_ids = request.allowed_candidate_ids()
    affected_day_set = set(request.affected_days)

    unaffected_candidate_ids: set[str] = {
        candidate_id
        for day in request.original_days
        if day.day_index not in affected_day_set
        for candidate_id in day.candidate_ids
    }

    seen_day_indexes: set[int] = set()
    seen_candidate_ids: dict[str, int] = {}

    for day in result.repaired_days:
        if day.day_index not in affected_day_set:
            violations.append(
                f"repaired day_index {day.day_index} is not in request.affected_days."
            )
        if day.day_index in seen_day_indexes:
            violations.append(f"repaired day_index {day.day_index} is used by more than one day.")
        seen_day_indexes.add(day.day_index)

        for candidate_id in day.candidate_ids:
            if candidate_id not in allowed_ids:
                violations.append(
                    f"candidate_id {candidate_id!r} on repaired day {day.day_index} is not "
                    "in request.allowed_candidates."
                )
            if candidate_id in unaffected_candidate_ids:
                violations.append(
                    f"candidate_id {candidate_id!r} on repaired day {day.day_index} is "
                    "already used by an unaffected original day."
                )
            if candidate_id in seen_candidate_ids:
                violations.append(
                    f"candidate_id {candidate_id!r} appears on both repaired day "
                    f"{seen_candidate_ids[candidate_id]} and day {day.day_index}."
                )
            else:
                seen_candidate_ids[candidate_id] = day.day_index

    return violations


def merge_repair_into_reasoning_result(
    original_result: AIItineraryReasoningResult,
    repair_request: AIItineraryRepairRequest,
    repair_result: AIItineraryRepairResult,
) -> AIItineraryReasoningResult:
    """Task 16's deterministic merge: affected days are replaced with
    `repair_result.repaired_days`, every other day is carried over from
    `original_result` completely unchanged, and `strategy`/
    `overall_tradeoffs` are always preserved from `original_result` --
    194A's `AIItineraryRepairResult` has no strategy field of its own, so
    there is nothing to adjust it with. Never mutates `original_result`
    (builds and returns a new `AIItineraryReasoningResult`).

    Raises `ValueError` if `repair_result.status` is not `completed`, or
    if the merged whole-trip result fails
    `validate_result_against_request` (re-run here as defense-in-depth,
    on top of `validate_repair_result_against_request` -- Task 16: "full
    result validates under the standard reasoning contract").
    """
    if repair_result.status != AIItineraryRepairStatus.COMPLETED:
        raise ValueError(
            "merge_repair_into_reasoning_result requires a completed AIItineraryRepairResult, "
            f"got status={repair_result.status.value!r}."
        )

    violations = validate_repair_result_against_request(repair_request, repair_result)
    if violations:
        raise ValueError(
            "AIItineraryRepairResult failed repair-safety validation: " + "; ".join(violations)
        )

    repaired_by_day_index = {day.day_index: day for day in repair_result.repaired_days}
    merged_days = [
        repaired_by_day_index.get(day.day_index, day) for day in original_result.days
    ]

    merged_result = AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.COMPLETED,
        strategy=original_result.strategy,
        days=merged_days,
        overall_tradeoffs=list(original_result.overall_tradeoffs),
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        provider_name=repair_result.provider_name,
        model_name=repair_result.model_name,
        confidence=repair_result.confidence,
    )

    reasoning_violations = validate_result_against_request(
        repair_request.to_reasoning_request(), merged_result
    )
    if reasoning_violations:
        raise ValueError(
            "Merged AIItineraryReasoningResult failed the standard reasoning-contract "
            "safety check: " + "; ".join(reasoning_violations)
        )

    return merged_result
