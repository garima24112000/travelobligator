"""Geoapify Places adapter -- the production POI provider (Section 203C.2B).

Broad factual inventory (attractions, food, accommodation locations) comes
from Geoapify Places, filtered to the destination's real boundary. The
destination itself and every named place (must-visit term, AI anchor) are
resolved by the configured geocoder through `DestinationResolutionMixin`,
exactly as for the OpenStreetMap adapter.

Contracts:
  * Only what the response states is kept: name, coordinates, identity,
    Geoapify's own categories and any Wikipedia/Wikidata/heritage/access
    evidence it carries. Never a rating, price, opening hours, availability
    or booking link.
  * The order Geoapify returns places in is NOT a prominence ranking and is
    never used as one; ranking is the candidate-quality stage's job.
  * A successful non-empty query is cached (POI TTL); a successful EMPTY
    one is cached for a short time; a failed one is never cached.
  * Every request reserves credits from the generation's usage tracker
    first and is refused locally when the budget does not allow it.
  * No fallback to another provider.
"""

from __future__ import annotations

import copy
import logging
import math
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import NormalizedPlace, ProviderResponse
from app.providers.base import (
    PlacesProvider,
    failed_response,
    not_connected_response,
    unavailable_response,
)
from app.providers.errors import ProviderRequestError
from app.providers.geoapify_client import geoapify_get
from app.providers.geocoding.base import GeocoderError, GeocodingProvider
from app.providers.geocoding.factory import get_geocoding_provider
from app.providers.places.destination_resolution import (
    DestinationResolutionMixin,
    _is_within_destination,
    _normalize_text,
    _ResolvedDestination,
    _user_agent,
)
from app.providers.places.geoapify_categories import (
    ACCOMMODATION_GROUP,
    ATTRACTION_GROUPS,
    FOOD_GROUP,
    CategoryGroup,
    evidence_tags,
    most_specific_category,
    taxonomy_tags_from_categories,
)
from app.core.provider_usage import GenerationProviderContext, ProviderUsageTracker
from app.services.place_taxonomy import filter_provider_tags
from app.storage.provider_cache_store import (
    ProviderCacheStore,
    get_provider_cache_store,
    make_query_hash,
)
from app.utils.geo import haversine_distance_km

logger = logging.getLogger(__name__)

_POI_CACHE_SOURCE = "geoapify_places"
_DETAILS_CACHE_SOURCE = "geoapify_place_details"
_CACHE_SCHEMA = "203c2b-v1"
_LANGUAGE = "en"
_PLACES_PER_CREDIT = 20
_DEFAULT_ATTRACTION_POOL = 60
_MAX_ATTRACTION_POOL = 110
_DEFAULT_FOOD_POOL = 30
_MAX_FOOD_POOL = 40
_ACCOMMODATION_POOL = 20
_MIN_GROUP_LIMIT = 5
_PARTIAL_RESULT_THRESHOLD = 3
_CONTAINMENT_RADIUS_METERS = 25000
_SAME_PLACE_METERS = 100
_POOL_MATCH_METERS = 150
_CATEGORY_TAG_ORDER = ("tourism", "amenity", "historic", "leisure", "man_made")


def places_request_credits(limit: int, returned: int | None = None) -> int:
    """Geoapify Places pricing: 1 credit up to 20 places; above that, 1 plus
    one per 20 places returned. With `returned=None` this is the
    conservative amount to RESERVE before the request."""
    if limit <= _PLACES_PER_CREDIT:
        return 1
    count = limit if returned is None else returned
    return 1 + math.ceil(count / _PLACES_PER_CREDIT)


def _same_place(a: NormalizedPlace, b: NormalizedPlace, meters: float) -> bool:
    if _normalize_text(a.name) != _normalize_text(b.name):
        return False
    distance_km = haversine_distance_km(a.coordinates, b.coordinates) if a.coordinates and b.coordinates else None
    return distance_km is not None and distance_km * 1000.0 <= meters


def _dedupe(places: list[NormalizedPlace]) -> list[NormalizedPlace]:
    """Provider identity first, then normalised name + proximity."""
    kept: list[NormalizedPlace] = []
    seen_ids: set[str] = set()
    for place in places:
        if place.place_id in seen_ids or any(_same_place(place, other, _SAME_PLACE_METERS) for other in kept):
            continue
        seen_ids.add(place.place_id)
        kept.append(place)
    return kept


class GeoapifyPlacesAdapter(DestinationResolutionMixin, PlacesProvider):
    provider_name = "geoapify_places"
    display_name = "Geoapify Places"
    # `DestinationContextService` passes trip-derived pool sizes via `filters`.
    supports_inventory_sizing = True

    def __init__(
        self,
        cache_store: ProviderCacheStore | None = None,
        geocoder: GeocodingProvider | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = settings.geoapify_api_url.rstrip("/")
        self._api_key = (settings.geoapify_api_key or "").strip()
        self._timeout = settings.geoapify_timeout_seconds
        self._geocoder = geocoder or get_geocoding_provider()
        self._user_agent = _user_agent()
        self._destination_cache: dict[str, _ResolvedDestination] = {}
        self._cache_enabled = settings.provider_cache_enabled
        self._geocode_cache_ttl_seconds = settings.osm_geocode_cache_ttl_seconds
        self._poi_cache_ttl_seconds = settings.osm_poi_cache_ttl_seconds
        self._empty_cache_ttl_seconds = settings.geoapify_empty_result_cache_ttl_seconds
        self._cache_path = settings.resolved_provider_cache_path()
        self._cache_store = cache_store
        self._context: GenerationProviderContext | None = None
        self._usage: ProviderUsageTracker | None = None

    def bound_to(self, provider_context: GenerationProviderContext | None) -> "GeoapifyPlacesAdapter":
        """A view of this adapter for ONE generation: same caches, but every
        request (including the geocoder's) charges that generation's usage
        tracker."""
        if provider_context is None:
            return self
        bound = copy.copy(self)
        bound._context = provider_context
        bound._usage = provider_context.usage_tracker
        bound._geocoder = self._geocoder.bound_to(provider_context)
        return bound

    def _resolve_cache_store(self) -> ProviderCacheStore | None:
        if not self._cache_enabled:
            return None
        if self._cache_store is None:
            self._cache_store = get_provider_cache_store(self._cache_path)
        return self._cache_store

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout, headers={"User-Agent": self._user_agent})

    # -- broad inventory ------------------------------------------------------------

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        pool = int((filters or {}).get("pool_size") or _DEFAULT_ATTRACTION_POOL)
        pool = max(_MIN_GROUP_LIMIT, min(pool, _MAX_ATTRACTION_POOL))
        plan = [(group, max(_MIN_GROUP_LIMIT, round(group.share * pool))) for group in ATTRACTION_GROUPS]
        response = self._search(destination, plan, "attractions", page=int((filters or {}).get("page") or 0))
        if self._context is not None and response.data:
            self._context.place_pool.extend(response.data)
        return response

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        pool = int((filters or {}).get("pool_size") or _DEFAULT_FOOD_POOL)
        pool = max(_MIN_GROUP_LIMIT, min(pool, _MAX_FOOD_POOL))
        return self._search(area, [(FOOD_GROUP, pool)], "restaurants")

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._search(destination, [(ACCOMMODATION_GROUP, _ACCOMMODATION_POOL)], "accommodation_pois")

    def _search(
        self,
        destination: str,
        plan: list[tuple[CategoryGroup, int]],
        field_name: str,
        page: int = 0,
    ) -> ProviderResponse[Any]:
        failures: list[str] = []
        places: list[NormalizedPlace] = []
        try:
            with self._client() as client:
                resolved = self._resolve_destination(client, destination)
                if resolved is None:
                    unresolved = unavailable_response(
                        self.provider_name,
                        self.provider_type,
                        unavailable_fields=[field_name],
                        message=(
                            f"Could not confidently resolve a location for "
                            f"'{destination}' via {self._geocoder.display_name}."
                        ),
                    )
                    unresolved.failure_reason = "destination_unresolved"
                    return unresolved
                for group, limit in plan:
                    try:
                        places.extend(self._query_group(client, resolved, group, limit, page * limit))
                    except ProviderRequestError as exc:
                        failures.append(exc.kind)
        except GeocoderError as exc:
            return self._geocoder_failure_response(exc, field_name)

        contained = [
            place
            for place in _dedupe(places)
            if place.coordinates is not None
            and _is_within_destination(place.coordinates, resolved, _CONTAINMENT_RADIUS_METERS)
        ]
        field_label = field_name.replace("_", " ")
        if contained:
            partial = bool(failures) or len(contained) < _PARTIAL_RESULT_THRESHOLD
            return ProviderResponse[list[NormalizedPlace]](
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                status=ProviderStatus.PARTIAL if partial else ProviderStatus.SUCCESS,
                data_status=DataStatus.LIVE,
                data=contained,
                unavailable_fields=[],
                confidence=0.4 if partial else 0.65,
                message=f"{len(contained)} {field_label} found via {self.display_name}.",
            )
        if failures:
            return self._places_failure_response(failures[0], field_name)
        return unavailable_response(
            self.provider_name,
            self.provider_type,
            unavailable_fields=[field_name],
            message=f"{self.display_name} returned no named {field_label} inside '{destination}'.",
        )

    def _places_failure_response(self, kind: str, field_name: str) -> ProviderResponse[Any]:
        """An honest result for a failed Places request: names the POI
        provider (never the geocoder, never Overpass), no exception text."""
        logger.warning("Place data request failed (provider=%s, kind=%s).", self.provider_name, kind)
        if kind == "not_connected":
            response = not_connected_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message="Place data provider is not connected.",
            )
        else:
            response = failed_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=f"Place data provider ({self.display_name}) was unavailable.",
            )
        response.failure_reason = f"places_{kind}"
        return response

    def _destination_filters(self, resolved: _ResolvedDestination) -> list[str]:
        """Boundary filter first; the bounding box only as its fallback."""
        filters: list[str] = []
        place_id = resolved.provider_place_id or ""
        if place_id.startswith("geoapify/"):
            filters.append(f"place:{place_id.split('/', 1)[1]}")
        if resolved.bounding_box is not None:
            south, north, west, east = resolved.bounding_box
            filters.append(f"rect:{west},{south},{east},{north}")
        if not filters:
            filters.append(f"circle:{resolved.point.lng},{resolved.point.lat},{_CONTAINMENT_RADIUS_METERS}")
        return filters

    def _query_group(
        self,
        client: httpx.Client,
        resolved: _ResolvedDestination,
        group: CategoryGroup,
        limit: int,
        offset: int,
    ) -> list[NormalizedPlace]:
        destination_filters = self._destination_filters(resolved)
        for index, destination_filter in enumerate(destination_filters):
            try:
                return self._query(client, destination_filter, group, limit, offset)
            except ProviderRequestError as exc:
                # Only a refused filter is retried, once, with the next one.
                if exc.kind != "bad_request" or index == len(destination_filters) - 1:
                    raise
        return []

    def _query(
        self,
        client: httpx.Client,
        destination_filter: str,
        group: CategoryGroup,
        limit: int,
        offset: int,
    ) -> list[NormalizedPlace]:
        query_hash = make_query_hash(
            {
                "filter": destination_filter,
                "categories": sorted(group.categories),
                "limit": limit,
                "offset": offset,
                "lang": _LANGUAGE,
                "schema": _CACHE_SCHEMA,
            }
        )
        cache_store = self._resolve_cache_store()
        if cache_store is not None:
            cached = self._read_cache(cache_store, _POI_CACHE_SOURCE, query_hash)
            if cached is not None:
                return [NormalizedPlace(**{**item, "data_status": DataStatus.CACHED}) for item in cached]

        payload = geoapify_get(
            client,
            base_url=self._base_url,
            path="/v2/places",
            params={
                "categories": ",".join(group.categories),
                "filter": destination_filter,
                "conditions": "named",
                "limit": limit,
                "offset": offset,
                "lang": _LANGUAGE,
            },
            api_key=self._api_key,
            timeout=self._timeout,
            api="places",
            usage=self._usage,
            reserve_credits=places_request_credits(limit),
            actual_credits=lambda body: places_request_credits(
                limit, len(body.get("features")) if isinstance(body.get("features"), list) else limit
            ),
        )
        features = payload.get("features")
        if not isinstance(features, list):
            raise ProviderRequestError("malformed")

        places = [place for place in (self._normalize(feature) for feature in features) if place is not None]
        if cache_store is not None:
            # A successful EMPTY answer is remembered briefly so the same
            # empty category is not paid for again on the next generation.
            ttl = self._poi_cache_ttl_seconds if places else self._empty_cache_ttl_seconds
            self._write_cache(
                cache_store, _POI_CACHE_SOURCE, query_hash, [p.model_dump(mode="json") for p in places], ttl
            )
        return places

    def _normalize(self, feature: Any) -> NormalizedPlace | None:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            return None
        return self._place_from_properties(properties)

    def _place_from_properties(self, properties: dict[str, Any]) -> NormalizedPlace | None:
        name, place_id = properties.get("name"), properties.get("place_id")
        lat, lon = properties.get("lat"), properties.get("lon")
        if not isinstance(name, str) or not name.strip() or not isinstance(place_id, str) or not place_id:
            return None
        try:
            coordinates = GeoPoint(lat=float(lat), lng=float(lon))
        except (TypeError, ValueError):
            return None

        categories = [c for c in (properties.get("categories") or []) if isinstance(c, str)]
        tags = taxonomy_tags_from_categories(categories)
        datasource = properties.get("datasource")
        raw = datasource.get("raw") if isinstance(datasource, dict) else None
        if isinstance(raw, dict):
            # The underlying OpenStreetMap tags, when Geoapify returns them,
            # are more precise than the category rename.
            tags.update(filter_provider_tags(raw))
        tags.update(evidence_tags(properties))

        category = next((tags[key] for key in _CATEGORY_TAG_ORDER if tags.get(key)), None)
        if category is None:
            specific = most_specific_category(categories)
            category = specific.rsplit(".", 1)[-1] if specific else None
        formatted = properties.get("formatted")
        return NormalizedPlace(
            place_id=f"geoapify/{place_id}",
            name=name.strip(),
            category=category,
            coordinates=coordinates,
            address=formatted if isinstance(formatted, str) and formatted else None,
            source=self.provider_name,
            data_status=DataStatus.LIVE,
            confidence=0.6,
            provider_tags=tags or None,
        )

    # -- named places (must-visits, AI anchors) -----------------------------------------

    def search_must_visit_place(
        self,
        must_visit_term: str,
        primary_destination: str,
        filters: dict[str, Any] | None = None,
    ) -> ProviderResponse[Any]:
        """The geocoder grounds the named place (same rules as every places
        adapter). Then, without changing what was found:
          * if the same place is already in this generation's broad pool
            (same normalised name within ~150 m) the POOL place is returned,
            so one real place keeps one identity;
          * otherwise its category/evidence is read from Place Details,
            within the generation's bounded allowance.
        """
        response = super().search_must_visit_place(must_visit_term, primary_destination, filters)
        if not response.data:
            return response
        place = response.data[0]

        pool = self._context.place_pool if self._context is not None else []
        match = next((p for p in pool if _same_place(place, p, _POOL_MATCH_METERS)), None)
        if match is not None:
            response.data = [match]
            return response

        enriched = self._enrich_with_details(place)
        if enriched is not None:
            response.data = [enriched]
        return response

    def _enrich_with_details(self, place: NormalizedPlace) -> NormalizedPlace | None:
        if place.provider_tags or not place.place_id.startswith("geoapify/"):
            return None
        raw_id = place.place_id.split("/", 1)[1]
        query_hash = make_query_hash({"id": raw_id, "lang": _LANGUAGE, "schema": _CACHE_SCHEMA})
        cache_store = self._resolve_cache_store()
        properties: dict[str, Any] | None = None
        if cache_store is not None:
            cached = self._read_cache(cache_store, _DETAILS_CACHE_SOURCE, query_hash)
            if isinstance(cached, dict):
                properties = cached

        if properties is None:
            if self._context is None or self._context.place_details_left <= 0:
                return None
            self._context.place_details_left -= 1
            try:
                with self._client() as client:
                    payload = geoapify_get(
                        client,
                        base_url=self._base_url,
                        path="/v2/place-details",
                        params={"id": raw_id, "features": "details", "lang": _LANGUAGE},
                        api_key=self._api_key,
                        timeout=self._timeout,
                        api="place_details",
                        usage=self._usage,
                    )
            except ProviderRequestError as exc:
                logger.warning("Place details request failed (provider=%s, kind=%s).", self.provider_name, exc.kind)
                return None
            features = payload.get("features")
            first = features[0] if isinstance(features, list) and features else None
            properties = first.get("properties") if isinstance(first, dict) else None
            if not isinstance(properties, dict):
                return None
            # Only the classification/evidence fields are kept.
            properties = {
                key: properties[key] for key in ("categories", "wiki_and_media", "datasource") if key in properties
            }
            if cache_store is not None:
                self._write_cache(
                    cache_store, _DETAILS_CACHE_SOURCE, query_hash, properties, self._poi_cache_ttl_seconds
                )

        categories = [c for c in (properties.get("categories") or []) if isinstance(c, str)]
        tags = taxonomy_tags_from_categories(categories)
        datasource = properties.get("datasource")
        raw = datasource.get("raw") if isinstance(datasource, dict) else None
        if isinstance(raw, dict):
            tags.update(filter_provider_tags(raw))
        tags.update(evidence_tags(properties))
        if not tags:
            return None
        category = next((tags[key] for key in _CATEGORY_TAG_ORDER if tags.get(key)), place.category)
        # Identity, name and coordinates stay exactly as the geocoder returned them.
        return place.model_copy(update={"provider_tags": tags, "category": category})

    # -- cache -----------------------------------------------------------------------

    def _read_cache(self, cache_store: ProviderCacheStore, source: str, query_hash: str) -> Any | None:
        """The cached payload, or None on a miss or a broken cache read --
        either way the caller makes the live request."""
        try:
            entry = cache_store.get(source, query_hash)
        except Exception:
            logger.warning("Place data cache read failed; falling back to live request.")
            return None
        if entry is None:
            return None
        payload = entry.payload
        if source == _POI_CACHE_SOURCE:
            places = payload.get("places") if isinstance(payload, dict) else None
            return places if isinstance(places, list) else None
        return payload

    def _write_cache(
        self, cache_store: ProviderCacheStore, source: str, query_hash: str, value: Any, ttl_seconds: int
    ) -> None:
        if ttl_seconds <= 0:
            return
        try:
            payload = {"places": value} if source == _POI_CACHE_SOURCE else value
            cache_store.set(source, query_hash, payload, ttl_seconds=ttl_seconds)
        except Exception:
            logger.warning("Place data cache write failed; returning live result anyway.")
