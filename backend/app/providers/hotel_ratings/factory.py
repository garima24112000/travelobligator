from __future__ import annotations

from app.core.config import get_settings
from app.providers.hotel_ratings.base import HotelRatingsProvider
from app.providers.hotel_ratings.not_connected import NotConnectedHotelRatingsProvider
from app.providers.hotel_ratings.scraped_adapter import ScrapedLocalHotelRatingsProvider

# Config-gated provider-selection boundary (Step 177B, wired to a real
# local/manual adapter in Step 185E), mirroring
# app.providers.accommodation.factory/app.providers.flights.factory. No
# live Google Places/Tripadvisor/Amadeus/Yelp dependency is added here --
# this module only selects between `HotelRatingsProvider` adapters that
# already exist: the always-`not_connected` `NotConnectedHotelRatingsProvider`,
# and (as of Step 185E) `ScrapedLocalHotelRatingsProvider`, which only
# ever reads an already-supplied local HTML file, never a live network
# call.
#
# Wired into `AccommodationInventoryService` via
# `HotelRatingEnrichmentService` (Step 177C) -- still not read by
# `ProviderGateway`/`PlanningOrchestrator` directly.

# "manual_html" (mirroring `accommodation_provider`/`flight_provider`'s
# identical Step 182E alias convention) is a non-breaking alias for
# "scraped_local" -- same adapter class, same local-file-only behavior,
# same honest `unavailable` result with no file present. "scraped_local"
# itself is never removed or deprecated.
_SUPPORTED_PROVIDERS: dict[str, type[HotelRatingsProvider]] = {
    "not_connected": NotConnectedHotelRatingsProvider,
    "scraped_local": ScrapedLocalHotelRatingsProvider,
    "manual_html": ScrapedLocalHotelRatingsProvider,
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
    treated identically. `"tripadvisor"`/`"google_places"` etc. are never
    valid *provider* names -- selecting a named brand's local file happens
    through `HOTEL_RATINGS_MANUAL_HTML_SOURCE`/the per-brand
    `SCRAPED_HOTEL_RATINGS_HTML_PATH_*` settings instead, once
    `hotel_ratings_provider` itself is `"scraped_local"`/`"manual_html"`.
    """
    resolved_name = (
        provider_name if provider_name is not None else get_settings().hotel_ratings_provider
    )

    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, NotConnectedHotelRatingsProvider)
    return provider_cls()
