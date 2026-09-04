from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

from app.models.common import DataStatus
from app.models.flight import (
    FlightOffer,
    FlightSearchRequest,
    FlightSearchResult,
    FlightSearchStatus,
    FlightSegment,
)
from app.models.scraping import (
    ScrapedDataConfidence,
    ScrapedDataProvenance,
    ScrapingExtractionMethod,
    ScrapingSourcePolicy,
)

logger = logging.getLogger(__name__)

# Static HTML parser framework for approved scraped flight sources (Step
# 169C, docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md), mirroring the accommodation parser
# (`app.providers.accommodation.scraped_parser.parse_scraped_accommodation_html`,
# Step 168B).
#
# This module only transforms an already-provided HTML string into a
# normalized `FlightSearchResult` -- it never fetches a URL, never opens a
# socket, never drives a browser, and never imports httpx/requests/a
# browser-automation library. `FlightSearchRequest`/`source_url` are
# accepted only as metadata to label the result; they are never used to
# construct or follow a live request.
#
# Not wired into `ScrapedLocalFlightProvider`, `ProviderGateway`, or
# `PlanningOrchestrator` yet -- this is a standalone parsing function a
# future provider step (169D) could call after it has already read a
# page's HTML through its own, separately-gated local-file-read path
# (`ScrapedLocalFlightProvider` today only checks file existence; it does
# not call this parser).
#
# Expected HTML micro-format (test fixtures only -- no real site is known
# to use this exact shape): a "flight offer" is any element carrying the
# class `flight-offer` and a `data-offer-id` attribute. Inside it:
#
#   outbound-segment (repeatable)  -- required at least once; one leg of
#                                     the outbound journey
#   return-segment (repeatable)    -- optional; one leg of the return
#                                     journey
#
# Inside each segment, fields are recognized by class name on a plain
# leaf element:
#
#   origin-airport        -- optional; text content is the origin airport
#   destination-airport    -- optional; text content is the destination
#                             airport
#   departure-time         -- optional; text content is an ISO-8601
#                             departure timestamp
#   arrival-time            -- optional; text content is an ISO-8601
#                             arrival timestamp
#   carrier-name            -- optional; text content is the carrier name
#   carrier-code             -- optional; text content is the carrier code
#   flight-number            -- optional; text content is the flight number
#   duration-minutes          -- optional; text content is an integer
#                             number of minutes
#
# Directly inside the offer (not inside a segment), fields are recognized
# the same way:
#
#   total-price              -- optional; text content is the total price,
#                             optional `data-currency="USD"` attribute
#   currency                 -- optional; text content is a 3-letter
#                             currency code (used when `total-price` has
#                             no `data-currency` attribute of its own)
#   availability-status       -- optional; free-form text content
#   baggage-policy            -- optional; free-form text content
#   cancellation-policy        -- optional; free-form text content
#   booking-link (an `<a href="...">`) -- optional; its `href` is the
#                             booking URL
#
# Any field not present in the HTML stays `None`/empty on the resulting
# `FlightOffer`/`FlightSegment` -- never guessed, estimated, or
# backfilled.


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_CARD_CLASS = "flight-offer"
_OUTBOUND_SEGMENT_CLASS = "outbound-segment"
_RETURN_SEGMENT_CLASS = "return-segment"

_SEGMENT_FIELD_CLASS_TO_KEY: dict[str, str] = {
    "origin-airport": "origin_airport",
    "destination-airport": "destination_airport",
    "departure-time": "departure_time_text",
    "arrival-time": "arrival_time_text",
    "carrier-name": "carrier_name",
    "carrier-code": "carrier_code",
    "flight-number": "flight_number",
    "duration-minutes": "duration_minutes_text",
}

_OFFER_FIELD_CLASS_TO_KEY: dict[str, str] = {
    "total-price": "total_price_text",
    "currency": "currency",
    "availability-status": "availability_status",
    "baggage-policy": "baggage_policy",
    "cancellation-policy": "cancellation_policy",
}


class _FlightOfferHTMLParser(HTMLParser):
    """Extracts a flat list of raw flight-offer dicts from static HTML
    using the fixed micro-format documented above. Uses only the Python
    standard library `html.parser.HTMLParser` -- no BeautifulSoup, no
    external dependency. Never executes a `<script>`, never renders
    anything, never follows a link -- this walks the parsed tag/attribute/
    text stream exactly once and never makes any network call.

    Tracks two nesting levels below the offer `<div>`: an optional
    "current segment" (outbound or return) and, within either the offer
    or the current segment, an optional "current field" -- mirroring
    `app.providers.accommodation.scraped_parser._PropertyCardHTMLParser`,
    extended by one level for outbound/return segments.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, Any]] = []
        self._card: dict[str, Any] | None = None
        self._card_open_tags = 0
        self._segment: dict[str, Any] | None = None
        self._segment_start_depth = 0
        self._field_key: str | None = None
        self._field_target: dict[str, Any] | None = None
        self._field_attrs: dict[str, str | None] = {}
        self._field_depth = 0
        self._text_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        if self._card is None:
            if _CARD_CLASS in classes:
                self._card = {
                    "offer_id": attr_dict.get("data-offer-id"),
                    "fields": {},
                    "outbound_segments": [],
                    "return_segments": [],
                }
                self._card_open_tags = 0
            return

        self._card_open_tags += 1

        if self._field_key is not None:
            # Fixtures don't nest one recognized field inside another --
            # a nested tag inside an already-open field is just part of
            # that field's own markup and is ignored here.
            return

        if self._segment is not None:
            matched_field = next(
                (
                    key
                    for class_name, key in _SEGMENT_FIELD_CLASS_TO_KEY.items()
                    if class_name in classes
                ),
                None,
            )
            if matched_field is not None:
                self._field_key = matched_field
                self._field_target = self._segment["fields"]
                self._field_attrs = attr_dict
                self._field_depth = self._card_open_tags
                self._text_buffer = []
            return

        if _OUTBOUND_SEGMENT_CLASS in classes or _RETURN_SEGMENT_CLASS in classes:
            segment_type = "outbound" if _OUTBOUND_SEGMENT_CLASS in classes else "return"
            self._segment = {"type": segment_type, "fields": {}}
            self._segment_start_depth = self._card_open_tags
            return

        if "booking-link" in classes:
            self._card["fields"]["booking_url"] = attr_dict.get("href")
            return

        matched_field = next(
            (
                key
                for class_name, key in _OFFER_FIELD_CLASS_TO_KEY.items()
                if class_name in classes
            ),
            None,
        )
        if matched_field is not None:
            self._field_key = matched_field
            self._field_target = self._card["fields"]
            self._field_attrs = attr_dict
            self._field_depth = self._card_open_tags
            self._text_buffer = []

    def handle_data(self, data: str) -> None:
        if self._field_key is not None:
            self._text_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return

        if self._card_open_tags == 0:
            self.cards.append(self._card)
            self._card = None
            self._segment = None
            return

        if self._field_key is not None and self._card_open_tags == self._field_depth:
            text = "".join(self._text_buffer).strip()
            if text and self._field_target is not None:
                self._field_target[self._field_key] = text
                if self._field_key == "total_price_text":
                    currency_attr = self._field_attrs.get("data-currency")
                    if currency_attr:
                        self._field_target.setdefault("price_currency_attr", currency_attr)
            self._field_key = None
            self._field_target = None
            self._field_attrs = {}
            self._text_buffer = []
        elif self._segment is not None and self._card_open_tags == self._segment_start_depth:
            if self._segment["type"] == "outbound":
                self._card["outbound_segments"].append(self._segment)
            else:
                self._card["return_segments"].append(self._segment)
            self._segment = None

        self._card_open_tags -= 1


def _parse_optional_int(text: str | None) -> int | None:
    """Parses a plain non-negative integer string, or returns `None` for
    anything missing/unparseable -- never guesses a number."""
    if text is None:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        value = int(cleaned)
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_optional_decimal(text: str | None) -> Decimal | None:
    """Parses a plain numeric string, or returns `None` for anything
    missing/unparseable -- never guesses a price. Strips common currency
    symbols and thousands separators only; never invents a digit that
    wasn't present in the source text.
    """
    if text is None:
        return None
    cleaned = text.strip().lstrip("$€£").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return value if value >= 0 else None


def _parse_optional_datetime(text: str | None) -> datetime | None:
    """Parses an ISO-8601 timestamp string, or returns `None` for anything
    missing/unparseable -- never guesses a departure/arrival time."""
    if text is None:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None


def _refusal_reason(source_policy: ScrapingSourcePolicy) -> str | None:
    """Returns a safe, honest reason this source will not be parsed, or
    `None` if parsing may proceed. Checked before any HTML is touched.
    """
    if source_policy.is_unsafe:
        return (
            f"Scraping source '{source_policy.source_id}' is unsafe "
            "(requires_login, paywalled, or captcha_expected is True, or it is "
            "not approved_for_personal_use) and will not be parsed."
        )
    if not source_policy.enabled:
        return f"Scraping source '{source_policy.source_id}' is not enabled."
    if not source_policy.allows_flights:
        return f"Scraping source '{source_policy.source_id}' does not allow flight data."
    return None


def _build_segment(raw_segment: dict[str, Any]) -> FlightSegment:
    fields: dict[str, Any] = raw_segment["fields"]
    return FlightSegment(
        origin_airport=fields.get("origin_airport"),
        destination_airport=fields.get("destination_airport"),
        departure_time=_parse_optional_datetime(fields.get("departure_time_text")),
        arrival_time=_parse_optional_datetime(fields.get("arrival_time_text")),
        carrier_name=fields.get("carrier_name"),
        carrier_code=fields.get("carrier_code"),
        flight_number=fields.get("flight_number"),
        duration_minutes=_parse_optional_int(fields.get("duration_minutes_text")),
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
    )


def _build_offer_from_card(
    raw_card: dict[str, Any],
    source_policy: ScrapingSourcePolicy,
    source_url: str | None,
    parser_version: str,
    fetched_at: datetime,
) -> FlightOffer | None:
    offer_id = raw_card.get("offer_id")
    raw_outbound_segments = raw_card.get("outbound_segments", [])
    if not offer_id or not raw_outbound_segments:
        # Not enough to safely identify an offer -- skip this card
        # entirely rather than inventing an id or a segment.
        return None

    fields: dict[str, Any] = raw_card["fields"]

    total_price_amount = _parse_optional_decimal(fields.get("total_price_text"))
    currency = (
        fields.get("currency") or fields.get("price_currency_attr")
        if total_price_amount is not None
        else None
    )

    provenance = ScrapedDataProvenance(
        source_id=source_policy.source_id,
        source_name=source_policy.source_name,
        confidence=ScrapedDataConfidence.EXPERIMENTAL,
        fetched_at=fetched_at,
        parser_version=parser_version,
        source_url=source_url,
        extraction_method=ScrapingExtractionMethod.STATIC_HTML_PARSER,
    )

    return FlightOffer(
        offer_id=offer_id,
        provider=f"scraped:{source_policy.source_id}",
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
        outbound_segments=[_build_segment(segment) for segment in raw_outbound_segments],
        return_segments=[
            _build_segment(segment) for segment in raw_card.get("return_segments", [])
        ],
        total_price_amount=total_price_amount,
        currency=currency,
        booking_url=fields.get("booking_url") or None,
        availability_status=fields.get("availability_status"),
        baggage_policy=fields.get("baggage_policy"),
        cancellation_policy=fields.get("cancellation_policy"),
        source_name=source_policy.source_name,
        source_url=source_url,
        fetched_at=fetched_at,
        scraped_provenance=provenance,
    )


def _empty_result(
    provider_name: str,
    status: FlightSearchStatus,
    message: str,
    request: FlightSearchRequest,
    searched_at: datetime | None = None,
) -> FlightSearchResult:
    return FlightSearchResult(
        provider=provider_name,
        status=status,
        offers=[],
        message=message,
        searched_at=searched_at,
        origin=request.origin,
        destination=request.destination,
        departure_date=request.departure_date,
        return_date=request.return_date,
        adults=request.adults,
        children=request.children,
        currency=request.currency,
    )


def parse_scraped_flight_html(
    html: str,
    source_policy: ScrapingSourcePolicy,
    request: FlightSearchRequest,
    source_url: str | None = None,
    parser_version: str = "unknown",
) -> FlightSearchResult:
    """Transforms an already-provided static HTML string into a normalized
    `FlightSearchResult`, honoring `source_policy`'s safety rules.

    Never fetches `source_url` or anything else -- `html` must already be
    the full page content the caller obtained through its own, separately
    gated fetch path (none exists in this codebase yet). `request` is used
    only to label the result's context; its fields are never used to
    construct or follow a live request either.

    Refuses to parse (returns `status=not_connected`, `offers=[]`) when
    `source_policy` is unsafe, not enabled, or does not allow flight data
    -- the HTML is never even touched in that case. Returns
    `status=unavailable` when the HTML is valid but no flight offer is
    found (or every candidate offer lacks an `offer_id`/outbound segment),
    and `status=failed` (never raising) if parsing/normalizing the HTML
    fails unexpectedly -- e.g. a field value that fails `FlightOffer`'s
    own validation. Every returned offer carries a `scraped_provenance`
    labeling it `scraped_public_page`/`experimental`, with
    `official_provider` structurally fixed to `False`.
    """
    provider_name = f"scraped:{source_policy.source_id}"

    refusal_message = _refusal_reason(source_policy)
    if refusal_message is not None:
        return _empty_result(provider_name, FlightSearchStatus.NOT_CONNECTED, refusal_message, request)

    try:
        card_parser = _FlightOfferHTMLParser()
        card_parser.feed(html)
        card_parser.close()

        fetched_at = _utc_now()
        offers = [
            offer
            for raw_card in card_parser.cards
            if (
                offer := _build_offer_from_card(
                    raw_card, source_policy, source_url, parser_version, fetched_at
                )
            )
            is not None
        ]
    except Exception:
        logger.warning(
            "parse_scraped_flight_html failed unexpectedly for source '%s'; "
            "returning a failed result so callers never crash on malformed HTML.",
            source_policy.source_id,
            exc_info=True,
        )
        return _empty_result(
            provider_name,
            FlightSearchStatus.FAILED,
            "The scraped HTML could not be parsed.",
            request,
        )

    if not offers:
        return _empty_result(
            provider_name,
            FlightSearchStatus.UNAVAILABLE,
            f"No valid flight offers were found in the HTML from '{source_policy.source_name}'.",
            request,
            searched_at=fetched_at,
        )

    return FlightSearchResult(
        provider=provider_name,
        status=FlightSearchStatus.SUCCESS,
        offers=offers,
        message=(
            f"Parsed from '{source_policy.source_name}' -- a scraped public page. "
            "This is experimental/fragile, non-official-provider data; treat it "
            "as unverified."
        ),
        searched_at=fetched_at,
        origin=request.origin,
        destination=request.destination,
        departure_date=request.departure_date,
        return_date=request.return_date,
        adults=request.adults,
        children=request.children,
        currency=request.currency,
    )
