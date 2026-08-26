from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from datetime import time as time_of_day

from app.models.common import ProviderStatus
from app.models.planning_state import ExperienceItem, PlanningState
from app.models.routing import (
    BufferSufficiencyStatus,
    MovementDataProvenance,
    RouteRequest,
    RouteResult,
    TravelTimeBuffer,
    TravelTimeBufferReport,
    TravelTimeBufferStatus,
    movement_data_provenance_from_status,
)
from app.providers.gateway import ProviderGateway, provider_gateway

logger = logging.getLogger(__name__)

# Deterministic provider infrastructure, not AI reasoning (Step 166C,
# docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). Computes travel-time buffer metadata
# between consecutive scheduled experiences within each day, using the
# same real routing provider RouteFeasibilityService/
# RouteAwareSequencingService already consume through ProviderGateway
# (Step 165B, cache-backed when configured, Step 165C). This never
# reorders or drops a scheduled experience, never inserts a fake travel
# segment into the plan, and never mutates planning_state beyond the
# report this module returns -- build_report is a pure read, exactly like
# RouteFeasibilityService.build_report/RouteAwareSequencingService.build_report.
#
# No distance, duration, or buffer is ever fabricated:
# `recommended_buffer_seconds` is only ever an exact restatement of a real
# successful RouteResult.duration_seconds -- never an invented padding
# percentage or safety margin, and never a straight-line (haversine)
# estimate substituted for a route duration. `available_gap_seconds` is
# only ever a real, parsed schedule gap (this app's scheduling does not
# currently populate ExperienceItem.start_time/end_time at all, so this is
# dormant in practice today) -- never a guessed or invented timestamp.

_NOT_CONNECTED_MESSAGE = (
    "No routing provider is connected, so no travel-time buffer could be "
    "computed for this leg."
)
_MISSING_COORDINATES_MESSAGE = (
    "One or both scheduled experiences are missing coordinates, so this leg's "
    "travel-time buffer is not computable."
)
_FAILED_MESSAGE = "The routing provider request failed for this leg."
_UNEXPECTED_FAILURE_MESSAGE = (
    "The routing provider request failed unexpectedly for this leg."
)
_UNAVAILABLE_MESSAGE = (
    "The routing provider returned no usable route for this leg, so no "
    "travel-time buffer could be computed."
)
_NO_TIMESTAMPS_MESSAGE_TEMPLATE = (
    "A provider-backed travel duration of {duration:.0f}s was found for this leg "
    "(via {provider}), recommended as its travel-time buffer. No schedule "
    "start/end times exist for these experiences yet, so buffer sufficiency "
    "against the schedule can't be assessed."
)
_INSUFFICIENT_MESSAGE_TEMPLATE = (
    "A provider-backed travel duration of {duration:.0f}s (via {provider}) exceeds "
    "the {gap:.0f}s gap scheduled between these experiences -- this leg's "
    "travel-time buffer is insufficient."
)
_SUFFICIENT_MESSAGE_TEMPLATE = (
    "A provider-backed travel duration of {duration:.0f}s (via {provider}) fits "
    "within the {gap:.0f}s gap scheduled between these experiences -- this "
    "leg's travel-time buffer is sufficient."
)
_TIME_FORMAT = "%H:%M"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_clock_time(value: str | None) -> time_of_day | None:
    """Parses a plain "HH:MM" schedule time, or returns `None` for anything
    missing/unparseable -- never guesses a time. Mirrors
    `RouteFeasibilityService._parse_clock_time` exactly; duplicated here
    rather than imported so this module stays self-contained, matching how
    `RouteAwareSequencingService` also never imports another stage
    service's private helpers.
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


class TravelTimeBufferService:
    """Builds a `TravelTimeBufferReport` for the current `experience_plan`
    (Step 166C). Run by `PlanningOrchestrator.run_experience_plan_stage`
    after `RouteFeasibilityService.build_report` and any Step 166B
    config-gated route-aware-scheduling application, and before
    `PlanValidatorService.run` -- so `buffers` always reflects the final
    scheduled order for this generation run.

    For each consecutive pair of scheduled experiences within a day, this
    calls `ProviderGateway.get_route` (never a provider adapter directly)
    and builds a `TravelTimeBuffer`:

    - A leg with a missing coordinate is `status=not_computable` without
      ever calling the routing provider for it.
    - A leg whose routing provider call is `not_connected`/`unavailable`/
      `failed` mirrors that status exactly, with no duration/distance/
      buffer populated.
    - A leg with a real, successful route gets `route_duration_seconds`/
      `route_distance_meters` straight from the provider, and
      `recommended_buffer_seconds` set to the exact same duration -- never
      padded, never estimated differently.
    - `available_gap_seconds` is the real, parsed schedule gap when both
      `end_time`/`start_time` exist and are parseable, else `None`.
    - `buffer_status` is `sufficient`/`insufficient` only when both a real
      successful duration and a real known gap exist; `not_computable`
      when a real duration exists but no gap is known; `unavailable`
      whenever no real duration exists at all (regardless of gap).

    `build_report` never mutates `planning_state`, never reorders or drops
    a scheduled experience, never inserts a fake travel segment into the
    plan, and only ever calls `ProviderGateway.get_route` -- never Groq/
    Anthropic/Kiwi/MCP, and never scrapes. With the default
    `routing_provider="not_connected"` configuration, every leg (and the
    report as a whole) honestly reports `not_connected` without any
    network call.
    """

    def __init__(self, gateway: ProviderGateway | None = None) -> None:
        self.gateway = gateway or provider_gateway

    def build_report(self, planning_state: PlanningState) -> TravelTimeBufferReport:
        provider_name = getattr(self.gateway.routing, "provider_name", "routing_provider")
        buffers: list[TravelTimeBuffer] = []

        experience_plan = planning_state.experience_plan
        if experience_plan is not None:
            for day_plan in experience_plan.daily_plans:
                experiences = day_plan.experiences
                for index in range(len(experiences) - 1):
                    buffers.append(
                        self._build_buffer(experiences[index], experiences[index + 1], provider_name)
                    )

        report_status = _aggregate_buffer_report_status([buffer.status for buffer in buffers])

        return TravelTimeBufferReport(
            status=report_status,
            buffers=buffers,
            generated_at=_utc_now(),
            uses_provider_backed_routes=True,
            movement_data_provenance=movement_data_provenance_from_status(report_status),
        )

    def _build_buffer(
        self,
        from_experience: ExperienceItem,
        to_experience: ExperienceItem,
        provider_name: str,
    ) -> TravelTimeBuffer:
        from_point = from_experience.coordinates
        to_point = to_experience.coordinates

        if from_point is None or to_point is None:
            return TravelTimeBuffer(
                from_experience_id=from_experience.experience_id,
                from_experience_name=from_experience.name,
                to_experience_id=to_experience.experience_id,
                to_experience_name=to_experience.name,
                provider=provider_name,
                status=TravelTimeBufferStatus.NOT_COMPUTABLE,
                route_duration_seconds=None,
                route_distance_meters=None,
                recommended_buffer_seconds=None,
                available_gap_seconds=_schedule_gap_seconds(from_experience, to_experience),
                buffer_status=BufferSufficiencyStatus.UNAVAILABLE,
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
        gap_seconds = _schedule_gap_seconds(from_experience, to_experience)

        status, route_duration, route_distance, recommended_buffer = _buffer_fields_from_result(
            result
        )
        buffer_status, message = _buffer_status_and_message(
            status, route_duration, gap_seconds, provider_name
        )

        return TravelTimeBuffer(
            from_experience_id=from_experience.experience_id,
            from_experience_name=from_experience.name,
            to_experience_id=to_experience.experience_id,
            to_experience_name=to_experience.name,
            provider=result.provider,
            status=status,
            route_duration_seconds=route_duration,
            route_distance_meters=route_distance,
            recommended_buffer_seconds=recommended_buffer,
            available_gap_seconds=gap_seconds,
            buffer_status=buffer_status,
            message=message,
            movement_data_provenance=movement_data_provenance_from_status(status),
        )


def _safe_get_route(gateway: ProviderGateway, request: RouteRequest) -> RouteResult:
    """Calls `gateway.get_route(request)`, but never lets an unexpected
    exception from that call escape (Step 166D hardening) -- mirrors
    `RouteFeasibilityService`'s own `_safe_get_route` exactly (duplicated
    rather than imported, keeping this module self-contained). Every real
    adapter already converts its own failure modes into an honest
    `RouteResult` without raising; this is a second line of defense for a
    genuinely unexpected bug, so one leg's failure can never crash the
    whole report.

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


_RESULT_STATUS_TO_BUFFER_STATUS = {
    ProviderStatus.NOT_CONNECTED: TravelTimeBufferStatus.NOT_CONNECTED,
    ProviderStatus.FAILED: TravelTimeBufferStatus.FAILED,
}


def _buffer_fields_from_result(
    result: RouteResult,
) -> tuple[TravelTimeBufferStatus, float | None, float | None, float | None]:
    """Maps a real `RouteResult` onto this leg's `TravelTimeBufferStatus`
    and its duration/distance/recommended-buffer fields -- only ever
    populated when `result.status == success` and a real duration exists;
    never a guessed or padded value otherwise.
    """
    if result.status == ProviderStatus.SUCCESS and result.duration_seconds is not None:
        return (
            TravelTimeBufferStatus.SUCCESS,
            result.duration_seconds,
            result.distance_meters,
            result.duration_seconds,
        )

    status = _RESULT_STATUS_TO_BUFFER_STATUS.get(result.status, TravelTimeBufferStatus.UNAVAILABLE)
    return status, None, None, None


def _buffer_status_and_message(
    status: TravelTimeBufferStatus,
    route_duration_seconds: float | None,
    gap_seconds: float | None,
    provider_name: str,
) -> tuple[BufferSufficiencyStatus, str]:
    if status != TravelTimeBufferStatus.SUCCESS or route_duration_seconds is None:
        message = {
            TravelTimeBufferStatus.NOT_CONNECTED: _NOT_CONNECTED_MESSAGE,
            TravelTimeBufferStatus.FAILED: _FAILED_MESSAGE,
            TravelTimeBufferStatus.UNAVAILABLE: _UNAVAILABLE_MESSAGE,
        }.get(status, _UNAVAILABLE_MESSAGE)
        return BufferSufficiencyStatus.UNAVAILABLE, message

    if gap_seconds is None:
        return BufferSufficiencyStatus.NOT_COMPUTABLE, _NO_TIMESTAMPS_MESSAGE_TEMPLATE.format(
            duration=route_duration_seconds, provider=provider_name
        )

    if route_duration_seconds > gap_seconds:
        return BufferSufficiencyStatus.INSUFFICIENT, _INSUFFICIENT_MESSAGE_TEMPLATE.format(
            duration=route_duration_seconds, gap=gap_seconds, provider=provider_name
        )

    return BufferSufficiencyStatus.SUFFICIENT, _SUFFICIENT_MESSAGE_TEMPLATE.format(
        duration=route_duration_seconds, gap=gap_seconds, provider=provider_name
    )


def _aggregate_buffer_report_status(
    buffer_statuses: list[TravelTimeBufferStatus],
) -> ProviderStatus:
    """Honest aggregate over every buffer's own `status` -- never
    optimistic. Mirrors `RouteFeasibilityService`'s `_aggregate_status`
    rules exactly: `success` only when every leg succeeded; `not_connected`
    only when every leg is `not_connected` (including the trivial case of
    zero legs, e.g. an empty or single-experience-per-day plan); `partial`
    when a mix of success and non-success exists; `failed` when at least
    one leg failed and none succeeded; `unavailable` otherwise (e.g. every
    leg is missing coordinates or otherwise not computable).
    """
    if not buffer_statuses:
        return ProviderStatus.NOT_CONNECTED
    if all(status == TravelTimeBufferStatus.SUCCESS for status in buffer_statuses):
        return ProviderStatus.SUCCESS
    if any(status == TravelTimeBufferStatus.SUCCESS for status in buffer_statuses):
        return ProviderStatus.PARTIAL
    if all(status == TravelTimeBufferStatus.NOT_CONNECTED for status in buffer_statuses):
        return ProviderStatus.NOT_CONNECTED
    if any(status == TravelTimeBufferStatus.FAILED for status in buffer_statuses):
        return ProviderStatus.FAILED
    return ProviderStatus.UNAVAILABLE


travel_time_buffer_service = TravelTimeBufferService()
