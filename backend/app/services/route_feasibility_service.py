from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from datetime import time as time_of_day

from app.models.common import ProviderStatus
from app.models.planning_state import ExperienceItem, PlanningState
from app.models.routing import (
    MovementDataProvenance,
    RouteFeasibilityReport,
    RouteFeasibilityStatus,
    RouteLegFeasibility,
    RouteRequest,
    RouteResult,
    movement_data_provenance_from_status,
)
from app.providers.gateway import ProviderGateway, provider_gateway

logger = logging.getLogger(__name__)

# Deterministic provider infrastructure, not AI reasoning (Step 165E,
# docs/12_provider_architecture.md section 35,
# docs/13_llm_reasoning_pipeline.md section 58). Computes route feasibility
# between consecutive scheduled experiences within each day, using the real
# routing provider already exposed through ProviderGateway (Step 165B) and
# cache-backed when configured (Step 165C). This never reorders or drops a
# scheduled experience and never performs route-aware scheduling -- that is
# Section 166's job, not this step's. No distance, duration, geometry, or
# feasibility judgement is ever fabricated: a leg only becomes "feasible"
# when RouteResult.status == success, and a leg with a missing coordinate
# is reported "unavailable" without ever calling the routing provider for
# it.

_NOT_CONNECTED_MESSAGE = (
    "No routing provider is connected, so this leg's route feasibility could not be checked."
)
_MISSING_COORDINATES_MESSAGE = (
    "One or both scheduled experiences are missing coordinates, so this leg's route "
    "feasibility is not computable."
)
_FAILED_MESSAGE = "The routing provider request failed for this leg."
_UNEXPECTED_FAILURE_MESSAGE = (
    "The routing provider request failed unexpectedly for this leg."
)
_SUCCESS_MESSAGE = "A provider-backed route was found for this leg."
_TIME_GAP_EXCEEDED_MESSAGE_TEMPLATE = (
    "A provider-backed route was found for this leg, but its duration "
    "({duration:.0f}s) exceeds the scheduled time gap ({gap:.0f}s) between these "
    "experiences."
)
_TIME_FORMAT = "%H:%M"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_clock_time(value: str | None) -> time_of_day | None:
    """Parses a plain "HH:MM" schedule time, or returns `None` for anything
    missing/unparseable -- never guesses a time. `ExperienceItem.start_time`/
    `end_time` are free-form strings that this app's scheduling does not
    currently populate at all (Step 156-era scheduling leaves them unset),
    so this is dormant in practice today; it exists so a future step that
    does populate real schedule times gets a working time-gap check for
    free, without inventing a threshold where no timestamp exists.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, _TIME_FORMAT).time()
    except ValueError:
        return None


def _schedule_gap_seconds(from_experience: ExperienceItem, to_experience: ExperienceItem) -> float | None:
    """Seconds between `from_experience.end_time` and `to_experience.start_time`,
    or `None` when either is missing/unparseable/negative -- never a
    fabricated gap. See `_parse_clock_time`.
    """
    end = _parse_clock_time(from_experience.end_time)
    start = _parse_clock_time(to_experience.start_time)
    if end is None or start is None:
        return None
    anchor_day = date.today()
    gap_seconds = (
        datetime.combine(anchor_day, start) - datetime.combine(anchor_day, end)
    ).total_seconds()
    return gap_seconds if gap_seconds >= 0 else None


def _feasibility_from_result(
    result: RouteResult, from_experience: ExperienceItem, to_experience: ExperienceItem
) -> tuple[RouteFeasibilityStatus, str]:
    if result.status != ProviderStatus.SUCCESS:
        if result.status == ProviderStatus.NOT_CONNECTED:
            return RouteFeasibilityStatus.NEEDS_REVIEW, _NOT_CONNECTED_MESSAGE
        if result.status == ProviderStatus.FAILED:
            return RouteFeasibilityStatus.NEEDS_REVIEW, _FAILED_MESSAGE
        return (
            RouteFeasibilityStatus.NEEDS_REVIEW,
            result.message or "The routing provider returned no usable route for this leg.",
        )

    if result.duration_seconds is not None:
        gap_seconds = _schedule_gap_seconds(from_experience, to_experience)
        if gap_seconds is not None and result.duration_seconds > gap_seconds:
            return (
                RouteFeasibilityStatus.NEEDS_REVIEW,
                _TIME_GAP_EXCEEDED_MESSAGE_TEMPLATE.format(
                    duration=result.duration_seconds, gap=gap_seconds
                ),
            )

    return RouteFeasibilityStatus.FEASIBLE, _SUCCESS_MESSAGE


class RouteFeasibilityService:
    """Builds a `RouteFeasibilityReport` for the current `experience_plan`
    (Step 165E). Run by `PlanningOrchestrator.run_experience_plan_stage`
    after `ExperiencePlannerService.run` and before `PlanValidatorService.run`.

    `build_report` is a pure read: it never mutates `planning_state`, never
    reorders or drops a scheduled experience, and only ever calls
    `ProviderGateway.get_route` -- never a provider adapter directly, never
    Groq/Anthropic/Kiwi/MCP, and never scrapes. With the default
    `routing_provider="not_connected"` configuration, every leg (and the
    report as a whole) honestly reports `not_connected` without any network
    call.

    Step 166D hardening: every `get_route` call goes through
    `_safe_get_route`, so an unexpected exception from the routing provider
    (as opposed to an honest `failed`/`unavailable` `RouteResult`, which
    every real adapter already returns on its own failure) can never crash
    this leg, this report, or generation as a whole -- it is treated
    exactly like a `failed` result instead.
    """

    def __init__(self, gateway: ProviderGateway | None = None) -> None:
        self.gateway = gateway or provider_gateway

    def build_report(self, planning_state: PlanningState) -> RouteFeasibilityReport:
        provider_name = getattr(self.gateway.routing, "provider_name", "routing_provider")
        legs: list[RouteLegFeasibility] = []

        experience_plan = planning_state.experience_plan
        if experience_plan is not None:
            for day_plan in experience_plan.daily_plans:
                experiences = day_plan.experiences
                for index in range(len(experiences) - 1):
                    legs.append(
                        self._build_leg(experiences[index], experiences[index + 1], provider_name)
                    )

        report_status = _aggregate_status([leg.status for leg in legs])
        route_data_source = (
            provider_name if any(leg.status == ProviderStatus.SUCCESS for leg in legs) else "not_connected"
        )

        return RouteFeasibilityReport(
            status=report_status,
            legs=legs,
            provider=provider_name,
            route_data_source=route_data_source,
            generated_at=_utc_now(),
            movement_data_provenance=movement_data_provenance_from_status(report_status),
        )

    def _build_leg(
        self,
        from_experience: ExperienceItem,
        to_experience: ExperienceItem,
        provider_name: str,
    ) -> RouteLegFeasibility:
        from_point = from_experience.coordinates
        to_point = to_experience.coordinates

        if from_point is None or to_point is None:
            return RouteLegFeasibility(
                from_experience_id=from_experience.experience_id,
                from_experience_name=from_experience.name,
                to_experience_id=to_experience.experience_id,
                to_experience_name=to_experience.name,
                from_lat=from_point.lat if from_point else None,
                from_lon=from_point.lng if from_point else None,
                to_lat=to_point.lat if to_point else None,
                to_lon=to_point.lng if to_point else None,
                provider=provider_name,
                status=ProviderStatus.UNAVAILABLE,
                distance_meters=None,
                duration_seconds=None,
                feasibility_status=RouteFeasibilityStatus.UNAVAILABLE,
                message=_MISSING_COORDINATES_MESSAGE,
                movement_data_provenance=MovementDataProvenance.NOT_COMPUTABLE,
            )

        request = RouteRequest(
            origin_lat=from_point.lat,
            origin_lon=from_point.lng,
            destination_lat=to_point.lat,
            destination_lon=to_point.lng,
        )
        result = _safe_get_route(self.gateway, request)
        feasibility_status, message = _feasibility_from_result(result, from_experience, to_experience)

        return RouteLegFeasibility(
            from_experience_id=from_experience.experience_id,
            from_experience_name=from_experience.name,
            to_experience_id=to_experience.experience_id,
            to_experience_name=to_experience.name,
            from_lat=from_point.lat,
            from_lon=from_point.lng,
            to_lat=to_point.lat,
            to_lon=to_point.lng,
            provider=result.provider,
            status=result.status,
            distance_meters=result.distance_meters,
            duration_seconds=result.duration_seconds,
            feasibility_status=feasibility_status,
            message=message,
            movement_data_provenance=movement_data_provenance_from_status(result.status),
            # Step 173A: copied verbatim from the routing provider's own
            # result -- already `None` unless `result.status == success`
            # and the provider itself returned usable geometry, so no
            # extra condition is needed here.
            route_geometry=result.geometry,
        )


def _safe_get_route(gateway: ProviderGateway, request: RouteRequest) -> RouteResult:
    """Calls `gateway.get_route(request)`, but never lets an unexpected
    exception from that call escape (Step 166D hardening). Every real
    routing adapter already converts its own failure modes (network
    error, timeout, malformed response) into an honest
    `RouteResult(status=failed/unavailable/not_connected)` without
    raising -- this is a second line of defense for a genuinely
    unexpected bug (a misbehaving adapter, a test double, a future
    provider) so a single leg's failure can never crash the whole
    `RouteFeasibilityReport`, and by extension the whole generation run.

    On an exception, this returns an honest `status=failed` `RouteResult`
    with a generic, safe message -- never the raw exception text or any
    provider payload, and never a straight-line/haversine estimate
    substituted in its place. The exception itself is only ever logged
    server-side (never shown to a caller).
    """
    try:
        return gateway.get_route(request)
    except Exception:
        logger.warning(
            "ProviderGateway.get_route raised unexpectedly; treating this leg as failed.",
            exc_info=True,
        )
        provider_name = getattr(gateway.routing, "provider_name", "routing_provider")
        return RouteResult(
            provider=provider_name,
            status=ProviderStatus.FAILED,
            distance_meters=None,
            duration_seconds=None,
            source=provider_name,
            confidence=0.0,
            message=_UNEXPECTED_FAILURE_MESSAGE,
        )


def _aggregate_status(leg_statuses: list[ProviderStatus]) -> ProviderStatus:
    """Honest aggregate over every leg's own `RouteResult.status` -- never
    optimistic. `success` only when every leg succeeded; `not_connected`
    only when every leg is `not_connected` (including the trivial case of
    zero legs, e.g. an empty or single-experience plan); `partial` when a
    mix of success and non-success exists; `failed` when at least one leg
    failed and none succeeded; `unavailable` otherwise (e.g. all legs
    missing coordinates).
    """
    if not leg_statuses:
        return ProviderStatus.NOT_CONNECTED
    if all(status == ProviderStatus.SUCCESS for status in leg_statuses):
        return ProviderStatus.SUCCESS
    if any(status == ProviderStatus.SUCCESS for status in leg_statuses):
        return ProviderStatus.PARTIAL
    if all(status == ProviderStatus.NOT_CONNECTED for status in leg_statuses):
        return ProviderStatus.NOT_CONNECTED
    if any(status == ProviderStatus.FAILED for status in leg_statuses):
        return ProviderStatus.FAILED
    return ProviderStatus.UNAVAILABLE


route_feasibility_service = RouteFeasibilityService()
