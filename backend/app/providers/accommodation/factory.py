from __future__ import annotations

from app.core.config import get_settings
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.accommodation.not_connected_adapter import (
    NotConnectedAccommodationProvider,
)

# Config-gated provider-selection boundary (Step 167B), mirroring
# app.providers.routing.factory. No Booking/Expedia/Hotelbeds/Hostelworld/
# Amadeus/Vrbo/Airbnb dependency is added here -- this module only selects
# between `AccommodationInventoryProvider` adapters that already exist. The
# default remains "not_connected", and no real lodging adapter exists yet
# to select.
#
# Not wired into `ProviderGateway` or `PlanningOrchestrator` yet -- nothing
# in the app calls this factory outside its own tests.

_SUPPORTED_PROVIDERS: dict[str, type[AccommodationInventoryProvider]] = {
    "not_connected": NotConnectedAccommodationProvider,
}


def get_accommodation_provider(
    provider_name: str | None = None,
) -> AccommodationInventoryProvider:
    """Resolves an `AccommodationInventoryProvider` from `provider_name`, or
    from `Settings.accommodation_provider` (default `"not_connected"`) when
    `provider_name` is omitted.

    An unsupported/unrecognized provider name can never silently create
    fake lodging inventory: it falls back to the same honest
    `NotConnectedAccommodationProvider` used when nothing is configured at
    all, rather than raising or guessing. Unknown configuration is
    functionally identical to "not connected," so it is treated
    identically.
    """
    resolved_name = (
        provider_name if provider_name is not None else get_settings().accommodation_provider
    )

    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, NotConnectedAccommodationProvider)
    return provider_cls()
