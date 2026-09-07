from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.models.common import ProviderStatus
from app.models.planning_state import DailyPlan, ExperienceItem, PlanningState
from app.models.routing import (
    MovementDataProvenance,
    RouteAwareSequenceSuggestion,
    RouteAwareSequencingReport,
    RouteRequest,
    RouteResult,
    movement_data_provenance_from_status,
    route_aware_suggestion_provenance,
)
from app.providers.gateway import ProviderGateway, provider_gateway

logger = logging.getLogger(__name__)

# Deterministic provider infrastructure, not AI reasoning (Steps 166A/
# 166B, docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). Computes a route-aware day-sequencing
# suggestion using the same real routing provider RouteFeasibilityService
# already consumes through ProviderGateway (Step 165B, cache-backed when
# configured, Step 165C).
#
# `build_report` (Step 166A) is always a pure read: it never mutates
# `planning_state` or any `ExperiencePlan`/`DailyPlan` object it reads,
# and it never touches `RouteAwareSequencingReport.is_shadow_only`/
# `applied_to_itinerary` beyond their defaults (True/False).
#
# `apply_report` (Step 166B) is the only thing in this module that ever
# reorders a scheduled experience, and it is never called automatically --
# only `PlanningOrchestrator` calls it, and only when
# `Settings.route_aware_scheduling_enabled` is True (default False). Even
# when called, it only ever reorders a day whose suggestion is `success`,
# has a real positive improvement past the configured minimum, and whose
# `suggested_order` is a verified exact permutation of that day's current
# scheduled experience IDs -- see `apply_report`'s own docstring for the
# full safety contract. Neither function ever adds or removes an
# experience, changes any experience's own fields, or changes a date.
#
# No distance, duration, geometry, or improvement is ever fabricated: a
# duration/distance total is only ever populated when every route lookup
# it depends on returned `RouteResult.status == success`, and a
# straight-line (haversine) distance is never substituted for a real
# route duration.

_MISSING_COORDINATES_MESSAGE_TEMPLATE = (
    "Fewer than two scheduled experiences in this day have coordinates "
    "({with_coords} of {total}), so no route-aware sequencing suggestion "
    "could be computed."
)
_NOT_CONNECTED_MESSAGE = (
    "No routing provider is connected, so no route-aware sequencing "
    "suggestion could be computed for this day. The scheduled order is "
    "unchanged."
)
_FAILED_MESSAGE = (
    "The routing provider request(s) needed for this day's sequencing "
    "suggestion failed. The scheduled order is unchanged."
)
_PARTIAL_MESSAGE_TEMPLATE = (
    "Only some of the route lookups needed for this day's sequencing "
    "suggestion succeeded{missing_note}, so no complete suggested order or "
    "duration/distance total could be computed. The scheduled order is "
    "unchanged."
)
_SUCCESS_MESSAGE_TEMPLATE = (
    "A provider-backed nearest-next sequencing suggestion is available for "
    "this day (via {provider}). This is a suggestion only -- it is not "
    "applied to the itinerary, and it is not claimed to be the optimal "
    "order."
)
_APPLIED_MESSAGE_TEMPLATE = (
    "A provider-backed nearest-next sequencing suggestion was applied to "
    "this day's schedule (via {provider}). This does not claim the "
    "resulting order is optimal."
)
_UNEXPECTED_FAILURE_MESSAGE = (
    "The routing provider request(s) needed for this day's sequencing "
    "suggestion failed unexpectedly. The scheduled order is unchanged."
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _route_key(from_id: str, to_id: str) -> tuple[str, str]:
    return (from_id, to_id)


class RouteAwareSequencingService:
    """Builds a `RouteAwareSequencingReport` for the current
    `experience_plan` (Step 166A). Run by
    `PlanningOrchestrator.run_experience_plan_stage`, after
    `RouteFeasibilityService.build_report` and before
    `PlanValidatorService.run`.

    For each scheduled day with two or more experiences, this looks up
    real point-to-point routes (via `ProviderGateway.get_route`, never a
    provider adapter directly) between every pair of that day's
    coordinate-backed experiences, then:

    1. Sums the real route duration along the day's *current* scheduled
       order (coordinate-backed experiences only) to get an "original"
       total -- only when every one of those consecutive-pair lookups
       succeeded.
    2. Computes a candidate reordering using a conservative nearest-next
       walk: starting from the day's first coordinate-backed experience
       (kept first, mirroring `ExperiencePlannerService`'s anchor-first
       convention), each next step picks whichever remaining
       coordinate-backed experience has the shortest real route duration
       from the current position, using only real `RouteResult`s -- never
       a straight-line (haversine) estimate. If no successful route
       exists to any remaining candidate, the remaining experiences keep
       their original relative order instead of being guessed at.
    3. Sums the real route duration/distance along that candidate order --
       again only when every edge succeeded -- to report as this
       suggestion's `route_duration_seconds`/`route_distance_meters`, and
       compares it against the original total for `improvement_seconds`.

    Experiences missing coordinates are never dropped: they are appended,
    in their original relative order, to the end of `suggested_order`
    exactly as they already sit relative to each other in
    `original_order`. Their presence caps this day's `status` at
    `partial` at best, since the day was not fully route-aware-sequenced.

    `build_report` never mutates `planning_state`, never reorders or drops
    a scheduled experience, and only ever calls `ProviderGateway.get_route`
    -- never Groq/Anthropic/Kiwi/MCP, and never scrapes. With the default
    `routing_provider="not_connected"` configuration, every day (and the
    report as a whole) honestly reports `not_connected` without any
    network call.
    """

    def __init__(self, gateway: ProviderGateway | None = None) -> None:
        self.gateway = gateway or provider_gateway

    def build_report(self, planning_state: PlanningState) -> RouteAwareSequencingReport:
        provider_name = getattr(self.gateway.routing, "provider_name", "routing_provider")
        suggestions: list[RouteAwareSequenceSuggestion] = []

        experience_plan = planning_state.experience_plan
        if experience_plan is not None:
            for day_plan in experience_plan.daily_plans:
                if len(day_plan.experiences) < 2:
                    continue
                suggestions.append(self._build_day_suggestion(day_plan, provider_name))

        report_status = _aggregate_report_status([suggestion.status for suggestion in suggestions])

        return RouteAwareSequencingReport(
            status=report_status,
            suggestions=suggestions,
            generated_at=_utc_now(),
            is_shadow_only=True,
            applied_to_itinerary=False,
            movement_data_provenance=movement_data_provenance_from_status(report_status),
        )

    def _build_day_suggestion(
        self, day_plan: DailyPlan, provider_name: str
    ) -> RouteAwareSequenceSuggestion:
        experiences = day_plan.experiences
        original_order = [experience.experience_id for experience in experiences]

        coord_backed = [experience for experience in experiences if experience.coordinates is not None]
        missing_coord = [experience for experience in experiences if experience.coordinates is None]

        if len(coord_backed) < 2:
            return RouteAwareSequenceSuggestion(
                day_index=day_plan.day_number,
                status=ProviderStatus.UNAVAILABLE,
                original_order=original_order,
                suggested_order=list(original_order),
                route_duration_seconds=None,
                route_distance_meters=None,
                improvement_seconds=None,
                provider=provider_name,
                message=_MISSING_COORDINATES_MESSAGE_TEMPLATE.format(
                    with_coords=len(coord_backed), total=len(experiences)
                ),
                movement_data_provenance=MovementDataProvenance.NOT_COMPUTABLE,
            )

        route_lookup: dict[tuple[str, str], RouteResult] = {}
        for from_experience in coord_backed:
            for to_experience in coord_backed:
                if from_experience is to_experience:
                    continue
                key = _route_key(from_experience.experience_id, to_experience.experience_id)
                if key in route_lookup:
                    continue
                route_lookup[key] = self._get_route(from_experience, to_experience)

        original_duration, original_all_success = _sum_consecutive_duration(
            coord_backed, route_lookup
        )

        suggested_coord_order, suggested_duration, suggested_distance, suggested_all_success = (
            _nearest_next_order(coord_backed, route_lookup)
        )

        suggested_order = [experience.experience_id for experience in suggested_coord_order] + [
            experience.experience_id for experience in missing_coord
        ]

        all_lookups_success = original_all_success and suggested_all_success
        any_lookup_success = any(
            result.status == ProviderStatus.SUCCESS for result in route_lookup.values()
        )
        all_not_connected = all(
            result.status == ProviderStatus.NOT_CONNECTED for result in route_lookup.values()
        )
        any_failed = any(result.status == ProviderStatus.FAILED for result in route_lookup.values())

        if all_not_connected:
            status = ProviderStatus.NOT_CONNECTED
            message = _NOT_CONNECTED_MESSAGE
        elif all_lookups_success and not missing_coord:
            status = ProviderStatus.SUCCESS
            message = _SUCCESS_MESSAGE_TEMPLATE.format(provider=provider_name)
        elif any_lookup_success:
            status = ProviderStatus.PARTIAL
            missing_note = (
                f", and {len(missing_coord)} scheduled experience(s) in this day are missing "
                "coordinates"
                if missing_coord
                else ""
            )
            message = _PARTIAL_MESSAGE_TEMPLATE.format(missing_note=missing_note)
        elif any_failed:
            status = ProviderStatus.FAILED
            message = _FAILED_MESSAGE
        else:
            status = ProviderStatus.UNAVAILABLE
            message = _PARTIAL_MESSAGE_TEMPLATE.format(missing_note="")

        route_duration_seconds = suggested_duration if status == ProviderStatus.SUCCESS else None
        route_distance_meters = suggested_distance if status == ProviderStatus.SUCCESS else None
        improvement_seconds = (
            original_duration - suggested_duration
            if status == ProviderStatus.SUCCESS
            and original_duration is not None
            and suggested_duration is not None
            else None
        )

        return RouteAwareSequenceSuggestion(
            day_index=day_plan.day_number,
            status=status,
            original_order=original_order,
            suggested_order=suggested_order,
            route_duration_seconds=route_duration_seconds,
            route_distance_meters=route_distance_meters,
            improvement_seconds=improvement_seconds,
            provider=provider_name,
            message=message,
            movement_data_provenance=route_aware_suggestion_provenance(status, applied=False),
        )

    def apply_report(
        self,
        planning_state: PlanningState,
        report: RouteAwareSequencingReport,
        min_improvement_seconds: float,
    ) -> bool:
        """Config-gated application path (Step 166B). Only ever called by
        `PlanningOrchestrator`, and only when
        `Settings.route_aware_scheduling_enabled` is `True` -- never called
        by `build_report` itself, and never called automatically.

        For each day suggestion in `report`, this reorders that day's real
        `planning_state.experience_plan.daily_plans[*].experiences` to
        match `suggested_order`, but only when *every* one of these safety
        checks passes:

        - `suggestion.status == ProviderStatus.SUCCESS` (never a
          `partial`/`unavailable`/`not_connected`/`failed` suggestion).
        - `suggestion.route_duration_seconds`/`improvement_seconds` are
          both real, provider-backed numbers (not `None`).
        - `suggestion.improvement_seconds` is strictly greater than
          `min_improvement_seconds` -- a suggestion with zero or negative
          real improvement is never applied.
        - The day's *real, current* scheduled experience IDs exactly match
          `suggestion.original_order` -- guards against a stale suggestion
          being applied against a schedule that has since changed.
        - `suggestion.suggested_order` is a verified exact permutation of
          those same IDs (same multiset, same length) -- no experience is
          ever added, removed, or duplicated by applying it.

        When a day is applied, only that `DailyPlan.experiences` list's
        *order* changes -- every `ExperienceItem` object is reused as-is,
        so no field of any scheduled experience (name, coordinates, dates,
        etc.) is ever altered, and no experience is added or removed.
        `suggestion.applied` flips to `True` for that day.

        Returns `True` if at least one day was actually reordered, in
        which case `report.is_shadow_only`/`applied_to_itinerary` are
        updated to reflect that (`False`/`True` respectively). Returns
        `False` (and leaves `report` untouched) if nothing was safe to
        apply -- including the trivial case of no `experience_plan` at
        all.
        """
        experience_plan = planning_state.experience_plan
        if experience_plan is None:
            return False

        day_plans_by_number = {
            day_plan.day_number: day_plan for day_plan in experience_plan.daily_plans
        }

        applied_any = False
        for suggestion in report.suggestions:
            day_plan = day_plans_by_number.get(suggestion.day_index)
            if day_plan is None:
                continue
            try:
                if not _is_safe_to_apply(day_plan, suggestion, min_improvement_seconds):
                    continue
                _apply_day_order(day_plan, suggestion.suggested_order)
                suggestion.applied = True
                # The message set at build_report time says "not applied"
                # (accurate then) -- update it now so it never goes stale
                # or contradicts `applied=True` (Step 166D hardening).
                suggestion.message = _APPLIED_MESSAGE_TEMPLATE.format(
                    provider=suggestion.provider or "the configured routing provider"
                )
                # Step 166E: flip movement-data provenance from
                # `not_applied` to `provider_backed` now that this day's
                # provider-backed suggestion has actually been applied --
                # mirrors the message update above so neither field goes
                # stale relative to `applied=True`.
                suggestion.movement_data_provenance = MovementDataProvenance.PROVIDER_BACKED
                applied_any = True
            except Exception:
                # An unexpected error applying one day's suggestion must
                # never abort applying other, still-safe days, and must
                # never crash generation (Step 166D hardening). The
                # schedule for this specific day is left exactly as it
                # was -- never partially reordered.
                logger.warning(
                    "Failed to apply route-aware sequencing suggestion for day %s; "
                    "leaving that day's schedule unchanged.",
                    suggestion.day_index,
                    exc_info=True,
                )
                continue

        if applied_any:
            report.is_shadow_only = False
            report.applied_to_itinerary = True

        return applied_any

    def _get_route(self, from_experience: ExperienceItem, to_experience: ExperienceItem) -> RouteResult:
        from_point = from_experience.coordinates
        to_point = to_experience.coordinates
        request = RouteRequest(
            origin_lat=from_point.lat,
            origin_lon=from_point.lng,
            destination_lat=to_point.lat,
            destination_lon=to_point.lng,
        )
        return _safe_get_route(self.gateway, request)


def _sum_consecutive_duration(
    ordered_experiences: list[ExperienceItem],
    route_lookup: dict[tuple[str, str], RouteResult],
) -> tuple[float | None, bool]:
    """Sums real route duration along consecutive pairs of
    `ordered_experiences`, using only `route_lookup` results already
    fetched via the routing provider. Returns `(None, False)` unless every
    consecutive pair succeeded -- never a partial sum, never a
    straight-line estimate.
    """
    total = 0.0
    for from_experience, to_experience in zip(ordered_experiences, ordered_experiences[1:]):
        result = route_lookup.get(
            _route_key(from_experience.experience_id, to_experience.experience_id)
        )
        if result is None or result.status != ProviderStatus.SUCCESS or result.duration_seconds is None:
            return None, False
        total += result.duration_seconds
    return total, True


def _nearest_next_order(
    coord_backed: list[ExperienceItem],
    route_lookup: dict[tuple[str, str], RouteResult],
) -> tuple[list[ExperienceItem], float | None, float | None, bool]:
    """Conservative nearest-next-by-real-route-duration walk over
    `coord_backed`, starting from the first experience (kept first,
    mirroring `ExperiencePlannerService`'s anchor-first convention).

    At each step, the remaining candidate with the shortest *real*,
    successful route duration from the current position is chosen next.
    If no remaining candidate has a successful route from the current
    position, the rest of `coord_backed` is appended in its existing
    stable order instead of being guessed at -- never a straight-line
    (haversine) fallback.

    Returns `(ordered_experiences, total_duration_seconds, total_distance_meters, all_edges_success)`.
    `total_duration_seconds`/`total_distance_meters` are `None` unless
    every edge actually walked succeeded.
    """
    if not coord_backed:
        return [], None, None, True

    anchor = coord_backed[0]
    ordered = [anchor]
    remaining = list(coord_backed[1:])
    current = anchor

    total_duration = 0.0
    total_distance = 0.0
    all_success = True

    while remaining:
        best_index: int | None = None
        best_result: RouteResult | None = None
        for index, candidate in enumerate(remaining):
            result = route_lookup.get(_route_key(current.experience_id, candidate.experience_id))
            if result is None or result.status != ProviderStatus.SUCCESS or result.duration_seconds is None:
                continue
            if best_result is None or result.duration_seconds < best_result.duration_seconds:
                best_index = index
                best_result = result

        if best_index is None:
            # No successful route from the current position to any
            # remaining candidate -- keep the rest in stable order rather
            # than guessing, and mark this walk as incomplete.
            ordered.extend(remaining)
            all_success = False
            break

        next_experience = remaining.pop(best_index)
        ordered.append(next_experience)
        total_duration += best_result.duration_seconds
        if best_result.distance_meters is None:
            all_success = False
        else:
            total_distance += best_result.distance_meters
        current = next_experience

    if not all_success:
        return ordered, None, None, False
    return ordered, total_duration, total_distance, True


def _is_safe_to_apply(
    day_plan: DailyPlan,
    suggestion: RouteAwareSequenceSuggestion,
    min_improvement_seconds: float,
) -> bool:
    """Every check `RouteAwareSequencingService.apply_report` requires
    before touching a day's real scheduled order -- see that method's own
    docstring for the full rationale. Conservative by construction: any
    unmet condition returns `False` (never applied), and nothing here
    fabricates a duration, distance, or improvement to make a check pass.
    """
    if suggestion.status != ProviderStatus.SUCCESS:
        return False
    if suggestion.route_duration_seconds is None or suggestion.improvement_seconds is None:
        return False
    if suggestion.improvement_seconds <= min_improvement_seconds:
        return False

    current_ids = [experience.experience_id for experience in day_plan.experiences]
    if current_ids != suggestion.original_order:
        # The day's real schedule has moved on since this suggestion was
        # computed -- never apply a stale suggestion.
        return False
    if sorted(suggestion.suggested_order) != sorted(current_ids):
        # Not a verified exact permutation of the current schedule (an ID
        # is missing, an extra ID appeared, or an ID is duplicated).
        return False

    return True


def _apply_day_order(day_plan: DailyPlan, suggested_order: list[str]) -> None:
    """Reorders `day_plan.experiences` to match `suggested_order` exactly.
    Only ever called after `_is_safe_to_apply` has already verified
    `suggested_order` is an exact permutation of the day's current
    experience IDs -- every `ExperienceItem` object is reused as-is (never
    rebuilt, never mutated field-by-field except the two ordering-metadata
    fields below), so only schedule order changes, never any experience's
    other content, and no experience is added, removed, or duplicated.

    Step 172A: also re-stamps `stop_order` (this item's new 1-based
    position in the reordered list) and `route_aware_provenance` (set to
    `MovementDataProvenance.PROVIDER_BACKED`, mirroring the day's own
    suggestion at the moment it is applied -- see `apply_report`) on every
    experience in this day, so `stop_order` never goes stale relative to
    the real, just-changed order. `day_number` is left untouched -- a
    route-aware reorder only ever changes position within a day, never
    which day an experience belongs to.
    """
    experiences_by_id = {experience.experience_id: experience for experience in day_plan.experiences}
    day_plan.experiences = [experiences_by_id[experience_id] for experience_id in suggested_order]
    for stop_index, experience in enumerate(day_plan.experiences, start=1):
        experience.stop_order = stop_index
        experience.route_aware_provenance = MovementDataProvenance.PROVIDER_BACKED


def _safe_get_route(gateway: ProviderGateway, request: RouteRequest) -> RouteResult:
    """Calls `gateway.get_route(request)`, but never lets an unexpected
    exception from that call escape (Step 166D hardening) -- mirrors
    `RouteFeasibilityService`'s own `_safe_get_route` exactly (duplicated
    rather than imported, keeping this module self-contained). Every real
    adapter already converts its own failure modes into an honest
    `RouteResult` without raising; this is a second line of defense for a
    genuinely unexpected bug, so one leg's failure can never crash the
    whole day's suggestion or the whole report.

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


def _aggregate_report_status(day_statuses: list[ProviderStatus]) -> ProviderStatus:
    """Honest aggregate over every day suggestion's own `status` -- never
    optimistic. Mirrors `RouteFeasibilityService`'s
    `_aggregate_status` rules exactly: `success` only when every day
    succeeded; `not_connected` only when every day is `not_connected`
    (including the trivial case of zero days, e.g. no experience_plan or
    no day with 2+ scheduled experiences); `partial` when a mix of success
    and non-success exists; `failed` when at least one day failed and none
    succeeded; `unavailable` otherwise (e.g. every day is missing
    coordinates).
    """
    if not day_statuses:
        return ProviderStatus.NOT_CONNECTED
    if all(status == ProviderStatus.SUCCESS for status in day_statuses):
        return ProviderStatus.SUCCESS
    if any(status == ProviderStatus.SUCCESS for status in day_statuses):
        return ProviderStatus.PARTIAL
    if all(status == ProviderStatus.NOT_CONNECTED for status in day_statuses):
        return ProviderStatus.NOT_CONNECTED
    if any(status == ProviderStatus.FAILED for status in day_statuses):
        return ProviderStatus.FAILED
    return ProviderStatus.UNAVAILABLE


route_aware_sequencing_service = RouteAwareSequencingService()
