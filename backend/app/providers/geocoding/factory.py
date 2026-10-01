from __future__ import annotations

from app.core.config import get_settings
from app.providers.geocoding.base import GeocodingProvider
from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
from app.providers.geocoding.nominatim_adapter import NominatimGeocoder

# Config-gated geocoder selection (Section 203C.1), mirroring
# `app.providers.routing.factory`. `GEOCODING_PROVIDER` is validated by
# `Settings` ("nominatim" default for development/tests, "geoapify" for
# production). There is deliberately no fallback from one geocoder to the
# other: Geoapify without a key reports not connected, it never quietly
# calls the public Nominatim endpoint instead.

_SUPPORTED_PROVIDERS: dict[str, type[GeocodingProvider]] = {
    "nominatim": NominatimGeocoder,
    "geoapify": GeoapifyGeocoder,
}


def get_geocoding_provider(provider_name: str | None = None) -> GeocodingProvider:
    resolved_name = provider_name if provider_name is not None else get_settings().geocoding_provider
    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, GeocodingProvider)
    return provider_cls()
