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

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.models.common import GeoPoint

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
}


class GeocoderError(Exception):
    """A geocoding request failed. `kind` is one of the fixed keys of
    `_SAFE_MESSAGES`; `str(error)` is always the fixed message for that
    kind, so it is safe to log and to show."""

    def __init__(self, kind: str) -> None:
        self.kind = kind if kind in _SAFE_MESSAGES else "network"
        super().__init__(_SAFE_MESSAGES[self.kind])


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


class CooldownBreaker:
    """Process-local circuit breaker for one geocoding endpoint.

    Once tripped (rate limit or rejected credentials), every further
    request is refused locally with the same `GeocoderError` kind until
    the cooldown ends -- so one 429 stops the rest of a generation's
    lookups instead of each named place hitting the endpoint again. Not
    shared across containers (PostgreSQL/Redis are never used as a lock).
    """

    default_cooldown_seconds = 60.0
    max_cooldown_seconds = 300.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._blocked_until = 0.0
        self._blocked_kind = "rate_limited"

    def reset(self) -> None:
        with self._lock:
            self._blocked_until = 0.0

    def check(self) -> None:
        with self._lock:
            if time.monotonic() < self._blocked_until:
                raise GeocoderError(self._blocked_kind)

    def trip(self, kind: str, retry_after: str | None = None) -> None:
        cooldown = self.default_cooldown_seconds
        try:
            if retry_after is not None:
                cooldown = max(cooldown, float(retry_after))
        except (TypeError, ValueError):
            pass  # an HTTP-date Retry-After: keep the default cooldown
        with self._lock:
            self._blocked_until = time.monotonic() + min(cooldown, self.max_cooldown_seconds)
            self._blocked_kind = kind


class GeocodingProvider:
    """Interface + honest default: not connected, never a guessed place."""

    provider_name: str = "not_connected"
    display_name: str = "not connected"
    # `ProviderCacheStore` source (cache namespace) for this provider's results.
    cache_source: str = "geocode"

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
