from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

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
    """

    name: str
    category: str | None = None
    reason: str | None = None


class ItineraryNarrativeDayInput(BaseModel):
    """One day's allow-listed input. `has_movement_data`/`has_restaurant_
    suggestions` are booleans only -- never a duration, distance, or
    rating value -- so the narrator can acknowledge what exists without
    ever being handed a number it could restate incorrectly or a fact it
    could embellish.
    """

    day_number: int
    date: date
    experiences: list[ItineraryNarrativeExperienceInput] = Field(default_factory=list)
    restaurant_names: list[str] = Field(default_factory=list)
    has_movement_data: bool = False


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


class ItineraryNarrativeDayOutput(BaseModel):
    """One day's narrator-generated prose (Step 182F). Deliberately has
    no field for a hotel price, flight number, rating, route duration,
    booking status, or invented clock time -- `narrative`/`caveats` are
    the only content fields, and both are plain strings/string lists a
    provider adapter validates through this exact model before it can
    ever reach `PlanningState`.
    """

    day_number: int
    date: date
    title: str
    narrative: str
    caveats: list[str] = Field(default_factory=list)


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
