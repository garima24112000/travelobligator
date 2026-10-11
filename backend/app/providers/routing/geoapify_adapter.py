"""Geoapify Routing adapter (Section 203C.2B).

`GET {GEOAPIFY_API_URL}/v1/routing?waypoints=lat,lon|lat,lon|...&mode=walk`.
One request routes a whole day's ordered stops and returns one leg per
consecutive pair, so `get_route_sequence` costs ONE request per day
(Geoapify charges 1 credit per waypoint pair). In-city legs are walking
routes (`GEOAPIFY_ROUTING_MODE`); a caller may ask for a DRIVING route for
specific legs (`profile=RoutingProfile.DRIVING`, mixed-mode transfers),
which is a separate request, cache entry, usage label and per-generation
cap. No transit schedule is ever invented.

Every leg result is remembered for the generation (so the feasibility,
sequencing and buffer services read the same data without another
request) and cached in the provider cache. Distance, duration and geometry
are exactly what Geoapify returned; a failure is reported as `failed` /
`unavailable` / `not_connected`, never estimated from straight-line
distance.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx

from app.core import generation_diagnostics, performance
from app.core.bounded_concurrency import DRIVE_ROUTE_BATCH_LIMIT, WALK_ROUTE_BATCH_LIMIT, run_bounded
from app.core.config import get_settings
from app.models.common import ProviderStatus
from app.models.routing import RouteRequest, RouteResult, RoutingProfile
from app.providers.errors import REASON_NO_ROUTE, REASON_UNROUTABLE_ENDPOINT, ProviderRequestError
from app.providers.geoapify_client import geoapify_get
from app.providers.routing.base import RoutingProvider
from app.providers.routing.osrm_adapter import _geometry_from_cache_payload, _parse_geojson_linestring
from app.core.provider_usage import GenerationProviderContext
from app.storage.provider_cache_store import (
    ProviderCacheStore,
    get_provider_cache_store,
    make_query_hash,
)

logger = logging.getLogger(__name__)

_ROUTE_CACHE_SOURCE = "geoapify_route"
_CACHE_SCHEMA = "203c2b-v1"
_SOURCE = "geoapify_routing"
_COORDINATE_PRECISION = 6
_DRIVE_MODE = "drive"

_FAILURE_MESSAGES = {
    "not_connected": "The routing provider is not connected.",
    "budget_exhausted": "The route request budget for this generation was reached.",
    "no_generation_context": "The routing provider was called outside a generation.",
}
_FAILED_MESSAGE = "The routing provider (Geoapify) request failed."
# The provider's own, definitive answer to a well-formed request: asking the
# identical request again in the same generation gets the same answer. Only
# these are remembered for the generation -- a timeout, a rate limit, a
# server error or a 400 this code does not recognise is always asked again.
_DEFINITIVE_FAILURE_REASONS = frozenset({REASON_NO_ROUTE, REASON_UNROUTABLE_ENDPOINT})
_UNAVAILABLE_MESSAGE = "The routing provider (Geoapify) returned no usable route."

Point = tuple[float, float]  # (lat, lon)


def _includes_ferry(raw_leg: Any) -> bool | None:
    """Whether the provider's route for one leg includes a ferry, from the
    documented per-step `ferry` flag of the Routing API response ("True if
    includes a ferry"). None when the leg carries no step data, so a missing
    statement is never read as "no ferry". Nothing is inferred from geometry
    and nothing about a timetable, a fare or availability is implied."""
    steps = raw_leg.get("steps") if isinstance(raw_leg, dict) else None
    if not isinstance(steps, list) or not steps or not all(isinstance(step, dict) for step in steps):
        return None
    return any(step.get("ferry") is True for step in steps)


@dataclass
class _RouteRequestPlan:
    """One routing request that still has to be made (allowance taken)."""

    points: list[Point]
    legs: list[tuple[Point, Point]]
    alternate: bool
    mode: str


class GeoapifyRoutingAdapter(RoutingProvider):
    provider_name = "geoapify_routing"
    # A driving route can be requested for a single leg (mixed-mode transfers).
    supports_alternate_mode = True

    def __init__(self, cache_store: ProviderCacheStore | None = None) -> None:
        settings = get_settings()
        self._base_url = settings.geoapify_api_url.rstrip("/")
        self._api_key = (settings.geoapify_api_key or "").strip()
        self._timeout = settings.geoapify_timeout_seconds
        self._mode = settings.geoapify_routing_mode
        self._cache_enabled = settings.provider_cache_enabled
        self._route_cache_ttl_seconds = settings.osrm_route_cache_ttl_seconds
        self._cache_path = settings.resolved_provider_cache_path()
        self._cache_store = cache_store
        self._context: GenerationProviderContext | None = None

    def bound_to(self, provider_context: GenerationProviderContext | None) -> "GeoapifyRoutingAdapter":
        if provider_context is None:
            return self
        bound = copy.copy(self)
        bound._context = provider_context
        return bound

    def _resolve_cache_store(self) -> ProviderCacheStore | None:
        if not self._cache_enabled:
            return None
        if self._cache_store is None:
            self._cache_store = get_provider_cache_store(self._cache_path)
        return self._cache_store

    def get_route(self, request: RouteRequest) -> RouteResult:
        points = [
            (request.origin_lat, request.origin_lon),
            (request.destination_lat, request.destination_lon),
        ]
        return self.get_route_sequence(points)[0]

    def get_route_sequence(
        self, points: list[Point], profile: RoutingProfile | None = None
    ) -> list[RouteResult]:
        return self.get_route_sequences([points], profile)[0]

    def get_route_sequences(
        self, sequences: list[list[Point]], profile: RoutingProfile | None = None
    ) -> list[list[RouteResult]]:
        """`get_route_sequence` for several INDEPENDENT sequences (the days
        of a plan, or single legs), one result list per sequence in the
        order given (Section 1B).

        Three steps. PLAN, on the calling thread and in order: what is
        already known (memo, cache), and one unit of the matching request
        allowance for every sequence that needs a request -- taken before
        anything is dispatched. FETCH: the requests, as one bounded
        concurrent batch; a request that fails or times out affects only its
        own sequence. FINISH, on the calling thread and in order: normalise,
        memoise and cache. So which request finished first never matters."""
        # Section 203C.2B (mixed-mode transfers): the configured mode (walk)
        # unless the caller asks for a driving route for these legs.
        alternate = profile == RoutingProfile.DRIVING and self._mode != _DRIVE_MODE
        mode = _DRIVE_MODE if alternate else self._mode

        planned = [self._plan_sequence(points, alternate, mode) for points in sequences]
        requests = [plan for plan in planned if isinstance(plan, _RouteRequestPlan)]
        outcomes = run_bounded(
            "drive_routes" if alternate else "walk_routes",
            [partial(self._fetch_sequence, plan) for plan in requests],
            DRIVE_ROUTE_BATCH_LIMIT if alternate else WALK_ROUTE_BATCH_LIMIT,
        )
        fetched = {id(plan): outcome for plan, outcome in zip(requests, outcomes)}
        return [
            self._finish_sequence(plan, fetched[id(plan)].unwrap()) if isinstance(plan, _RouteRequestPlan) else plan
            for plan in planned
        ]

    def _plan_sequence(
        self, points: list[Point], alternate: bool, mode: str
    ) -> "list[RouteResult] | _RouteRequestPlan":
        """The final results for `points` when no request is needed (or
        allowed); otherwise the request to make, its allowance already taken."""
        legs = list(zip(points, points[1:]))
        if not legs:
            return []
        if not self._api_key:
            return [self._result(ProviderStatus.NOT_CONNECTED, _FAILURE_MESSAGES["not_connected"])] * len(legs)

        known = [self._known_leg(origin, destination, mode) for origin, destination in legs]
        if all(result is not None for result in known):
            generation_diagnostics.route_request("served_from_known_legs")
            return [result for result in known if result is not None]

        context = self._context
        if context is not None:
            # The provider already answered this exact request, in this
            # generation, with a definitive failure: the same answer, with no
            # request and no allowance taken.
            remembered = context.route_failure_memo.get(self._request_key(points, mode))
            if remembered is not None:
                performance.count("route_memo_hits")
                generation_diagnostics.route_request("served_from_failure_memo")
                return [remembered] * len(legs)
            # An alternate-mode request draws on its own per-generation cap,
            # never on the day-route allowance.
            if alternate:
                if context.alternate_mode_requests_left <= 0:
                    generation_diagnostics.route_request("alternate_mode_refused_by_allowance")
                    # Refused locally, no request: the fixed code only names
                    # the cause for the report (same code as a credit-cap
                    # refusal); the cap and what happens next are unchanged.
                    refused = self._result(ProviderStatus.UNAVAILABLE, _FAILURE_MESSAGES["budget_exhausted"])
                    return [refused.model_copy(update={"failure_reason": "budget_exhausted"})] * len(legs)
                context.alternate_mode_requests_left -= 1
            else:
                if context.route_requests_left <= 0:
                    generation_diagnostics.route_request("refused_by_allowance")
                    return [
                        self._result(ProviderStatus.UNAVAILABLE, _FAILURE_MESSAGES["budget_exhausted"])
                    ] * len(legs)
                context.route_requests_left -= 1
        # Diagnostic only (a no-op unless the evaluation tooling is recording).
        generation_diagnostics.route_request("live_alternate_mode" if alternate else "live")
        return _RouteRequestPlan(points=list(points), legs=legs, alternate=alternate, mode=mode)

    def _fetch_sequence(self, plan: "_RouteRequestPlan") -> "dict[str, Any] | ProviderRequestError":
        """ONE routing request (its allowance was already taken by
        `_plan_sequence`). Safe to run concurrently: it touches only the
        usage tracker. A provider failure is returned, not raised, so it is
        reported for its own sequence when the results are finished."""
        context = self._context
        try:
            with httpx.Client(timeout=self._timeout) as client:
                return geoapify_get(
                    client,
                    base_url=self._base_url,
                    path="/v1/routing",
                    params={
                        "waypoints": "|".join(f"{lat},{lon}" for lat, lon in plan.points),
                        "mode": plan.mode,
                    },
                    api_key=self._api_key,
                    timeout=self._timeout,
                    # Usage is accounted per mode: `routing` is the configured
                    # (walking) mode, `routing_drive` the alternate one.
                    api="routing_drive" if plan.alternate else "routing",
                    usage=context.usage_tracker if context is not None else None,
                    # Geoapify Routing: 1 credit per waypoint pair.
                    reserve_credits=len(plan.legs),
                )
        except ProviderRequestError as exc:
            return exc

    def _finish_sequence(
        self, plan: "_RouteRequestPlan", fetched: "dict[str, Any] | ProviderRequestError"
    ) -> list[RouteResult]:
        if isinstance(fetched, ProviderRequestError):
            # Fixed identifiers only: the failure kind, its classified reason
            # and how many waypoints the rejected request carried.
            reason = fetched.reason or fetched.kind
            logger.warning(
                "Routing request failed (provider=%s, kind=%s, reason=%s, mode=%s, waypoints=%d).",
                self.provider_name, fetched.kind, reason, plan.mode, len(plan.points),
            )
            status = (
                ProviderStatus.NOT_CONNECTED
                if fetched.kind == "not_connected"
                else ProviderStatus.UNAVAILABLE
                if fetched.kind in ("budget_exhausted", "no_generation_context")
                else ProviderStatus.FAILED
            )
            failed = self._result(status, _FAILURE_MESSAGES.get(fetched.kind, _FAILED_MESSAGE)).model_copy(
                update={"failure_reason": reason}
            )
            if (
                self._context is not None
                and fetched.kind == "bad_request"
                and fetched.reason in _DEFINITIVE_FAILURE_REASONS
            ):
                # Generation memory only: a failure is never written to the
                # provider cache, and a new generation asks the provider again.
                self._context.route_failure_memo[self._request_key(plan.points, plan.mode)] = failed
                generation_diagnostics.route_request("live_definitive_failure")
            return [failed] * len(plan.legs)

        results = self._normalize(fetched, len(plan.legs), plan.mode)
        for (origin, destination), result in zip(plan.legs, results):
            if result.status == ProviderStatus.SUCCESS:
                self._remember_leg(origin, destination, result, plan.mode)
        return results

    # -- response ------------------------------------------------------------------

    def _result(self, status: ProviderStatus, message: str) -> RouteResult:
        return RouteResult(provider=self.provider_name, status=status, source=_SOURCE, message=message)

    def _normalize(self, payload: dict[str, Any], leg_count: int, mode: str) -> list[RouteResult]:
        features = payload.get("features")
        feature = features[0] if isinstance(features, list) and features else None
        properties = feature.get("properties") if isinstance(feature, dict) else None
        raw_legs = properties.get("legs") if isinstance(properties, dict) else None
        if not isinstance(raw_legs, list) or len(raw_legs) != leg_count:
            return [self._result(ProviderStatus.UNAVAILABLE, _UNAVAILABLE_MESSAGE)] * leg_count

        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        lines = geometry.get("coordinates") if isinstance(geometry, dict) else None
        per_leg_lines = (
            lines
            if isinstance(geometry, dict)
            and geometry.get("type") == "MultiLineString"
            and isinstance(lines, list)
            and len(lines) == leg_count
            else [None] * leg_count
        )

        results: list[RouteResult] = []
        for raw_leg, line in zip(raw_legs, per_leg_lines):
            distance = raw_leg.get("distance") if isinstance(raw_leg, dict) else None
            duration = raw_leg.get("time") if isinstance(raw_leg, dict) else None
            if not isinstance(distance, (int, float)) or not isinstance(duration, (int, float)):
                results.append(self._result(ProviderStatus.UNAVAILABLE, _UNAVAILABLE_MESSAGE))
                continue
            results.append(
                RouteResult(
                    provider=self.provider_name,
                    status=ProviderStatus.SUCCESS,
                    distance_meters=float(distance),
                    duration_seconds=float(duration),
                    geometry=_parse_geojson_linestring({"type": "LineString", "coordinates": line}),
                    source=_SOURCE,
                    confidence=0.8,
                    message=f"{mode} route from Geoapify Routing.",
                    mode=mode,
                    includes_ferry=_includes_ferry(raw_leg),
                )
            )
        return results

    # -- per-generation memo + provider cache ----------------------------------------

    def _leg_key(self, origin: Point, destination: Point, mode: str) -> str:
        # Coordinates + mode: a walking and a driving route for the same two
        # points are different facts and never share a cache entry.
        return make_query_hash(
            {
                "from": [round(origin[0], _COORDINATE_PRECISION), round(origin[1], _COORDINATE_PRECISION)],
                "to": [round(destination[0], _COORDINATE_PRECISION), round(destination[1], _COORDINATE_PRECISION)],
                "mode": mode,
                "schema": _CACHE_SCHEMA,
            }
        )

    def _request_key(self, points: list[Point], mode: str) -> str:
        # The whole request as it is sent: every waypoint exactly as written
        # into the `waypoints` parameter, in order, plus the mode. A rejected
        # multi-waypoint request says nothing about any one of its legs, so
        # this is never a per-leg key; a reversed or different sequence, or
        # another mode, is a different request.
        return make_query_hash(
            {
                "waypoints": [f"{lat},{lon}" for lat, lon in points],
                "mode": mode,
                "schema": _CACHE_SCHEMA,
            }
        )

    def _known_leg(self, origin: Point, destination: Point, mode: str) -> RouteResult | None:
        key = self._leg_key(origin, destination, mode)
        if self._context is not None and key in self._context.route_memo:
            performance.count("route_memo_hits")
            return self._context.route_memo[key]
        cache_store = self._resolve_cache_store()
        if cache_store is None:
            return None
        try:
            entry = cache_store.get(_ROUTE_CACHE_SOURCE, key)
            if entry is None:
                return None
            payload = entry.payload
            result = RouteResult(
                provider=self.provider_name,
                status=ProviderStatus.SUCCESS,
                distance_meters=float(payload["distance_meters"]),
                duration_seconds=float(payload["duration_seconds"]),
                geometry=_geometry_from_cache_payload(payload.get("geometry")),
                source=_SOURCE,
                confidence=0.8,
                message=f"{mode} route from Geoapify Routing (cached).",
                mode=mode,
                # None for an entry cached before the flag was kept: unknown,
                # never "no ferry".
                includes_ferry=payload.get("includes_ferry"),
            )
        except Exception:
            logger.warning("Route cache read failed; falling back to live request.")
            return None
        if self._context is not None:
            self._context.route_memo[key] = result
        return result

    def _remember_leg(self, origin: Point, destination: Point, result: RouteResult, mode: str) -> None:
        key = self._leg_key(origin, destination, mode)
        if self._context is not None:
            self._context.route_memo[key] = result
        cache_store = self._resolve_cache_store()
        if cache_store is None:
            return
        try:
            cache_store.set(
                _ROUTE_CACHE_SOURCE,
                key,
                {
                    "distance_meters": result.distance_meters,
                    "duration_seconds": result.duration_seconds,
                    "geometry": [point.model_dump() for point in result.geometry] if result.geometry else None,
                    "includes_ferry": result.includes_ferry,
                },
                ttl_seconds=self._route_cache_ttl_seconds,
            )
        except Exception:
            logger.warning("Route cache write failed; returning live result anyway.")
