from __future__ import annotations

from app.models.accommodation import (
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.providers.accommodation.base import AccommodationInventoryProvider

# Default accommodation inventory adapter (Step 167B,
# docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). No lodging provider is connected yet,
# so `search_accommodations` always returns an honest `not_connected`
# result -- it never calls a network service, never inspects `request`
# beyond accepting it, and never invents a property, price, availability,
# rating, amenity, cancellation policy, or booking link.


class NotConnectedAccommodationProvider(AccommodationInventoryProvider):
    """`AccommodationInventoryProvider` implementation used until a real
    lodging inventory adapter is configured. `search_accommodations` is
    deterministic: given any request, it always returns the same
    `not_connected` `AccommodationSearchResult` with an empty `offers`
    list.
    """

    provider_name = "accommodation_inventory_provider"

    def search_accommodations(
        self, request: AccommodationSearchRequest
    ) -> AccommodationSearchResult:
        return AccommodationSearchResult(
            provider=self.provider_name,
            status=AccommodationSearchStatus.NOT_CONNECTED,
            offers=[],
            message="Accommodation inventory provider is not connected.",
        )
