from __future__ import annotations

import logging
import re
from typing import Any, NamedTuple

import httpx

from app.core.config import get_settings
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import NormalizedPlace, ProviderResponse
from app.providers.base import PlacesProvider, failed_response, unavailable_response
from app.storage.provider_cache_store import (
    ProviderCacheStore,
    get_provider_cache_store,
    make_query_hash,
)
from app.utils.geo import haversine_distance_km, point_in_bounding_box

logger = logging.getLogger(__name__)

_USER_AGENT = "TravelObligator/0.1 (dev; legit-data-only)"
# Step 164E: source label for the persistent geocode cache -- distinct from
# `OpenStreetMapPlacesAdapter.provider_name` ("openstreetmap_places"), which
# still labels every `ProviderResponse` this adapter returns (attractions,
# restaurants, accommodation POIs, must-visit lookups). Only destination
# geocoding (`_resolve_destination`) is cached under this source.
_GEOCODE_CACHE_SOURCE = "openstreetmap_geocode"
# Step 164G: source label for the persistent Overpass POI search cache --
# one row per (point, radius, tag set) Overpass query, whether it's the
# primary query or a single fallback tag query. `search_must_visit_place`'s
# `_lookup_named_place` (a Nominatim search, not Overpass) is not cached
# under this source or any other.
_POI_CACHE_SOURCE = "openstreetmap_poi"
_SEARCH_RADIUS_METERS = 6000
_FALLBACK_SEARCH_RADIUS_METERS = 12000
_MAX_RESULTS = 20
_PARTIAL_RESULT_THRESHOLD = 3
_REQUEST_TIMEOUT_SECONDS = 15.0

# Minimum word length counted as "significant" when checking whether a
# geocode result actually relates to the requested destination (Step
# 155C). Filters out trivial short tokens ("of", "de", "la") while still
# counting real destination words like "new".
_MIN_PLAUSIBLE_TOKEN_LENGTH = 3

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
# rating/price/review/opening-hour fields are ever attached. A wider radius
# still never means "unrelated destination": every fallback result is still
# geographically contained to the resolved destination (see
# `_is_within_destination`), same as primary results.
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


class _ResolvedDestination(NamedTuple):
    """A destination geocode result that has passed the plausibility check
    in `_is_plausible_geocode_match` (Step 155C). `bounding_box` is
    `(south, north, west, east)` when Nominatim returned one; `None`
    otherwise, in which case containment falls back to a radius check
    around `point`.
    """

    point: GeoPoint
    bounding_box: tuple[float, float, float, float] | None
    display_name: str


def _significant_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= _MIN_PLAUSIBLE_TOKEN_LENGTH
    }


def _is_plausible_geocode_match(query: str, display_name: str) -> bool:
    """Conservative plausibility check for a Nominatim geocode result
    (Step 155C).

    Requires at least one significant (3+ character) word from `query` to
    also appear in the resolved `display_name`. This is deliberately
    simple -- no fuzzy matching, no LLM -- but is enough to reject a
    geocode result that shares nothing in common with what was actually
    asked for (e.g. a vague/degenerate query resolving to an unrelated
    place in a different country, with a `display_name` that shares no
    words with the query at all). Returns False (never a guessed match)
    when `query` has no significant tokens to check against, since there
    is nothing conservative left to verify.
    """
    query_tokens = _significant_tokens(query)
    if not query_tokens:
        return False
    return bool(query_tokens & _significant_tokens(display_name))


def _parse_bounding_box(raw: Any) -> tuple[float, float, float, float] | None:
    """Parses Nominatim's `boundingbox` field (`[south, north, west,
    east]` as strings) into floats. Returns None (never a guessed box) if
    the field is missing or malformed.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        south, north, west, east = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    return south, north, west, east


def _is_within_destination(
    point: GeoPoint, resolved: _ResolvedDestination, radius_meters: float
) -> bool:
    """True if `point` is geographically contained within `resolved`:
    inside its Nominatim bounding box when one is available (more
    precise), otherwise within `radius_meters` of the resolved
    destination point. Used to filter every attraction/restaurant/
    accommodation/must-visit result so a place is never accepted as
    provider-backed for a destination it isn't actually located in (Step
    155C) -- even one that came back from a real, named Overpass/Nominatim
    result.
    """
    if resolved.bounding_box is not None:
        return point_in_bounding_box(point, resolved.bounding_box)
    distance_km = haversine_distance_km(resolved.point, point)
    return distance_km is not None and distance_km <= (radius_meters / 1000.0)


class OpenStreetMapPlacesAdapter(PlacesProvider):
    """PlacesProvider backed by OpenStreetMap/Overpass open data
    (docs/07_production_data_sources.md section 5/7, docs/12_provider_architecture.md
    section 10).

    Only `search_attractions`, `search_restaurants`,
    `search_accommodation_pois`, `search_must_visit_place`, and
    `resolve_coordinates` are implemented. `search_places` and
    `get_place_details` fall back to the base class's honest
    `not_connected` response. `resolve_coordinates` is a thin public wrapper
    around the same cached `_resolve_destination` Nominatim lookup the
    search methods already use, so other providers/services (e.g.
    WeatherProvider) can reuse real destination coordinates without a
    second geocoding implementation.

    Destination resolution is conservative (Step 155C, fixing a bug where
    an under-resolved/degenerate destination string silently anchored
    every subsequent POI search to an unrelated place in a different
    country): every Nominatim geocode result is checked by
    `_is_plausible_geocode_match` before being trusted -- the result's
    `display_name` must share at least one significant word with the
    query. If it doesn't (or Nominatim finds nothing), destination
    resolution honestly fails and every dependent field is reported
    `unavailable` instead of silently using an unrelated location.

    Once a destination is resolved, every candidate place (attractions,
    restaurants, accommodation POIs, and the must-visit targeted lookup)
    is additionally required to be geographically contained within it --
    inside its Nominatim bounding box when available, otherwise within the
    query's search radius of the resolved point (`_is_within_destination`).
    A named, coordinate-backed Overpass/Nominatim result that falls
    outside that containment is discarded rather than used, even though it
    is real provider data -- it just isn't data *for this destination*.
    There is no broad token-fallback retry (e.g. retrying a failed "New
    York" query with just "New"): if the full destination string can't be
    confidently resolved, the field is reported unavailable, never guessed
    at with a shorter/looser query.

    `search_must_visit_place` is a targeted lookup for one explicit
    must-visit place, used only as a fallback when general attraction
    search misses it. It geocodes `"{must_visit_term}, {primary_destination}"`
    directly via Nominatim -- never a global, destination-unconstrained
    search -- and additionally requires the resolved destination itself,
    plus containment of the found place within it, before accepting the
    result. It returns at most one real, named, coordinate-backed,
    destination-contained place; if Nominatim finds nothing, the
    destination can't be resolved, or the found place is outside the
    resolved destination, it honestly reports that instead of inventing or
    substituting an unrelated place.

    Only real Overpass elements that have a `name` tag are returned. No
    rating, opening hours, price level, or review data is fabricated;
    Overpass does not reliably supply those fields so they are simply
    omitted rather than guessed.

    For `search_attractions`, `search_restaurants`, and
    `search_accommodation_pois`, if the primary Overpass query fails
    (request error) or returns no usable named *and contained* results,
    conservative fallback tag filters are attempted using a broader set of
    safe, well-established OSM tags at a wider search radius (still
    subject to the exact same containment check). Fallback tag filters are
    queried one at a time (not combined into a single large query), so one
    oversized/failing query can't take the whole field down with it: named
    results are aggregated across every fallback tag query that succeeds,
    deduplicated by `place_id`, and capped at `_MAX_RESULTS`. Fallback
    results are normalized through the exact same code path as primary
    results, so they are still real, named, provider-backed, contained
    places — never invented — and the response honestly reports
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

    Geocode cache (Step 164E, docs/12_provider_architecture.md "Provider
    Cache Foundation" section): `_resolve_destination`'s Nominatim
    destination lookup -- the geocoding step used by `_search`,
    `resolve_coordinates`, and `search_must_visit_place` -- is cached in
    `ProviderCacheStore` under source `"openstreetmap_geocode"`, keyed by a
    hash of the normalized query text (plus the fixed `format`/`limit`
    params sent to Nominatim). This sits underneath the existing
    per-instance `self._destination_cache` dict (checked first, unchanged)
    and extends it across process restarts/dev runs. Only a successfully
    resolved, plausibility-checked destination is cached -- an unresolved
    or implausible geocode result is never cached, and never fabricated.

    POI cache (Step 164G, docs/12_provider_architecture.md "Provider Cache
    Foundation" section): each individual Overpass query run by
    `_try_query` -- one per (resolved point, search radius, tag filter set)
    -- is additionally cached under source `"openstreetmap_poi"`, keyed by
    a hash of that normalized request. This covers the primary query for
    `search_attractions`/`search_restaurants`/`search_accommodation_pois`
    and every individual fallback tag query, but not
    `search_must_visit_place`'s Nominatim named-place lookup. Only a
    successful query with at least one named, destination-contained result
    is cached; an empty or failed query is never cached, and no rating,
    price, opening hours, availability, booking link, or route time is ever
    added to a cached place -- the normalized shape and content are
    identical to what the live Overpass path already produces. Cache reads/
    writes never fail POI search: a broken cache read falls back to the
    live Overpass request, and a broken cache write still returns the live
    result. The overall `ProviderResponse.status`/`data_status` returned by
    `search_attractions`/`search_restaurants`/`search_accommodation_pois`
    is unchanged by caching -- it still reflects only whether fallback was
    needed, exactly as before this step; only each cached
    `NormalizedPlace.data_status` is relabeled `"cached"` on a hit, which
    nothing in `CandidateQualityService`, `ExperiencePlannerService`, or
    `provider_coverage`/`data_sources_used` filters or gates on.
    """

    provider_name = "openstreetmap_places"

    def __init__(self, cache_store: ProviderCacheStore | None = None) -> None:
        settings = get_settings()
        self._overpass_url = settings.overpass_api_url
        self._nominatim_url = settings.nominatim_api_url
        self._destination_cache: dict[str, _ResolvedDestination] = {}
        self._cache_enabled = settings.provider_cache_enabled
        self._geocode_cache_ttl_seconds = settings.osm_geocode_cache_ttl_seconds
        self._poi_cache_ttl_seconds = settings.osm_poi_cache_ttl_seconds
        self._cache_path = settings.resolved_provider_cache_path()
        self._cache_store = cache_store

    def _resolve_cache_store(self) -> ProviderCacheStore | None:
        """Lazily resolves the shared cache store, or `None` when the cache
        is disabled entirely. Injecting `cache_store` in the constructor
        bypasses this lazy resolution."""
        if not self._cache_enabled:
            return None
        if self._cache_store is None:
            self._cache_store = get_provider_cache_store(self._cache_path)
        return self._cache_store

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
                resolved = self._resolve_destination(client, primary_destination)
                if resolved is None:
                    return unavailable_response(
                        self.provider_name,
                        self.provider_type,
                        unavailable_fields=[field_name],
                        message=(
                            f"Could not confidently resolve the destination "
                            f"'{primary_destination}', so the must-visit place "
                            f"'{must_visit_term}' cannot be grounded to it."
                        ),
                    )
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

        if place.coordinates is None or not _is_within_destination(
            place.coordinates, resolved, _FALLBACK_SEARCH_RADIUS_METERS
        ):
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"OpenStreetMap found a place for '{query}', but it is outside the "
                    f"resolved destination '{primary_destination}', so it was not used."
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

    def resolve_coordinates(self, destination: str) -> GeoPoint | None:
        """Best-effort geocode of `destination` for other providers/services
        (e.g. WeatherProvider) that need real coordinates, reusing the exact
        same cached, plausibility-checked Nominatim resolution already used
        by `search_attractions`/`search_restaurants`/
        `search_accommodation_pois` -- never a second, duplicated geocoding
        implementation. Returns None (never a guessed coordinate) if
        resolution finds nothing, isn't a plausible match for `destination`,
        or the request fails.
        """
        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT}
            ) as client:
                resolved = self._resolve_destination(client, destination)
                return resolved.point if resolved is not None else None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OpenStreetMap geocoding failed for %s: %s", destination, exc)
            return None

    def _lookup_named_place(self, client: httpx.Client, query: str) -> NormalizedPlace | None:
        """Look up exactly one named, coordinate-backed place for `query` via
        Nominatim's search endpoint. Returns None (never a guessed place) if
        Nominatim has no usable result. `query` is always the must-visit term
        combined with the trip's primary destination, so this never falls
        back to an unconstrained global search that could resolve to the
        wrong city. (Containment against the resolved destination is
        checked by the caller, `search_must_visit_place`, not here.)
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
                resolved = self._resolve_destination(client, place_name)
                if resolved is None:
                    return unavailable_response(
                        self.provider_name,
                        self.provider_type,
                        unavailable_fields=[field_name],
                        message=(
                            f"Could not confidently resolve a location for "
                            f"'{place_name}' via Nominatim."
                        ),
                    )

                primary_places, primary_failed = self._try_query(
                    client, resolved, tag_filters, _SEARCH_RADIUS_METERS, place_name
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
                    client, resolved, fallback_tag_filters, _FALLBACK_SEARCH_RADIUS_METERS, place_name
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
        resolved: _ResolvedDestination,
        tag_filters: list[str],
        radius_meters: int,
        place_name: str,
    ) -> tuple[list[NormalizedPlace], bool]:
        """Run one Overpass query, normalize it, and keep only results
        geographically contained within `resolved` (see
        `_is_within_destination`) -- a real, named Overpass result that
        falls outside the resolved destination is discarded here rather
        than returned as provider-backed data for this destination.

        Returns `(places, request_failed)`. Request-level failures are
        caught here, rather than left to propagate, so a fallback query can
        still be attempted after a primary failure.

        Step 164G: this exact (point, radius, tag set) query is cached in
        `ProviderCacheStore` -- checked first, and written to only on a
        successful, non-empty result. A cache hit returns `(places, False)`
        without calling Overpass at all.
        """
        poi_query_hash = make_query_hash(
            {
                "lat": resolved.point.lat,
                "lon": resolved.point.lng,
                "radius_meters": radius_meters,
                "tags": sorted(tag_filters),
                "limit": _MAX_RESULTS,
            }
        )
        cache_store = self._resolve_cache_store()

        if cache_store is not None:
            cached_places = self._read_poi_cache(cache_store, poi_query_hash)
            if cached_places is not None:
                return cached_places, False

        try:
            elements = self._query_overpass(client, resolved.point, tag_filters, radius_meters)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OpenStreetMap Overpass query failed for %s: %s", place_name, exc)
            return [], True

        places = self._normalize(elements)
        contained_places = [
            place
            for place in places
            if place.coordinates is not None
            and _is_within_destination(place.coordinates, resolved, radius_meters)
        ]

        if cache_store is not None and contained_places:
            self._write_poi_cache(cache_store, poi_query_hash, contained_places)

        return contained_places, False

    def _try_fallback_queries(
        self,
        client: httpx.Client,
        resolved: _ResolvedDestination,
        tag_filters: list[str],
        radius_meters: int,
        place_name: str,
    ) -> tuple[list[NormalizedPlace], bool]:
        """Run each fallback tag filter as its own Overpass query, one at a
        time, instead of combining them into a single large query.

        A single oversized Overpass query can fail (timeout/error) as a
        whole even when some of its tag filters would have succeeded on
        their own. Running tags individually means one failing or empty tag
        filter never sinks the others: named, destination-contained results
        (via `_try_query`, which applies `_is_within_destination` per tag
        query) are aggregated across every tag query that does succeed,
        deduplicated by `place_id`, and capped at `_MAX_RESULTS` (stopping
        early once reached, so remaining tag filters aren't queried
        unnecessarily).

        Returns `(places, any_request_failed)`. `any_request_failed` is
        only used by the caller when `places` ends up empty, to decide
        between an honest `failed` vs `unavailable` response.
        """
        places: list[NormalizedPlace] = []
        seen_ids: set[str] = set()
        any_request_failed = False

        for tag in tag_filters:
            tag_places, tag_failed = self._try_query(
                client, resolved, [tag], radius_meters, place_name
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
                f"OpenStreetMap returned no named, destination-contained {field_label} for "
                f"'{place_name}'{fallback_note}."
            ),
        )

    def _resolve_destination(
        self, client: httpx.Client, place_name: str
    ) -> _ResolvedDestination | None:
        """Conservatively geocodes `place_name` via Nominatim (Step 155C).

        Requires the result's `display_name` to plausibly relate to the
        query (`_is_plausible_geocode_match`) before trusting it -- this is
        what stops a degenerate/under-specified destination string from
        silently resolving to an unrelated place (e.g. in a different
        country) and anchoring every subsequent POI search there. Returns
        None (never a guessed location) if Nominatim finds nothing, the
        request fails, or the top result isn't a plausible match -- callers
        report the destination/field as unavailable in that case rather
        than using an unrelated location. Never falls back to a shorter or
        looser query (e.g. retrying with just the first word) -- exactly
        `place_name` is geocoded, once, and the result is cached under that
        exact string.

        Step 164E: underneath this per-instance dict, a successful
        resolution is also read from/written to the persistent
        `ProviderCacheStore` (source `"openstreetmap_geocode"`), so a later
        process/dev run can reuse it too. That cache is checked only after
        the in-memory dict misses, and is skipped entirely when disabled.
        """
        cached = self._destination_cache.get(place_name)
        if cached is not None:
            return cached

        query_hash = make_query_hash(
            {
                "query": place_name.strip().lower(),
                "format": "jsonv2",
                "limit": 1,
            }
        )
        cache_store = self._resolve_cache_store()

        if cache_store is not None:
            persisted = self._read_geocode_cache(cache_store, query_hash)
            if persisted is not None:
                self._destination_cache[place_name] = persisted
                return persisted

        response = client.get(
            f"{self._nominatim_url}/search",
            params={"q": place_name, "format": "jsonv2", "limit": 1},
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

        display_name = result.get("display_name") or ""
        if not _is_plausible_geocode_match(place_name, display_name):
            logger.warning(
                "Rejecting implausible OpenStreetMap/Nominatim geocode match for %r: "
                "display_name=%r",
                place_name,
                display_name,
            )
            return None

        resolved = _ResolvedDestination(
            point=GeoPoint(lat=float(lat), lng=float(lon)),
            bounding_box=_parse_bounding_box(result.get("boundingbox")),
            display_name=display_name,
        )
        self._destination_cache[place_name] = resolved
        if cache_store is not None:
            self._write_geocode_cache(cache_store, query_hash, resolved)
        return resolved

    def _read_geocode_cache(
        self, cache_store: ProviderCacheStore, query_hash: str
    ) -> _ResolvedDestination | None:
        """Returns the cached geocode result, or `None` on a cache miss/
        expiry or a broken cache read -- either way, the caller falls back
        to the live Nominatim request rather than failing."""
        try:
            entry = cache_store.get(_GEOCODE_CACHE_SOURCE, query_hash)
        except Exception:
            logger.warning(
                "OpenStreetMap geocode cache read failed; falling back to live request."
            )
            return None

        if entry is None:
            return None

        try:
            payload = entry.payload
            bounding_box = payload.get("bounding_box")
            return _ResolvedDestination(
                point=GeoPoint(lat=payload["lat"], lng=payload["lng"]),
                bounding_box=tuple(bounding_box) if bounding_box is not None else None,
                display_name=payload["display_name"],
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "OpenStreetMap geocode cache entry was unusable; falling back to live request."
            )
            return None

    def _write_geocode_cache(
        self,
        cache_store: ProviderCacheStore,
        query_hash: str,
        resolved: _ResolvedDestination,
    ) -> None:
        """Best-effort cache write -- a failure here must never affect the
        already-computed live result being returned to the caller."""
        try:
            cache_store.set(
                _GEOCODE_CACHE_SOURCE,
                query_hash,
                {
                    "lat": resolved.point.lat,
                    "lng": resolved.point.lng,
                    "bounding_box": (
                        list(resolved.bounding_box) if resolved.bounding_box is not None else None
                    ),
                    "display_name": resolved.display_name,
                },
                ttl_seconds=self._geocode_cache_ttl_seconds,
            )
        except Exception:
            logger.warning(
                "OpenStreetMap geocode cache write failed; returning live result anyway."
            )

    def _read_poi_cache(
        self, cache_store: ProviderCacheStore, query_hash: str
    ) -> list[NormalizedPlace] | None:
        """Returns the cached POI list for one Overpass query, or `None` on
        a cache miss/expiry or a broken cache read -- either way, the
        caller falls back to the live Overpass request rather than
        failing."""
        try:
            entry = cache_store.get(_POI_CACHE_SOURCE, query_hash)
        except Exception:
            logger.warning("OpenStreetMap POI cache read failed; falling back to live request.")
            return None

        if entry is None:
            return None

        try:
            return [
                NormalizedPlace(**{**item, "data_status": DataStatus.CACHED})
                for item in entry.payload
            ]
        except (TypeError, ValueError):
            logger.warning(
                "OpenStreetMap POI cache entry was unusable; falling back to live request."
            )
            return None

    def _write_poi_cache(
        self,
        cache_store: ProviderCacheStore,
        query_hash: str,
        places: list[NormalizedPlace],
    ) -> None:
        """Best-effort cache write -- a failure here must never affect the
        already-computed live result being returned to the caller."""
        try:
            cache_store.set(
                _POI_CACHE_SOURCE,
                query_hash,
                [place.model_dump(mode="json") for place in places],
                ttl_seconds=self._poi_cache_ttl_seconds,
            )
        except Exception:
            logger.warning(
                "OpenStreetMap POI cache write failed; returning live result anyway."
            )

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
