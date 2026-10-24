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


class FlightAvailabilityReason(str, Enum):
    """M2 Phase 1 (D2): WHY a flight result has the offers it has. `status`
    alone cannot say it: `unavailable` covers both "no flight source is set
    up" and "a real search found nothing". Only
    `SEARCH_COMPLETED_NO_OFFERS` means a live search ran and returned zero
    offers."""

    OFFERS_FOUND = "offers_found"
    # No flight source is set up (not connected, switched off, or no local file).
    NO_SOURCE_CONFIGURED = "no_source_configured"
    # A manually supplied local file was read; it held no offer for this trip.
    LOCAL_SOURCE_NO_MATCHING_OFFERS = "local_source_no_matching_offers"
    # A source exists, but this trip's request could not be sent to it
    # (e.g. no origin, or the source does not offer a flight search).
    SEARCH_NOT_POSSIBLE = "search_not_possible"
    # A live search completed and returned zero offers.
    SEARCH_COMPLETED_NO_OFFERS = "search_completed_no_offers"
    # The source answered, but nothing in the answer could be used.
    RESPONSE_UNUSABLE = "response_unusable"
    # The request to the source failed.
    PROVIDER_FAILED = "provider_failed"
    # M2 Phase 2 (Kiwi remediation): a search by place NAME returned zero
    # offers. The source does not say which airports it matched to a name, so
    # this is never reported as "no flights" -- the name may simply not have
    # been matched to an airport.
    LOCATION_UNCONFIRMED = "location_unconfirmed"
    # No searchable airport could be found for a place (the place itself was
    # not found, or no international airport lies within the lookup radius).
    NO_SEARCHABLE_AIRPORT = "no_searchable_airport"


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
    # M2 Phase 2: the destination's city name as the destination geocoder
    # resolved it (no new request), when known. A provider that resolves place
    # names itself (Kiwi MCP) is sent this instead of the trip's free text.
    destination_city: str | None = Field(default=None, max_length=160)

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


class FlightLocationResolution(BaseModel):
    """M2 Phase 2 (Kiwi remediation): how ONE side of a flight search was
    turned into what the flight source was asked -- the provenance of every
    substituted airport. Facts only from the traveller's own input or the
    airport-lookup provider's answer; `straight_line_km` is a straight-line
    distance between two provider coordinates, never a travel distance."""

    role: str = Field(pattern="^(origin|destination)$")
    requested: str = Field(max_length=160)
    searched: str | None = Field(default=None, max_length=160)
    # `airport_code` (the traveller typed a code), `place_name` (sent as a
    # name for the source to resolve), `nearby_airport` (an airport the lookup
    # provider found near the place), or `not_resolved`.
    method: str = Field(pattern="^(airport_code|place_name|nearby_airport|not_resolved)$")
    airport_name: str | None = Field(default=None, max_length=160)
    airport_code: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    straight_line_km: float | None = Field(default=None, ge=0)
    # Where the airport (and its code, when present) came from.
    lookup_source: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=400)
    # M2 Phase 2 (second correction): what makes a substituted airport suitable
    # for an ordinary commercial flight, and why it was chosen. A
    # `nearby_airport` side WITHOUT `commercial_service_evidence` is never a
    # basis for "no offers".
    airport_lat: float | None = Field(default=None, ge=-90, le=90)
    airport_lon: float | None = Field(default=None, ge=-180, le=180)
    airport_type: str | None = Field(default=None, max_length=40)
    airport_country: str | None = Field(default=None, max_length=2)
    commercial_service_evidence: str | None = Field(default=None, max_length=80)
    selection_rationale: str | None = Field(default=None, max_length=400)
    dataset_version: str | None = Field(default=None, max_length=40)


class FlightSearchResult(BaseModel):
    """Normalized response envelope returned by
    `FlightInventoryProvider.search_flights` (Step 169A).

    `offers` may only be non-empty when `status == success` -- a
    `not_connected`/`unavailable`/`failed` result must never carry a
    fabricated or leftover offer alongside its honest failure status.

    `warnings` (Step 185D) is additive, optional per-source detail for
    multi-source flight ingestion (`ScrapedLocalFlightProvider`) -- e.g.
    "google_flights: no local file configured/found" alongside an
    overall `success` result from other sources that did produce real
    offers. Empty by default, so every result built before Step 185D
    (and every single-source result today) stays fully backward
    compatible. Never itself a claim that a source succeeded or failed
    to fabricate data -- purely a human-readable trace of what this call
    actually checked.
    """

    provider: str
    status: FlightSearchStatus
    offers: list[FlightOffer] = Field(default_factory=list)
    message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    searched_at: datetime | None = None
    # M2 Phase 1 (D2): structured cause of this result. None only on a result
    # stored before the field existed (or a status no rule can classify).
    availability_reason: FlightAvailabilityReason | None = None
    # M2 Phase 2: for a PROVIDER_FAILED result, the fixed kind of failure when
    # known (`timeout` / `connection` / `tool_error`). Never exception text.
    failure_kind: str | None = None
    # M2 Phase 2 (Kiwi remediation): per side, what the source was asked and
    # why; how many searches and transport requests this result took. All
    # optional (None on results stored before them).
    location_resolution: list[FlightLocationResolution] | None = None
    search_attempts: int | None = Field(default=None, ge=0)
    provider_http_requests: int | None = Field(default=None, ge=0)

    origin: str | None = None
    destination: str
    departure_date: date
    return_date: date | None = None
    adults: int = Field(default=1, ge=0)
    children: int = Field(default=0, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    def with_reason(self, reason: FlightAvailabilityReason) -> "FlightSearchResult":
        return self.model_copy(update={"availability_reason": reason})

    @model_validator(mode="after")
    def validate_offers_match_status(self) -> "FlightSearchResult":
        if self.status != FlightSearchStatus.SUCCESS and self.offers:
            raise ValueError(
                f"offers must be empty when status is '{self.status.value}' -- "
                "only a 'success' result may carry real, provider-backed offers."
            )
        return self
