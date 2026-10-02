from __future__ import annotations

from app.core.config import get_settings
from app.providers.base import PlacesProvider
from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter
from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter

# Config-gated places-provider selection (Section 203C.2B), mirroring
# `app.providers.geocoding.factory`. `PLACES_PROVIDER` is validated by
# `Settings`: "openstreetmap" (default; Overpass, development/experiments)
# or "geoapify" (production). There is deliberately no fallback from one to
# the other: a Geoapify outage is reported as such, it never turns into
# Overpass requests.

_SUPPORTED_PROVIDERS: dict[str, type[PlacesProvider]] = {
    "openstreetmap": OpenStreetMapPlacesAdapter,
    "geoapify": GeoapifyPlacesAdapter,
}


def get_places_provider(provider_name: str | None = None) -> PlacesProvider:
    resolved_name = provider_name if provider_name is not None else get_settings().places_provider
    return _SUPPORTED_PROVIDERS.get(resolved_name, OpenStreetMapPlacesAdapter)()
