from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.models.common import ProviderStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

# Contract models for the routing provider skeleton (Step 165A,
# docs/12_provider_architecture.md section 11/31,
# docs/13_llm_reasoning_pipeline.md section 54). This is a contract only:
#
# - No adapter here is wired into `ProviderGateway`, `PlanningOrchestrator`,
#   `ExperiencePlannerService`, or `PlanValidatorService` yet.
# - Nothing in the app currently constructs a `RouteRequest`/consumes a
#   `RouteResult` outside this subsystem's own tests.
#
# `RouteResult` is deliberately not nested inside the generic
# `ProviderResponse[T]` envelope every other provider in this codebase
# returns -- `status` here already carries the same four-state honesty
# contract (`not_connected`/`unavailable`/`failed`/`success`) directly on
# the result itself, since a routing caller almost always wants exactly
# one point-to-point result, not a list. `distance_meters`/
# `duration_seconds` are the only factual route fields this step supports;
# neither is ever a guess -- both stay `None` whenever the underlying
# provider didn't return a usable value.


class RoutingProfile(str, Enum):
    DRIVING = "driving"
    WALKING = "walking"
    CYCLING = "cycling"


class RouteRequest(BaseModel):
    """A single point-to-point routing request. Coordinates only -- never a
    free-text address; callers resolve real coordinates via a
    `PlacesProvider` first (e.g. `OpenStreetMapPlacesAdapter.resolve_coordinates`),
    the same pattern `WeatherProvider`/`HolidayProvider`/`CurrencyProvider`
    already follow for their own destination-resolution needs.

    Latitude/longitude bounds match `GeoPoint` (docs/10_data_model.md) so an
    out-of-range coordinate is rejected by validation rather than silently
    accepted.
    """

    origin_lat: float = Field(ge=-90.0, le=90.0)
    origin_lon: float = Field(ge=-180.0, le=180.0)
    destination_lat: float = Field(ge=-90.0, le=90.0)
    destination_lon: float = Field(ge=-180.0, le=180.0)
    profile: RoutingProfile = RoutingProfile.DRIVING


class RouteResult(BaseModel):
    """Normalized point-to-point route result returned by a
    `RoutingProvider` adapter (Step 165A).

    Only fields the underlying source actually returned are populated:
    `distance_meters`/`duration_seconds` are `None` whenever the provider
    didn't supply a usable route -- never a guessed, estimated, or
    haversine-derived value (straight-line distance is a different concept
    entirely; see `app.utils.geo.haversine_distance_km`, which is not a
    route and is never substituted here). `geometry` is not populated by
    this step's adapter -- it exists as a placeholder for a future step
    that parses OSRM's route geometry.
    """

    provider: str
    status: ProviderStatus
    distance_meters: float | None = None
    duration_seconds: float | None = None
    geometry: str | None = None
    source: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str | None = None


class RouteFeasibilityStatus(str, Enum):
    FEASIBLE = "feasible"
    NEEDS_REVIEW = "needs_review"
    UNAVAILABLE = "unavailable"


class RouteLegFeasibility(BaseModel):
    """Route feasibility for one leg between two consecutive scheduled
    experiences within the same day (Step 165E,
    docs/12_provider_architecture.md section 35). Built by
    `RouteFeasibilityService` from a real `ProviderGateway.get_route` call
    -- never a straight-line/haversine estimate presented as route data,
    and never a guessed distance, duration, or feasibility judgement.

    `status` mirrors the underlying `RouteResult.status` this leg was built
    from (`not_connected`/`unavailable`/`failed`/`success`), so a caller can
    always tell whether real provider data was returned. `feasibility_status`
    is this service's own honest interpretation of that result for
    itinerary review purposes:

    - `feasible`: the routing provider returned a usable route and (when a
      schedule time gap between these two experiences is actually known)
      the route duration does not clearly exceed it.
    - `needs_review`: the routing provider is not connected, returned no
      usable route, the request failed, or a known schedule time gap is
      clearly exceeded by the route duration.
    - `unavailable`: one or both experiences are missing coordinates, so
      this leg is not computable at all -- the routing provider is never
      even called for it.

    This is feasibility *reporting* only -- it never reorders or drops a
    scheduled experience, and it is not full route-aware scheduling
    (Section 166).
    """

    from_experience_id: str
    from_experience_name: str
    to_experience_id: str
    to_experience_name: str

    from_lat: float | None = None
    from_lon: float | None = None
    to_lat: float | None = None
    to_lon: float | None = None

    provider: str
    status: ProviderStatus
    distance_meters: float | None = None
    duration_seconds: float | None = None

    feasibility_status: RouteFeasibilityStatus
    message: str | None = None


class RouteFeasibilityReport(BaseModel):
    """Plan-level route feasibility report across every scheduled day (Step
    165E, docs/12_provider_architecture.md section 35,
    docs/14_backend_architecture.md section 35). Computed by
    `RouteFeasibilityService` after `experience_plan` exists and before
    `PlanValidatorService` runs; consumed by `PlanValidatorService` to
    describe route feasibility honestly instead of a blanket "not checked"
    warning.

    `status` is an honest aggregate over `legs`' own `status` values --
    `success` only when every leg succeeded, `partial` when some did,
    `not_connected` when no routing provider is configured, `failed`/
    `unavailable` otherwise. `route_data_source` names the provider actually
    used for any successful leg (e.g. `"osrm"`), or `"not_connected"` when
    no leg succeeded. This never fabricates a route duration, distance,
    geometry, or feasibility judgement, and it never reorders or drops a
    scheduled experience -- this is not route-aware scheduling (Section
    166).
    """

    status: ProviderStatus
    legs: list[RouteLegFeasibility] = Field(default_factory=list)
    provider: str
    route_data_source: str
    generated_at: datetime = Field(default_factory=_utc_now)
