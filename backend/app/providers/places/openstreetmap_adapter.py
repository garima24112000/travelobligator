from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import NormalizedPlace, ProviderResponse
from app.providers.base import PlacesProvider, failed_response, unavailable_response

logger = logging.getLogger(__name__)

_USER_AGENT = "TravelObligator/0.1 (dev; legit-data-only)"
_SEARCH_RADIUS_METERS = 6000
_MAX_RESULTS = 20
_PARTIAL_RESULT_THRESHOLD = 3
_REQUEST_TIMEOUT_SECONDS = 15.0

_ATTRACTION_TAG_FILTERS = [
    '"tourism"~"attraction|museum|gallery|viewpoint|artwork|zoo|theme_park"',
    '"historic"',
]
_RESTAURANT_TAG_FILTERS = [
    '"amenity"~"restaurant|cafe|fast_food|bar|pub"',
]
_ACCOMMODATION_TAG_FILTERS = [
    '"tourism"~"hotel|hostel|guest_house|motel|apartment|chalet"',
]


class OpenStreetMapPlacesAdapter(PlacesProvider):
    """PlacesProvider backed by OpenStreetMap/Overpass open data
    (docs/07_production_data_sources.md section 5/7, docs/12_provider_architecture.md
    section 10).

    Only `search_attractions`, `search_restaurants`, and
    `search_accommodation_pois` are implemented. `search_places` and
    `get_place_details` fall back to the base class's honest
    `not_connected` response.

    Only real Overpass elements that have a `name` tag are returned. No
    rating, opening hours, price level, or review data is fabricated;
    Overpass does not reliably supply those fields so they are simply
    omitted rather than guessed.
    """

    provider_name = "openstreetmap_places"

    def __init__(self) -> None:
        settings = get_settings()
        self._overpass_url = settings.overpass_api_url
        self._nominatim_url = settings.nominatim_api_url
        self._geocode_cache: dict[str, GeoPoint] = {}

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._search(destination, _ATTRACTION_TAG_FILTERS, "attractions")

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._search(area, _RESTAURANT_TAG_FILTERS, "restaurants")

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._search(destination, _ACCOMMODATION_TAG_FILTERS, "accommodation_pois")

    def _search(
        self, place_name: str, tag_filters: list[str], field_name: str
    ) -> ProviderResponse[Any]:
        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT}
            ) as client:
                point = self._geocode(client, place_name)
                if point is None:
                    return unavailable_response(
                        self.provider_name,
                        self.provider_type,
                        unavailable_fields=[field_name],
                        message=f"Could not resolve a location for '{place_name}' via Nominatim.",
                    )
                elements = self._query_overpass(client, point, tag_filters)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OpenStreetMap request failed for %s: %s", place_name, exc)
            return failed_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=f"OpenStreetMap/Overpass request failed for '{place_name}'.",
            )

        places = self._normalize(elements)
        if not places:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=f"OpenStreetMap returned no named {field_name.replace('_', ' ')} for '{place_name}'.",
            )

        is_partial = len(places) < _PARTIAL_RESULT_THRESHOLD
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.PARTIAL if is_partial else ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            unavailable_fields=[],
            confidence=0.4 if is_partial else 0.65,
            message=f"{len(places)} {field_name.replace('_', ' ')} found via OpenStreetMap/Overpass.",
        )

    def _geocode(self, client: httpx.Client, place_name: str) -> GeoPoint | None:
        cached = self._geocode_cache.get(place_name)
        if cached is not None:
            return cached

        response = client.get(
            f"{self._nominatim_url}/search",
            params={"q": place_name, "format": "json", "limit": 1},
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            return None

        point = GeoPoint(lat=float(results[0]["lat"]), lng=float(results[0]["lon"]))
        self._geocode_cache[place_name] = point
        return point

    def _query_overpass(
        self, client: httpx.Client, point: GeoPoint, tag_filters: list[str]
    ) -> list[dict[str, Any]]:
        clauses = "".join(
            f"node(around:{_SEARCH_RADIUS_METERS},{point.lat},{point.lng})[{tag}];"
            f"way(around:{_SEARCH_RADIUS_METERS},{point.lat},{point.lng})[{tag}];"
            for tag in tag_filters
        )
        query = f"[out:json][timeout:20];({clauses});out center {_MAX_RESULTS};"

        response = client.post(self._overpass_url, data={"data": query})
        response.raise_for_status()
        payload = response.json()
        return payload.get("elements", [])

    def _normalize(self, elements: list[dict[str, Any]]) -> list[NormalizedPlace]:
        places: list[NormalizedPlace] = []
        seen_ids: set[str] = set()

        for element in elements:
            tags = element.get("tags") or {}
            name = tags.get("name")
            if not name:
                continue

            place_id = f"{element.get('type')}/{element.get('id')}"
            if place_id in seen_ids:
                continue

            lat = element.get("lat")
            lon = element.get("lon")
            if lat is None or lon is None:
                center = element.get("center") or {}
                lat = center.get("lat")
                lon = center.get("lon")
            if lat is None or lon is None:
                continue

            category = tags.get("tourism") or tags.get("amenity") or tags.get("historic")
            address = _format_address(tags)

            places.append(
                NormalizedPlace(
                    place_id=place_id,
                    name=name,
                    category=category,
                    coordinates=GeoPoint(lat=float(lat), lng=float(lon)),
                    address=address,
                    source=self.provider_name,
                    data_status=DataStatus.LIVE,
                    confidence=0.6,
                )
            )
            seen_ids.add(place_id)

            if len(places) >= _MAX_RESULTS:
                break

        return places


def _format_address(tags: dict[str, str]) -> str | None:
    parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:city"),
    ]
    present = [part for part in parts if part]
    return ", ".join(present) if present else None
