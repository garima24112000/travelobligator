from __future__ import annotations

from app.core.config import get_settings
from app.providers.hotel_ratings.base import HotelRatingsProvider
from app.providers.hotel_ratings.not_connected import NotConnectedHotelRatingsProvider

# Config-gated provider-selection boundary (Step 177B), mirroring
# app.providers.accommodation.factory. No Google Places/Tripadvisor/
# Amadeus/Yelp dependency is added here -- this module only selects
# between `HotelRatingsProvider` adapters that already exist, and as of
# Step 177B the only adapter that exists is the always-`not_connected`
# `NotConnectedHotelRatingsProvider`.
#
# Not wired into `ProviderGateway`, `PlanningOrchestrator`, or
# `AccommodationInventoryService` yet -- nothing in the app calls this
# factory outside its own tests.

_SUPPORTED_PROVIDERS: dict[str, type[HotelRatingsProvider]] = {
    "not_connected": NotConnectedHotelRatingsProvider,
}


def get_hotel_ratings_provider(provider_name: str | None = None) -> HotelRatingsProvider:
    """Resolves a `HotelRatingsProvider` from `provider_name`, or from
    `Settings.hotel_ratings_provider` (default `"not_connected"`) when
    `provider_name` is omitted.

    An unsupported/unrecognized provider name can never silently create
    fake rating data: it falls back to the same honest
    `NotConnectedHotelRatingsProvider` used when explicitly configured to
    `"not_connected"`, rather than raising or guessing. Unknown
    configuration is functionally identical to "not connected," so it is
    treated identically.
    """
    resolved_name = (
        provider_name if provider_name is not None else get_settings().hotel_ratings_provider
    )

    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, NotConnectedHotelRatingsProvider)
    return provider_cls()
