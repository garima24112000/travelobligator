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
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx

from app.core import performance
from app.core.bounded_concurrency import (
    NAMED_LOOKUP_BATCH_LIMIT,
    PLACE_DETAILS_BATCH_LIMIT,
    PLACES_BATCH_LIMIT,
    Outcome,
    run_bounded,
)
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
    _ResolvedDestination,
    _user_agent,
)
from app.providers.places.entity_identity import (
    COLLOCATED_METERS,
    RESOLVED_DISTINCT,
    RESOLVED_MERGED,
    UNRESOLVED,
    absorb,
    alternate_names,
    collision_record,
    conclusively_distinct,
    dedupe_places,
    merge_rule,
    source_entity_id,
    suspect_pair,
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
from app.services.place_taxonomy import classify_place, filter_provider_tags
from app.services.schedule_diversity import HISTORY_ARCHITECTURE, MUSEUM_CULTURE, coarse_class
from app.utils.names import comparable_name
from app.storage.provider_cache_store import (
    ProviderCacheStore,
    get_provider_cache_store,
    make_query_hash,
)

logger = logging.getLogger(__name__)

_POI_CACHE_SOURCE = "geoapify_places"
_DETAILS_CACHE_SOURCE = "geoapify_place_details"
# v2: cached places carry the sanitised source identity used for de-duplication.
_CACHE_SCHEMA = "203c2b-v2"
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


# A historic building and the museum inside it are commonly two records of
# one place, so those two coarse classes are compatible with each other.
_BUILDING_CLASSES = frozenset({HISTORY_ARCHITECTURE, MUSEUM_CULTURE})


def _compatible_classes(first: str, second: str) -> bool:
    return first == second or {first, second} <= _BUILDING_CLASSES


@dataclass
class _DetailsLookup:
    """One planned Place Details lookup: answered from the cache
    (`cached`), or still to be fetched with its allowance already taken."""

    raw_id: str
    query_hash: str
    cached: dict[str, Any] | None


def _value_or_error(task: Any) -> tuple[Any, Exception | None]:
    try:
        return task(), None
    except Exception as exc:  # noqa: BLE001 - handed back to the caller, per term
        return None, exc


def places_request_credits(limit: int, returned: int | None = None) -> int:
    """Geoapify Places pricing: 1 credit up to 20 places; above that, 1 plus
    one per 20 places returned. With `returned=None` this is the
    conservative amount to RESERVE before the request."""
    if limit <= _PLACES_PER_CREDIT:
        return 1
    count = limit if returned is None else returned
    return 1 + math.ceil(count / _PLACES_PER_CREDIT)


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

    @staticmethod
    def _attraction_plan(filters: dict[str, Any] | None) -> list[tuple[CategoryGroup, int]]:
        pool = int((filters or {}).get("pool_size") or _DEFAULT_ATTRACTION_POOL)
        pool = max(_MIN_GROUP_LIMIT, min(pool, _MAX_ATTRACTION_POOL))
        return [(group, max(_MIN_GROUP_LIMIT, round(group.share * pool))) for group in ATTRACTION_GROUPS]

    @staticmethod
    def _food_plan(filters: dict[str, Any] | None) -> list[tuple[CategoryGroup, int]]:
        pool = int((filters or {}).get("pool_size") or _DEFAULT_FOOD_POOL)
        pool = max(_MIN_GROUP_LIMIT, min(pool, _MAX_FOOD_POOL))
        return [(FOOD_GROUP, pool)]

    def search_attractions(
        self,
        destination: str,
        filters: dict[str, Any] | None = None,
        _prefetched: list[Outcome[list[NormalizedPlace]]] | None = None,
    ) -> ProviderResponse[Any]:
        response = self._search(
            destination,
            self._attraction_plan(filters),
            "attractions",
            page=int((filters or {}).get("page") or 0),
            prefetched=_prefetched,
        )
        if self._context is not None and response.data:
            response.data = self._resolve_suspect_collisions(response.data)
            self._context.place_pool.extend(response.data)
        return response

    def search_broad_inventory(
        self,
        destination: str,
        attraction_filters: dict[str, Any] | None = None,
        food_filters: dict[str, Any] | None = None,
    ) -> tuple[ProviderResponse[Any], ProviderResponse[Any], ProviderResponse[Any]]:
        """`search_attractions`, `search_restaurants` and
        `search_accommodation_pois` for one destination, with their Places
        requests fetched as ONE bounded concurrent batch (Section 1B).

        Only the fetching is concurrent. The three responses are then built
        one after the other, in that order and from the requests in their
        original category order -- exactly what three separate calls do -- so
        de-duplication, containment, collision resolution and the place pool
        never depend on which request finished first."""
        page = int((attraction_filters or {}).get("page") or 0)
        attraction_plan = self._attraction_plan(attraction_filters)
        food_plan = self._food_plan(food_filters)
        accommodation_plan = [(ACCOMMODATION_GROUP, _ACCOMMODATION_POOL)]
        try:
            with self._client() as client:
                resolved = self._resolve_destination(client, destination)
                self._resolve_cache_store()  # resolved once, before any task runs
                outcomes = (
                    run_bounded(
                        "places",
                        [
                            partial(self._query_group, client, resolved, group, limit, offset)
                            for group, limit, offset in (
                                *((group, limit, page * limit) for group, limit in attraction_plan),
                                *((group, limit, 0) for group, limit in (*food_plan, *accommodation_plan)),
                            )
                        ],
                        PLACES_BATCH_LIMIT,
                    )
                    if resolved is not None
                    else None
                )
        except GeocoderError:
            outcomes = None
        if outcomes is None:
            # Unresolved destination or geocoder failure: the three searches
            # report it themselves, exactly as they always have.
            return (
                self.search_attractions(destination, attraction_filters),
                self.search_restaurants(destination, food_filters),
                self.search_accommodation_pois(destination),
            )
        split = len(attraction_plan)
        return (
            self.search_attractions(destination, attraction_filters, _prefetched=outcomes[:split]),
            self._search(destination, food_plan, "restaurants", prefetched=outcomes[split : split + 1]),
            self._search(destination, accommodation_plan, "accommodation_pois", prefetched=outcomes[split + 1 :]),
        )

    # -- suspected duplicate candidates ---------------------------------------------

    @staticmethod
    def _coarse_class(place: NormalizedPlace) -> str:
        return coarse_class(classify_place(place.provider_tags, place.category).primary_category)

    def _identity_enriched_pair(
        self, first: NormalizedPlace, second: NormalizedPlace
    ) -> tuple[tuple[NormalizedPlace, bool], tuple[NormalizedPlace, bool]]:
        """Each place of a suspected pair with whatever identity evidence
        one Place Details lookup adds, and whether a lookup was attempted.
        Bounded twice: by the identity-lookup allowance and by the Place
        Details allowance.

        Section 1B: both allowances are taken here, for `first` then for
        `second`, before anything is fetched; only then are the pair's (at
        most two) lookups fetched concurrently. The decisions are therefore
        exactly those of looking `first` up and then `second`."""
        context = self._context
        planned: list[tuple[NormalizedPlace, bool, _DetailsLookup | None]] = []
        for place in (first, second):
            if context is None or context.identity_lookups_left <= 0 or place.place_id in context.identity_checked:
                planned.append((place, False, None))
                continue
            context.identity_lookups_left -= 1
            context.identity_checked.add(place.place_id)
            planned.append((place, True, self._plan_details(place, for_identity=True)))
        with performance.stage("identity_details"):
            properties = self._resolve_details([lookup for _, _, lookup in planned])
        enriched = [
            ((self._apply_details(place, found) if found is not None else None) or place, tried)
            for (place, tried, _), found in zip(planned, properties)
        ]
        return enriched[0], enriched[1]

    def _resolve_suspect_collisions(self, places: list[NormalizedPlace]) -> list[NormalizedPlace]:
        """Finds SUSPECTED duplicates among `places` (and against the pool
        this generation already holds): co-located records of a compatible
        class that no identity rule merged. Each pair gets a bounded identity
        enrichment; then it is merged ONLY on conclusive provider evidence,
        cleared when the evidence says the two are different entities, and
        otherwise left as two candidates and recorded as unresolved, so the
        scheduler never puts both on one itinerary. Proximity never merges."""
        context = self._context
        assert context is not None
        kept: list[NormalizedPlace] = []
        for place in places:
            merged = False
            for others in (kept, context.place_pool):
                for index, existing in enumerate(others):
                    classes = (self._coarse_class(existing), self._coarse_class(place))
                    if not suspect_pair(existing, place, _compatible_classes(*classes)):
                        continue
                    (existing_enriched, tried_a), (place, tried_b) = self._identity_enriched_pair(existing, place)
                    others[index] = existing = existing_enriched
                    attempted = tried_a or tried_b
                    rule = merge_rule(existing, place, COLLOCATED_METERS)
                    if rule is not None:
                        others[index] = absorb(existing, place)
                        context.entity_merges[rule] = context.entity_merges.get(rule, 0) + 1
                        context.suspect_collisions.append(
                            collision_record(existing, place, classes, RESOLVED_MERGED, rule, attempted)
                        )
                        merged = True
                        break
                    resolution = RESOLVED_DISTINCT if conclusively_distinct(existing, place) else UNRESOLVED
                    context.suspect_collisions.append(
                        collision_record(existing, place, classes, resolution, None, attempted)
                    )
                if merged:
                    break
            if not merged:
                kept.append(place)
        return kept

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._search(area, self._food_plan(filters), "restaurants")

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
        prefetched: list[Outcome[list[NormalizedPlace]]] | None = None,
    ) -> ProviderResponse[Any]:
        """`prefetched` (Section 1B): one already-fetched outcome per entry
        of `plan`, in plan order, from `search_broad_inventory`. Without it
        the plan's requests are fetched here, as one bounded concurrent
        batch. Either way the outcomes are applied in plan order."""
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
                self._resolve_cache_store()  # resolved once, before any task runs
                outcomes = (
                    prefetched
                    if prefetched is not None
                    else run_bounded(
                        "places",
                        [
                            partial(self._query_group, client, resolved, group, limit, page * limit)
                            for group, limit in plan
                        ],
                        PLACES_BATCH_LIMIT,
                    )
                )
                for outcome in outcomes:
                    try:
                        places.extend(outcome.unwrap())
                    except ProviderRequestError as exc:
                        failures.append(exc.kind)
        except GeocoderError as exc:
            return self._geocoder_failure_response(exc, field_name)

        # One candidate per real entity: Geoapify place id, then the
        # underlying source identity, then an equal name close by.
        merges = self._context.entity_merges if self._context is not None else None
        contained = [
            place
            for place in dedupe_places(places, _SAME_PLACE_METERS, merges)
            if place.coordinates is not None
            and _is_within_destination(place.coordinates, resolved, _CONTAINMENT_RADIUS_METERS)
        ]
        field_label = field_name.replace("_", " ")
        if contained:
            is_partial = bool(failures) or len(contained) < _PARTIAL_RESULT_THRESHOLD
            return ProviderResponse[list[NormalizedPlace]](
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                status=ProviderStatus.PARTIAL if is_partial else ProviderStatus.SUCCESS,
                data_status=DataStatus.LIVE,
                data=contained,
                unavailable_fields=[],
                confidence=0.4 if is_partial else 0.65,
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
                cache_store,
                _POI_CACHE_SOURCE,
                query_hash,
                # The internal identity is excluded from a normal dump, so it
                # is written to the cache explicitly.
                [{**p.model_dump(mode="json"), **p.internal_identity()} for p in places],
                ttl,
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
            # Sanitised internal identity for de-duplication only; the raw
            # source record itself is never kept.
            source_entity_id=source_entity_id(raw),
            alt_names=alternate_names(raw, name.strip()),
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
        return self._with_pool_identity_and_details([response])[0]

    def search_must_visit_places(
        self, must_visit_terms: list[str], primary_destination: str
    ) -> list[ProviderResponse[Any] | Exception]:
        """`search_must_visit_place` for several INDEPENDENT terms (Section
        1B): one response per term, in the order given -- or the exception
        that term's lookup raised, which never affects the other terms.

        Only the network requests are concurrent: first the geocoder
        lookups, then the Place Details lookups they lead to. Pool matching,
        the Place Details allowance and the merge counters are handled on
        the calling thread, term by term in the given order, so the result
        is the one a term-by-term loop produces."""
        try:
            with self._client() as client:
                # Resolved (and cached) once, so no task has to geocode it.
                resolved = self._resolve_destination(client, primary_destination)
        except GeocoderError:
            resolved = None
        if resolved is None or len(must_visit_terms) < 2:
            # Nothing to run concurrently, or a destination problem that
            # every lookup reports for itself exactly as before.
            results: list[ProviderResponse[Any] | Exception] = []
            for term in must_visit_terms:
                value, error = _value_or_error(partial(self.search_must_visit_place, term, primary_destination))
                results.append(error if error is not None else value)
            return results

        self._resolve_cache_store()  # resolved once, before any task runs
        # The same query twice is one lookup, shared (a term-by-term loop
        # answers the repeat from the cache the first lookup filled).
        unique_terms = list(dict.fromkeys(must_visit_terms))
        geocode = super().search_must_visit_place
        outcomes = dict(
            zip(
                unique_terms,
                run_bounded(
                    "named_place_geocoding",
                    [partial(geocode, term, primary_destination) for term in unique_terms],
                    NAMED_LOOKUP_BATCH_LIMIT,
                ),
            )
        )
        located = [outcomes[term] for term in must_visit_terms]
        finished = iter(
            self._with_pool_identity_and_details(
                [outcome.value.model_copy(deep=True) for outcome in located if outcome.error is None]
            )
        )
        return [outcome.error if outcome.error is not None else next(finished) for outcome in located]

    def _with_pool_identity_and_details(self, responses: list[ProviderResponse[Any]]) -> list[ProviderResponse[Any]]:
        """Each geocoded named place replaced by its pool identity, or
        enriched from Place Details -- `responses` in, the same responses
        out, handled strictly in order; only the Details requests themselves
        run concurrently."""
        places: list[NormalizedPlace | None] = []
        lookups: list[_DetailsLookup | None] = []
        claimed: dict[str, _DetailsLookup] = {}
        for response in responses:
            place = response.data[0] if response.data else None
            lookup: _DetailsLookup | None = None
            if place is not None:
                match = self._pool_match(place)
                if match is not None:
                    response.data = [match]
                    place = None
                else:
                    lookup = self._plan_details(place, claimed=claimed)
            places.append(place)
            lookups.append(lookup)

        if any(lookup is not None for lookup in lookups):
            with performance.stage("anchor_details"):
                details = self._resolve_details(lookups)
        else:
            details = [None] * len(lookups)

        for response, place, properties in zip(responses, places, details):
            enriched = self._apply_details(place, properties) if place is not None and properties is not None else None
            if enriched is not None:
                # Details can reveal the source identity or another name of the
                # place, which may show it IS a pool place after all (e.g. the
                # pool holds it under its local-script name).
                response.data = [self._pool_match(enriched) or enriched]
        return responses

    def _pool_match(self, place: NormalizedPlace) -> NormalizedPlace | None:
        """The pool place that is the same real entity as `place`, if any."""
        if self._context is None:
            return None
        for candidate in self._context.place_pool:
            rule = merge_rule(candidate, place, _POOL_MATCH_METERS)
            if rule is not None:
                merges = self._context.entity_merges
                merges[rule] = merges.get(rule, 0) + 1
                return candidate
        return None

    # -- Place Details: plan (calling thread) -> fetch (concurrent) -> apply (calling thread) ---

    def _plan_details(
        self,
        place: NormalizedPlace,
        for_identity: bool = False,
        claimed: dict[str, "_DetailsLookup"] | None = None,
    ) -> "_DetailsLookup | None":
        """Decides, on the calling thread, whether `place` gets a Place
        Details lookup: eligibility, then the cache, then -- only for a
        lookup that needs a request -- one unit of the generation's Place
        Details allowance, taken HERE, before anything is dispatched. None
        when there is nothing to look up or no allowance left.

        `claimed` holds the lookups already planned in the same batch: a
        second lookup of the same place shares the first one's request (a
        one-at-a-time loop answers it from the cache the first one filled)."""
        existing_tags = dict(place.provider_tags or {})
        # Details add Wikipedia/Wikidata/heritage evidence. They are only
        # worth a call for a place that has none yet -- or, `for_identity`,
        # for a suspected duplicate whose source identity and other names
        # are what is being asked for.
        if not place.place_id.startswith("geoapify/") or (
            not for_identity and any(existing_tags.get(key) for key in ("wikipedia", "wikidata", "heritage"))
        ):
            return None
        raw_id = place.place_id.split("/", 1)[1]
        if claimed is not None and raw_id in claimed:
            return claimed[raw_id]
        query_hash = make_query_hash({"id": raw_id, "lang": _LANGUAGE, "schema": _CACHE_SCHEMA})
        cache_store = self._resolve_cache_store()
        if cache_store is not None:
            cached = self._read_cache(cache_store, _DETAILS_CACHE_SOURCE, query_hash)
            if isinstance(cached, dict):
                return _DetailsLookup(raw_id, query_hash, cached)

        if self._context is None or self._context.place_details_left <= 0:
            return None
        self._context.place_details_left -= 1
        lookup = _DetailsLookup(raw_id, query_hash, None)
        if claimed is not None:
            claimed[raw_id] = lookup
        return lookup

    def _fetch_details(self, lookup: "_DetailsLookup") -> dict[str, Any] | None:
        """ONE Place Details request (its allowance was already taken by
        `_plan_details`). Safe to run concurrently: it touches only the
        provider cache and the usage tracker."""
        try:
            with self._client() as client:
                payload = geoapify_get(
                    client,
                    base_url=self._base_url,
                    path="/v2/place-details",
                    params={"id": lookup.raw_id, "features": "details", "lang": _LANGUAGE},
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
        cache_store = self._resolve_cache_store()
        if cache_store is not None:
            self._write_cache(
                cache_store, _DETAILS_CACHE_SOURCE, lookup.query_hash, properties, self._poi_cache_ttl_seconds
            )
        return properties

    def _resolve_details(self, lookups: list["_DetailsLookup | None"]) -> list[dict[str, Any] | None]:
        """The Details properties for each planned lookup, in the order
        given (None where there is no lookup or the request did not
        succeed). The requests still needed are fetched as one bounded
        concurrent batch; a lookup shared by several entries is fetched once."""
        pending = list({id(lookup): lookup for lookup in lookups if lookup is not None and lookup.cached is None}.values())
        outcomes = run_bounded(
            "place_details", [partial(self._fetch_details, lookup) for lookup in pending], PLACE_DETAILS_BATCH_LIMIT
        )
        fetched = {id(lookup): outcome for lookup, outcome in zip(pending, outcomes)}
        return [
            None if lookup is None else lookup.cached if lookup.cached is not None else fetched[id(lookup)].unwrap()
            for lookup in lookups
        ]

    def _enrich_with_details(self, place: NormalizedPlace, for_identity: bool = False) -> NormalizedPlace | None:
        lookup = self._plan_details(place, for_identity)
        if lookup is None:
            return None
        properties = self._resolve_details([lookup])[0]
        return self._apply_details(place, properties) if properties is not None else None

    def _apply_details(self, place: NormalizedPlace, properties: dict[str, Any]) -> NormalizedPlace | None:
        """`place` with the classification, evidence and identity that
        `properties` (a Place Details answer) adds; None when it adds none."""
        existing_tags = dict(place.provider_tags or {})
        categories = [c for c in (properties.get("categories") or []) if isinstance(c, str)]
        tags = taxonomy_tags_from_categories(categories)
        datasource = properties.get("datasource")
        raw = datasource.get("raw") if isinstance(datasource, dict) else None
        if isinstance(raw, dict):
            tags.update(filter_provider_tags(raw))
        tags.update(evidence_tags(properties))
        known_names = [*(place.alt_names or [])]
        for name in alternate_names(raw, place.name) or []:
            if comparable_name(name) not in {comparable_name(known) for known in known_names}:
                known_names.append(name)
        identity = {
            "source_entity_id": place.source_entity_id or source_entity_id(raw),
            "alt_names": known_names or None,
        }
        if not tags:
            return place.model_copy(update=identity) if any(identity.values()) else None
        tags = {**existing_tags, **tags}
        category = next((tags[key] for key in _CATEGORY_TAG_ORDER if tags.get(key)), place.category)
        # Identity, name and coordinates stay exactly as the geocoder returned them.
        return place.model_copy(update={"provider_tags": tags, "category": category, **identity})

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
