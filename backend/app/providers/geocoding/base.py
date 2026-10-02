"""Geocoding / named-place search boundary (Section 203C.1).

Geocoding (text -> one real place with coordinates) is a separate concern
from Overpass POI discovery: `OpenStreetMapPlacesAdapter` keeps owning the
Overpass queries, destination plausibility, containment and caching, and
asks a `GeocodingProvider` only for the two lookups that used to be
hard-wired to the public Nominatim endpoint:

  * `search_destination` -- the trip destination itself;
  * `search_named_place` -- one explicit named place (must-visit term or an
    AI-proposed search intent) inside an already resolved destination.

A provider returns a vendor-neutral `GeocodeHit` or `None` (no usable
match). It never guesses: every failure is a `GeocoderError` carrying a
fixed `kind` and a fixed, secret-free message -- never the request URL,
the query, the response body or an API key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.models.common import GeoPoint
from app.providers.errors import CooldownBreaker, ProviderRequestError  # noqa: F401 - CooldownBreaker re-exported

BoundingBox = tuple[float, float, float, float]  # (south, north, west, east)

_SAFE_MESSAGES = {
    "not_connected": "Place geocoding provider is not connected.",
    "rate_limited": "Place geocoding provider rate limit was reached.",
    "auth": "Place geocoding provider rejected the configured credentials.",
    "timeout": "Place geocoding provider timed out.",
    "server": "Place geocoding provider returned a server error.",
    "malformed": "Place geocoding provider returned an unusable response.",
    "network": "Place geocoding provider could not be reached.",
    "bad_request": "Place geocoding provider rejected the request.",
    "budget_exhausted": "The provider call budget for this generation was reached.",
    "no_generation_context": "Place geocoding provider was called outside a generation.",
}


class GeocoderError(ProviderRequestError):
    """A GEOCODING request failed: the generic `ProviderRequestError` with
    geocoder wording. Other provider APIs (Places, Routing, ...) raise the
    generic error, never this one."""

    safe_messages = _SAFE_MESSAGES


@dataclass(frozen=True)
class GeocodeHit:
    """One real provider result, translated out of the vendor's shape.

    `feature_class` is `"place"`/`"boundary"` for a settlement or
    administrative area (what a trip destination must be) and the
    provider's own feature class otherwise; `None` only when the provider
    gave no structural evidence at all. `provider_place_id` + `source`
    are the identity exactly as the provider supports it -- an OSM
    `type/id` only when the provider actually returned one.
    """

    lat: float
    lon: float
    name: str
    display_name: str
    source: str
    provider_place_id: str
    feature_class: str | None = None
    feature_type: str | None = None
    alt_names: tuple[str, ...] = ()
    address: dict[str, str] = field(default_factory=dict)
    bounding_box: BoundingBox | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    # Sanitised identity of the underlying source object (`osm/<type>/<id>`)
    # when the provider's result carries one. De-duplication evidence only.
    source_entity_id: str | None = None


class GeocodingProvider:
    """Interface + honest default: not connected, never a guessed place."""

    provider_name: str = "not_connected"
    display_name: str = "not connected"
    # `ProviderCacheStore` source (cache namespace) for this provider's results.
    cache_source: str = "geocode"

    def bound_to(self, provider_context: Any) -> "GeocodingProvider":
        """A view of this provider that charges `provider_context`'s usage
        tracker (Section 203C.2B). A provider with no per-call cost returns
        itself."""
        return self

    def destination_cache_query(self, query: str) -> dict[str, Any]:
        """The cache-key material for one destination lookup."""
        return {"provider": self.provider_name, "kind": "destination", "query": query.strip().lower()}

    def search_destination(self, client: httpx.Client, query: str) -> GeocodeHit | None:
        raise GeocoderError("not_connected")

    def search_named_place(
        self,
        client: httpx.Client,
        query: str,
        *,
        near: GeoPoint | None = None,
        bounding_box: BoundingBox | None = None,
    ) -> GeocodeHit | None:
        """`near`/`bounding_box` describe the already resolved destination;
        a provider may use them to constrain the search. Containment is
        still checked by the caller either way."""
        raise GeocoderError("not_connected")
