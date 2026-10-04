"""Destination resolution and named-place lookup shared by every places
adapter (Section 203C.2B, lifted unchanged out of `openstreetmap_adapter`).

Whichever provider discovers POIs (Overpass or Geoapify Places), the trip
destination is resolved once through the configured `GeocodingProvider`,
checked for plausibility, cached, and used to contain every candidate;
`search_must_visit_place` grounds one explicit named place inside it.
`DestinationResolutionMixin` expects the host adapter to provide
`_geocoder`, `_user_agent`, `_destination_cache`,
`_geocode_cache_ttl_seconds` and `_resolve_cache_store()`.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, NamedTuple

import httpx

from app.core import performance
from app.core.config import get_settings
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import NormalizedPlace, ProviderResponse
from app.providers.base import failed_response, not_connected_response, unavailable_response
from app.providers.geocoding.base import GeocodeHit, GeocoderError
from app.services.place_taxonomy import filter_provider_tags
from app.storage.provider_cache_store import ProviderCacheStore, make_query_hash
from app.utils.geo import haversine_distance_km, point_in_bounding_box

logger = logging.getLogger(__name__)

_USER_AGENT = "TravelObligator/0.1 (dev; legit-data-only)"
_NAMED_PLACE_CACHE_SCHEMA = "203c1-v1"
_REQUEST_TIMEOUT_SECONDS = 30.0
_FALLBACK_SEARCH_RADIUS_METERS = 12000
# Minimum word length counted as "significant" when checking whether a
# geocode result actually relates to the requested destination (Step
# 155C). Filters out trivial short tokens ("of", "de", "la") while still
# counting real destination words like "new".
_MIN_PLAUSIBLE_TOKEN_LENGTH = 3


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
    # Section 202B.2 (Task 33): the provider's own structured address
    # components for the resolved place (city/region/country ...), exactly
    # as returned -- never inferred.
    address: dict[str, str] = {}
    # Section 203C.2B: the geocoder's own identity for the destination
    # (e.g. `geoapify/<place_id>`), used by a places provider that can
    # filter by the destination's real boundary. None for older cache
    # entries, which then fall back to the bounding box.
    provider_place_id: str | None = None


def _normalize_text(text: str) -> str:
    """Unicode-normalizes for comparison only: NFKD, strip combining marks
    (Córdoba == Cordoba), casefold, punctuation -> single spaces. Never
    used to *change* a stored/displayed name."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).strip()


def _significant_tokens(text: str) -> set[str]:
    return {
        token
        for token in _normalize_text(text).split()
        if len(token) >= _MIN_PLAUSIBLE_TOKEN_LENGTH
    }


def _is_plausible_geocode_match(query: str, display_name: str) -> bool:
    """LEGACY, display-name-only plausibility check (Step 155C), kept as
    the fallback for a Nominatim result that carries no structural
    fields (no `category`). Requires at least one significant (3+
    character, diacritic/case-normalized) word from `query` to also
    appear in `display_name`. See `_is_plausible_geocode_result` for the
    structural rule used for real Nominatim results.
    """
    query_tokens = _significant_tokens(query)
    if not query_tokens:
        return False
    return bool(query_tokens & _significant_tokens(display_name))


# Section 202B.1 (Tasks 12-14). Audit of the old rule: display-name token
# overlap only. It rejected legitimate localized/accented names ("Lisbon"
# vs a provider display name of "Lisboa, Portugal"; "Cordoba" vs
# "Córdoba", because the ASCII-only tokenizer split "Córdoba" into "c" +
# "rdoba"), while at the same time accepting any result -- including a
# business/POI in another country -- that merely shared one word.
#
# New rule for a real Nominatim result (requested with addressdetails/
# namedetails/accept-language=en): provider evidence is authoritative.
#   1. the FEATURE must be a place/administrative boundary (city, town,
#      municipality, ...), never an amenity/shop/tourism POI;
#   2. the first segment of the query must be compatible (token-subset in
#      either direction, after Unicode/diacritic normalization) with a
#      NAME the provider itself reports for that feature -- its primary
#      name, its `namedetails` (incl. `name:en`/`int_name`/`alt_name`), or
#      an address component -- so an exonym is accepted only when the
#      provider says it is a name of that place, with no alias table;
#   3. every further query segment that is long enough to be verifiable
#      must match a provider address/display component (country, region,
#      ...); 1-3 letter segments ("US", "DC") are unverifiable
#      abbreviations and neither confirm nor contradict.
_ACCEPTED_GEOCODE_CATEGORIES = frozenset({"place", "boundary"})
_UNVERIFIABLE_SEGMENT_MAX_LENGTH = 3


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_normalize_text(text).split())


def _compatible(a: frozenset[str], b: frozenset[str]) -> bool:
    return bool(a) and bool(b) and (a <= b or b <= a)


def _provider_place_names(result: dict[str, Any]) -> list[frozenset[str]]:
    names: list[str] = []
    for key in ("name",):
        if isinstance(result.get(key), str):
            names.append(result[key])
    namedetails = result.get("namedetails")
    if isinstance(namedetails, dict):
        names.extend(v for v in namedetails.values() if isinstance(v, str))
    address = result.get("address")
    if isinstance(address, dict):
        for key in ("city", "town", "village", "municipality", "hamlet", "suburb", "city_district", "borough"):
            if isinstance(address.get(key), str):
                names.append(address[key])
    display_name = result.get("display_name")
    if isinstance(display_name, str) and display_name:
        names.append(display_name.split(",")[0])
    return [t for t in (_tokens(n) for n in names) if t]


def _provider_context_components(result: dict[str, Any]) -> list[frozenset[str]]:
    values: list[str] = []
    address = result.get("address")
    if isinstance(address, dict):
        values.extend(v for v in address.values() if isinstance(v, str))
    display_name = result.get("display_name")
    if isinstance(display_name, str):
        values.extend(part for part in display_name.split(","))
    return [t for t in (_tokens(v) for v in values) if t]


# Section 3C.1: why a geocode result was not accepted as the destination.
# Fixed codes only -- they are logged, and never carry the query, a provider
# name for the place, or any part of the provider's response.
REJECT_UNSUPPORTED_RESULT_TYPE = "unsupported_result_type"
REJECT_INSUFFICIENT_GEO_EVIDENCE = "insufficient_geo_evidence"
REJECT_LOCALITY_MISMATCH = "locality_mismatch"
REJECT_COUNTRY_MISMATCH = "country_mismatch"
REJECT_REGION_MISMATCH = "region_mismatch"

# Section 3C.1 (equivalent locality). The provider answers in ONE language,
# so a city whose name is romanised in more than one way comes back under a
# spelling the traveller did not type, and rule 2 above (token-exact names)
# rejected a result that is plainly the requested city. There is no alias
# table and no fuzzy matching of the whole answer; instead the alternate
# spelling is accepted only on STRUCTURED evidence, all of it required:
#
#   a. the result is itself a city-level settlement (never a suburb,
#      district, county, state or country standing in for the city);
#   b. the name that differs is the provider's own name for that settlement
#      (the feature's name or its city/town/village/municipality component);
#   c. it differs from the requested name by a single edit (one letter
#      substituted, inserted, dropped, or two adjacent letters swapped) and
#      is long enough for that to be meaningful;
#   d. the query names a country and the provider's COUNTRY component
#      matches it exactly -- a segment too short to verify (rule 3) is not
#      country agreement here.
#
# Every other check (result type, further region segments) still applies, so
# a same-name place in another country, an unrelated locality and a
# non-settlement result stay rejected exactly as before.
_CITY_LEVEL_TYPES = frozenset({"city", "town", "village", "municipality"})
# Result types that may appear in a log line (a closed vocabulary; anything
# else is logged as "other").
_LOGGABLE_RESULT_TYPES = _CITY_LEVEL_TYPES | frozenset(
    {"suburb", "district", "county", "state", "country", "postcode", "street", "amenity", "building", "administrative", "unknown"}
)
_LOCALITY_COMPONENT_KEYS = ("city", "town", "village", "municipality")
_EQUIVALENT_NAME_MIN_LENGTH = 6


def _single_edit_apart(a: str, b: str) -> bool:
    """True when `a` and `b` differ by exactly one substitution, insertion,
    deletion or adjacent transposition."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    index = 0
    while index < len(a) and a[index] == b[index]:
        index += 1
    if len(a) == len(b):
        return a[index + 1 :] == b[index + 1 :] or (
            index + 1 < len(a)
            and a[index] == b[index + 1]
            and a[index + 1] == b[index]
            and a[index + 2 :] == b[index + 2 :]
        )
    return a[index:] == b[index + 1 :]


def _settlement_names(result: dict[str, Any]) -> list[str]:
    """Normalised names the provider gives the SETTLEMENT a city-level
    result is: its own name and its locality address component."""
    names = [result.get("name")]
    address = result.get("address")
    if isinstance(address, dict):
        names.extend(address.get(key) for key in _LOCALITY_COMPONENT_KEYS)
    return [text for text in (_normalize_text(name) for name in names if isinstance(name, str)) if text]


def _country_agrees(segments: list[str], result: dict[str, Any]) -> bool:
    address = result.get("address")
    country = address.get("country") if isinstance(address, dict) else None
    if len(segments) < 2 or not isinstance(country, str):
        return False
    requested = _tokens(segments[-1])
    return len("".join(requested)) > _UNVERIFIABLE_SEGMENT_MAX_LENGTH and requested == _tokens(country)


def _is_equivalent_locality(segments: list[str], result: dict[str, Any]) -> bool:
    if result.get("type") not in _CITY_LEVEL_TYPES or not _country_agrees(segments, result):
        return False
    requested = _normalize_text(segments[0])
    return len(requested) >= _EQUIVALENT_NAME_MIN_LENGTH and any(
        len(name) >= _EQUIVALENT_NAME_MIN_LENGTH and _single_edit_apart(requested, name)
        for name in _settlement_names(result)
    )


def destination_rejection_reason(query: str, result: dict[str, Any]) -> str | None:
    """None when `result` is a plausible resolution of `query`; otherwise the
    fixed code of the first check it failed."""
    category = result.get("category") or result.get("class")
    if category is None:
        # No structural evidence at all (older/minimal response shape).
        if _is_plausible_geocode_match(query, str(result.get("display_name") or "")):
            return None
        return REJECT_INSUFFICIENT_GEO_EVIDENCE if not _significant_tokens(query) else REJECT_LOCALITY_MISMATCH
    if category not in _ACCEPTED_GEOCODE_CATEGORIES:
        return REJECT_UNSUPPORTED_RESULT_TYPE

    segments = [segment for segment in (part.strip() for part in query.split(",")) if segment]
    place_tokens = _tokens(segments[0]) if segments else frozenset()
    provider_names = _provider_place_names(result)
    if not place_tokens or not provider_names:
        return REJECT_INSUFFICIENT_GEO_EVIDENCE
    if not any(_compatible(place_tokens, name) for name in provider_names) and not _is_equivalent_locality(
        segments, result
    ):
        return REJECT_LOCALITY_MISMATCH

    components = _provider_context_components(result)
    for index, segment in enumerate(segments[1:], start=1):
        segment_tokens = _tokens(segment)
        if not segment_tokens:
            continue
        if len("".join(segment_tokens)) <= _UNVERIFIABLE_SEGMENT_MAX_LENGTH:
            continue
        if not any(_compatible(segment_tokens, component) for component in components):
            return REJECT_COUNTRY_MISMATCH if index == len(segments) - 1 else REJECT_REGION_MISMATCH
    return None


def _is_plausible_geocode_result(query: str, result: dict[str, Any]) -> bool:
    return destination_rejection_reason(query, result) is None


_DESTINATION_ADDRESS_KEYS = (
    "city", "town", "village", "municipality", "county", "state", "region", "country", "country_code",
)


def _destination_address(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {key: str(raw[key]) for key in _DESTINATION_ADDRESS_KEYS if isinstance(raw.get(key), str) and raw[key]}


def _hit_evidence(hit: GeocodeHit) -> dict[str, Any]:
    """The structural evidence `_is_plausible_geocode_result` judges, built
    from a vendor-neutral `GeocodeHit` (whichever geocoder produced it)."""
    evidence: dict[str, Any] = {
        "name": hit.name or None,
        "namedetails": dict(enumerate(hit.alt_names)),
        "address": hit.address,
        "display_name": hit.display_name,
    }
    if hit.feature_class is not None:
        evidence["category"] = hit.feature_class
    if hit.feature_type is not None:
        # the provider's own result type (city, suburb, county, ...)
        evidence["type"] = hit.feature_type
    return evidence


def _user_agent() -> str:
    contact = (get_settings().osm_user_agent_contact or "").strip()
    return f"TravelObligator/0.1 ({contact})" if contact else _USER_AGENT


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


class DestinationResolutionMixin:
    """Geocoder-backed destination resolution, named-place lookup and their
    caches. See the module docstring for what the host class provides."""

    def search_must_visit_place(
        self,
        must_visit_term: str,
        primary_destination: str,
        filters: dict[str, Any] | None = None,
    ) -> ProviderResponse[Any]:
        field_name = "must_visit_place"
        query = f"{must_visit_term}, {primary_destination}"
        # Section 1A (measurement only): counts repeated named lookups.
        performance.note_request("named_lookup", query.strip().lower())

        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": self._user_agent}
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
                place = self._lookup_named_place(client, query, resolved)
        except GeocoderError as exc:
            return self._geocoder_failure_response(exc, field_name)

        if place is None:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"{self._geocoder.display_name} found no named place with coordinates for '{query}'."
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
                    f"{self._geocoder.display_name} found a place for '{query}', but it is outside the "
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
                f"Found a named place for must-visit term '{must_visit_term}' "
                f"via a targeted {self._geocoder.display_name} lookup."
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
                timeout=_REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": self._user_agent}
            ) as client:
                resolved = self._resolve_destination(client, destination)
                return resolved.point if resolved is not None else None
        except GeocoderError as exc:
            self._log_geocoder_failure(exc)
            return None

    def describe_destination(self, destination: str) -> dict[str, str] | None:
        """Section 202B.2 (Task 33): the provider's own description of what
        `destination` resolved to -- display name plus whichever structured
        components (city, region, country) it returned. Reuses the cached,
        plausibility-checked resolution; returns None (never a guess) when
        unresolved."""
        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": self._user_agent}
            ) as client:
                resolved = self._resolve_destination(client, destination)
        except GeocoderError:
            return None
        if resolved is None:
            return None
        return {"display_name": resolved.display_name, **resolved.address}

    def _lookup_named_place(
        self,
        client: httpx.Client,
        query: str,
        resolved: _ResolvedDestination | None = None,
    ) -> NormalizedPlace | None:
        """Look up exactly one named, coordinate-backed place for `query` via
        the configured geocoder. Returns None (never a guessed place) if the
        geocoder has no usable result. `query` is always the must-visit term
        combined with the trip's primary destination, so this never falls
        back to an unconstrained global search that could resolve to the
        wrong city. (Containment against the resolved destination is
        checked by the caller, `search_must_visit_place`, not here.)

        Section 203C.1: a found place is cached under the geocoder's own
        source; "no match" and every failure are never cached. The place's
        `source`/`place_id` are whatever identity the geocoder genuinely
        returned.
        """
        query_hash = make_query_hash(
            {
                "provider": self._geocoder.provider_name,
                "kind": "named_place",
                "query": query.strip().lower(),
                "schema": _NAMED_PLACE_CACHE_SCHEMA,
            }
        )
        cache_store = self._resolve_cache_store()
        if cache_store is not None:
            cached = self._read_named_place_cache(cache_store, query_hash)
            if cached is not None:
                return cached

        hit = self._geocoder.search_named_place(
            client,
            query,
            near=resolved.point if resolved is not None else None,
            bounding_box=resolved.bounding_box if resolved is not None else None,
        )
        if hit is None:
            return None

        # Whitelisted tags only (`filter_provider_tags`): never opening
        # hours, phone, website, ratings or any other non-taxonomy tag.
        place = NormalizedPlace(
            place_id=hit.provider_place_id,
            name=hit.name,
            category=hit.feature_type or hit.feature_class,
            coordinates=GeoPoint(lat=hit.lat, lng=hit.lon),
            address=hit.display_name or None,
            source=hit.source,
            data_status=DataStatus.LIVE,
            confidence=0.5,
            provider_tags=filter_provider_tags(hit.tags) or None,
            source_entity_id=hit.source_entity_id,
        )
        if cache_store is not None:
            try:
                cache_store.set(
                    self._geocoder.cache_source,
                    query_hash,
                    place.model_dump(mode="json"),
                    ttl_seconds=self._geocode_cache_ttl_seconds,
                )
            except Exception:
                logger.warning("Named-place cache write failed; returning live result anyway.")
        return place

    def _read_named_place_cache(
        self, cache_store: ProviderCacheStore, query_hash: str
    ) -> NormalizedPlace | None:
        try:
            entry = cache_store.get(self._geocoder.cache_source, query_hash)
            if entry is None:
                return None
            return NormalizedPlace(**{**entry.payload, "data_status": DataStatus.CACHED})
        except Exception:
            logger.warning("Named-place cache read failed; falling back to live request.")
            return None

    def _log_geocoder_failure(self, exc: GeocoderError) -> None:
        # Fixed identifiers only: never the query, a URL or exception text.
        logger.warning(
            "Place geocoding failed (provider=%s, kind=%s).", self._geocoder.provider_name, exc.kind
        )

    def _geocoder_failure_response(self, exc: GeocoderError, field_name: str) -> ProviderResponse[Any]:
        """An honest result for a failed geocoder request. The message names
        the geocoder (not Overpass) and carries no exception text;
        `failure_reason` is the machine-readable `geocoder_<kind>`."""
        self._log_geocoder_failure(exc)
        if exc.kind == "not_connected":
            response = not_connected_response(
                self.provider_name, self.provider_type, unavailable_fields=[field_name], message=str(exc)
            )
        else:
            response = failed_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=f"Place geocoding provider ({self._geocoder.display_name}) was unavailable.",
            )
        response.failure_reason = f"geocoder_{exc.kind}"
        return response

    def _resolve_destination(
        self, client: httpx.Client, place_name: str
    ) -> _ResolvedDestination | None:
        """`_resolve_destination_untimed`, timed (Section 1A, measurement
        only): its wall-clock is the `destination_resolution` stage, and each
        call is counted so repeated resolutions of one destination show up."""
        performance.note_request("destination_resolution", place_name)
        with performance.stage("destination_resolution"):
            return self._resolve_destination_untimed(client, place_name)

    def _resolve_destination_untimed(
        self, client: httpx.Client, place_name: str
    ) -> _ResolvedDestination | None:
        """Conservatively geocodes `place_name` via the configured geocoder (Step 155C).

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

        query_hash = make_query_hash(self._geocoder.destination_cache_query(place_name))
        cache_store = self._resolve_cache_store()

        if cache_store is not None:
            persisted = self._read_geocode_cache(cache_store, query_hash)
            if persisted is not None:
                self._destination_cache[place_name] = persisted
                return persisted

        hit = self._geocoder.search_destination(client, place_name)
        if hit is None:
            return None

        rejection = destination_rejection_reason(place_name, _hit_evidence(hit))
        if rejection is not None:
            # Fixed identifiers only: the provider, the reason code and the
            # provider's result type -- never the query or the response.
            logger.warning(
                "Rejecting implausible geocode match (provider=%s, reason=%s, result_type=%s).",
                self._geocoder.provider_name,
                rejection,
                hit.feature_type if hit.feature_type in _LOGGABLE_RESULT_TYPES else "other",
            )
            return None

        resolved = _ResolvedDestination(
            point=GeoPoint(lat=hit.lat, lng=hit.lon),
            bounding_box=hit.bounding_box,
            display_name=hit.display_name,
            address=_destination_address(hit.address),
            provider_place_id=(
                hit.provider_place_id
                if hit.provider_place_id and not hit.provider_place_id.endswith("/None")
                else None
            ),
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
        to the live geocoder request rather than failing."""
        try:
            entry = cache_store.get(self._geocoder.cache_source, query_hash)
        except Exception:
            logger.warning(
                "Geocode cache read failed; falling back to live request."
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
                address=_destination_address(payload.get("address")),
                provider_place_id=payload.get("provider_place_id") or None,
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "Geocode cache entry was unusable; falling back to live request."
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
                self._geocoder.cache_source,
                query_hash,
                {
                    "lat": resolved.point.lat,
                    "lng": resolved.point.lng,
                    "bounding_box": (
                        list(resolved.bounding_box) if resolved.bounding_box is not None else None
                    ),
                    "display_name": resolved.display_name,
                    "address": dict(resolved.address),
                    **(
                        {"provider_place_id": resolved.provider_place_id}
                        if resolved.provider_place_id
                        else {}
                    ),
                },
                ttl_seconds=self._geocode_cache_ttl_seconds,
            )
        except Exception:
            logger.warning(
                "Geocode cache write failed; returning live result anyway."
            )

