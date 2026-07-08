from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DestinationScope(str, Enum):
    SINGLE_CITY = "single_city"
    MULTI_CITY_FUTURE = "multi_city_future"


class DataStatus(str, Enum):
    LIVE = "live"
    CACHED = "cached"
    FALLBACK_USED = "fallback_used"
    ESTIMATED = "estimated"
    SCHEDULED = "scheduled"
    USER_PROVIDED = "user_provided"
    AI_INFERRED = "ai_inferred"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    NOT_CONNECTED = "not_connected"


class ProviderStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    SUCCESS = "success"
    RETRYING = "retrying"
    FALLBACK_USED = "fallback_used"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    NOT_CONNECTED = "not_connected"


class ClaimSourceType(str, Enum):
    PROVIDER_FACT = "provider_fact"
    OPEN_DATA_FACT = "open_data_fact"
    USER_INPUT = "user_input"
    SYSTEM_RULE = "system_rule"
    AI_INFERENCE = "ai_inference"
    ASSUMPTION = "assumption"
    UNAVAILABLE_DATA = "unavailable_data"


class ReadinessStatus(str, Enum):
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


class ValidationSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    SUGGESTION = "suggestion"


class RegenerationStrategy(str, Enum):
    EXPLANATION_ONLY = "explanation_only"
    SECTION_LEVEL_UPDATE = "section_level_update"
    DAY_LEVEL_UPDATE = "day_level_update"
    PIPELINE_LEVEL_UPDATE = "pipeline_level_update"
    FULL_REGENERATION = "full_regeneration"


class AccommodationType(str, Enum):
    HOTEL = "hotel"
    MOTEL = "motel"
    HOSTEL = "hostel"
    RESORT = "resort"
    SERVICED_APARTMENT = "serviced_apartment"
    VACATION_RENTAL = "vacation_rental"
    GUESTHOUSE = "guesthouse"
    BOUTIQUE_STAY = "boutique_stay"
    AIRBNB_STYLE = "airbnb_style"
    UNKNOWN = "unknown"


class RecommendationType(str, Enum):
    ATTRACTION = "attraction"
    RESTAURANT = "restaurant"
    MEAL_AREA = "meal_area"
    ACCOMMODATION = "accommodation"
    TRANSPORT = "transport"
    STAY_AREA = "stay_area"
    ROUTE = "route"
    WARNING = "warning"
    SUMMARY = "summary"


class SourceAttribution(BaseModel):
    source_name: str | None = None
    source_type: ClaimSourceType
    source_url: str | None = None
    retrieved_at: str | None = None
    license: str | None = None


class DataQuality(BaseModel):
    data_status: DataStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: SourceAttribution | None = None
    unavailable_reason: str | None = None


class GeoPoint(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)


class MoneyAmount(BaseModel):
    amount: float | None = Field(default=None, ge=0.0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    data_quality: DataQuality


class TimeWindow(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    data_quality: DataQuality | None = None


class ClaimSource(BaseModel):
    claim: str
    source_type: ClaimSourceType
    source: str | None = None
    based_on: list[str] = Field(default_factory=list)


class ProviderStatusEntry(BaseModel):
    provider_name: str
    provider_type: str
    status: ProviderStatus
    data_status: DataStatus
    fallback_used: bool = False
    fallback_provider: str | None = None
    unavailable_fields: list[str] = Field(default_factory=list)
    error_message: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    retrieved_at: str | None = None


class ProviderCoverage(BaseModel):
    places: str | None = None
    routes: str | None = None
    restaurants: str | None = None
    accommodations: str | None = None
    hotel_prices: str | None = None
    vacation_rentals: str | None = None
    airbnb: str | None = None
    flights: str | None = None
    weather: str | None = None
    holidays: str | None = None
    currency: str | None = None


class UnavailableDataItem(BaseModel):
    field: str
    reason: str
    data_status: DataStatus
    source: str | None = None


class FlexibleMetadata(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)