from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.routing import RouteRequest, RouteResult

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
