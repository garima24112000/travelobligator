from __future__ import annotations

from datetime import date, datetime

import pytest

from app.models.common import DataStatus
from app.models.flight import FlightSearchRequest, FlightSearchStatus
from app.providers.flights.kiwi_mcp_client import KiwiMcpToolCallResult, KiwiMcpToolCallStatus
from app.providers.flights.kiwi_mcp_parser import parse_kiwi_mcp_search_result

# Step 178C: deterministic Kiwi MCP parser tests. Every payload below is a
# fixed, in-memory dict shaped exactly like a real, live search-flight
# response observed during this step -- never a real network call.


def _request(**overrides: object) -> FlightSearchRequest:
    fields: dict[str, object] = {
        "origin": "London",
        "destination": "Paris",
        "departure_date": date(2026, 12, 15),
    }
    fields.update(overrides)
    return FlightSearchRequest(**fields)


def _success_call_result(structured_content: object) -> KiwiMcpToolCallResult:
    return KiwiMcpToolCallResult(
        status=KiwiMcpToolCallStatus.SUCCESS,
        structured_content=structured_content,
        is_error=False,
    )


_ONE_WAY_SEGMENT = {
    "from": "LGW",
    "to": "CDG",
    "fromCity": "London",
    "toCity": "Paris",
    "fromName": "Gatwick",
    "toName": "Charles de Gaulle Airport",
    "fromCountry": "United Kingdom",
    "toCountry": "France",
    "departureTime": "2026-12-15T16:45:00",
    "arrivalTime": "2026-12-15T19:00:00",
    "durationSeconds": 4500,
    "carrier": "U2",
    "carrierName": "easyJet",
    "flightNumber": "U28407",
    "cabinClass": "Economy",
}

_ONE_WAY_ITINERARY = {
    "id": "22f525c35142000074e76ad3_0",
    "price": 53.0,
    "priceFormatted": "53 EUR",
    "totalDurationSeconds": 4500,
    "bookingUrl": "https://kiwi.com/u/m9fxvz",
    "imageId": "paris_fr",
    "baggage": {"personalItem": 1, "cabinBag": 0, "checkedBag": 0},
    "outbound": {
        "from": "LGW",
        "to": "CDG",
        "departureTime": "2026-12-15T16:45:00",
        "arrivalTime": "2026-12-15T19:00:00",
        "durationSeconds": 4500,
        "stops": 0,
        "route": ["LGW", "CDG"],
        "cabinClass": "Economy",
        "segments": [_ONE_WAY_SEGMENT],
    },
    "inbound": None,
}

_SUCCESS_PAYLOAD = {
    "query": "London → Paris on 15/12/2026, 1 adult",
    "currency": "EUR",
    "passengers": {"adults": 1, "children": 0, "infants": 0},
    "resultsCount": 1,
    "itineraries": [_ONE_WAY_ITINERARY],
    "searchTimeMs": 1260,
    "error": None,
}


# ---------------------------------------------------------------------------
# 6. Parser converts a structured Kiwi MCP success payload into
#    FlightSearchResult success.
# ---------------------------------------------------------------------------


def test_parser_converts_success_payload_into_success_result() -> None:
    result = parse_kiwi_mcp_search_result(_success_call_result(_SUCCESS_PAYLOAD), _request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.offer_id == "22f525c35142000074e76ad3_0"
    assert offer.provider == "kiwi_mcp"
    assert offer.data_status == DataStatus.LIVE


# ---------------------------------------------------------------------------
# 7. Parser maps price/currency only from payload.
# ---------------------------------------------------------------------------


def test_parser_maps_price_and_currency_from_payload() -> None:
    result = parse_kiwi_mcp_search_result(_success_call_result(_SUCCESS_PAYLOAD), _request())

    offer = result.offers[0]
    assert offer.total_price_amount == pytest.approx(53.0)
    assert offer.currency == "EUR"
    assert result.currency == "EUR"


def test_parser_never_invents_price_when_absent() -> None:
    itinerary = {**_ONE_WAY_ITINERARY, "price": None}
    payload = {**_SUCCESS_PAYLOAD, "itineraries": [itinerary]}

    result = parse_kiwi_mcp_search_result(_success_call_result(payload), _request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert result.offers[0].total_price_amount is None


# ---------------------------------------------------------------------------
# 8. Parser maps booking_url only from payload.
# ---------------------------------------------------------------------------


def test_parser_maps_booking_url_from_payload() -> None:
    result = parse_kiwi_mcp_search_result(_success_call_result(_SUCCESS_PAYLOAD), _request())
    assert result.offers[0].booking_url == "https://kiwi.com/u/m9fxvz"


def test_parser_never_constructs_booking_url_when_absent() -> None:
    itinerary = {**_ONE_WAY_ITINERARY, "bookingUrl": None}
    payload = {**_SUCCESS_PAYLOAD, "itineraries": [itinerary]}

    result = parse_kiwi_mcp_search_result(_success_call_result(payload), _request())

    assert result.offers[0].booking_url is None


# ---------------------------------------------------------------------------
# 9. Parser maps outbound segments only from payload.
# ---------------------------------------------------------------------------


def test_parser_maps_outbound_segments_from_payload() -> None:
    result = parse_kiwi_mcp_search_result(_success_call_result(_SUCCESS_PAYLOAD), _request())

    offer = result.offers[0]
    assert len(offer.outbound_segments) == 1
    segment = offer.outbound_segments[0]
    assert segment.origin_airport == "LGW"
    assert segment.destination_airport == "CDG"
    assert segment.carrier_code == "U2"
    assert segment.carrier_name == "easyJet"
    assert segment.flight_number == "U28407"
    assert segment.duration_minutes == 75
    assert segment.departure_time == datetime(2026, 12, 15, 16, 45, 0)
    assert segment.arrival_time == datetime(2026, 12, 15, 19, 0, 0)
    assert segment.data_status == DataStatus.LIVE


def test_parser_rejects_itinerary_with_no_outbound_segments() -> None:
    itinerary = {**_ONE_WAY_ITINERARY, "outbound": {**_ONE_WAY_ITINERARY["outbound"], "segments": []}}
    payload = {**_SUCCESS_PAYLOAD, "itineraries": [itinerary]}

    result = parse_kiwi_mcp_search_result(_success_call_result(payload), _request())

    # No safely-buildable offer -> honest unavailable, never a fabricated
    # segment to fill the gap.
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


# ---------------------------------------------------------------------------
# 10. Parser maps return segments only when present.
# ---------------------------------------------------------------------------


def test_parser_maps_return_segments_when_present() -> None:
    return_segment = {
        **_ONE_WAY_SEGMENT,
        "from": "CDG",
        "to": "LGW",
        "departureTime": "2026-12-20T07:00:00",
        "arrivalTime": "2026-12-20T07:10:00",
        "flightNumber": "U28402",
        "durationSeconds": 4200,
    }
    itinerary = {
        **_ONE_WAY_ITINERARY,
        "inbound": {
            "from": "CDG",
            "to": "LGW",
            "departureTime": "2026-12-20T07:00:00",
            "arrivalTime": "2026-12-20T07:10:00",
            "durationSeconds": 4200,
            "stops": 0,
            "route": ["CDG", "LGW"],
            "cabinClass": "Economy",
            "segments": [return_segment],
        },
    }
    payload = {**_SUCCESS_PAYLOAD, "itineraries": [itinerary]}
    request = _request(return_date=date(2026, 12, 20))

    result = parse_kiwi_mcp_search_result(_success_call_result(payload), request)

    offer = result.offers[0]
    assert len(offer.return_segments) == 1
    assert offer.return_segments[0].origin_airport == "CDG"
    assert offer.return_segments[0].destination_airport == "LGW"


def test_parser_leaves_return_segments_empty_when_inbound_is_none() -> None:
    result = parse_kiwi_mcp_search_result(_success_call_result(_SUCCESS_PAYLOAD), _request())
    assert result.offers[0].return_segments == []


# ---------------------------------------------------------------------------
# 11. Parser leaves missing optional baggage/cancellation/carrier/flight-
#     number fields as None.
# ---------------------------------------------------------------------------


def test_parser_leaves_missing_optional_fields_as_none() -> None:
    minimal_segment = {"from": "LGW", "to": "CDG"}
    itinerary = {
        "id": "minimal-1",
        "outbound": {"segments": [minimal_segment]},
        "inbound": None,
    }
    payload = {"currency": None, "itineraries": [itinerary], "resultsCount": 1, "error": None}

    result = parse_kiwi_mcp_search_result(_success_call_result(payload), _request())

    assert result.status == FlightSearchStatus.SUCCESS
    offer = result.offers[0]
    assert offer.total_price_amount is None
    assert offer.booking_url is None
    assert offer.baggage_policy is None
    assert offer.cancellation_policy is None
    assert offer.availability_status is None
    segment = offer.outbound_segments[0]
    assert segment.carrier_name is None
    assert segment.carrier_code is None
    assert segment.flight_number is None
    assert segment.duration_minutes is None
    assert segment.departure_time is None
    assert segment.arrival_time is None


# ---------------------------------------------------------------------------
# 12. Parser rejects a malformed payload safely with failed/unavailable
#     and empty offers -- never raises.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not a dict",
        123,
        {"itineraries": "not a list"},
        {"itineraries": None},
    ],
)
def test_parser_rejects_malformed_payload_as_failed(payload: object) -> None:
    result = parse_kiwi_mcp_search_result(_success_call_result(payload), _request())

    assert result.status == FlightSearchStatus.FAILED
    assert result.offers == []


def test_parser_skips_one_malformed_itinerary_without_aborting_others() -> None:
    malformed_itinerary = {"id": "bad", "outbound": "not a dict"}
    payload = {**_SUCCESS_PAYLOAD, "itineraries": [malformed_itinerary, _ONE_WAY_ITINERARY]}

    result = parse_kiwi_mcp_search_result(_success_call_result(payload), _request())

    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].offer_id == "22f525c35142000074e76ad3_0"


def test_parser_returns_failed_when_call_itself_failed() -> None:
    call_result = KiwiMcpToolCallResult(
        status=KiwiMcpToolCallStatus.FAILED, message="Could not call the Kiwi MCP 'search-flight' tool."
    )

    result = parse_kiwi_mcp_search_result(call_result, _request())

    assert result.status == FlightSearchStatus.FAILED
    assert result.offers == []


def test_parser_returns_failed_when_tool_reports_error() -> None:
    payload = {
        "currency": None,
        "passengers": None,
        "resultsCount": 0,
        "itineraries": [],
        "error": "1 validation error for SearchFlightsStructuredInput\ndepartureDate\n  Value error",
    }

    result = parse_kiwi_mcp_search_result(_success_call_result(payload), _request())

    assert result.status == FlightSearchStatus.FAILED
    assert result.offers == []
    assert "1 validation error" in (result.message or "")


def test_parser_returns_failed_when_is_error_flag_set() -> None:
    call_result = KiwiMcpToolCallResult(
        status=KiwiMcpToolCallStatus.SUCCESS,
        structured_content={"itineraries": []},
        is_error=True,
    )

    result = parse_kiwi_mcp_search_result(call_result, _request())

    assert result.status == FlightSearchStatus.FAILED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 13. Parser returns unavailable when payload contains no offers.
# ---------------------------------------------------------------------------


def test_parser_returns_unavailable_when_zero_itineraries() -> None:
    payload = {
        "currency": "EUR",
        "passengers": {"adults": 1, "children": 0, "infants": 0},
        "resultsCount": 0,
        "itineraries": [],
        "error": None,
    }

    result = parse_kiwi_mcp_search_result(_success_call_result(payload), _request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


def test_parser_never_returns_success_with_zero_offers() -> None:
    """Structural guarantee: FlightSearchResult itself would reject
    status=success with an empty offers list -- this test proves the
    parser never even attempts that combination."""
    payload = {"currency": "EUR", "resultsCount": 0, "itineraries": [], "error": None}

    result = parse_kiwi_mcp_search_result(_success_call_result(payload), _request())

    assert not (result.status == FlightSearchStatus.SUCCESS and not result.offers)
