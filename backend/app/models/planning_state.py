from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.common import (
    AccommodationType,
    ChecklistItemStatus,
    ClaimSource,
    DataQuality,
    DataStatus,
    DestinationScope,
    GeoPoint,
    MoneyAmount,
    ProviderCoverage,
    ProviderStatusEntry,
    ReadinessStatus,
    RegenerationStrategy,
    UnavailableDataItem,
    ValidationSeverity,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TravelGroupType(str, Enum):
    SOLO = "solo"
    COUPLE = "couple"
    FAMILY = "family"
    FRIENDS = "friends"
    GROUP = "group"


class TripPace(str, Enum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    PACKED = "packed"


class TransportPreference(str, Enum):
    PUBLIC_TRANSPORT = "public_transport"
    TAXI = "taxi"
    SELF_DRIVE = "self_drive"
    TRAIN = "train"
    FLIGHT = "flight"
    WALKING = "walking"
    NO_PREFERENCE = "no_preference"


class PipelineStatus(str, Enum):
    DRAFT = "draft"
    GENERATING = "generating"
    PROFILE_CREATED = "profile_created"
    DESTINATION_CONTEXT_CREATED = "destination_context_created"
    STRATEGY_CREATED = "strategy_created"
    STAY_TRANSPORT_CREATED = "stay_transport_created"
    EXPERIENCE_PLAN_CREATED = "experience_plan_created"
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    UPDATED_AFTER_FEEDBACK = "updated_after_feedback"
    FAILED = "failed"


class PlanningStage(str, Enum):
    CREATE_TRIP = "create_trip"
    TRAVELER_PROFILE = "traveler_profile"
    DESTINATION_CONTEXT = "destination_context"
    TRIP_STRATEGY = "trip_strategy"
    STAY_TRANSPORT = "stay_transport"
    EXPERIENCE_PLAN = "experience_plan"
    VALIDATION = "validation"
    FEEDBACK = "feedback"


class TripRequest(BaseModel):
    trip_request_id: str = Field(default_factory=lambda: _new_id("trip_request"))

    destination_scope: DestinationScope = DestinationScope.SINGLE_CITY
    primary_destination: str = Field(min_length=1, max_length=160)
    origin_city: str | None = Field(default=None, max_length=160)

    start_date: date
    end_date: date

    travelers_count: int = Field(gt=0, le=20)
    travel_group_type: TravelGroupType

    budget_min: float | None = Field(default=None, ge=0)
    budget_max: float | None = Field(default=None, ge=0)
    budget_currency: str = Field(default="USD", min_length=3, max_length=3)

    pace: TripPace = TripPace.BALANCED
    preferred_accommodation_types: list[AccommodationType] = Field(default_factory=list)
    transport_preferences: list[TransportPreference] = Field(default_factory=list)

    interests: list[str] = Field(default_factory=list)
    must_visit: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    free_text_preferences: str | None = Field(default=None, max_length=3000)

    @field_validator(
        "interests",
        "must_visit",
        "must_avoid",
        "constraints",
        mode="before",
    )
    @classmethod
    def clean_string_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []

        if not isinstance(value, list):
            return []

        cleaned: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                cleaned.append(item.strip())

        return cleaned

    @field_validator("primary_destination", "origin_city", "free_text_preferences", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def validate_dates_and_budget(self) -> "TripRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")

        if (
            self.budget_min is not None
            and self.budget_max is not None
            and self.budget_min > self.budget_max
        ):
            raise ValueError("budget_min must be less than or equal to budget_max")

        return self


class TravelerProfile(BaseModel):
    profile_id: str = Field(default_factory=lambda: _new_id("traveler_profile"))

    travel_group_type: TravelGroupType
    travelers_count: int = Field(gt=0)
    pace: TripPace

    interests: list[str] = Field(default_factory=list)
    must_visit: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    decision_weights: dict[str, float] = Field(default_factory=dict)
    mobility_profile: dict[str, Any] = Field(default_factory=dict)
    budget_profile: dict[str, Any] = Field(default_factory=dict)
    stay_profile: dict[str, Any] = Field(default_factory=dict)
    transport_profile: dict[str, Any] = Field(default_factory=dict)

    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class DestinationContext(BaseModel):
    context_id: str = Field(default_factory=lambda: _new_id("destination_context"))

    destination_name: str
    resolved_coordinates: GeoPoint | None = None

    destination_overview: str | None = None
    candidate_pois: list[dict[str, Any]] = Field(default_factory=list)
    candidate_restaurants: list[dict[str, Any]] = Field(default_factory=list)
    # Open-data location candidates only (e.g. OSM), never bookable inventory.
    # No price, availability, rating, or booking link should be attached here.
    candidate_accommodation_pois: list[dict[str, Any]] = Field(default_factory=list)
    neighborhood_candidates: list[dict[str, Any]] = Field(default_factory=list)
    attraction_clusters: list[dict[str, Any]] = Field(default_factory=list)
    rough_transport_feasibility: dict[str, Any] = Field(default_factory=dict)
    average_cost_hints: dict[str, Any] = Field(default_factory=dict)

    provider_coverage: ProviderCoverage = Field(default_factory=ProviderCoverage)
    unavailable_data: list[UnavailableDataItem] = Field(default_factory=list)
    data_sources_used: list[str] = Field(default_factory=list)

    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class DailyWeather(BaseModel):
    """One day of provider-backed weather forecast data
    (docs/12_provider_architecture.md section 15). Only fields the
    underlying source (Open-Meteo) actually returned are populated -- no
    weather description/condition is invented from `weather_code`, and no
    humidity, UV, alert, or severe-weather value is ever fabricated.
    """

    date: date
    temperature_max_c: float | None = None
    temperature_min_c: float | None = None
    precipitation_probability_max: float | None = None
    precipitation_sum_mm: float | None = None
    weather_code: int | None = None
    source: str
    data_status: DataStatus


class WeatherContext(BaseModel):
    """Plan-level provider-backed weather forecast for the trip's date range
    (docs/12_provider_architecture.md section 15, docs/14_backend_architecture.md
    section 10). Built from a real WeatherProvider call (Open-Meteo) using
    destination coordinates resolved via the existing places/geocoding flow
    -- no provider call is duplicated to get those coordinates. Weather data
    here is not yet used to adjust the itinerary; no rerouting or
    rescheduling reasoning is implemented yet, and no rain, temperature,
    humidity, alert, UV, or severe-weather value is ever invented. If
    coordinates or usable daily data are unavailable, `daily_weather` stays
    empty and this is reported honestly via `data_status`/`warnings` rather
    than guessed.
    """

    destination: str
    start_date: date
    end_date: date
    daily_weather: list[DailyWeather] = Field(default_factory=list)
    source: str | None = None
    data_status: DataStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Holiday(BaseModel):
    """One provider-backed public holiday falling within the trip's date
    range (docs/12_provider_architecture.md section 16). Only fields the
    underlying source (Nager.Date) actually returned are populated -- no
    closure, crowd, opening-hour, event, festival, strike, or risk
    assessment is ever fabricated.
    """

    date: date
    local_name: str
    name: str
    country_code: str
    is_global: bool
    counties: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    source: str
    data_status: DataStatus


class HolidayContext(BaseModel):
    """Plan-level provider-backed public holiday context for the trip's
    date range (docs/12_provider_architecture.md section 16,
    docs/14_backend_architecture.md section 10). Built from a real
    HolidayProvider call (Nager.Date) using a country code conservatively
    inferred from the destination -- no LLM, no fuzzy guess. `holidays`
    only ever contains real provider-backed public holidays that fall
    inside the trip's date range; it is never used to infer closures,
    crowds, opening hours, events, festivals, strikes, or risk, and no such
    value is ever invented. If the provider has real data for the relevant
    year(s) but none of it falls inside the trip's date range, `holidays`
    stays empty while `data_status` still reflects a successful/live
    response -- that is reported honestly via `assumptions`, not treated as
    unavailable. If the country can't be inferred, or the provider truly
    has no usable data, `holidays` stays empty and `data_status`/`warnings`
    honestly reflect that instead.
    """

    destination: str
    start_date: date
    end_date: date
    country_code: str | None = None
    holidays: list[Holiday] = Field(default_factory=list)
    source: str | None = None
    data_status: DataStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CurrencyContext(BaseModel):
    """Plan-level provider-backed currency exchange-rate context for the
    trip (docs/12_provider_architecture.md section 17,
    docs/14_backend_architecture.md section 10). Built from a real
    CurrencyProvider call (Frankfurter) using a destination currency
    conservatively inferred from the destination -- no LLM, no fuzzy
    guess. This only ever converts one unit of currency; it does not
    validate or calculate total trip cost, budget fit, hotel prices,
    restaurant prices, attraction prices, fees, tax, or any other cost
    value, and no such value is ever invented. If the destination currency
    equals `base_currency`, this is still built honestly with
    `exchange_rate=1.0` and an assumption explaining no conversion is
    needed. If the destination currency can't be inferred, or the provider
    has no usable rate, `exchange_rate` stays unset and this is reported
    honestly via `data_status`/`warnings` rather than guessed.
    """

    base_currency: str
    destination_currency: str | None = None
    exchange_rate: float | None = None
    rate_date: date | None = None
    source: str | None = None
    data_status: DataStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RouteSegment(BaseModel):
    """One point-to-point route segment between two scheduled experiences,
    a data-model foundation for a future real RoutesProvider
    (docs/12_provider_architecture.md section 12). Every field is
    provider-backed only -- until a routes provider is connected, no
    distance, duration, or travel mode is ever invented, and no segment is
    ever created without real provider data.
    """

    from_place_id: str | None = None
    from_name: str | None = None
    to_place_id: str | None = None
    to_name: str | None = None
    travel_mode: str | None = None
    distance_meters: float | None = None
    duration_minutes: float | None = None
    source: str | None = None
    data_status: DataStatus
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DailyRouteFeasibility(BaseModel):
    """Route/walking feasibility for one scheduled day, a data-model
    foundation for a future real RoutesProvider
    (docs/12_provider_architecture.md section 12). Empty `segments` and
    `data_status=not_connected` mean route feasibility for this day has
    not been checked -- never a straight-line distance, walking time, or
    feasibility score presented as real route data.
    """

    day_number: int = Field(gt=0)
    segments: list[RouteSegment] = Field(default_factory=list)
    data_status: DataStatus
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RouteFeasibilityContext(BaseModel):
    """Plan-level route/walking feasibility context -- a data-model
    foundation only, so the app is ready for a real RoutesProvider later
    (docs/12_provider_architecture.md section 12,
    docs/14_backend_architecture.md section 13). No RoutesProvider is
    connected in this deployment, so `daily_route_feasibility` always stays
    empty: no straight-line distance is ever presented as route distance,
    no walking time is ever calculated, and no travel mode is ever
    inferred. This never marks route times or walking feasibility as
    checked in the readiness checklist, and never affects validation
    readiness by itself.
    """

    source: str | None = None
    data_status: DataStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    daily_route_feasibility: list[DailyRouteFeasibility] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TripStrategy(BaseModel):
    strategy_id: str = Field(default_factory=lambda: _new_id("trip_strategy"))

    destination_suitability: dict[str, Any] = Field(default_factory=dict)
    duration_assessment: dict[str, Any] = Field(default_factory=dict)
    budget_assessment: dict[str, Any] = Field(default_factory=dict)

    recommended_trip_style: str | None = None
    planning_strategy: list[str] = Field(default_factory=list)
    planning_targets: dict[str, Any] = Field(default_factory=dict)

    tradeoffs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class StayArea(BaseModel):
    stay_area_id: str = Field(default_factory=lambda: _new_id("stay_area"))

    name: str
    coordinates: GeoPoint | None = None
    reasons: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    data_quality: DataQuality | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class RatingValue(BaseModel):
    value: float | None = Field(default=None, ge=0.0, le=5.0)
    review_count: int | None = Field(default=None, ge=0)
    data_quality: DataQuality


class AvailabilityValue(BaseModel):
    available: bool | None = None
    data_quality: DataQuality


class UrlValue(BaseModel):
    url: str | None = None
    data_quality: DataQuality


class AccommodationOption(BaseModel):
    accommodation_id: str = Field(default_factory=lambda: _new_id("accommodation"))

    name: str
    accommodation_type: AccommodationType = AccommodationType.UNKNOWN
    area: str | None = None
    coordinates: GeoPoint | None = None

    estimated_price_per_night: MoneyAmount | None = None
    availability_status: AvailabilityValue | None = None
    rating: RatingValue | None = None
    booking_url: UrlValue | None = None

    amenities: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)

    source: str | None = None
    data_quality: DataQuality | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class TransportStrategy(BaseModel):
    transport_strategy_id: str = Field(default_factory=lambda: _new_id("transport_strategy"))

    primary_mode: TransportPreference | None = None
    secondary_modes: list[TransportPreference] = Field(default_factory=list)

    local_transport_summary: str | None = None
    intercity_transport_summary: str | None = None

    rationale: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    provider_notes: list[str] = Field(default_factory=list)

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class StayTransportDecision(BaseModel):
    stay_transport_id: str = Field(default_factory=lambda: _new_id("stay_transport"))

    recommended_stay_area: StayArea | None = None
    alternative_stay_areas: list[StayArea] = Field(default_factory=list)
    accommodation_recommendations: list[AccommodationOption] = Field(default_factory=list)
    transport_strategy: TransportStrategy | None = None

    unavailable_data: list[UnavailableDataItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExperienceItem(BaseModel):
    experience_id: str = Field(default_factory=lambda: _new_id("experience"))

    name: str
    category: str
    coordinates: GeoPoint | None = None

    start_time: str | None = None
    end_time: str | None = None
    estimated_duration_minutes: int | None = Field(default=None, gt=0)

    why_included: str | None = None
    tradeoffs: list[str] = Field(default_factory=list)
    nearby_alternatives: list[str] = Field(default_factory=list)

    data_quality: DataQuality | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class RestaurantOption(BaseModel):
    restaurant_id: str = Field(default_factory=lambda: _new_id("restaurant"))

    name: str
    cuisine_or_category: str | None = None
    coordinates: GeoPoint | None = None

    rating: RatingValue | None = None
    price_level: DataQuality | None = None
    source: str | None = None

    reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class RestaurantSuggestion(BaseModel):
    """A day-level nearby-restaurant suggestion drawn from
    `destination_context.candidate_restaurants` only (docs/14_backend_architecture.md
    section 13). Deliberately carries only fields already present on a
    provider-backed candidate plus `why_suggested` -- no rating, price,
    review, opening-hours, reservation/booking link, availability, route
    time, walking distance, or cost is ever attached here.
    """

    name: str
    category: str | None = None
    coordinates: GeoPoint | None = None
    address: str | None = None
    source: str | None = None
    data_status: DataStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    why_suggested: str


class AccommodationSuggestion(BaseModel):
    """A day-level nearby-accommodation-POI suggestion drawn from
    `destination_context.candidate_accommodation_pois` only
    (docs/14_backend_architecture.md section 13). Deliberately carries only
    fields already present on a provider-backed candidate plus
    `why_suggested` -- no rating, price, review, opening-hours, booking
    link, availability, route time, walking distance, or cost is ever
    attached here. These are open-data location candidates only, never
    bookable inventory.
    """

    name: str
    category: str | None = None
    coordinates: GeoPoint | None = None
    address: str | None = None
    source: str | None = None
    data_status: DataStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    why_suggested: str


class MealPlanItem(BaseModel):
    meal_plan_id: str = Field(default_factory=lambda: _new_id("meal_plan"))

    meal_type: str
    meal_area: str | None = None
    restaurant_recommendation: RestaurantOption | None = None

    is_meal_area_fallback: bool = False
    fallback_reason: str | None = None

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class DailyPlan(BaseModel):
    day_plan_id: str = Field(default_factory=lambda: _new_id("day_plan"))

    day_number: int = Field(gt=0)
    date: date
    theme: str | None = None
    goal: str | None = None

    experiences: list[ExperienceItem] = Field(default_factory=list)
    meal_plan: list[MealPlanItem] = Field(default_factory=list)
    # Up to 2 nearby restaurants per day, drawn only from
    # destination_context.candidate_restaurants and selected by straight-line
    # (haversine) proximity to this day's first coordinate-backed scheduled
    # experience. Never a reservation, rating, price, or route recommendation.
    restaurant_suggestions: list[RestaurantSuggestion] = Field(default_factory=list)
    # Up to 2 nearby accommodation POIs per day, drawn only from
    # destination_context.candidate_accommodation_pois and selected by the
    # same straight-line (haversine) proximity rule. Open-data location
    # candidates only, never bookable inventory, and never a price,
    # availability, rating, booking, or route recommendation.
    accommodation_suggestions: list[AccommodationSuggestion] = Field(default_factory=list)

    estimated_walking_km: float | None = Field(default=None, ge=0.0)
    estimated_travel_time_minutes: int | None = Field(default=None, ge=0)
    estimated_cost: MoneyAmount | None = None

    energy_level: str | None = None
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class StayAreaGuidance(BaseModel):
    """Plan-level (not day-level) stay-area guidance summarizing which
    `destination_context.candidate_accommodation_pois` sit closest, on
    average, to every coordinate-backed scheduled experience across the
    whole plan (docs/14_backend_architecture.md section 13). Built purely
    from existing candidates and already-scheduled experiences -- no
    provider call, no AI/LLM, no invented place. `suggested_anchor_
    accommodation_pois` carries only fields already present on a
    provider-backed candidate plus `why_suggested`; like the day-level
    `AccommodationSuggestion`, it never carries a price, availability,
    rating, review, opening-hours, booking link, route time, walking
    distance, or cost. This guidance never affects validation readiness by
    itself.
    """

    summary: str
    suggested_anchor_accommodation_pois: list[AccommodationSuggestion] = Field(
        default_factory=list
    )
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DecisionSummary(BaseModel):
    """Plan-level decision summary explaining, from existing `PlanningState`/
    `ExperiencePlan`/`DestinationContext` data only, why the plan was built
    this way, what is provider-backed, what is proximity-based only, what is
    still unvalidated, and what the user should review before trusting it
    (docs/14_backend_architecture.md section 13). No provider call, no
    AI/LLM, no invented fact -- purely a restatement of decisions already
    made elsewhere in the pipeline. This never affects validation readiness
    by itself.
    """

    summary: str
    provider_backed_facts: list[str] = Field(default_factory=list)
    proximity_based_decisions: list[str] = Field(default_factory=list)
    unvalidated_items: list[str] = Field(default_factory=list)
    user_review_required: list[str] = Field(default_factory=list)


class ImplementationGaps(BaseModel):
    """Plan-level summary of which providers/data sources are connected now,
    which are missing, what would be needed next, and why the plan still
    needs review -- built purely from `PlanningState.provider_coverage`,
    `PlanningState.provider_status`, `PlanningState.unavailable_data`, and
    already-computed `ExperiencePlan` data (docs/12_provider_architecture.md,
    docs/14_backend_architecture.md section 13). No provider call, no
    AI/LLM, no invented fact. This section explains the readiness gaps that
    already exist elsewhere in the pipeline; it does not create or resolve
    them, and it never affects validation readiness by itself.
    """

    summary: str
    connected_data: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    next_data_needed: list[str] = Field(default_factory=list)
    why_needs_review: list[str] = Field(default_factory=list)


class ReadinessChecklistItem(BaseModel):
    """One user-facing checklist line explaining whether a specific kind of
    validation has actually happened yet. `status` is one of
    `ChecklistItemStatus` (`checked`/`needs_review`/`missing_data`/
    `not_implemented`) and is derived purely from existing `PlanningState`/
    `provider_coverage`/`provider_status`/`unavailable_data`/`ExperiencePlan`
    data -- never from a provider call, AI/LLM, or an invented fact.
    """

    label: str
    status: ChecklistItemStatus
    explanation: str


class ReadinessChecklist(BaseModel):
    """Plan-level checklist explaining what has and has not been validated
    before the user trusts the plan (docs/14_backend_architecture.md section
    13). Built purely from existing `PlanningState`/`provider_coverage`/
    `provider_status`/`unavailable_data`/`ExperiencePlan` data. No provider
    call, no AI/LLM, no invented fact. This never marks the plan ready by
    itself -- `ValidationReport.readiness_status` remains the single source
    of truth for overall readiness.
    """

    summary: str
    items: list[ReadinessChecklistItem] = Field(default_factory=list)


class ExperiencePlan(BaseModel):
    experience_plan_id: str = Field(default_factory=lambda: _new_id("experience_plan"))

    trip_overview: str | None = None
    daily_plans: list[DailyPlan] = Field(default_factory=list)
    stay_area_guidance: StayAreaGuidance = Field(
        default_factory=lambda: StayAreaGuidance(summary="Stay-area guidance not yet computed.")
    )
    decision_summary: DecisionSummary = Field(
        default_factory=lambda: DecisionSummary(summary="Decision summary not yet computed.")
    )
    implementation_gaps: ImplementationGaps = Field(
        default_factory=lambda: ImplementationGaps(
            summary="Implementation gaps not yet computed."
        )
    )
    readiness_checklist: ReadinessChecklist = Field(
        default_factory=lambda: ReadinessChecklist(
            summary="Readiness checklist not yet computed."
        )
    )
    route_feasibility_context: RouteFeasibilityContext = Field(
        default_factory=lambda: RouteFeasibilityContext(
            data_status=DataStatus.NOT_CONNECTED,
            assumptions=["Route feasibility context not yet computed."],
        )
    )

    provider_coverage: ProviderCoverage = Field(default_factory=ProviderCoverage)
    unavailable_data: list[UnavailableDataItem] = Field(default_factory=list)

    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ValidationIssue(BaseModel):
    issue_id: str = Field(default_factory=lambda: _new_id("validation_issue"))

    severity: ValidationSeverity
    category: str
    message: str
    affected_section: str | None = None
    suggested_fix: str | None = None
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class ValidationReport(BaseModel):
    validation_report_id: str = Field(default_factory=lambda: _new_id("validation_report"))

    readiness_status: ReadinessStatus = ReadinessStatus.NEEDS_REVIEW
    overall_score: float | None = Field(default=None, ge=0.0, le=1.0)

    critical_issues: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    suggestions: list[ValidationIssue] = Field(default_factory=list)

    category_scores: dict[str, float] = Field(default_factory=dict)
    provider_coverage_notes: list[str] = Field(default_factory=list)
    unavailable_data_notes: list[str] = Field(default_factory=list)

    validated_at: datetime = Field(default_factory=_utc_now)


class BaseExplanationCard(BaseModel):
    card_id: str = Field(default_factory=lambda: _new_id("card"))

    title: str
    summary: str
    reasons: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    data_sources: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    claim_sources: list[ClaimSource] = Field(default_factory=list)


class DecisionCard(BaseExplanationCard):
    stage: PlanningStage


class ExperienceCard(BaseExplanationCard):
    experience_id: str | None = None


class ValidationCard(BaseExplanationCard):
    severity: ValidationSeverity
    issue_id: str | None = None


class FeedbackEvent(BaseModel):
    feedback_event_id: str = Field(default_factory=lambda: _new_id("feedback"))

    feedback_text: str
    feedback_type: str | None = None
    affected_stages: list[PlanningStage] = Field(default_factory=list)
    regeneration_strategy: RegenerationStrategy

    # Set by an AIReasoningProvider once feedback interpretation is
    # connected (docs/13_llm_reasoning_pipeline.md). Stays None until then --
    # never a guessed interpretation of what the feedback means.
    interpretation: dict[str, Any] | None = None
    # Lifecycle of this feedback event itself, independent of
    # `regeneration_strategy` (e.g. "captured" now; later values like
    # "interpreted"/"applied" once regeneration is implemented).
    handling_status: str = "captured"

    change_summary: dict[str, Any] = Field(default_factory=dict)
    follow_up_question: str | None = None

    created_at: datetime = Field(default_factory=_utc_now)


class UserLock(BaseModel):
    lock_id: str = Field(default_factory=lambda: _new_id("lock"))

    locked_item_type: str
    locked_item_id: str
    reason: str = "user_approved"

    is_active: bool = True
    created_at: datetime = Field(default_factory=_utc_now)
    removed_at: datetime | None = None


class VersionHistoryItem(BaseModel):
    version_id: str = Field(default_factory=lambda: _new_id("version"))

    version_label: str
    created_by: str
    summary: str | None = None

    changed_sections: list[str] = Field(default_factory=list)
    preserved_sections: list[str] = Field(default_factory=list)

    feedback_event_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class PlanningMetadata(BaseModel):
    current_version: str = "v1"
    pipeline_status: PipelineStatus = PipelineStatus.DRAFT
    active_stage: PlanningStage | None = None

    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    extra: dict[str, Any] = Field(default_factory=dict)


class PlanningState(BaseModel):
    planning_state_id: str = Field(default_factory=lambda: _new_id("planning_state"))
    trip_id: str = Field(default_factory=lambda: _new_id("trip"))

    trip_request: TripRequest

    traveler_profile: TravelerProfile | None = None
    destination_context: DestinationContext | None = None
    weather_context: WeatherContext | None = None
    holiday_context: HolidayContext | None = None
    currency_context: CurrencyContext | None = None
    trip_strategy: TripStrategy | None = None
    stay_transport: StayTransportDecision | None = None
    experience_plan: ExperiencePlan | None = None
    validation_report: ValidationReport | None = None

    decision_cards: list[DecisionCard] = Field(default_factory=list)
    experience_cards: list[ExperienceCard] = Field(default_factory=list)
    validation_cards: list[ValidationCard] = Field(default_factory=list)

    feedback_history: list[FeedbackEvent] = Field(default_factory=list)
    user_locks: list[UserLock] = Field(default_factory=list)
    version_history: list[VersionHistoryItem] = Field(default_factory=list)

    provider_status: dict[str, ProviderStatusEntry] = Field(default_factory=dict)
    provider_coverage: ProviderCoverage = Field(default_factory=ProviderCoverage)
    unavailable_data: list[UnavailableDataItem] = Field(default_factory=list)
    data_sources_used: list[str] = Field(default_factory=list)

    metadata: PlanningMetadata = Field(default_factory=PlanningMetadata)

    def touch(self) -> None:
        self.metadata.updated_at = _utc_now()

    def set_active_stage(self, stage: PlanningStage | None) -> None:
        self.metadata.active_stage = stage
        self.touch()

    def set_pipeline_status(self, status: PipelineStatus) -> None:
        self.metadata.pipeline_status = status
        self.touch()