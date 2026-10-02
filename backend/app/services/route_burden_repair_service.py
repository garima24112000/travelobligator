"""Bounded post-routing day repair (Section 203C.2B, final correction).

A long-route day is not only reported. When unused grounded inventory
exists, each such day gets exactly ONE deterministic repair attempt:

  1. the geographically isolated stop of the day is identified -- never a
     grounded user must-visit and never a user-locked stop;
  2. the unused, schedulable candidates are filtered: quality tier not
     materially worse, the plan's interest coverage preserved, not a
     low-value/private place, and closer to the rest of the day;
  3. they are ranked by straight-line distance and ONLY the best one is
     evaluated, with ONE real routing request for the changed day;
  4. the change is kept only when the provider's own walking total falls
     materially and no leg gets longer.

No routing matrix, no permutation search, no second candidate, no extra
model call. A day that cannot be improved stays exactly as it was and
keeps its long-travel warning.
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.provider_usage import GenerationProviderContext, context_kwargs
from app.models.common import GeoPoint, ProviderStatus
from app.models.planning_state import DailyPlan, ExperienceItem, PlanningState
from app.models.route_burden_repair import RouteBurdenRepairAttempt, RouteBurdenRepairReport
from app.models.routing import RouteResult
from app.services.day_order_heuristics import centroid, nearest_next_order, path_length_km
from app.services.day_rationale import RATIONALE_WARNING_PREFIX, deterministic_day_summary
from app.services.experience_planner_service import (
    ReplacementOption,
    build_replacement_experience,
    quality_tier_rank,
    recompute_food_suggestions,
    unused_replacement_options,
)
from app.services.must_visit_matching import must_visit_place_ids
from app.services.route_burden import DayRouteBurden, day_route_burdens
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.usefulness_contract import is_meaningful_stop

logger = logging.getLogger(__name__)

# A replacement may sit at most one quality tier below the stop it replaces.
_MAX_TIER_DROP = 1


def _km(a: GeoPoint | None, b: GeoPoint | None) -> float:
    return path_length_km([a, b])


class RouteBurdenRepairService:
    def __init__(self, route_feasibility_service: RouteFeasibilityService | None = None) -> None:
        self.route_feasibility_service = route_feasibility_service or RouteFeasibilityService()
        self.gateway = self.route_feasibility_service.gateway

    def repair(
        self,
        planning_state: PlanningState,
        provider_context: GenerationProviderContext | None = None,
    ) -> RouteBurdenRepairReport | None:
        """Runs the single attempt for every long-route day and returns the
        report (None when no day is a long-route day). Mutates
        `planning_state` only for an accepted replacement."""
        plan = planning_state.experience_plan
        if plan is None or planning_state.route_feasibility_report is None:
            return None
        long_days = [burden for burden in day_route_burdens(planning_state) if burden.long_route]
        if not long_days:
            return None

        protected_place_ids = must_visit_place_ids(planning_state)
        locked_ids = {lock.locked_item_id for lock in planning_state.user_locks if lock.is_active}
        options = unused_replacement_options(planning_state)
        days = {day.day_number: day for day in plan.daily_plans}

        attempts: list[RouteBurdenRepairAttempt] = []
        changed = False
        for burden in long_days:
            attempt, used = self._repair_day(
                planning_state, days[burden.day_number], burden, options,
                protected_place_ids, locked_ids, provider_context,
            )
            attempts.append(attempt)
            if used is not None:
                options = [option for option in options if option is not used]
                changed = True

        if changed:
            # Food is judged against the stops the traveller is shown.
            recompute_food_suggestions(planning_state)
        return RouteBurdenRepairReport(attempts=attempts)

    def _repair_day(
        self,
        planning_state: PlanningState,
        day: DailyPlan,
        burden: DayRouteBurden,
        options: list[ReplacementOption],
        protected_place_ids: set[str],
        locked_ids: set[str],
        provider_context: GenerationProviderContext | None,
    ) -> tuple[RouteBurdenRepairAttempt, ReplacementOption | None]:
        def outcome(reason: str, **fields: object) -> RouteBurdenRepairAttempt:
            return RouteBurdenRepairAttempt(
                day_number=day.day_number,
                reason=reason,
                before_duration_seconds=burden.total_duration_seconds,
                before_distance_meters=burden.total_distance_meters,
                **fields,
            )

        stops = day.experiences
        if len(stops) < 2 or burden.routed_legs != burden.required_legs:
            return outcome("no_replaceable_stop"), None

        # 1. The isolated stop: farthest from the centre of the day's other
        #    stops, among the stops that may be replaced at all.
        isolated: ExperienceItem | None = None
        isolated_km = 0.0
        rest_centre: GeoPoint | None = None
        for stop in stops:
            if (
                stop.coordinates is None
                or stop.experience_id in locked_ids
                or (stop.provider_place_id and stop.provider_place_id in protected_place_ids)
            ):
                continue
            centre = centroid([other.coordinates for other in stops if other is not stop])
            distance = _km(stop.coordinates, centre)
            if centre is not None and distance > isolated_km:
                isolated, isolated_km, rest_centre = stop, distance, centre
        if isolated is None or rest_centre is None:
            return outcome("no_replaceable_stop"), None

        # 2. Unused candidates that would not make the plan worse.
        plan = planning_state.experience_plan
        covered_without = {
            interest
            for other_day in plan.daily_plans
            for stop in other_day.experiences
            if stop is not isolated
            for interest in stop.matched_interests
        }
        must_cover = set(isolated.matched_interests) - covered_without
        floor_rank = quality_tier_rank(isolated.quality_tier) - _MAX_TIER_DROP
        suitable = [
            option
            for option in options
            if option.tier_rank >= floor_rank
            and must_cover <= option.matched_interests
            and _km(option.coordinates, rest_centre) < isolated_km
        ]
        if not suitable:
            return outcome("no_suitable_candidate", replaced_place=isolated.name), None

        # 3. Straight-line ranking; only the best candidate is evaluated.
        best = min(
            suitable,
            key=lambda option: (_km(option.coordinates, rest_centre), -option.tier_rank, -option.profile.score),
        )
        replacement = build_replacement_experience(planning_state, best)
        named = {"replaced_place": isolated.name, "replacement_place": replacement.name}
        if is_meaningful_stop(isolated) and not is_meaningful_stop(replacement):
            return outcome("no_suitable_candidate", replaced_place=isolated.name), None

        substituted = [replacement if stop is isolated else stop for stop in stops]
        reordered = nearest_next_order(substituted, [stop.coordinates for stop in substituted])
        new_order = min(
            (substituted, reordered),
            key=lambda order: path_length_km([stop.coordinates for stop in order]),
        )

        # 4. ONE real routing request for the changed day.
        if provider_context is not None and provider_context.route_requests_left <= 0:
            return outcome("route_budget_exhausted", **named), None
        results = self._route(new_order, provider_context)
        if results is None:
            return outcome("replacement_route_unavailable", route_burden_repair_attempted=True, **named), None

        after_duration = sum(result.duration_seconds for result in results)
        after_distance = sum(result.distance_meters for result in results)
        measured = {
            "route_burden_repair_attempted": True,
            "after_duration_seconds": after_duration,
            "after_distance_meters": after_distance,
            **named,
        }
        min_ratio = get_settings().route_burden_repair_min_improvement_ratio
        improved = (
            after_duration <= burden.total_duration_seconds * (1.0 - min_ratio)
            and max(result.duration_seconds for result in results) <= burden.max_leg_duration_seconds
        )
        if not improved:
            return outcome("no_material_improvement", **measured), None

        self._apply(planning_state, day, new_order, results)
        return outcome("accepted", accepted=True, **measured), best

    def _route(
        self,
        ordered: list[ExperienceItem],
        provider_context: GenerationProviderContext | None,
    ) -> list[RouteResult] | None:
        """Provider legs for `ordered` from one request, or None unless every
        leg came back with a real distance and duration."""
        route_sequence = getattr(self.gateway, "get_route_sequence", None)
        if not callable(route_sequence):
            return None
        points = [(stop.coordinates.lat, stop.coordinates.lng) for stop in ordered]
        try:
            results = route_sequence(points, **context_kwargs(provider_context))
        except Exception:
            logger.warning("Routing the repaired day failed; the day is left unchanged.", exc_info=True)
            return None
        if len(results) != len(ordered) - 1 or any(
            result.status != ProviderStatus.SUCCESS
            or result.duration_seconds is None
            or result.distance_meters is None
            for result in results
        ):
            return None
        return results

    def _apply(
        self,
        planning_state: PlanningState,
        day: DailyPlan,
        new_order: list[ExperienceItem],
        results: list[RouteResult],
    ) -> None:
        previous = list(day.experiences)
        for stop_index, stop in enumerate(new_order, start=1):
            stop.day_number = day.day_number
            stop.stop_order = stop_index
        day.experiences = new_order
        # The day changed: its summary is rebuilt and a rationale written for
        # the superseded day is dropped.
        day.goal = deterministic_day_summary([stop.name for stop in new_order])
        day.warnings = [w for w in day.warnings if not w.startswith(RATIONALE_WARNING_PREFIX)]
        self.route_feasibility_service.replace_day_legs(
            planning_state.route_feasibility_report, previous, new_order, results
        )
        sequencing = planning_state.route_aware_sequencing_report
        if sequencing is not None:
            # A reorder suggestion computed for the superseded day is stale.
            sequencing.suggestions = [
                suggestion for suggestion in sequencing.suggestions if suggestion.day_index != day.day_number
            ]


def apply_route_burden_repair_safely(
    planning_state: PlanningState,
    route_feasibility_service: RouteFeasibilityService,
    provider_context: GenerationProviderContext | None = None,
) -> None:
    """Runs the bounded repair after routing/sequencing and stores its
    report. Never lets a repair problem fail generation: on an unexpected
    error the report is cleared and the plan keeps its long-travel warning."""
    planning_state.route_burden_repair_report = None
    if not get_settings().route_burden_repair_enabled:
        return
    try:
        planning_state.route_burden_repair_report = RouteBurdenRepairService(
            route_feasibility_service
        ).repair(planning_state, provider_context)
    except Exception:
        logger.warning("Route-burden repair failed unexpectedly; leaving the plan unchanged.", exc_info=True)
