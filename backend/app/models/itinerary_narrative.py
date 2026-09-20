from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, ValidationInfo, field_validator

# Step 182F: LLM itinerary narrator. Everything in this module is either
# (a) a strict, allow-listed *input* extracted from an already-computed
# `PlanningState` (never raw provider internals, never an API key, never
# a fabricated fact), or (b) the *report* the narrator provider returns,
# which is presentation prose only -- narrative text plus caveats, never
# a new hotel/flight/price/rating/route/booking fact. See
# `app.services.itinerary_narrative_request_builder` for what actually
# populates an `ItineraryNarrativeRequest`, and
# `app.services.itinerary_narrative_service` for how a report gets
# attached to `PlanningState` (read-only, optional, additive -- it never
# mutates `experience_plan`, `validation_report`, `provider_coverage`, or
# any other factual field).
#
# Section 195 (docs/14_backend_architecture.md, following section 145)
# added three things to this pre-existing Step 182F contract, all in the
# same spirit as Sections 193A/194A's own AI-facing contracts:
#
# 1. `ItineraryNarrativeExperienceInput.experience_id` (the request side)
#    and `ItineraryNarrativeDayOutput.referenced_experience_ids` (the
#    output side), validated against each other by
#    `validate_narrative_against_request` below -- a structural (never
#    fuzzy free-text name matching) guard against the narrator claiming
#    to narrate a day around a place that isn't actually part of that
#    day's real, final, post-repair `ExperiencePlan`.
# 2. The same forbidden-factual-claim-pattern check every other AI-facing
#    contract in this repo already enforces (`_FORBIDDEN_TEXT_PATTERNS`)
#    is now applied to `title`/`narrative`/`caveats`/`summary`/
#    `assumptions`/`warnings` -- this model previously had zero such
#    guard, unlike `ai_candidate_proposal`/`ai_itinerary_reasoning`/
#    `ai_itinerary_repair`.
# 3. `reasoning_rationale`/`reasoning_strategy_summary` (LLM #2's own
#    rationale prose, included as *explanatory context only* -- never
#    read by the narrator's own logic as a factual claim) and
#    `was_adjusted_after_feasibility_checks` (a plain boolean, Section
#    194B repair awareness with zero implementation detail exposed).

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
    "optimal",
    "verified",
    "travel-ready",
    "booking-ready",
    "safest",
    "the best",
)


def _find_forbidden_pattern(text: str) -> str | None:
    lowered = text.lower()
    for pattern in _FORBIDDEN_TEXT_PATTERNS:
        if pattern in lowered:
            return pattern
    return None


def _check_forbidden(value: str, field_name: str) -> str:
    forbidden = _find_forbidden_pattern(value)
    if forbidden is not None:
        raise ValueError(f"{field_name} must not contain forbidden pattern: {forbidden!r}")
    return value


def _check_forbidden_list(values: list[str], field_name: str) -> list[str]:
    for value in values:
        _check_forbidden(value, field_name)
    return values


class ItineraryNarrativeStatus(str, Enum):
    SUCCESS = "success"
    NOT_CONNECTED = "not_connected"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class ItineraryNarrativeExperienceInput(BaseModel):
    """One scheduled experience, reduced to only the fields a narrator is
    allowed to see: its name, category, and the backend's own
    `why_included` reasoning. Deliberately excludes coordinates,
    provider ids, confidence scores, timing fields, and every other
    `ExperienceItem` field -- none of those are needed to write prose
    about what's scheduled and why.

    `experience_id` (Section 195) is the one exception: it is included
    specifically so a narrator's structured output can reference *which*
    already-scheduled item its prose is about
    (`ItineraryNarrativeDayOutput.referenced_experience_ids`), checked
    deterministically by `validate_narrative_against_request` below --
    never displayed to the traveler itself, and never a provider id.
    """

    experience_id: str
    name: str
    category: str | None = None
    reason: str | None = None


class ItineraryNarrativeDayInput(BaseModel):
    """One day's allow-listed input. `has_movement_data`/`has_restaurant_
    suggestions` are booleans only -- never a duration, distance, or
    rating value -- so the narrator can acknowledge what exists without
    ever being handed a number it could restate incorrectly or a fact it
    could embellish.

    `reasoning_rationale` (Section 195) is LLM #2's own already-generated
    rationale prose for this exact day (`ai_itinerary_reasoning_result`),
    included purely as explanatory *context* -- the narrator's own output
    is still independently validated against this same allow-list and the
    forbidden-factual-claim patterns, so reasoning prose can never smuggle
    an unsupported factual claim through into the narrative. `None`
    whenever no completed reasoning result exists for this day (e.g. the
    plan used the deterministic fallback path).
    """

    day_number: int
    date: date
    experiences: list[ItineraryNarrativeExperienceInput] = Field(default_factory=list)
    restaurant_names: list[str] = Field(default_factory=list)
    has_movement_data: bool = False
    reasoning_rationale: str | None = None


class ItineraryNarrativeRequest(BaseModel):
    """The complete, strict allow-list of `PlanningState` fields a
    narrator provider is ever given (Step 182F). Every field here is
    already-computed, already-validated backend data -- this model adds
    no new fact of its own, and building one never calls a provider,
    LLM, or network service (see the request builder). Truncated to
    `Settings.itinerary_narrator_max_days`/`itinerary_narrator_max_items_per_day`
    before being handed to any provider, for prompt-size safety --
    `days`/`experiences`/`restaurant_names` may therefore be a prefix of
    the real plan, never a fabricated superset.

    Deliberately absent from this model, by design (see
    `docs/13_llm_reasoning_pipeline.md` for the full rationale): any
    price, rating, review count, route/travel-time duration, booking
    link, availability flag, opening hour, or flight schedule field.
    `flight_offer_count`/`accommodation_offer_count` are counts only.
    `weather_summary` is a plain-language range string this builder
    itself computes deterministically from real `WeatherContext.daily_weather`
    values (mirroring the frontend's own `summarizeWeatherForTravelerView`) --
    never something the LLM invents, and never a raw provider payload.
    """

    destination: str
    start_date: date
    end_date: date
    travelers_count: int
    travel_group_type: str | None = None
    pace: str | None = None
    interests: list[str] = Field(default_factory=list)

    days: list[ItineraryNarrativeDayInput] = Field(default_factory=list)
    stay_area_names: list[str] = Field(default_factory=list)
    accommodation_offer_count: int = 0
    flight_offer_count: int = 0

    weather_available: bool = False
    weather_summary: str | None = None

    validation_status: str | None = None
    warning_count: int = 0
    critical_issue_count: int = 0
    unavailable_data_fields: list[str] = Field(default_factory=list)

    truncated: bool = False

    # Section 195: LLM #2 rationale context (never factual truth -- see
    # this module's own docstring) and plain repair awareness.
    # `reasoning_strategy_summary` is `ai_itinerary_reasoning_result.strategy.summary`,
    # included only when that result's `status == completed`. Per-day
    # rationale lives on `ItineraryNarrativeDayInput.reasoning_rationale`
    # instead (Task 3: keep the per-day/trip-level split the reasoning
    # contract itself already uses).
    reasoning_strategy_summary: str | None = None
    # True only when the final, currently-in-effect plan is the result of
    # at least one *completed* Section 194B repair attempt
    # (`ai_itinerary_repair_attempt_count > 0` and the latest
    # `ai_itinerary_repair_result.status == completed`) -- never set for a
    # disabled/not_connected/rejected/exhausted-without-success repair
    # attempt (Task 6: "do not mention repair when none occurred", which
    # includes "repair was attempted but never actually changed
    # anything").
    was_adjusted_after_feasibility_checks: bool = False

    def allowed_experience_ids_by_day(self) -> dict[int, set[str]]:
        return {day.day_number: {item.experience_id for item in day.experiences} for day in self.days}


class ItineraryNarrativeDayOutput(BaseModel):
    """One day's narrator-generated prose (Step 182F). Deliberately has
    no field for a hotel price, flight number, rating, route duration,
    booking status, or invented clock time -- `narrative`/`caveats` are
    the only content fields carrying free-form text, and both are
    checked against `_FORBIDDEN_TEXT_PATTERNS` (Section 195) before this
    model will construct at all.

    `referenced_experience_ids` (Section 195, Task 12) is a structured,
    deterministically-checkable declaration of which already-scheduled
    items this day's prose is about -- `validate_narrative_against_
    request` below rejects a day whose referenced ids aren't an exact
    subset of that same day's real `ItineraryNarrativeDayInput.experiences`
    ids. This is a structural identity check, not a claim that the free
    text mentions only these items by name -- see this module's own
    docstring for why a structural check was chosen over fuzzy free-text
    place-name matching.
    """

    day_number: int
    date: date
    title: str
    narrative: str
    caveats: list[str] = Field(default_factory=list)
    referenced_experience_ids: list[str] = Field(default_factory=list)

    @field_validator("title", "narrative")
    @classmethod
    def validate_text_not_blank_and_safe(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank.")
        return _check_forbidden(value, info.field_name)

    @field_validator("caveats")
    @classmethod
    def validate_caveats_safe(cls, value: list[str]) -> list[str]:
        return _check_forbidden_list(value, "caveats")


class ItineraryNarrativeReport(BaseModel):
    """Optional, additive LLM-narrator output attached to `PlanningState`
    (Step 182F). This is presentation prose only -- it is never treated
    as provider data, never feeds `validation_report`/`provider_coverage`/
    `regeneration_readiness`, and never replaces `experience_plan`.
    `status` follows the same `success`/`not_connected`/`unavailable`/
    `failed` vocabulary every other provider report in this codebase
    uses (see `AccommodationSearchStatus`/`FlightSearchStatus`/
    `HotelRatingsStatus`) -- `message` always carries an honest,
    human-readable reason for anything other than `success`.
    `source_fields_used` names which `ItineraryNarrativeRequest` fields
    were actually populated for this call (for Developer view
    transparency) -- it is bookkeeping about the input, never a claim
    about the output's accuracy.
    """

    status: ItineraryNarrativeStatus
    provider: str | None = None
    model: str | None = None
    message: str | None = None
    summary: str | None = None
    daily_narratives: list[ItineraryNarrativeDayOutput] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    source_fields_used: list[str] = Field(default_factory=list)
    generated_at: datetime | None = None

    @field_validator("summary")
    @classmethod
    def validate_summary_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _check_forbidden(value, "summary")

    @field_validator("warnings", "assumptions")
    @classmethod
    def validate_text_lists_safe(cls, value: list[str], info: ValidationInfo) -> list[str]:
        return _check_forbidden_list(value, info.field_name)


def validate_narrative_against_request(
    request: ItineraryNarrativeRequest, report: ItineraryNarrativeReport
) -> list[str]:
    """Section 195, Task 12's deterministic place-identity safety check.
    Pure, side-effect-free, and independently callable -- returns every
    violation found, never raises itself, so a caller can log/report all
    problems at once rather than only the first.

    Checks, per day in `report.daily_narratives`:
    - the day's own `day_number` actually exists in `request.days`.
    - every `referenced_experience_ids` entry is in *that specific day's*
      real, final, post-repair set of scheduled experience ids (never
      "anywhere in the trip" -- a day cannot claim credit for an item
      scheduled on a different day).

    A day with an empty `referenced_experience_ids` list is never itself
    a violation (a day may legitimately have nothing scheduled, or a
    narrator may reasonably choose not to enumerate every item) -- this
    only ever rejects an *incorrect* reference, never requires one.
    """
    violations: list[str] = []
    allowed_by_day = request.allowed_experience_ids_by_day()

    for day in report.daily_narratives:
        allowed_ids = allowed_by_day.get(day.day_number)
        if allowed_ids is None:
            violations.append(
                f"day_number {day.day_number} is not in the request's own days."
            )
            continue
        for experience_id in day.referenced_experience_ids:
            if experience_id not in allowed_ids:
                violations.append(
                    f"referenced_experience_id {experience_id!r} on day {day.day_number} is "
                    "not one of that day's real scheduled experiences."
                )

    return violations
