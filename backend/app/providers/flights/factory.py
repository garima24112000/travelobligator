from __future__ import annotations

from app.core.config import get_settings
from app.providers.flights.base import FlightInventoryProvider
from app.providers.flights.kiwi_mcp_adapter import KiwiMcpFlightProvider
from app.providers.flights.not_connected_adapter import NotConnectedFlightProvider
from app.providers.flights.scraped_adapter import ScrapedLocalFlightProvider

# Config-gated provider-selection boundary (Step 169B, extended in Step
# 178B), mirroring app.providers.accommodation.factory. No Amadeus/
# Duffel/Google Flights dependency is added here -- this module only
# selects between `FlightInventoryProvider` adapters that already exist.
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
# `"kiwi_mcp"` (Step 178B) selects `KiwiMcpFlightProvider`, the first
# provider in this factory backed by a real, live external service --
# still never selected by default, and even when selected, never causes a
# network call unless `Settings.kiwi_mcp_enabled` is also explicitly
# `True` (see that adapter's own docstring).
#
# Wired into `ProviderGateway.flight_inventory` (Step 169E), which
# `FlightInventoryService`/`PlanningOrchestrator` already call for every
# trip generation -- this factory decides which adapter that path
# actually reaches, so its own default (`"scraped_local"`, never
# `"kiwi_mcp"`) is what runs in production today.

_SUPPORTED_PROVIDERS: dict[str, type[FlightInventoryProvider]] = {
    "not_connected": NotConnectedFlightProvider,
    "scraped_local": ScrapedLocalFlightProvider,
    "kiwi_mcp": KiwiMcpFlightProvider,
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
