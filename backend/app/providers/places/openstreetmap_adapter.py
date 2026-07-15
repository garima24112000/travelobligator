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
_FALLBACK_SEARCH_RADIUS_METERS = 12000
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

# Conservative fallback tags used only when the primary query for that field
# fails or returns no *named* usable results. Kept deliberately broad-but-safe
# (well-established, common OSM tags) and combined with a wider search
# radius, so common destinations are less likely to come back empty. Fallback
# results go through the exact same `_normalize` step as primary results, so
# unnamed elements are still discarded and no rating/price/review/opening-hour
# fields are ever attached.
_ATTRACTION_FALLBACK_TAG_FILTERS = [
    '"tourism"~"attraction|museum|viewpoint"',
    '"historic"',
    '"amenity"="arts_centre"',
    '"leisure"="park"',
]
_RESTAURANT_FALLBACK_TAG_FILTERS = [
    '"amenity"~"restaurant|cafe|fast_food|bar|pub"',
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

    For `search_attractions` and `search_restaurants`, if the primary
    Overpass query fails (request error) or returns no usable named results,
    a conservative fallback query is attempted using a broader set of safe,
    well-established OSM tags at a wider search radius. Fallback results are
    normalized through the exact same code path as primary results, so they
    are still real, named, provider-backed places — never invented — and
    the response honestly reports `fallback_used`/`FALLBACK_USED` status so
    callers can tell fallback data from a primary result. If both the
    primary and fallback queries fail or return nothing usable, the response
    honestly stays `failed`/`unavailable`. `search_accommodation_pois` has no
    fallback query and is unchanged.
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
        return self._search(
            destination,
            _ATTRACTION_TAG_FILTERS,
            "attractions",
            fallback_tag_filters=_ATTRACTION_FALLBACK_TAG_FILTERS,
        )

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._search(
            area,
            _RESTAURANT_TAG_FILTERS,
            "restaurants",
            fallback_tag_filters=_RESTAURANT_FALLBACK_TAG_FILTERS,
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._search(destination, _ACCOMMODATION_TAG_FILTERS, "accommodation_pois")

    def _search(
        self,
        place_name: str,
        tag_filters: list[str],
        field_name: str,
        fallback_tag_filters: list[str] | None = None,
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

                primary_places, primary_failed = self._try_query(
                    client, point, tag_filters, _SEARCH_RADIUS_METERS, place_name
                )
                if primary_places:
                    return self._named_results_response(
                        primary_places, field_name, fallback_used=False
                    )

                if fallback_tag_filters is None:
                    return self._no_results_response(
                        field_name, place_name, request_failed=primary_failed
                    )

                fallback_places, fallback_failed = self._try_query(
                    client, point, fallback_tag_filters, _FALLBACK_SEARCH_RADIUS_METERS, place_name
                )
                if fallback_places:
                    return self._named_results_response(
                        fallback_places, field_name, fallback_used=True
                    )

                return self._no_results_response(
                    field_name,
                    place_name,
                    request_failed=primary_failed or fallback_failed,
                    fallback_attempted=True,
                )
        except (httpx.HTTPError, ValueError) as exc:
            # Only geocoding failures reach here; per-query Overpass failures
            # are caught in `_try_query` so a fallback can still be attempted.
            logger.warning("OpenStreetMap request failed for %s: %s", place_name, exc)
            return failed_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=f"OpenStreetMap/Overpass request failed for '{place_name}'.",
            )

    def _try_query(
        self,
        client: httpx.Client,
        point: GeoPoint,
        tag_filters: list[str],
        radius_meters: int,
        place_name: str,
    ) -> tuple[list[NormalizedPlace], bool]:
        """Run one Overpass query and normalize it.

        Returns `(places, request_failed)`. Request-level failures are
        caught here, rather than left to propagate, so a fallback query can
        still be attempted after a primary failure.
        """
        try:
            elements = self._query_overpass(client, point, tag_filters, radius_meters)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OpenStreetMap Overpass query failed for %s: %s", place_name, exc)
            return [], True
        return self._normalize(elements), False

    def _named_results_response(
        self, places: list[NormalizedPlace], field_name: str, fallback_used: bool
    ) -> ProviderResponse[Any]:
        is_partial = len(places) < _PARTIAL_RESULT_THRESHOLD
        field_label = field_name.replace("_", " ")

        if fallback_used:
            status = ProviderStatus.FALLBACK_USED
            data_status = DataStatus.FALLBACK_USED
            confidence = 0.3 if is_partial else 0.5
            message = (
                f"{len(places)} {field_label} found via OpenStreetMap/Overpass using a "
                "broader fallback query after the primary query returned no usable named "
                f"results.{' Only a few results were found.' if is_partial else ''}"
            )
        else:
            status = ProviderStatus.PARTIAL if is_partial else ProviderStatus.SUCCESS
            data_status = DataStatus.LIVE
            confidence = 0.4 if is_partial else 0.65
            message = f"{len(places)} {field_label} found via OpenStreetMap/Overpass."

        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=status,
            data_status=data_status,
            data=places,
            unavailable_fields=[],
            fallback_used=fallback_used,
            fallback_provider=self.provider_name if fallback_used else None,
            confidence=confidence,
            message=message,
        )

    def _no_results_response(
        self,
        field_name: str,
        place_name: str,
        request_failed: bool,
        fallback_attempted: bool = False,
    ) -> ProviderResponse[Any]:
        field_label = field_name.replace("_", " ")
        fallback_note = " (including a broader fallback query)" if fallback_attempted else ""

        if request_failed:
            return failed_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"OpenStreetMap/Overpass request failed for '{place_name}'{fallback_note}."
                ),
            )

        return unavailable_response(
            self.provider_name,
            self.provider_type,
            unavailable_fields=[field_name],
            message=(
                f"OpenStreetMap returned no named {field_label} for '{place_name}'"
                f"{fallback_note}."
            ),
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
        self,
        client: httpx.Client,
        point: GeoPoint,
        tag_filters: list[str],
        radius_meters: int = _SEARCH_RADIUS_METERS,
    ) -> list[dict[str, Any]]:
        clauses = "".join(
            f"node(around:{radius_meters},{point.lat},{point.lng})[{tag}];"
            f"way(around:{radius_meters},{point.lat},{point.lng})[{tag}];"
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
