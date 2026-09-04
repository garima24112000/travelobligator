from __future__ import annotations

from app.models.flight import FlightSearchRequest, FlightSearchResult, FlightSearchStatus
from app.providers.flights.base import FlightInventoryProvider

# Default flight inventory adapter (Step 169B, mirroring
# `NotConnectedAccommodationProvider`, Step 167B), docs/12_provider_
# architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md. No flight provider is connected yet,
# so `search_flights` always returns an honest `not_connected` result --
# it never calls a network service, never inspects `request` beyond
# accepting it, and never invents an airline, flight number, airport,
# departure/arrival time, duration, price, availability, baggage policy,
# cancellation policy, or booking link.


class NotConnectedFlightProvider(FlightInventoryProvider):
    """`FlightInventoryProvider` implementation used until a real flight
    inventory adapter is configured. `search_flights` is deterministic:
    given any request, it always returns the same `not_connected`
    `FlightSearchResult` with an empty `offers` list.
    """

    provider_name = "flight_inventory_provider"

    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResult:
        return FlightSearchResult(
            provider=self.provider_name,
            status=FlightSearchStatus.NOT_CONNECTED,
            offers=[],
            message="Flight inventory provider is not connected.",
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            adults=request.adults,
            children=request.children,
            currency=request.currency,
        )
