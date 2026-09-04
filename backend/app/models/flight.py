from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.models.common import DataStatus
from app.models.scraping import ScrapedDataProvenance

# Contract models for the flight inventory provider foundation (Step 169A,
# docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). This is a contract only:
#
# - No adapter here is wired into `ProviderGateway`, `PlanningOrchestrator`,
#   `TripStrategyService`, `StayTransportService`, or `PlanValidatorService`
#   yet.
# - No real Amadeus/Duffel/Kiwi/Google Flights (or any other) flight
#   provider is connected, called, or scraped by this step.
# - No live website is fetched and no flight scraper exists -- mirroring
#   `app.models.accommodation`/`app.models.scraping`, a future scraped
#   flight offer must be labeled `scraped_public_page`/`experimental`/
#   `fragile` via `scraped_provenance`, exactly like `AccommodationOffer`.
# - Nothing in the app currently constructs a `FlightSearchRequest` or
#   consumes a `FlightSearchResult` outside this subsystem's own tests.
#
# Every optional fact field on `FlightSegment`/`FlightOffer` stays `None`
# (never a guessed/default value) unless a real future flight provider
# adapter actually returned it -- no default fake airline, airport, flight
# number, price, availability, baggage policy, cancellation policy, or
# booking link is ever created here.


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FlightSearchStatus(str, Enum):
    SUCCESS = "success"
    NOT_CONNECTED = "not_connected"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class FlightSearchRequest(BaseModel):
    """A single flight inventory search request. `origin` is optional
    (some future providers/flows may search by destination + dates only);
    `destination` and `departure_date` are always required.
    """

    origin: str | None = Field(default=None, max_length=160)
    destination: str = Field(min_length=1, max_length=160)
    departure_date: date
    return_date: date | None = None
    adults: int = Field(default=1, ge=0)
    children: int = Field(default=0, ge=0)
    cabin_class: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    trip_id: str | None = None

    @model_validator(mode="after")
    def validate_return_date(self) -> "FlightSearchRequest":
        if self.return_date is not None and self.return_date < self.departure_date:
            raise ValueError("return_date cannot be before departure_date")
        return self


class FlightSegment(BaseModel):
    """One real or future-provider-backed flight leg. Every field besides
    `data_status` is optional and stays `None` unless a real adapter
    actually returned it -- there is no default airline, airport, flight
    number, or scheduled time here.
    """

    origin_airport: str | None = None
    destination_airport: str | None = None
    departure_time: datetime | None = None
    arrival_time: datetime | None = None
    carrier_name: str | None = None
    carrier_code: str | None = None
    flight_number: str | None = None
    duration_minutes: int | None = Field(default=None, ge=0)
    data_status: DataStatus


class FlightOffer(BaseModel):
    """Normalized, provider-backed flight offer returned by a
    `FlightInventoryProvider` adapter (Step 169A). Only fields the
    underlying source actually returned are populated -- `total_price_
    amount`, `booking_url`, `availability_status`, `baggage_policy`, and
    `cancellation_policy` all stay at their honest empty/`None` default
    unless a real adapter sets them; none is ever guessed, estimated, or
    backfilled.

    `scraped_provenance` mirrors `AccommodationOffer.scraped_provenance`
    (docs/12_provider_architecture.md section 47): populated only for an
    offer built by a static HTML parser from an explicitly-approved public
    page, never for a real booking-API-backed offer. When set, `data_status`
    must be `DataStatus.SCRAPED_PUBLIC_PAGE` -- an offer can never carry
    scraped provenance while claiming a `live`/`cached`/other
    official-looking `data_status`, and `scraped_provenance.
    official_provider` is itself structurally fixed to `False` (see
    `ScrapedDataProvenance`).
    """

    offer_id: str = Field(min_length=1)
    provider: str
    data_status: DataStatus

    outbound_segments: list[FlightSegment]
    return_segments: list[FlightSegment] = Field(default_factory=list)

    total_price_amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    booking_url: str | None = None
    availability_status: str | None = None
    baggage_policy: str | None = None
    cancellation_policy: str | None = None

    source_name: str | None = None
    source_url: str | None = None
    fetched_at: datetime = Field(default_factory=_utc_now)
    scraped_provenance: ScrapedDataProvenance | None = None

    @model_validator(mode="after")
    def validate_scraped_provenance_consistency(self) -> "FlightOffer":
        if (
            self.scraped_provenance is not None
            and self.data_status != DataStatus.SCRAPED_PUBLIC_PAGE
        ):
            raise ValueError(
                "FlightOffer.scraped_provenance can only be set when data_status "
                "is DataStatus.SCRAPED_PUBLIC_PAGE -- scraped data must never be "
                "presented under an official-looking data_status."
            )
        return self


class FlightSearchResult(BaseModel):
    """Normalized response envelope returned by
    `FlightInventoryProvider.search_flights` (Step 169A).

    `offers` may only be non-empty when `status == success` -- a
    `not_connected`/`unavailable`/`failed` result must never carry a
    fabricated or leftover offer alongside its honest failure status.
    """

    provider: str
    status: FlightSearchStatus
    offers: list[FlightOffer] = Field(default_factory=list)
    message: str | None = None
    searched_at: datetime | None = None

    origin: str | None = None
    destination: str
    departure_date: date
    return_date: date | None = None
    adults: int = Field(default=1, ge=0)
    children: int = Field(default=0, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_offers_match_status(self) -> "FlightSearchResult":
        if self.status != FlightSearchStatus.SUCCESS and self.offers:
            raise ValueError(
                f"offers must be empty when status is '{self.status.value}' -- "
                "only a 'success' result may carry real, provider-backed offers."
            )
        return self
