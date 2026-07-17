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
# (well-established, common OSM tags) and queried at a wider search radius,
# so common destinations are less likely to come back empty. Each tag filter
# below is run as its own Overpass query (see `_try_fallback_queries`) rather
# than combined into one large query, so a single failing/empty tag can't
# sink the others. Fallback results go through the exact same `_normalize`
# step as primary results, so unnamed elements are still discarded and no
# rating/price/review/opening-hour fields are ever attached.
_ATTRACTION_FALLBACK_TAG_FILTERS = [
    '"tourism"~"attraction|museum|viewpoint"',
    '"historic"',
    '"amenity"="arts_centre"',
    '"leisure"="park"',
]
_RESTAURANT_FALLBACK_TAG_FILTERS = [
    '"amenity"~"restaurant|cafe|fast_food|bar|pub"',
]
_ACCOMMODATION_FALLBACK_TAG_FILTERS = [
    '"tourism"~"hotel|hostel|guest_house|motel|apartment|chalet|resort"',
]


class OpenStreetMapPlacesAdapter(PlacesProvider):
    """PlacesProvider backed by OpenStreetMap/Overpass open data
    (docs/07_production_data_sources.md section 5/7, docs/12_provider_architecture.md
    section 10).

    Only `search_attractions`, `search_restaurants`,
    `search_accommodation_pois`, and `search_must_visit_place` are
    implemented. `search_places` and `get_place_details` fall back to the
    base class's honest `not_connected` response.

    `search_must_visit_place` is a targeted lookup for one explicit
    must-visit place, used only as a fallback when general attraction
    search misses it. It geocodes `"{must_visit_term}, {primary_destination}"`
    directly via Nominatim -- never a global, destination-unconstrained
    search -- so it can't resolve to a same-named place in the wrong city.
    It returns at most one real, named, coordinate-backed place; if
    Nominatim finds nothing (or the request fails), it honestly reports
    that instead of inventing a place.

    Only real Overpass elements that have a `name` tag are returned. No
    rating, opening hours, price level, or review data is fabricated;
    Overpass does not reliably supply those fields so they are simply
    omitted rather than guessed.

    For `search_attractions`, `search_restaurants`, and
    `search_accommodation_pois`, if the primary Overpass query fails
    (request error) or returns no usable named results, conservative
    fallback tag filters are attempted using a broader set of safe,
    well-established OSM tags at a wider search radius. Fallback tag
    filters are queried one at a time (not combined into a single large
    query), so one oversized/failing query can't take the whole field down
    with it: named results are aggregated across every fallback tag query
    that succeeds, deduplicated by `place_id`, and capped at `_MAX_RESULTS`.
    Fallback results are normalized through the exact same code path as
    primary results, so they are still real, named, provider-backed places
    — never invented — and the response honestly reports
    `fallback_used`/`FALLBACK_USED` status so callers can tell fallback data
    from a primary result. If at least one fallback tag query returns usable
    named results, the field succeeds via fallback even if other fallback
    tag queries failed or came back empty. Only if the primary query and
    *every* fallback tag query fail or return nothing usable does the
    response honestly stay `failed`/`unavailable`. The accommodation
    fallback tags are limited to safe, well-established `tourism` values
    (hotel, hostel, guest_house, motel, apartment, chalet, resort) at the
    wider radius; accommodation POIs returned this way are still open-data
    location candidates only, never bookable inventory — no price,
    availability, rating, review, opening hours, or booking/reservation link
    is ever attached, exactly like the primary accommodation query.
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
        return self._search(
            destination,
            _ACCOMMODATION_TAG_FILTERS,
            "accommodation_pois",
            fallback_tag_filters=_ACCOMMODATION_FALLBACK_TAG_FILTERS,
        )

    def search_must_visit_place(
        self,
        must_visit_term: str,
        primary_destination: str,
        filters: dict[str, Any] | None = None,
    ) -> ProviderResponse[Any]:
        field_name = "must_visit_place"
        query = f"{must_visit_term}, {primary_destination}"

        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT}
            ) as client:
                place = self._lookup_named_place(client, query)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OpenStreetMap must-visit lookup failed for %s: %s", query, exc)
            return failed_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=f"OpenStreetMap/Nominatim request failed for '{query}'.",
            )

        if place is None:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"OpenStreetMap found no named place with coordinates for '{query}'."
                ),
            )

        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=[place],
            unavailable_fields=[],
            confidence=0.5,
            message=(
                f"Found a named OpenStreetMap place for must-visit term "
                f"'{must_visit_term}' via a targeted Nominatim lookup."
            ),
        )

    def _lookup_named_place(self, client: httpx.Client, query: str) -> NormalizedPlace | None:
        """Look up exactly one named, coordinate-backed place for `query` via
        Nominatim's search endpoint. Returns None (never a guessed place) if
        Nominatim has no usable result. `query` is always the must-visit term
        combined with the trip's primary destination, so this never falls
        back to an unconstrained global search that could resolve to the
        wrong city.
        """
        response = client.get(
            f"{self._nominatim_url}/search",
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "namedetails": 1,
            },
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            return None

        result = results[0]
        lat = result.get("lat")
        lon = result.get("lon")
        if lat is None or lon is None:
            return None

        namedetails = result.get("namedetails") or {}
        display_name = result.get("display_name") or ""
        name = namedetails.get("name") or display_name.split(",")[0].strip()
        if not name:
            return None

        osm_type = result.get("osm_type")
        osm_id = result.get("osm_id")
        place_id = (
            f"{osm_type}/{osm_id}"
            if osm_type and osm_id is not None
            else f"nominatim/{result.get('place_id')}"
        )

        return NormalizedPlace(
            place_id=place_id,
            name=name,
            category=result.get("type") or result.get("class"),
            coordinates=GeoPoint(lat=float(lat), lng=float(lon)),
            address=display_name or None,
            source=self.provider_name,
            data_status=DataStatus.LIVE,
            confidence=0.5,
        )

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

                fallback_places, fallback_failed = self._try_fallback_queries(
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

    def _try_fallback_queries(
        self,
        client: httpx.Client,
        point: GeoPoint,
        tag_filters: list[str],
        radius_meters: int,
        place_name: str,
    ) -> tuple[list[NormalizedPlace], bool]:
        """Run each fallback tag filter as its own Overpass query, one at a
        time, instead of combining them into a single large query.

        A single oversized Overpass query can fail (timeout/error) as a
        whole even when some of its tag filters would have succeeded on
        their own. Running tags individually means one failing or empty tag
        filter never sinks the others: named results are aggregated across
        every tag query that does succeed, deduplicated by `place_id`, and
        capped at `_MAX_RESULTS` (stopping early once reached, so remaining
        tag filters aren't queried unnecessarily).

        Returns `(places, any_request_failed)`. `any_request_failed` is
        only used by the caller when `places` ends up empty, to decide
        between an honest `failed` vs `unavailable` response.
        """
        places: list[NormalizedPlace] = []
        seen_ids: set[str] = set()
        any_request_failed = False

        for tag in tag_filters:
            tag_places, tag_failed = self._try_query(
                client, point, [tag], radius_meters, place_name
            )
            if tag_failed:
                any_request_failed = True
                continue

            for place in tag_places:
                if place.place_id in seen_ids:
                    continue
                places.append(place)
                seen_ids.add(place.place_id)
                if len(places) >= _MAX_RESULTS:
                    return places, any_request_failed

        return places, any_request_failed

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
