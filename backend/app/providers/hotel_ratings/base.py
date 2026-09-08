from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.hotel_ratings import HotelRatingsRequest, HotelRatingsResult

# Provider boundary for hotel ratings enrichment (Step 177B,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md). This
# module only defines the interface a real ratings adapter must implement --
# no adapter here is wired into `ProviderGateway`, `PlanningOrchestrator`,
# `AccommodationInventoryService`, or `ProviderCoverage` yet, and no real
# Google Places/Tripadvisor/Amadeus/Yelp integration is added by this step.


class HotelRatingsProvider(ABC):
    """Interface for a provider that returns real, provider-backed hotel
    rating data (rating value, review count, source) for a list of
    already-known lodging properties, identified conservatively by fields
    already present on an `AccommodationOffer`.

    Uses `abc.ABC` (mirroring `AccommodationInventoryProvider`) so every
    concrete adapter must explicitly implement `get_ratings` -- there is
    no silent default behavior to fall back on.
    """

    provider_name: str = "hotel_ratings_provider"

    @abstractmethod
    def get_ratings(self, requests: list[HotelRatingsRequest]) -> HotelRatingsResult:
        """Return a `HotelRatingsResult` for `requests`.

        Implementations must never invent a rating value, review count,
        or source, and must never resolve a request to a property via
        fuzzy/best-guess matching -- an item that cannot be safely,
        exactly matched must be returned with `matched=False` and
        `rating=None`, never a guessed value. Missing/unusable rating
        data must be reported with an honest `not_connected`/
        `unavailable`/`failed` status and an empty `items` list -- never
        guessed, and never backfilled from `AccommodationOffer.rating` or
        any other existing field.
        """
        raise NotImplementedError
