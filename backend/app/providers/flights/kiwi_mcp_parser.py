from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.common import DataStatus
from app.models.flight import (
    FlightOffer,
    FlightSearchRequest,
    FlightSearchResult,
    FlightSearchStatus,
    FlightSegment,
)
from app.providers.flights.kiwi_mcp_client import KiwiMcpToolCallResult, KiwiMcpToolCallStatus

logger = logging.getLogger(__name__)

# Deterministic Kiwi MCP response parser (Step 178C,
# docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md).
#
# This module only transforms an already-received `KiwiMcpToolCallResult`
# (specifically its `structured_content` JSON payload -- never the
# free-form `content` text blocks a tool may also return, which are meant
# for an LLM/human to read, not to be treated as flight data) into a
# normalized `FlightSearchResult`. It never calls the MCP client itself,
# never fetches anything, and never invents a value absent from the
# payload.
#
# The exact shape parsed here (`itineraries[].{id,price,priceFormatted,
# totalDurationSeconds,bookingUrl,baggage,outbound,inbound}`,
# `outbound`/`inbound`.{route,segments[]}, `segments[].{from,to,fromCity,
# ...,carrier,carrierName,flightNumber,durationSeconds}`) was observed
# from a real, live call to Kiwi's `search-flight` tool during this step
# (see docs/12_provider_architecture.md section 59) -- it is not assumed
# from the public Tequila API docs, which use different field names and
# are a different product. Any field this shape doesn't provide (an
# itinerary-level total duration/stop count/route list, a cancellation
# policy, an explicit availability flag) has no home on `FlightOffer`
# today and is honestly dropped, never guessed into an existing field.
#
# `outbound_segments`/`return_segments`, `total_price_amount`, `currency`,
# `booking_url`, `baggage_policy` (flattened from Kiwi's own structured
# `{personalItem, cabinBag, checkedBag}` counts), origin/destination
# airports, departure/arrival times, and carrier/flight-number/duration
# are every one of them read only from the payload -- never inferred,
# estimated, or backfilled. `cancellation_policy` and `availability_status`
# have no corresponding field in Kiwi's structured output and always stay
# `None`.

_KIWI_PROVIDER_NAME = "kiwi_mcp"
_KIWI_SOURCE_NAME = "Kiwi MCP"

_CALL_FAILED_MESSAGE_TEMPLATE = "The Kiwi MCP search-flight call failed ({detail})."
_TOOL_REPORTED_ERROR_MESSAGE_TEMPLATE = "Kiwi MCP reported an error for this search: {detail}"
_MALFORMED_PAYLOAD_MESSAGE = "Kiwi MCP returned a response that could not be safely parsed."
_NO_OFFERS_MESSAGE_TEMPLATE = "Kiwi MCP returned no flight offers for this search{detail}."
_SUCCESS_MESSAGE_TEMPLATE = (
    "{count} provider-backed flight offer(s) were found via Kiwi MCP, but these have not "
    "been reviewed for schedule, price, availability, baggage-policy, or booking-link "
    "accuracy, and they are not scheduled into the itinerary. Booking links are Kiwi's own "
    "third-party links, not a TravelObligator booking confirmation."
)

_MAX_ERROR_DETAIL_LENGTH = 200


def _empty_result(
    request: FlightSearchRequest, status: FlightSearchStatus, message: str
) -> FlightSearchResult:
    return FlightSearchResult(
        provider=_KIWI_PROVIDER_NAME,
        status=status,
        offers=[],
        message=message,
        origin=request.origin,
        destination=request.destination,
        departure_date=request.departure_date,
        return_date=request.return_date,
        adults=request.adults,
        children=request.children,
        currency=request.currency,
    )


def _first_line(text: str, max_length: int = _MAX_ERROR_DETAIL_LENGTH) -> str:
    """Returns only the first line of a provider-supplied error string,
    truncated defensively -- Kiwi's own validation errors can be
    multi-line (a pydantic error block); this never relays more than a
    short, safe summary."""
    first = text.strip().splitlines()[0] if text.strip() else text
    return first[:max_length]


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_optional_decimal(value: Any) -> Decimal | None:
    """Accepts only a real numeric value already present in the payload --
    never a guessed price. Rejects a negative value (never possible for a
    real price) rather than silently accepting it."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _parse_optional_datetime(value: Any) -> datetime | None:
    """Accepts only an already-present ISO-8601 timestamp string from the
    payload -- never guesses a departure/arrival time."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _seconds_to_minutes(value: Any) -> int | None:
    """Direct unit conversion of a duration Kiwi itself reported in
    seconds -- never a computed/estimated duration. Rejects a negative or
    non-numeric value rather than guessing."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return int(value) // 60


def _flatten_baggage(raw_baggage: Any) -> str | None:
    """Flattens Kiwi's own structured `{personalItem, cabinBag,
    checkedBag}` counts into one human-readable, clearly provider-derived
    string -- never a policy description Kiwi didn't itself provide."""
    if not isinstance(raw_baggage, dict):
        return None
    parts: list[str] = []
    for key, label in (
        ("personalItem", "personal item"),
        ("cabinBag", "cabin bag"),
        ("checkedBag", "checked bag"),
    ):
        value = raw_baggage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            parts.append(f"{value} {label}(s)")
    if not parts:
        return None
    return "Kiwi-reported baggage allowance: " + ", ".join(parts)


def _build_segments(raw_segments: Any) -> list[FlightSegment]:
    if not isinstance(raw_segments, list):
        return []
    segments: list[FlightSegment] = []
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, dict):
            continue
        segments.append(
            FlightSegment(
                origin_airport=_optional_str(raw_segment.get("from")),
                destination_airport=_optional_str(raw_segment.get("to")),
                departure_time=_parse_optional_datetime(raw_segment.get("departureTime")),
                arrival_time=_parse_optional_datetime(raw_segment.get("arrivalTime")),
                carrier_name=_optional_str(raw_segment.get("carrierName")),
                carrier_code=_optional_str(raw_segment.get("carrier")),
                flight_number=_optional_str(raw_segment.get("flightNumber")),
                duration_minutes=_seconds_to_minutes(raw_segment.get("durationSeconds")),
                data_status=DataStatus.LIVE,
            )
        )
    return segments


def _build_offer(raw_itinerary: dict[str, Any], currency: str | None) -> FlightOffer | None:
    """Builds one `FlightOffer` from one raw `itineraries[]` entry, or
    `None` when there isn't enough structured data to safely identify one
    (no id, or no outbound leg with at least one real segment) -- never
    inventing an id or a segment to fill the gap.

    `offer_id` is Kiwi's own itinerary `id` (a real, stable, provider-
    supplied identifier for this exact result, confirmed present on every
    live itinerary observed during this step) -- never a locally
    synthesized correlation key, since Kiwi already provides a real one.
    """
    offer_id = raw_itinerary.get("id")
    outbound_raw = raw_itinerary.get("outbound")
    if not isinstance(offer_id, str) or not offer_id or not isinstance(outbound_raw, dict):
        return None

    outbound_segments = _build_segments(outbound_raw.get("segments"))
    if not outbound_segments:
        # An itinerary with no real, structured outbound segment is not
        # safely representable -- never backfilled with a placeholder leg.
        return None

    inbound_raw = raw_itinerary.get("inbound")
    return_segments = (
        _build_segments(inbound_raw.get("segments")) if isinstance(inbound_raw, dict) else []
    )

    return FlightOffer(
        offer_id=offer_id,
        provider=_KIWI_PROVIDER_NAME,
        data_status=DataStatus.LIVE,
        outbound_segments=outbound_segments,
        return_segments=return_segments,
        total_price_amount=_parse_optional_decimal(raw_itinerary.get("price")),
        currency=currency,
        booking_url=_optional_str(raw_itinerary.get("bookingUrl")),
        # Kiwi's structured output has no explicit availability flag or
        # cancellation-policy field -- both stay honestly None rather than
        # being inferred from "an offer was returned at all".
        availability_status=None,
        baggage_policy=_flatten_baggage(raw_itinerary.get("baggage")),
        cancellation_policy=None,
        source_name=_KIWI_SOURCE_NAME,
        source_url=None,
    )


def _try_build_offer(raw_itinerary: Any, currency: str | None) -> FlightOffer | None:
    """Wraps `_build_offer` so one malformed itinerary (e.g. a field that
    fails `FlightOffer`'s own validation) is skipped, never aborting the
    whole batch -- mirroring
    `app.providers.flights.scraped_parser`'s per-card safety."""
    if not isinstance(raw_itinerary, dict):
        return None
    try:
        return _build_offer(raw_itinerary, currency)
    except Exception:
        logger.warning(
            "Skipping one malformed Kiwi MCP itinerary that failed FlightOffer validation.",
            exc_info=True,
        )
        return None


def parse_kiwi_mcp_search_result(
    call_result: KiwiMcpToolCallResult,
    request: FlightSearchRequest,
) -> FlightSearchResult:
    """Transforms an already-received `KiwiMcpToolCallResult` into a
    normalized `FlightSearchResult`. Never calls anything itself -- the
    MCP call already happened in `KiwiMcpClient.call_tool`.

    Returns `status=failed` when the call itself failed, when Kiwi's own
    `error` field is populated (a real validation/tool-level error, not
    "no flights found"), or when the payload's shape can't be safely
    interpreted at all (not a dict, or `itineraries` isn't a list).
    Returns `status=unavailable` when the call succeeded and the payload
    is well-formed but yields zero safely-parseable offers (including the
    honest "Kiwi searched and found nothing" case). Returns
    `status=success` only when at least one real, structured itinerary was
    parsed into a `FlightOffer` -- never with an empty `offers` list
    (`FlightSearchResult.validate_offers_match_status` would reject that
    combination anyway).
    """
    if call_result.status != KiwiMcpToolCallStatus.SUCCESS:
        return _empty_result(
            request,
            FlightSearchStatus.FAILED,
            _CALL_FAILED_MESSAGE_TEMPLATE.format(detail=call_result.message or "unknown error"),
        )

    if call_result.is_error:
        return _empty_result(
            request,
            FlightSearchStatus.FAILED,
            _CALL_FAILED_MESSAGE_TEMPLATE.format(detail="the tool reported an error"),
        )

    payload = call_result.structured_content
    if not isinstance(payload, dict):
        return _empty_result(request, FlightSearchStatus.FAILED, _MALFORMED_PAYLOAD_MESSAGE)

    reported_error = payload.get("error")
    if isinstance(reported_error, str) and reported_error.strip():
        return _empty_result(
            request,
            FlightSearchStatus.FAILED,
            _TOOL_REPORTED_ERROR_MESSAGE_TEMPLATE.format(detail=_first_line(reported_error)),
        )

    raw_itineraries = payload.get("itineraries")
    if not isinstance(raw_itineraries, list):
        return _empty_result(request, FlightSearchStatus.FAILED, _MALFORMED_PAYLOAD_MESSAGE)

    try:
        currency = _optional_str(payload.get("currency")) or request.currency
        offers = [
            offer
            for raw_itinerary in raw_itineraries
            if (offer := _try_build_offer(raw_itinerary, currency)) is not None
        ]
    except Exception:
        logger.warning(
            "parse_kiwi_mcp_search_result failed unexpectedly; returning a failed result so "
            "callers never crash on an unexpected payload shape.",
            exc_info=True,
        )
        return _empty_result(request, FlightSearchStatus.FAILED, _MALFORMED_PAYLOAD_MESSAGE)

    if not offers:
        results_count = payload.get("resultsCount")
        detail = (
            f" ({results_count} reported by Kiwi, but none could be safely parsed)"
            if isinstance(results_count, int) and not isinstance(results_count, bool) and results_count > 0
            else ""
        )
        return _empty_result(
            request,
            FlightSearchStatus.UNAVAILABLE,
            _NO_OFFERS_MESSAGE_TEMPLATE.format(detail=detail),
        )

    return FlightSearchResult(
        provider=_KIWI_PROVIDER_NAME,
        status=FlightSearchStatus.SUCCESS,
        offers=offers,
        message=_SUCCESS_MESSAGE_TEMPLATE.format(count=len(offers)),
        origin=request.origin,
        destination=request.destination,
        departure_date=request.departure_date,
        return_date=request.return_date,
        adults=request.adults,
        children=request.children,
        currency=currency,
    )
