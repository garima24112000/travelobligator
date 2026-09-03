from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchResult

# Provider boundary for bookable accommodation inventory (Step 167A,
# docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). This module only defines the interface
# a real lodging inventory adapter must implement -- no adapter here is
# wired into `ProviderGateway`, `PlanningOrchestrator`,
# `StayTransportService`, or `PlanValidatorService` yet, and no real
# Booking/Expedia/Hotelbeds/Hostelworld/Amadeus/Vrbo/Airbnb integration is
# added by this step.


class AccommodationInventoryProvider(ABC):
    """Interface for a provider that returns real, bookable lodging
    inventory (property, price, availability, rating, booking link) for a
    date-bounded search.

    Deliberately named differently from the pre-existing
    `app.providers.base.AccommodationProvider` stub interface (still
    unwired, always `not_connected`, used by `ProviderGateway`'s
    `accommodation` slot) -- the same way `app.providers.routing.base.
    RoutingProvider` is kept distinct from the pre-existing `RoutesProvider`
    stub interface. This is a separate, standalone contract for a future
    real lodging inventory adapter and is not a replacement for the
    existing slot.

    Uses `abc.ABC` (mirroring `RoutingProvider`, not the
    `app.providers.base` interfaces that default to an honest
    `not_connected` `ProviderResponse`) so every concrete adapter must
    explicitly implement `search_accommodations` -- there is no silent
    default behavior to fall back on.
    """

    provider_name: str = "accommodation_inventory_provider"

    @abstractmethod
    def search_accommodations(
        self, request: AccommodationSearchRequest
    ) -> AccommodationSearchResult:
        """Return an `AccommodationSearchResult` for `request`.

        Implementations must never invent a property, nightly/total price,
        availability, rating, amenity, cancellation policy, or booking
        link. Missing/unusable inventory must be reported with an honest
        `not_connected`/`unavailable`/`failed` status and an empty
        `offers` list -- never guessed, and never backfilled from an
        OSM/open-data accommodation POI (`DestinationContext.
        candidate_accommodation_pois` are location candidates only, and
        must never be presented as bookable inventory here).
        """
        raise NotImplementedError
