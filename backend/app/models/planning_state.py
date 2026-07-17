from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.common import (
    AccommodationType,
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