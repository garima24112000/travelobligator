"""Geoapify forward geocoder (the production geocoder; Section 203C.1).

`GET {GEOAPIFY_API_URL}/v1/geocode/search?text=...&format=json&limit=1`.
Geoapify only accepts its key as the `apiKey` query parameter, so the key
is part of the request URL: this module therefore never logs, and never
lets an httpx exception (whose text contains the URL) leave it -- every
failure becomes a `GeocoderError` with a fixed message.

Identity is honest: forward geocoding returns Geoapify's own `place_id`
and no OSM `type/id`, so a place found here is `geoapify/<place_id>` with
source `geoapify`, never presented as an OpenStreetMap object id. Geoapify
returns no OSM tags either, so a named place carries only Geoapify's own
category as evidence.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.models.common import GeoPoint
from app.providers.geocoding.base import (
    BoundingBox,
    CooldownBreaker,
    GeocodeHit,
    GeocoderError,
    GeocodingProvider,
)

_LANGUAGE = "en"
_MAX_RESULTS = 1
_NAMED_PLACE_RADIUS_METERS = 12000

# `result_type` values that are a settlement/administrative area, i.e. what
# a trip destination may resolve to (reported as feature class "place").
_AREA_RESULT_TYPES = frozenset({"suburb", "district", "city", "county", "state", "country"})
# A named place must be a specific feature, never the surrounding
# street/postcode/city Geoapify falls back to when it cannot find the name.
_NAMED_PLACE_RESULT_TYPES = frozenset({"amenity", "building", "suburb", "district"})
_NAMED_PLACE_MATCH_TYPES = frozenset({"full_match", "inner_part"})
_NAMED_PLACE_MIN_CONFIDENCE = 0.5

_ADDRESS_KEYS = (
    "suburb", "district", "city", "county", "state", "country", "country_code",
)

request_breaker = CooldownBreaker()


def _bounding_box(raw: Any) -> BoundingBox | None:
    if not isinstance(raw, dict):
        return None
    try:
        lats = (float(raw["lat1"]), float(raw["lat2"]))
        lons = (float(raw["lon1"]), float(raw["lon2"]))
    except (KeyError, TypeError, ValueError):
        return None
    return min(lats), max(lats), min(lons), max(lons)


class GeoapifyGeocoder(GeocodingProvider):
    provider_name = "geoapify"
    display_name = "Geoapify"
    cache_source = "geoapify_geocode"

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.geoapify_api_url.rstrip("/")
        self._api_key = (settings.geoapify_api_key or "").strip()
        self._timeout = settings.geoapify_timeout_seconds

    def destination_cache_query(self, query: str) -> dict[str, Any]:
        return {**super().destination_cache_query(query), "lang": _LANGUAGE, "schema": "v1"}

    def _first_result(self, client: httpx.Client, params: dict[str, Any]) -> dict[str, Any] | None:
        if not self._api_key:
            raise GeocoderError("not_connected")
        request_breaker.check()
        try:
            response = client.get(
                f"{self._base_url}/v1/geocode/search",
                params={
                    **params,
                    "format": "json",
                    "limit": _MAX_RESULTS,
                    "lang": _LANGUAGE,
                    "apiKey": self._api_key,
                },
                timeout=self._timeout,
            )
            status = response.status_code
        except httpx.TimeoutException:
            raise GeocoderError("timeout") from None
        except httpx.HTTPError:
            raise GeocoderError("network") from None

        if status == 429:
            request_breaker.trip("rate_limited", response.headers.get("Retry-After"))
            raise GeocoderError("rate_limited")
        if status in (401, 403):
            request_breaker.trip("auth")
            raise GeocoderError("auth")
        if status >= 500:
            raise GeocoderError("server")
        if status >= 400:
            raise GeocoderError("bad_request")

        try:
            payload = response.json()
        except ValueError:
            raise GeocoderError("malformed") from None
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise GeocoderError("malformed")
        if not results:
            return None
        if not isinstance(results[0], dict):
            raise GeocoderError("malformed")
        return results[0]

    def _hit(self, result: dict[str, Any]) -> GeocodeHit | None:
        """Translates one Geoapify result. None when it has no coordinates
        or no identity; `GeocoderError("malformed")` when they are unusable."""
        lat, lon, place_id = result.get("lat"), result.get("lon"), result.get("place_id")
        if lat is None or lon is None or not isinstance(place_id, str) or not place_id:
            return None
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            raise GeocoderError("malformed") from None

        result_type = result.get("result_type") if isinstance(result.get("result_type"), str) else None
        formatted = result.get("formatted") if isinstance(result.get("formatted"), str) else ""
        name = result.get("name") if isinstance(result.get("name"), str) else ""
        address = {key: result[key] for key in _ADDRESS_KEYS if isinstance(result.get(key), str) and result[key]}
        # For an area result, the component named by its own type is a name
        # of that feature (e.g. `city` of a `result_type: city`).
        own_component = address.get(result_type) if result_type in _AREA_RESULT_TYPES else None
        category = result.get("category") if isinstance(result.get("category"), str) else None
        return GeocodeHit(
            lat=lat,
            lon=lon,
            name=name or own_component or "",
            display_name=formatted,
            source=self.provider_name,
            provider_place_id=f"{self.provider_name}/{place_id}",
            feature_class="place" if result_type in _AREA_RESULT_TYPES else (result_type or "unknown"),
            feature_type=category.rsplit(".", 1)[-1] if category else result_type,
            alt_names=tuple(n for n in (own_component,) if n),
            address=address,
            bounding_box=_bounding_box(result.get("bbox")),
        )

    def search_destination(self, client: httpx.Client, query: str) -> GeocodeHit | None:
        result = self._first_result(client, {"text": query})
        return self._hit(result) if result is not None else None

    def search_named_place(
        self,
        client: httpx.Client,
        query: str,
        *,
        near: GeoPoint | None = None,
        bounding_box: BoundingBox | None = None,
    ) -> GeocodeHit | None:
        params: dict[str, Any] = {"text": query}
        if bounding_box is not None:
            south, north, west, east = bounding_box
            params["filter"] = f"rect:{west},{south},{east},{north}"
        elif near is not None:
            params["filter"] = f"circle:{near.lng},{near.lat},{_NAMED_PLACE_RADIUS_METERS}"

        result = self._first_result(client, params)
        if result is None or not _is_specific_named_match(result):
            return None
        hit = self._hit(result)
        return hit if hit is not None and hit.name else None


def _is_specific_named_match(result: dict[str, Any]) -> bool:
    """True only when Geoapify itself says it matched the named feature --
    not a street/postcode/city-level fallback, and not a low-confidence
    fuzzy match. Missing evidence is treated as no match."""
    if result.get("result_type") not in _NAMED_PLACE_RESULT_TYPES:
        return False
    rank = result.get("rank")
    if not isinstance(rank, dict) or rank.get("match_type") not in _NAMED_PLACE_MATCH_TYPES:
        return False
    confidence = rank.get("confidence")
    return isinstance(confidence, (int, float)) and confidence >= _NAMED_PLACE_MIN_CONFIDENCE
