from __future__ import annotations

from datetime import date

from app.models.flight import FlightSearchRequest, FlightSearchStatus
from app.providers.flights.not_connected_adapter import NotConnectedFlightProvider

# Behavior tests for Step 169B's default flight inventory adapter. Never
# calls a real network service and never requires any flight provider
# configuration.


def _request(**overrides: object) -> FlightSearchRequest:
    fields: dict[str, object] = {
        "origin": "JFK",
        "destination": "LIS",
        "departure_date": date(2026, 10, 10),
    }
    fields.update(overrides)
    return FlightSearchRequest(**fields)


# ---------------------------------------------------------------------------
# 5. NotConnectedFlightProvider returns not_connected with empty offers.
# ---------------------------------------------------------------------------


def test_returns_not_connected_status() -> None:
    provider = NotConnectedFlightProvider()
    result = provider.search_flights(_request())
    assert result.status == FlightSearchStatus.NOT_CONNECTED


def test_returns_empty_offers() -> None:
    provider = NotConnectedFlightProvider()
    result = provider.search_flights(_request())
    assert result.offers == []


def test_does_not_fabricate_message_is_honest() -> None:
    provider = NotConnectedFlightProvider()
    result = provider.search_flights(_request())
    assert result.message == "Flight inventory provider is not connected."
    assert result.provider == "flight_inventory_provider"


def test_output_is_stable_across_calls() -> None:
    provider = NotConnectedFlightProvider()
    first = provider.search_flights(_request())
    second = provider.search_flights(_request(destination="OPO"))
    assert first.status == second.status == FlightSearchStatus.NOT_CONNECTED
    assert first.offers == second.offers == []
    assert first.provider == second.provider
    assert first.message == second.message


def test_does_not_inspect_destination_to_invent_data() -> None:
    provider = NotConnectedFlightProvider()
    result_lisbon = provider.search_flights(_request(destination="LIS"))
    result_tokyo = provider.search_flights(_request(destination="NRT"))

    assert result_lisbon.offers == result_tokyo.offers == []
    assert result_lisbon.status == result_tokyo.status == FlightSearchStatus.NOT_CONNECTED
    assert result_lisbon.message == result_tokyo.message


def test_echoes_request_context_without_inventing_facts() -> None:
    provider = NotConnectedFlightProvider()
    result = provider.search_flights(
        _request(
            origin="JFK",
            destination="LIS",
            departure_date=date(2026, 10, 10),
            return_date=date(2026, 10, 17),
            adults=2,
            children=1,
            currency="EUR",
        )
    )
    assert result.origin == "JFK"
    assert result.destination == "LIS"
    assert result.departure_date == date(2026, 10, 10)
    assert result.return_date == date(2026, 10, 17)
    assert result.adults == 2
    assert result.children == 1
    assert result.currency == "EUR"


# ---------------------------------------------------------------------------
# 6. NotConnectedFlightProvider does not fabricate airline, flight
#    number, airport, price, time, duration, baggage, cancellation, or
#    booking_url.
# ---------------------------------------------------------------------------


def test_never_returns_placeholder_offers_regardless_of_request_size() -> None:
    provider = NotConnectedFlightProvider()
    result = provider.search_flights(_request(adults=6, children=3, currency="USD"))
    assert result.offers == []
    assert result.status == FlightSearchStatus.NOT_CONNECTED


def test_result_carries_no_airline_flight_number_price_or_booking_url() -> None:
    """With `offers` always empty, there is no field on the result itself
    (besides the echoed request context) that could carry a fabricated
    airline, flight number, airport, departure/arrival time, duration,
    price, availability, baggage policy, cancellation policy, or booking
    link -- confirmed here by construction rather than by inspecting a
    per-offer field that doesn't exist on an empty list."""
    provider = NotConnectedFlightProvider()
    result = provider.search_flights(_request())
    assert result.offers == []
    result_fields = set(result.model_dump().keys())
    forbidden_fields = {
        "airline",
        "flight_number",
        "price",
        "booking_url",
        "baggage_policy",
        "cancellation_policy",
    }
    assert not (result_fields & forbidden_fields)
