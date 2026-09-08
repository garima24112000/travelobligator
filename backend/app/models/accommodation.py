from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.models.common import DataStatus
from app.models.hotel_ratings import AccommodationRating, HotelRatingsStatus
from app.models.scraping import ScrapedDataProvenance


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

    `scraped_provenance` (Step 168B, docs/12_provider_architecture.md
    section 47) is populated only for an offer built by a static HTML
    parser from an explicitly-approved public page, never for a real
    booking-API-backed offer. When set, `data_status` must be
    `DataStatus.SCRAPED_PUBLIC_PAGE` -- an offer can never carry scraped
    provenance while claiming a `live`/`cached`/other official-looking
    `data_status`, and `scraped_provenance.official_provider` is itself
    structurally fixed to `False` (see `ScrapedDataProvenance`).

    `rating_details` (Step 177B) is a separate, optional, richer rating
    snapshot (`AccommodationRating`: bounded 0-5 value, review count,
    provider/source, data status, retrieval time) for a future hotel
    ratings provider to populate -- distinct from the pre-existing bare
    `rating: float | None` field above, which this step leaves completely
    unchanged (no upper bound added, no renaming, still populated only by
    `ScrapedAccommodationProvider`'s free-form HTML parsing). This step
    never auto-copies `rating` into `rating_details`, and never
    constructs a non-`None` `rating_details` anywhere in this codebase --
    it stays `None` until a real hotel ratings provider adapter exists.
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
    rating_details: AccommodationRating | None = None
    amenities: list[str] = Field(default_factory=list)
    cancellation_policy: str | None = None

    data_status: DataStatus
    source_name: str | None = None
    source_url: str | None = None
    fetched_at: datetime = Field(default_factory=_utc_now)
    scraped_provenance: ScrapedDataProvenance | None = None

    @model_validator(mode="after")
    def validate_scraped_provenance_consistency(self) -> "AccommodationOffer":
        if (
            self.scraped_provenance is not None
            and self.data_status != DataStatus.SCRAPED_PUBLIC_PAGE
        ):
            raise ValueError(
                "AccommodationOffer.scraped_provenance can only be set when "
                "data_status is DataStatus.SCRAPED_PUBLIC_PAGE -- scraped data "
                "must never be presented under an official-looking data_status."
            )
        return self


class AccommodationSearchResult(BaseModel):
    """Normalized response envelope returned by
    `AccommodationInventoryProvider.search_accommodations` (Step 167A).

    `offers` may only be non-empty when `status == success` -- a
    `not_connected`/`unavailable`/`failed` result must never carry a
    fabricated or leftover offer alongside its honest failure status.

    `hotel_ratings_*` fields (Step 177C) are optional, additive metadata
    describing the separate hotel-ratings enrichment pass
    (`HotelRatingEnrichmentService`) that may run *after* this result is
    first built -- they say nothing about the base inventory lookup
    itself. All default to `None`/`0`, so every result built before Step
    177C (and every result for which enrichment never ran, e.g. an empty/
    non-success base result) stays fully backward compatible.
    `hotel_ratings_enriched_offer_count` counts only offers that actually
    received a conservatively-matched `rating_details` -- never a raw
    "items returned" count, which could include unmatched/ambiguous
    items that were correctly ignored.
    """

    provider: str
    status: AccommodationSearchStatus
    offers: list[AccommodationOffer] = Field(default_factory=list)
    message: str | None = None
    generated_at: datetime = Field(default_factory=_utc_now)

    hotel_ratings_status: HotelRatingsStatus | None = None
    hotel_ratings_provider: str | None = None
    hotel_ratings_message: str | None = None
    hotel_ratings_enriched_offer_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_offers_match_status(self) -> "AccommodationSearchResult":
        if self.status != AccommodationSearchStatus.SUCCESS and self.offers:
            raise ValueError(
                f"offers must be empty when status is '{self.status.value}' -- "
                "only a 'success' result may carry real, provider-backed offers."
            )
        return self
