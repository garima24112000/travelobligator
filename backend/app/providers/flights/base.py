from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.flight import FlightSearchRequest, FlightSearchResult

# Provider boundary for bookable flight inventory (Step 169A,
# docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). This module only defines the interface
# a real flight inventory adapter must implement -- no adapter here is
# wired into `ProviderGateway`, `PlanningOrchestrator`, `TripStrategyService`,
# `StayTransportService`, or `PlanValidatorService` yet, and no real
# Amadeus/Duffel/Kiwi/Google Flights integration is added by this step.


class FlightInventoryProvider(ABC):
    """Interface for a provider that returns real, bookable flight
    inventory (segments, price, availability, baggage/cancellation policy,
    booking link) for a date-bounded search.

    Deliberately named differently from the pre-existing
    `app.providers.base.FlightProvider` stub interface (still unwired,
    always `not_connected`, used by `ProviderGateway`'s `flight` slot) --
    the same way `app.providers.accommodation.base.
    AccommodationInventoryProvider` is kept distinct from the pre-existing
    `app.providers.base.AccommodationProvider` stub interface. This is a
    separate, standalone contract for a future real flight inventory
    adapter and is not a replacement for the existing slot.

    Uses `abc.ABC` (mirroring `AccommodationInventoryProvider`/
    `RoutingProvider`, not the `app.providers.base` interfaces that
    default to an honest `not_connected` `ProviderResponse`) so every
    concrete adapter must explicitly implement `search_flights` -- there
    is no silent default behavior to fall back on.
    """

    provider_name: str = "flight_inventory_provider"

    @abstractmethod
    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResult:
        """Return a `FlightSearchResult` for `request`.

        Implementations must never invent an airline, flight number,
        airport, departure/arrival time, duration, price, availability,
        baggage policy, cancellation policy, or booking link. Missing/
        unusable inventory must be reported with an honest
        `not_connected`/`unavailable`/`failed` status and an empty
        `offers` list -- never guessed.
        """
        raise NotImplementedError
