from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.routing import RouteRequest, RouteResult, RoutingProfile

# Provider boundary for point-to-point routing (Step 165A,
# docs/12_provider_architecture.md section 11/31,
# docs/14_backend_architecture.md section 25). This module only defines the
# interface a routing adapter must implement -- no adapter here is wired
# into `ProviderGateway`, `PlanningOrchestrator`, `ExperiencePlannerService`,
# or `PlanValidatorService` yet, and no cache is wired in (unlike
# Open-Meteo/Nager.Date/Frankfurter/OSM geocoding+POI, which already read
# `ProviderCacheStore`).


class RoutingProvider(ABC):
    """Interface for a provider that returns a real, point-to-point route
    between two coordinates. Uses `abc.ABC` (mirroring
    `AICandidateProposalProvider`, not the `app.providers.base` interfaces
    that default to an honest `not_connected` `ProviderResponse`) so every
    concrete adapter must explicitly implement `get_route` -- there is no
    silent default behavior to fall back on.
    """

    provider_name: str = "routing_provider"

    @abstractmethod
    def get_route(self, request: RouteRequest) -> RouteResult:
        """Return a `RouteResult` for `request`.

        Implementations must never invent a distance, duration, or route
        geometry. Missing/unusable route data must be reported as
        `not_connected`, `unavailable`, or `failed` -- never guessed, and
        never backfilled from a straight-line (haversine) estimate.
        """
        raise NotImplementedError

    def bound_to(self, provider_context: Any) -> "RoutingProvider":
        """A view of this provider that charges `provider_context`'s usage
        tracker (Section 203C.2B). A provider with no per-call cost returns
        itself."""
        return self

    def get_route_sequence(
        self, points: list[tuple[float, float]], profile: RoutingProfile | None = None
    ) -> list[RouteResult]:
        """One `RouteResult` per consecutive leg of `points` (`(lat, lon)`
        pairs, in visiting order). The default asks `get_route` leg by leg;
        an adapter whose API routes several waypoints at once overrides
        this to make ONE request for the whole day (Section 203C.2B)."""
        results: list[RouteResult] = []
        for (from_lat, from_lon), (to_lat, to_lon) in zip(points, points[1:]):
            request = RouteRequest(
                origin_lat=from_lat, origin_lon=from_lon, destination_lat=to_lat, destination_lon=to_lon
            )
            if profile is not None:
                request.profile = profile
            results.append(self.get_route(request))
        return results
