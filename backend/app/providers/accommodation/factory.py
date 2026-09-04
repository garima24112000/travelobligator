from __future__ import annotations

from app.core.config import get_settings
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.accommodation.not_connected_adapter import (
    NotConnectedAccommodationProvider,
)
from app.providers.accommodation.scraped_adapter import ScrapedAccommodationProvider

# Config-gated provider-selection boundary (Step 167B, extended in Step
# 168C), mirroring app.providers.routing.factory. No Booking/Expedia/
# Hotelbeds/Hostelworld/Amadeus/Vrbo/Airbnb dependency is added here --
# this module only selects between `AccommodationInventoryProvider`
# adapters that already exist.
#
# As of Step 168F, `Settings.accommodation_provider` defaults to
# `"scraped_local"`, which selects `ScrapedAccommodationProvider` --
# itself still safe by default: with no local HTML file present at
# `Settings.scraped_accommodation_html_path`'s default location, it
# returns an honest `unavailable` result rather than reading anything or
# fabricating an offer. Selecting `"scraped_local"` here never itself
# causes a file access or network call -- only actually calling
# `search_accommodations` does, and even that never reaches the network
# (see `backend/app/providers/accommodation/scraped_adapter.py`).
# Explicitly setting `accommodation_provider="not_connected"` still
# selects the always-`not_connected` `NotConnectedAccommodationProvider`.
#
# Not wired into `ProviderGateway` or `PlanningOrchestrator` yet -- nothing
# in the app calls this factory outside its own tests.

_SUPPORTED_PROVIDERS: dict[str, type[AccommodationInventoryProvider]] = {
    "not_connected": NotConnectedAccommodationProvider,
    "scraped_local": ScrapedAccommodationProvider,
}


def get_accommodation_provider(
    provider_name: str | None = None,
) -> AccommodationInventoryProvider:
    """Resolves an `AccommodationInventoryProvider` from `provider_name`, or
    from `Settings.accommodation_provider` (default `"scraped_local"`,
    Step 168F) when `provider_name` is omitted.

    An unsupported/unrecognized provider name can never silently create
    fake lodging inventory: it falls back to the same honest
    `NotConnectedAccommodationProvider` used when explicitly configured to
    `"not_connected"`, rather than raising or guessing. Unknown
    configuration is functionally identical to "not connected," so it is
    treated identically.
    """
    resolved_name = (
        provider_name if provider_name is not None else get_settings().accommodation_provider
    )

    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, NotConnectedAccommodationProvider)
    return provider_cls()
