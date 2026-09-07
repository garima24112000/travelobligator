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


class MovementDataProvenance(str, Enum):
    """Shared, cross-cutting label for where a piece of movement/route
    data actually came from (Step 166E, docs/12_provider_architecture.md,
    docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
    Every `RouteLegFeasibility`, `RouteAwareSequenceSuggestion`, and
    `TravelTimeBuffer` (and each of their parent reports) carries one of
    these *in addition to* -- never instead of -- its own existing,
    finer-grained status field, so a caller can tell at a glance whether a
    given duration/distance/reorder/buffer figure is real, provider-backed
    data or something else, without first learning each report's own
    status vocabulary.

    - `provider_backed`: a real, successful `RouteResult` exists for this
      leg/day, and any duration/distance/buffer/reorder shown for it came
      directly from that result -- never fabricated, never a
      straight-line/haversine estimate.
    - `not_connected`: no routing provider is configured at all.
    - `unavailable`: the routing provider was reached but returned no
      usable route (also used for a `partial` aggregate, since a mixed
      result is not fully usable either).
    - `not_computable`: a required input (most commonly, one or both
      experiences missing coordinates) meant the routing provider was
      never even called for this leg/day.
    - `failed`: the routing provider request failed, or an unexpected
      exception was safely contained (Step 166D) -- either way, no real
      route data exists for this leg/day.
    - `not_applied`: specific to `RouteAwareSequenceSuggestion` -- real,
      provider-backed data may well exist (`status == success`), but it
      was never applied to the actual schedule, whether because
      `Settings.route_aware_scheduling_enabled` is `False` (the default),
      the suggestion didn't clear `apply_report`'s safety bar, or
      `apply_report` was simply never called. `provider_backed` and
      `not_applied` are not mutually exclusive concepts -- data can be
      genuinely provider-backed and still not applied; this value flags
      exactly that case so it is never confused with `provider_backed`
      data that *was* applied.
    """

    PROVIDER_BACKED = "provider_backed"
    NOT_CONNECTED = "not_connected"
    UNAVAILABLE = "unavailable"
    NOT_COMPUTABLE = "not_computable"
    FAILED = "failed"
    NOT_APPLIED = "not_applied"


def movement_data_provenance_from_status(
    status: "ProviderStatus | TravelTimeBufferStatus",
) -> MovementDataProvenance:
    """Shared mapping from any of this subsystem's provider-outcome
    statuses (`RouteResult.status`, `RouteFeasibilityReport.status`,
    `RouteAwareSequencingReport.status`, `TravelTimeBufferReport.status`,
    `TravelTimeBufferStatus`) onto the cross-cutting
    `MovementDataProvenance` vocabulary (Step 166E). Compares `.value`
    strings rather than enum identity so this one function works for both
    `ProviderStatus` and `TravelTimeBufferStatus` inputs, since their
    shared members use identical string values. `partial` and
    `unavailable` both map onto `MovementDataProvenance.UNAVAILABLE` -- a
    coarser, shared label sitting alongside (never replacing) each
    report's own finer-grained status.
    """
    value = status.value
    if value == "success":
        return MovementDataProvenance.PROVIDER_BACKED
    if value == "not_connected":
        return MovementDataProvenance.NOT_CONNECTED
    if value == "failed":
        return MovementDataProvenance.FAILED
    if value == "not_computable":
        return MovementDataProvenance.NOT_COMPUTABLE
    return MovementDataProvenance.UNAVAILABLE


def route_aware_suggestion_provenance(
    status: "ProviderStatus", applied: bool
) -> MovementDataProvenance:
    """`MovementDataProvenance` for one `RouteAwareSequenceSuggestion`
    (Step 166E) -- the only place `not_applied` is ever produced. A
    `success` suggestion that hasn't (yet, or ever) been applied is
    `not_applied`, not `provider_backed`, since `provider_backed` alone
    would not communicate that the schedule itself is untouched. Once
    `RouteAwareSequencingService.apply_report` actually reorders this
    day, the caller is responsible for flipping this to
    `MovementDataProvenance.PROVIDER_BACKED` directly (mirroring how
    `suggestion.message` is also updated at that point) -- this function
    only ever computes the initial, at-`build_report`-time value.
    """
    if status == ProviderStatus.SUCCESS and not applied:
        return MovementDataProvenance.NOT_APPLIED
    return movement_data_provenance_from_status(status)


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


class RoutePathPoint(BaseModel):
    """One ordered point along a provider-backed route's real path
    geometry (Step 173A, docs/13_llm_reasoning_pipeline.md,
    docs/14_backend_architecture.md). Coordinate bounds match `GeoPoint`.

    Every `RoutePathPoint` is copied verbatim from a routing provider's
    own geometry payload -- never interpolated, simplified beyond what
    the provider itself already did, or synthesized from an origin/
    destination pair. A straight line between two stops is never
    represented as a `RoutePathPoint` sequence.
    """

    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)


class RouteResult(BaseModel):
    """Normalized point-to-point route result returned by a
    `RoutingProvider` adapter (Step 165A).

    Only fields the underlying source actually returned are populated:
    `distance_meters`/`duration_seconds` are `None` whenever the provider
    didn't supply a usable route -- never a guessed, estimated, or
    haversine-derived value (straight-line distance is a different concept
    entirely; see `app.utils.geo.haversine_distance_km`, which is not a
    route and is never substituted here).

    `geometry` (Step 173A) is `None` whenever `status != success`, or when
    the provider's own response didn't include a usable path -- an
    `OSRMRoutingAdapter` is never expected to invent one, and no other
    adapter in this codebase currently populates it either. When present,
    it is the real, ordered sequence of `RoutePathPoint`s the provider
    itself returned for this exact route -- never a straight line between
    `RouteRequest.origin_*`/`destination_*`, and never derived from
    those coordinates by this app.
    """

    provider: str
    status: ProviderStatus
    distance_meters: float | None = None
    duration_seconds: float | None = None
    geometry: list[RoutePathPoint] | None = None
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
    # Step 166E: cross-cutting movement-data-provenance label -- see
    # `MovementDataProvenance`'s own docstring. Computed alongside
    # `status`/`feasibility_status`, never replacing either. Default is
    # the conservative `unavailable` value so a leg constructed without
    # setting this explicitly (e.g. in an older test fixture) is never
    # mistaken for `provider_backed`.
    movement_data_provenance: MovementDataProvenance = MovementDataProvenance.UNAVAILABLE
    # Step 173A: real, provider-backed route path geometry for this exact
    # leg -- a full map path visualization contract only, not drawn by
    # any frontend yet (Section 173 continues that work). `None` unless
    # `status == ProviderStatus.SUCCESS` *and* the routing provider's own
    # `RouteResult.geometry` was populated; copied verbatim from there,
    # never re-derived from `from_lat`/`from_lon`/`to_lat`/`to_lon`, and
    # never a straight line between them. Backward compatible: an older
    # `RouteLegFeasibility` persisted before this step simply has this
    # field default to `None` on load.
    route_geometry: list[RoutePathPoint] | None = None


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
    # Step 166E: report-level movement-data-provenance label, derived from
    # `status` via `movement_data_provenance_from_status` -- see
    # `MovementDataProvenance`'s own docstring.
    movement_data_provenance: MovementDataProvenance = MovementDataProvenance.UNAVAILABLE


class RouteAwareSequenceSuggestion(BaseModel):
    """One day's route-aware sequencing suggestion (Step 166A,
    docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
    docs/14_backend_architecture.md). Built by
    `RouteAwareSequencingService` from real `ProviderGateway.get_route`
    calls only -- never a straight-line/haversine estimate presented as a
    route duration/distance, and never a guessed improvement.

    This is a **shadow/report-only suggestion**: `original_order` always
    matches the day's actual scheduled experience order exactly, and
    `suggested_order` is never applied back onto `ExperiencePlan` by this
    step or any step before Section 166B+ -- see
    `RouteAwareSequencingReport.is_shadow_only`/`applied_to_itinerary`.

    `status` mirrors the same four-state provider honesty contract used
    elsewhere in this subsystem (`success`/`partial`/`unavailable`/
    `not_connected`/`failed`):

    - `success`: every scheduled experience in this day has coordinates,
      and every route lookup needed to both measure the original order and
      compute a candidate reordering returned a real, successful route.
    - `partial`: some, but not all, of those route lookups succeeded (e.g.
      the routing provider returned `not_connected`/`unavailable`/`failed`
      for at least one pair, or at least one -- but not all -- scheduled
      experience in the day is missing coordinates).
    - `unavailable`: fewer than two scheduled experiences in this day have
      coordinates, so no route lookup could even be attempted.
    - `not_connected`: no routing provider is configured at all, so every
      route lookup this day needed came back `not_connected`.
    - `failed`: at least one required route lookup failed, and none
      succeeded.

    `route_duration_seconds`/`route_distance_meters` are the *suggested*
    order's own total duration/distance, summed only from real successful
    `RouteResult`s along every consecutive pair of the suggested order --
    both stay `None` unless every one of those pairs succeeded, never a
    partial sum presented as a complete total. `improvement_seconds` is
    `original` minus `suggested` total duration, and likewise stays `None`
    unless both totals are fully computed from real provider data. Nothing
    here is ever an optimality claim -- see
    `RouteAwareSequencingService`'s own docstring for the conservative
    nearest-next heuristic used.

    `applied` (Step 166B, docs/12_provider_architecture.md,
    docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md)
    stays `False` unless `RouteAwareSequencingService.apply_report`
    actually reordered this specific day's scheduled experiences to match
    `suggested_order` -- which only ever happens when
    `Settings.route_aware_scheduling_enabled` is `True`, this suggestion's
    own `status == success`, its real `improvement_seconds` exceeds the
    configured minimum, and `suggested_order` is verified to be an exact
    permutation of this day's real, current scheduled experience IDs (no
    experience lost, added, or duplicated).
    """

    day_index: int = Field(gt=0)
    status: ProviderStatus
    original_order: list[str] = Field(default_factory=list)
    suggested_order: list[str] = Field(default_factory=list)
    route_duration_seconds: float | None = None
    route_distance_meters: float | None = None
    improvement_seconds: float | None = None
    provider: str | None = None
    message: str | None = None
    applied: bool = False
    # Step 166E: cross-cutting movement-data-provenance label -- the only
    # place `MovementDataProvenance.NOT_APPLIED` is ever produced. Set by
    # `route_aware_suggestion_provenance` at build_report time (a
    # `success` suggestion is `not_applied` until actually applied), then
    # flipped to `PROVIDER_BACKED` directly by `apply_report` once it
    # actually reorders this day -- mirroring how `message` is updated at
    # that same point. See `MovementDataProvenance`'s own docstring.
    movement_data_provenance: MovementDataProvenance = MovementDataProvenance.UNAVAILABLE


class RouteAwareSequencingReport(BaseModel):
    """Plan-level route-aware sequencing report across every scheduled day
    with two or more scheduled experiences (Step 166A,
    docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
    docs/14_backend_architecture.md). Computed by
    `RouteAwareSequencingService`, after `route_feasibility_report` and
    before `PlanValidatorService` runs.

    **Building this report (`RouteAwareSequencingService.build_report`) is
    always shadow/report-only** (`is_shadow_only=True`,
    `applied_to_itinerary=False` immediately after it is built -- neither
    field is ever flipped by `build_report` itself). It never reorders,
    adds, or drops a scheduled experience: `ExperiencePlannerService`'s
    straight-line/haversine scheduling (Step 156C) and the actual
    `ExperiencePlan.daily_plans[*].experiences` order are both completely
    untouched by building this report. `status` is an honest aggregate
    over `suggestions`' own `status` values, using the same rules as
    `RouteFeasibilityReport.status` -- `success` only when every day
    suggestion succeeded, `not_connected` when every day suggestion (or
    zero days, e.g. no experience_plan, or no day with 2+ scheduled
    experiences) is `not_connected`, `partial` when a mix of success and
    non-success exists, `failed` when at least one day failed and none
    succeeded, `unavailable` otherwise (e.g. every day is missing
    coordinates). This never fabricates a route duration, distance, or
    sequencing improvement, and it does not change `validation_report` or
    regeneration behavior.

    **Step 166B (docs/12_provider_architecture.md,
    docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md)
    adds an explicitly config-gated application path,
    `RouteAwareSequencingService.apply_report`, called only from
    `PlanningOrchestrator` and only when
    `Settings.route_aware_scheduling_enabled` is `True` (default
    `False`).** When disabled -- the default -- `is_shadow_only` stays
    `True` and `applied_to_itinerary` stays `False`, exactly as Step 166A
    left them, and the scheduled itinerary order is completely unchanged.
    When enabled and `apply_report` actually reorders at least one day
    (every safety check in its own docstring passed), this report's
    `is_shadow_only` flips to `False` and `applied_to_itinerary` flips to
    `True`, and each affected `RouteAwareSequenceSuggestion.applied` flips
    to `True`. `apply_report` never applies a `partial`/`unavailable`/
    `not_connected`/`failed` suggestion, never applies a suggestion whose
    real improvement doesn't exceed the configured minimum, and never
    applies a `suggested_order` that isn't a verified exact permutation of
    that day's real, current scheduled experience IDs -- no experience is
    ever added, removed, or duplicated, and no experience field other than
    schedule position is ever changed.
    """

    status: ProviderStatus
    suggestions: list[RouteAwareSequenceSuggestion] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utc_now)
    is_shadow_only: bool = True
    applied_to_itinerary: bool = False
    # Step 166E: report-level movement-data-provenance label, derived from
    # `status` via `movement_data_provenance_from_status` -- see
    # `MovementDataProvenance`'s own docstring. This is a coarser,
    # report-wide label; `is_shadow_only`/`applied_to_itinerary` above
    # remain the authoritative fields for whether anything was actually
    # applied.
    movement_data_provenance: MovementDataProvenance = MovementDataProvenance.UNAVAILABLE


class TravelTimeBufferStatus(str, Enum):
    """Honest outcome of the route lookup a `TravelTimeBuffer` was built
    from (Step 166C). Distinguishes `unavailable` (the routing provider
    was actually called and returned a real, non-`success` response) from
    `not_computable` (one or both experiences are missing coordinates, so
    the routing provider was never even called for this leg) -- a finer
    distinction than `RouteLegFeasibility.status` draws, since a travel-
    time buffer specifically needs to say *why* no duration could be
    recommended.
    """

    SUCCESS = "success"
    NOT_CONNECTED = "not_connected"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    NOT_COMPUTABLE = "not_computable"


class BufferSufficiencyStatus(str, Enum):
    """This leg's honest travel-time-buffer verdict (Step 166C):

    - `sufficient`: a real, successful route duration and a real,
      known schedule gap (both `end_time`/`start_time` parsed) both
      exist, and the duration does not exceed the gap.
    - `insufficient`: same as above, but the real route duration exceeds
      the real known gap.
    - `not_computable`: a real, successful route duration exists, but no
      schedule gap is known (no `end_time`/`start_time` exist yet on
      these scheduled experiences, which is the case for every plan this
      app currently generates, Step 156-era scheduling) -- sufficiency is
      never guessed in this case, only left uncomputed.
    - `unavailable`: no real route duration exists at all (routing
      `not_connected`/`unavailable`/`failed`, or one or both experiences
      are missing coordinates) -- sufficiency can't be assessed either
      way, regardless of whether a schedule gap happens to be known.
    """

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"
    NOT_COMPUTABLE = "not_computable"


class TravelTimeBuffer(BaseModel):
    """Travel-time buffer for one leg between two consecutive scheduled
    experiences within the same day (Step 166C,
    docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
    docs/14_backend_architecture.md). Built by `TravelTimeBufferService`
    from a real `ProviderGateway.get_route` call -- never a straight-
    line/haversine estimate presented as a route duration, and never a
    guessed or padded buffer duration.

    `recommended_buffer_seconds` is never anything other than an exact
    restatement of `route_duration_seconds` when `status == success` --
    this step never adds an invented padding percentage or safety margin
    on top of the real provider-backed duration, since doing so would
    itself be a fabricated buffer figure. `available_gap_seconds` is the
    real, parsed gap between `from_experience.end_time` and
    `to_experience.start_time` when both are set and parseable (mirroring
    `RouteFeasibilityService`'s own schedule-gap parsing) -- `None`
    whenever no such schedule timestamp exists, which is the case for
    every plan this app currently generates. This never invents a
    schedule timestamp, a rating, a price, an opening hour, a booking/
    availability claim, or a closure/crowd/safety judgement -- it is
    travel-time data only.
    """

    from_experience_id: str
    from_experience_name: str
    to_experience_id: str
    to_experience_name: str

    provider: str
    status: TravelTimeBufferStatus
    route_duration_seconds: float | None = None
    route_distance_meters: float | None = None
    recommended_buffer_seconds: float | None = None
    available_gap_seconds: float | None = None
    buffer_status: BufferSufficiencyStatus
    message: str | None = None
    # Step 166E: cross-cutting movement-data-provenance label -- see
    # `MovementDataProvenance`'s own docstring. Computed alongside
    # `status`/`buffer_status`, never replacing either.
    movement_data_provenance: MovementDataProvenance = MovementDataProvenance.UNAVAILABLE
    # Step 173A: real, provider-backed route path geometry for this exact
    # leg -- see `RouteLegFeasibility.route_geometry`'s docstring for the
    # full contract (same rules apply here: `None` unless `status ==
    # TravelTimeBufferStatus.SUCCESS` and the routing provider's own
    # `RouteResult.geometry` was populated, copied verbatim, never
    # re-derived or straight-lined). Backward compatible: an older
    # `TravelTimeBuffer` persisted before this step simply has this field
    # default to `None` on load.
    route_geometry: list[RoutePathPoint] | None = None


class TravelTimeBufferReport(BaseModel):
    """Plan-level travel-time buffer report across every scheduled day
    (Step 166C, docs/12_provider_architecture.md,
    docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
    Computed by `TravelTimeBufferService` inside
    `PlanningOrchestrator.run_experience_plan_stage`, after
    `route_feasibility_report`, after any Step 166B config-gated
    route-aware-scheduling application, and before `PlanValidatorService`
    runs -- so `buffers` always reflects the final scheduled order for
    this generation run.

    `status` is an honest aggregate over `buffers`' own `status` values,
    using the same rules as `RouteFeasibilityReport.status` -- `success`
    only when every leg succeeded, `not_connected` when every leg (or zero
    legs, e.g. no experience_plan, or every day has at most one scheduled
    experience) is `not_connected`, `partial` when a mix of success and
    non-success exists, `failed` when at least one leg failed and none
    succeeded, `unavailable` otherwise (e.g. every leg is missing
    coordinates). `uses_provider_backed_routes` is a static, always-`True`
    marker (mirroring `RouteAwareSequencingReport.is_shadow_only`'s
    role) -- this report never falls back to a straight-line/haversine
    estimate for any buffer figure. This never reorders or drops a
    scheduled experience, never fabricates a route duration, distance, or
    buffer, and it does not change regeneration behavior by itself.
    """

    status: ProviderStatus
    buffers: list[TravelTimeBuffer] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utc_now)
    uses_provider_backed_routes: bool = True
    # Step 166E: report-level movement-data-provenance label, derived from
    # `status` via `movement_data_provenance_from_status` -- see
    # `MovementDataProvenance`'s own docstring.
    movement_data_provenance: MovementDataProvenance = MovementDataProvenance.UNAVAILABLE
