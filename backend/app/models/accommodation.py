from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.models.common import DataStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Contract models for the accommodation inventory provider foundation (Step
# 167A, docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). This is a contract only:
#
# - No adapter here is wired into `ProviderGateway`, `PlanningOrchestrator`,
#   `StayTransportService`, `PlanValidatorService`, or `ProviderCoverage`
#   yet.
# - No real Booking/Expedia/Hotelbeds/Hostelworld/Amadeus/Vrbo/Airbnb (or
#   any other) lodging provider is connected, called, or scraped by this
#   step.
# - Nothing in the app currently constructs an `AccommodationSearchRequest`
#   or consumes an `AccommodationSearchResult` outside this subsystem's own
#   tests.
#
# `AccommodationOffer` is deliberately a separate, bookable-inventory
# concept from `DestinationContext.candidate_accommodation_pois` (open-data
# OSM location candidates, see `planning_state.AccommodationSuggestion`'s
# own docstring) -- an OSM accommodation POI is a real place that exists,
# but it is never a price, an availability window, a rating, or a booking
# link, and it must never be presented as one. Every optional fact field on
# `AccommodationOffer` stays `None` (never a guessed/default value) unless a
# real lodging provider adapter actually returned it.


class AccommodationAvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    NOT_CONNECTED = "not_connected"


class AccommodationSearchStatus(str, Enum):
    SUCCESS = "success"
    NOT_CONNECTED = "not_connected"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class AccommodationSearchRequest(BaseModel):
    """A single accommodation inventory search request. Coordinates
    (`latitude`/`longitude`) are optional and, when given, follow the same
    lat/lon bounds as `GeoPoint`/`RouteRequest` so an out-of-range value is
    rejected by validation rather than silently accepted.
    """

    destination: str = Field(min_length=1, max_length=160)
    check_in_date: date
    check_out_date: date
    adults: int = Field(gt=0)
    children: int | None = Field(default=None, ge=0)
    rooms: int = Field(gt=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    radius_meters: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def validate_dates(self) -> "AccommodationSearchRequest":
        if self.check_out_date <= self.check_in_date:
            raise ValueError("check_out_date must be after check_in_date")
        return self


class AccommodationOffer(BaseModel):
    """Normalized, provider-backed lodging offer returned by an
    `AccommodationInventoryProvider` adapter (Step 167A). Only fields the
    underlying source actually returned are populated -- `nightly_price_
    amount`, `total_price_amount`, `rating`, `booking_url`, `amenities`,
    and `cancellation_policy` all stay at their honest empty/`None`
    default unless a real adapter sets them; none is ever guessed,
    estimated, or backfilled from an OSM accommodation POI.
    """

    provider: str
    provider_property_id: str
    property_name: str

    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    address: str | None = None

    nightly_price_amount: float | None = Field(default=None, ge=0.0)
    total_price_amount: float | None = Field(default=None, ge=0.0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    availability_status: AccommodationAvailabilityStatus = (
        AccommodationAvailabilityStatus.UNKNOWN
    )
    booking_url: str | None = None
    rating: float | None = Field(default=None, ge=0.0)
    amenities: list[str] = Field(default_factory=list)
    cancellation_policy: str | None = None

    data_status: DataStatus
    source_name: str | None = None
    source_url: str | None = None
    fetched_at: datetime = Field(default_factory=_utc_now)


class AccommodationSearchResult(BaseModel):
    """Normalized response envelope returned by
    `AccommodationInventoryProvider.search_accommodations` (Step 167A).

    `offers` may only be non-empty when `status == success` -- a
    `not_connected`/`unavailable`/`failed` result must never carry a
    fabricated or leftover offer alongside its honest failure status.
    """

    provider: str
    status: AccommodationSearchStatus
    offers: list[AccommodationOffer] = Field(default_factory=list)
    message: str | None = None
    generated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_offers_match_status(self) -> "AccommodationSearchResult":
        if self.status != AccommodationSearchStatus.SUCCESS and self.offers:
            raise ValueError(
                f"offers must be empty when status is '{self.status.value}' -- "
                "only a 'success' result may carry real, provider-backed offers."
            )
        return self
