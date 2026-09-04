from __future__ import annotations

from app.core.config import get_settings
from app.providers.flights.base import FlightInventoryProvider
from app.providers.flights.not_connected_adapter import NotConnectedFlightProvider
from app.providers.flights.scraped_adapter import ScrapedLocalFlightProvider

# Config-gated provider-selection boundary (Step 169B), mirroring
# app.providers.accommodation.factory. No Amadeus/Duffel/Kiwi/Google
# Flights dependency is added here -- this module only selects between
# `FlightInventoryProvider` adapters that already exist.
#
# `Settings.flight_provider` defaults to `"scraped_local"`, which selects
# `ScrapedLocalFlightProvider` -- itself still safe by default: with no
# flight HTML parser implemented yet (Step 169C) and no local HTML file
# present at `Settings.scraped_flight_html_path`'s default location, it
# returns an honest `unavailable` result rather than reading anything or
# fabricating an offer. Selecting `"scraped_local"` here never itself
# causes a file access or network call -- only actually calling
# `search_flights` does, and even that never reaches the network (see
# `backend/app/providers/flights/scraped_adapter.py`). Explicitly setting
# `flight_provider="not_connected"` still selects the always-
# `not_connected` `NotConnectedFlightProvider`.
#
# Not wired into `ProviderGateway` or `PlanningOrchestrator` yet --
# nothing in the app calls this factory outside its own tests.

_SUPPORTED_PROVIDERS: dict[str, type[FlightInventoryProvider]] = {
    "not_connected": NotConnectedFlightProvider,
    "scraped_local": ScrapedLocalFlightProvider,
}


def get_flight_provider(provider_name: str | None = None) -> FlightInventoryProvider:
    """Resolves a `FlightInventoryProvider` from `provider_name`, or from
    `Settings.flight_provider` (default `"scraped_local"`) when
    `provider_name` is omitted.

    An unsupported/unrecognized provider name can never silently create
    fake flight inventory: it falls back to the same honest
    `NotConnectedFlightProvider` used when explicitly configured to
    `"not_connected"`, rather than raising or guessing. Unknown
    configuration is functionally identical to "not connected," so it is
    treated identically.
    """
    resolved_name = provider_name if provider_name is not None else get_settings().flight_provider

    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, NotConnectedFlightProvider)
    return provider_cls()
