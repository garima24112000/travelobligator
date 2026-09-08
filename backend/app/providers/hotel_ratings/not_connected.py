from __future__ import annotations

from app.models.hotel_ratings import HotelRatingsRequest, HotelRatingsResult, HotelRatingsStatus
from app.providers.hotel_ratings.base import HotelRatingsProvider

# Default hotel ratings provider (Step 177B, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). No rating provider is connected yet, so
# `get_ratings` always returns an honest `not_connected` result -- it never
# calls a network service, never inspects `requests` beyond accepting them,
# and never invents a rating value, review count, or source.


class NotConnectedHotelRatingsProvider(HotelRatingsProvider):
    """`HotelRatingsProvider` implementation used until a real hotel
    ratings adapter is configured. `get_ratings` is deterministic: given
    any requests, it always returns the same `not_connected`
    `HotelRatingsResult` with an empty `items` list.
    """

    provider_name = "hotel_ratings_provider"

    def get_ratings(self, requests: list[HotelRatingsRequest]) -> HotelRatingsResult:
        return HotelRatingsResult(
            provider=self.provider_name,
            status=HotelRatingsStatus.NOT_CONNECTED,
            items=[],
            message="No hotel ratings provider is configured.",
        )
