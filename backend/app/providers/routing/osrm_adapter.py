from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.common import ProviderStatus
from app.models.routing import RouteRequest, RouteResult
from app.providers.routing.base import RoutingProvider
from app.storage.provider_cache_store import (
    ProviderCacheStore,
    get_provider_cache_store,
    make_query_hash,
)

logger = logging.getLogger(__name__)

_USER_AGENT = "TravelObligator/0.1 (dev; legit-data-only)"
# Step 165C: source label for the persistent route cache -- distinct from
# `OSRMRoutingAdapter.provider_name` ("osrm"), which still labels every
# `RouteResult` this adapter returns (cached or live).
_ROUTE_CACHE_SOURCE = "osrm_route"


class OSRMRoutingAdapter(RoutingProvider):
    """RoutingProvider backed by an OSRM-compatible route service (Step
    165A, docs/12_provider_architecture.md section 31).

    **Not wired into planning.** This adapter is exposed through
    `ProviderGateway` (Step 165B) but is not called by
    `PlanningOrchestrator`, `ExperiencePlannerService`, or
    `PlanValidatorService` yet.

    If no base URL is configured (`Settings.osrm_base_url`, unset by
    default), this honestly reports `not_connected` without making any
    network call -- no public/demo OSRM instance is assumed. On a real
    request, only `code == "Ok"` with at least one route is treated as a
    usable result; `distance_meters`/`duration_seconds` are read directly
    from the first route's own `distance`/`duration` fields (OSRM already
    returns meters/seconds) and are never invented when missing. A
    `NoRoute`/other non-`"Ok"` `code`, a malformed response, or a response
    with no usable route is reported `unavailable`; a request-level
    failure (network error, timeout, non-2xx status) is reported `failed`.
    `geometry` is not parsed by this step -- the request always asks OSRM
    for `overview=false` so no geometry payload is fetched or discarded.

    This adapter never calls `haversine_distance_km` (a straight-line
    estimate, not a route) and never falls back to one when OSRM data is
    unavailable -- an unavailable route stays unavailable.

    Route cache (Step 165C, docs/12_provider_architecture.md "Provider
    Cache Foundation" section): a successful, usable route is cached in
    `ProviderCacheStore` under source `"osrm_route"`, keyed by a hash of
    the normalized request (origin/destination lat/lon, resolved profile)
    -- never the raw coordinate query text or the request URL. A cache hit
    returns the exact same normalized `RouteResult` shape a live call
    would (only `message` is relabeled to note it came from cache). Cache
    reads/writes never fail routing: a broken cache read falls back to the
    live OSRM request, and a broken cache write still returns the live
    result. `not_connected`/`unavailable`/`failed` responses are never
    cached.
    """

    provider_name = "osrm"

    def __init__(self, cache_store: ProviderCacheStore | None = None) -> None:
        settings = get_settings()
        self._base_url = settings.osrm_base_url
        self._timeout_seconds = settings.osrm_timeout_seconds
        self._default_profile = settings.osrm_profile
        self._cache_enabled = settings.provider_cache_enabled
        self._route_cache_ttl_seconds = settings.osrm_route_cache_ttl_seconds
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

    def get_route(self, request: RouteRequest) -> RouteResult:
        if not self._base_url:
            return self._not_connected_result(
                "No OSRM base URL is configured, so routing cannot be requested."
            )

        profile = request.profile.value if request.profile else self._default_profile
        coordinates = (
            f"{request.origin_lon},{request.origin_lat};"
            f"{request.destination_lon},{request.destination_lat}"
        )

        query_hash = make_query_hash(
            {
                "origin_lat": request.origin_lat,
                "origin_lon": request.origin_lon,
                "destination_lat": request.destination_lat,
                "destination_lon": request.destination_lon,
                "profile": profile,
            }
        )
        cache_store = self._resolve_cache_store()

        if cache_store is not None:
            cached_result = self._read_route_cache(cache_store, query_hash)
            if cached_result is not None:
                return cached_result

        try:
            with httpx.Client(
                timeout=self._timeout_seconds, headers={"User-Agent": _USER_AGENT}
            ) as client:
                response = client.get(
                    f"{self._base_url}/route/v1/{profile}/{coordinates}",
                    params={"overview": "false"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OSRM request failed: %s", exc)
            return self._failed_result("OSRM request failed.")

        result = self._normalize(payload)

        if cache_store is not None and result.status == ProviderStatus.SUCCESS:
            self._write_route_cache(cache_store, query_hash, result)

        return result

    def _read_route_cache(
        self, cache_store: ProviderCacheStore, query_hash: str
    ) -> RouteResult | None:
        """Returns the cached route, or `None` on a cache miss/expiry or a
        broken cache read -- either way, the caller falls back to the live
        OSRM request rather than failing."""
        try:
            entry = cache_store.get(_ROUTE_CACHE_SOURCE, query_hash)
        except Exception:
            logger.warning("OSRM route cache read failed; falling back to live request.")
            return None

        if entry is None:
            return None

        try:
            payload = entry.payload
            return RouteResult(
                provider=self.provider_name,
                status=ProviderStatus.SUCCESS,
                distance_meters=payload["distance_meters"],
                duration_seconds=payload["duration_seconds"],
                geometry=payload.get("geometry"),
                source=self.provider_name,
                confidence=payload.get("confidence", 0.6),
                message="Route found via OSRM (cached).",
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("OSRM route cache entry was unusable; falling back to live request.")
            return None

    def _write_route_cache(
        self,
        cache_store: ProviderCacheStore,
        query_hash: str,
        result: RouteResult,
    ) -> None:
        """Best-effort cache write -- a failure here must never affect the
        already-computed live result being returned to the caller."""
        try:
            cache_store.set(
                _ROUTE_CACHE_SOURCE,
                query_hash,
                {
                    "distance_meters": result.distance_meters,
                    "duration_seconds": result.duration_seconds,
                    "geometry": result.geometry,
                    "confidence": result.confidence,
                },
                ttl_seconds=self._route_cache_ttl_seconds,
            )
        except Exception:
            logger.warning("OSRM route cache write failed; returning live result anyway.")

    def _normalize(self, payload: Any) -> RouteResult:
        if not isinstance(payload, dict):
            return self._unavailable_result("OSRM returned a malformed response.")

        code = payload.get("code")
        routes = payload.get("routes")
        if code != "Ok" or not isinstance(routes, list) or not routes:
            return self._unavailable_result(
                f"OSRM reported no usable route (code={code!r})."
            )

        first_route = routes[0]
        if not isinstance(first_route, dict):
            return self._unavailable_result("OSRM returned a malformed route entry.")

        distance = first_route.get("distance")
        duration = first_route.get("duration")
        distance_meters = float(distance) if isinstance(distance, (int, float)) else None
        duration_seconds = float(duration) if isinstance(duration, (int, float)) else None

        if distance_meters is None and duration_seconds is None:
            return self._unavailable_result(
                "OSRM route had no usable distance or duration."
            )

        return RouteResult(
            provider=self.provider_name,
            status=ProviderStatus.SUCCESS,
            distance_meters=distance_meters,
            duration_seconds=duration_seconds,
            geometry=None,
            source=self.provider_name,
            confidence=0.6,
            message="Route found via OSRM.",
        )

    def _not_connected_result(self, message: str) -> RouteResult:
        return RouteResult(
            provider=self.provider_name,
            status=ProviderStatus.NOT_CONNECTED,
            distance_meters=None,
            duration_seconds=None,
            geometry=None,
            source=self.provider_name,
            confidence=0.0,
            message=message,
        )

    def _unavailable_result(self, message: str) -> RouteResult:
        return RouteResult(
            provider=self.provider_name,
            status=ProviderStatus.UNAVAILABLE,
            distance_meters=None,
            duration_seconds=None,
            geometry=None,
            source=self.provider_name,
            confidence=0.0,
            message=message,
        )

    def _failed_result(self, message: str) -> RouteResult:
        return RouteResult(
            provider=self.provider_name,
            status=ProviderStatus.FAILED,
            distance_meters=None,
            duration_seconds=None,
            geometry=None,
            source=self.provider_name,
            confidence=0.0,
            message=message,
        )
