"""Nominatim geocoder (development/test default; Section 203C.1).

The same two `/search` requests `OpenStreetMapPlacesAdapter` used to make
itself, now behind `GeocodingProvider`, plus what the public
nominatim.openstreetmap.org usage policy requires: at most one request per
second, never in parallel, and no further requests after an HTTP 429 until
a cooldown has passed. The identifying User-Agent is set on the shared
client by the caller. Not the production geocoder (see
`core/operational_config.py`).
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

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

# Identity of a Nominatim result is a genuine OSM `type/id`, so a place
# found this way keeps the OpenStreetMap places source.
_PLACE_SOURCE = "openstreetmap_places"


class _RequestGuard(CooldownBreaker):
    """Serialises and paces every Nominatim request in this process."""

    min_interval_seconds = 1.0

    def __init__(self) -> None:
        super().__init__(error_cls=GeocoderError)
        self._request_lock = threading.Lock()
        self._last_request_at: float | None = None

    def reset(self) -> None:
        super().reset()
        self._last_request_at = None

    @contextmanager
    def slot(self) -> Iterator[None]:
        with self._request_lock:
            self.check()
            if self._last_request_at is not None:
                wait = self.min_interval_seconds - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    time.sleep(wait)
            try:
                yield
            finally:
                self._last_request_at = time.monotonic()


request_guard = _RequestGuard()


def _parse_bounding_box(raw: Any) -> BoundingBox | None:
    """Nominatim's `boundingbox` is `[south, north, west, east]` as strings.
    None (never a guessed box) if missing or malformed."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        south, north, west, east = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    return south, north, west, east


class NominatimGeocoder(GeocodingProvider):
    provider_name = "nominatim"
    display_name = "Nominatim"
    cache_source = "openstreetmap_geocode"

    def __init__(self) -> None:
        self._base_url = get_settings().nominatim_api_url

    def destination_cache_query(self, query: str) -> dict[str, Any]:
        # Unchanged from Step 164E/202B.1, so existing cache entries stay valid.
        return {
            "query": query.strip().lower(),
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            "namedetails": 1,
            "accept-language": "en",
        }

    def _first_result(self, client: httpx.Client, params: dict[str, Any]) -> dict[str, Any] | None:
        with request_guard.slot():
            try:
                response = client.get(f"{self._base_url}/search", params=params)
                if getattr(response, "status_code", None) == 429:
                    headers = getattr(response, "headers", None) or {}
                    request_guard.trip("rate_limited", headers.get("Retry-After"))
                    raise GeocoderError("rate_limited")
                response.raise_for_status()
                results = response.json()
            except httpx.TimeoutException:
                raise GeocoderError("timeout") from None
            except httpx.HTTPStatusError as exc:
                raise GeocoderError("server" if exc.response.status_code >= 500 else "bad_request") from None
            except httpx.HTTPError:
                raise GeocoderError("network") from None
            except ValueError:
                raise GeocoderError("malformed") from None
        if not results:
            return None
        if not isinstance(results, list) or not isinstance(results[0], dict):
            raise GeocoderError("malformed")
        return results[0]

    @staticmethod
    def _coordinates(result: dict[str, Any]) -> tuple[float, float] | None:
        lat, lon = result.get("lat"), result.get("lon")
        if lat is None or lon is None:
            return None
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            raise GeocoderError("malformed") from None

    def search_destination(self, client: httpx.Client, query: str) -> GeocodeHit | None:
        result = self._first_result(
            client,
            {
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                # Section 202B.1: structural provider evidence for the
                # plausibility decision, in English so an English query
                # compares against English provider names.
                "addressdetails": 1,
                "namedetails": 1,
                "accept-language": "en",
            },
        )
        coordinates = self._coordinates(result) if result else None
        if result is None or coordinates is None:
            return None

        namedetails = result.get("namedetails")
        address = result.get("address")
        name = result.get("name")
        return GeocodeHit(
            lat=coordinates[0],
            lon=coordinates[1],
            name=name if isinstance(name, str) else "",
            display_name=result.get("display_name") or "",
            source=_PLACE_SOURCE,
            provider_place_id=_place_id(result),
            feature_class=result.get("category") or result.get("class"),
            feature_type=result.get("type"),
            alt_names=tuple(v for v in namedetails.values() if isinstance(v, str))
            if isinstance(namedetails, dict)
            else (),
            address={k: v for k, v in address.items() if isinstance(v, str)} if isinstance(address, dict) else {},
            bounding_box=_parse_bounding_box(result.get("boundingbox")),
        )

    def search_named_place(
        self,
        client: httpx.Client,
        query: str,
        *,
        near: GeoPoint | None = None,
        bounding_box: BoundingBox | None = None,
    ) -> GeocodeHit | None:
        result = self._first_result(
            client,
            {
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "namedetails": 1,
                # Section 202C.1C: the place's own OSM tags, in the same single
                # request. Without them a targeted-lookup result reached scoring
                # with no provider evidence at all (no wikipedia/heritage/type),
                # so a nationally significant museum ranked below minor objects.
                "extratags": 1,
            },
        )
        coordinates = self._coordinates(result) if result else None
        if result is None or coordinates is None:
            return None

        namedetails = result.get("namedetails") or {}
        display_name = result.get("display_name") or ""
        name = namedetails.get("name") or display_name.split(",")[0].strip()
        if not name:
            return None

        # The primary OSM key/value Nominatim reports for the object
        # (`category`/`class` + `type`, e.g. tourism=museum) plus its
        # extratags; the caller whitelists them.
        tags: dict[str, Any] = {}
        extratags = result.get("extratags")
        if isinstance(extratags, dict):
            tags.update(extratags)
        primary_key = result.get("category") or result.get("class")
        primary_value = result.get("type")
        if isinstance(primary_key, str) and isinstance(primary_value, str):
            tags[primary_key] = primary_value

        return GeocodeHit(
            lat=coordinates[0],
            lon=coordinates[1],
            name=name,
            display_name=display_name,
            source=_PLACE_SOURCE,
            provider_place_id=_place_id(result),
            feature_class=result.get("class"),
            feature_type=result.get("type"),
            tags=tags,
        )


def _place_id(result: dict[str, Any]) -> str:
    osm_type = result.get("osm_type")
    osm_id = result.get("osm_id")
    if osm_type and osm_id is not None:
        return f"{osm_type}/{osm_id}"
    return f"nominatim/{result.get('place_id')}"
