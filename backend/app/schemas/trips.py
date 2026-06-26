from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

TravelGroupType = Literal["solo", "couple", "family", "friends", "group"]
TripPace = Literal["relaxed", "balanced", "packed"]
AccommodationType = Literal["hotel", "airbnb", "hostel", "resort", "no_preference"]
TransportPreference = Literal[
    "public_transport",
    "taxi",
    "self_drive",
    "train",
    "flight",
    "no_preference",
]


def _clean_strings(values: list[str]) -> list[str]:
    return [value.strip() for value in values if value and value.strip()]


class TripRequestSchema(BaseModel):
    id: str | None = Field(default=None, description="Optional client trip identifier.")
    destination: str = Field(min_length=1, max_length=120)
    originCity: str | None = Field(default=None, max_length=120)
    startDate: str = Field(min_length=10, max_length=30)
    endDate: str = Field(min_length=10, max_length=30)
    travelersCount: int = Field(gt=0, le=20)
    travelGroupType: TravelGroupType
    budgetMin: int | None = Field(default=None, ge=0)
    budgetMax: int | None = Field(default=None, ge=0)
    pace: TripPace
    accommodationType: AccommodationType
    transportPreference: TransportPreference
    interests: list[str] = Field(default_factory=list)
    mustVisit: list[str] = Field(default_factory=list)
    mustAvoid: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    freeTextPreferences: str | None = Field(default=None, max_length=2000)

    @field_validator("destination", "originCity", "freeTextPreferences", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("interests", "mustVisit", "mustAvoid", "constraints")
    @classmethod
    def clean_string_lists(cls, value: list[str]) -> list[str]:
        return _clean_strings(value)

    @model_validator(mode="after")
    def validate_dates_and_budget(self) -> "TripRequestSchema":
        start = date.fromisoformat(self.startDate)
        end = date.fromisoformat(self.endDate)
        if end < start:
            raise ValueError("endDate must be on or after startDate")
        if (
            self.budgetMin is not None
            and self.budgetMax is not None
            and self.budgetMin > self.budgetMax
        ):
            raise ValueError("budgetMin must be less than or equal to budgetMax")
        return self


class AccommodationRecommendationSchema(BaseModel):
    id: str
    name: str
    type: AccommodationType
    neighborhood: str
    nightlyPrice: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    rating: float = Field(ge=0, le=5)
    bookingUrl: str
    reasons: list[str] = Field(default_factory=list)
    amenities: list[str] = Field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    isMockData: bool = True


class TravelFromPreviousSchema(BaseModel):
    mode: str
    timeMinutes: int = Field(ge=0)
    distanceKm: float | None = Field(default=None, ge=0)


class ItineraryActivitySchema(BaseModel):
    id: str
    name: str
    category: str
    startTime: str
    endTime: str
    durationMinutes: int = Field(gt=0)
    latitude: float
    longitude: float
    whyIncluded: str
    travelFromPrevious: TravelFromPreviousSchema | None = None


class ItineraryDaySchema(BaseModel):
    dayNumber: int = Field(gt=0)
    date: str | None = None
    baseCity: str
    theme: str
    activities: list[ItineraryActivitySchema] = Field(default_factory=list)
    foodSuggestions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    totalTravelTimeMinutes: int = Field(ge=0)
    estimatedCost: float = Field(ge=0)


class TransportStrategySchema(BaseModel):
    localTransport: str
    intercityTransport: str | None = None
    rationale: list[str] = Field(default_factory=list)


class BudgetBreakdownSchema(BaseModel):
    stay: float = Field(ge=0)
    food: float = Field(ge=0)
    transport: float = Field(ge=0)
    activities: float = Field(ge=0)
    misc: float = Field(ge=0)
    total: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class TripSummarySchema(BaseModel):
    destination: str
    durationDays: int = Field(gt=0)
    travelStyle: str
    summaryText: str


class StayRecommendationSchema(BaseModel):
    recommendedArea: str
    reasons: list[str] = Field(default_factory=list)
    topAccommodations: list[AccommodationRecommendationSchema] = Field(
        default_factory=list
    )


class ItinerarySchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    tripRequestId: str | None = None
    versionNumber: int = Field(default=1, ge=1)
    tripSummary: TripSummarySchema
    stayRecommendation: StayRecommendationSchema
    transportStrategy: TransportStrategySchema
    dailyPlan: list[ItineraryDaySchema] = Field(default_factory=list)
    importantTips: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    estimatedBudgetBreakdown: BudgetBreakdownSchema
    isMockData: bool = True


class TripGenerationMetadataSchema(BaseModel):
    generatedAt: str
    source: str = Field(default="mock_demo")
    warning: str = Field(default="Demo itinerary generated from mock providers.")


class TripGenerationResponseSchema(BaseModel):
    tripRequest: TripRequestSchema
    itinerary: ItinerarySchema
    metadata: TripGenerationMetadataSchema
