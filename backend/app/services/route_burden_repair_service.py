"""Bounded post-routing day repair (Section 203C.2B, final correction).

This runs AFTER the bounded per-leg mode adaptation: a leg that is too long
to walk has already been given a factual driving route where the provider
has one, and a day whose long walk became a reasonable vehicle transfer is
no longer a long-route day and is not touched here.

A day that is STILL a long-route day is not only reported. When unused
grounded inventory exists, each such day gets exactly ONE deterministic
repair attempt:

  1. the geographically isolated stop of the day is identified -- never a
     grounded user must-visit, a user-locked stop, a primary anchor or a
     grounded AI anchor;
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
from app.core.provider_usage import GenerationProviderContext
from app.models.candidate_quality import CandidateQualityTier
from app.models.common import GeoPoint, ProviderStatus
from app.models.planning_state import DailyPlan, ExperienceItem, PlanningState
from app.models.route_burden_repair import RouteBurdenRepairAttempt, RouteBurdenRepairReport
from app.models.routing import RouteLegFeasibility
from app.services import schedule_diversity as diversity
from app.services.pace_targets import pace_of
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
from app.services.route_burden import DayRouteBurden, burden_of_legs, day_route_burdens
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
                # A primary anchor or a grounded AI anchor is never traded
                # away for a shorter route; its transfer is adapted instead.
                or stop.quality_tier == CandidateQualityTier.PRIMARY_ANCHOR.value
                or stop.promoted_from_ai
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
        # A replacement never makes the day more concentrated in one coarse
        # attraction class than it already is (schedule-diversity contract).
        markets_requested = diversity.markets_requested_for(planning_state)
        rest_classes = [
            diversity.coarse_class(stop.normalized_category) for stop in stops if stop is not isolated
        ]
        current_excess = sum(
            diversity.excess_by_class(
                [*rest_classes, diversity.coarse_class(isolated.normalized_category)], markets_requested
            ).values()
        )

        def keeps_diversity(option: ReplacementOption) -> bool:
            classes = [*rest_classes, diversity.coarse_class(option.profile.primary)]
            return sum(diversity.excess_by_class(classes, markets_requested).values()) <= current_excess

        suitable = [
            option
            for option in options
            if option.tier_rank >= floor_rank
            and must_cover <= option.matched_interests
            and _km(option.coordinates, rest_centre) < isolated_km
            and keeps_diversity(option)
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

        # 4. ONE real routing request for the changed day (plus, as for every
        #    day, at most one driving request per over-long walking leg).
        if provider_context is not None and provider_context.route_requests_left <= 0:
            return outcome("route_budget_exhausted", **named), None
        legs = self._route(new_order, provider_context)
        if legs is None:
            return outcome("replacement_route_unavailable", route_burden_repair_attempted=True, **named), None

        after = burden_of_legs(day.day_number, len(new_order) - 1, legs, pace_of(planning_state))
        measured = {
            "route_burden_repair_attempted": True,
            "after_duration_seconds": after.total_duration_seconds,
            "after_distance_meters": after.total_distance_meters,
            **named,
        }
        min_ratio = get_settings().route_burden_repair_min_improvement_ratio
        improved = (
            after.total_duration_seconds <= burden.total_duration_seconds * (1.0 - min_ratio)
            and after.max_leg_duration_seconds <= burden.max_leg_duration_seconds
            and after.walking_duration_seconds <= burden.walking_duration_seconds
        )
        if not improved:
            return outcome("no_material_improvement", **measured), None

        self._apply(planning_state, day, new_order, legs)
        return outcome("accepted", accepted=True, **measured), best

    def _route(
        self,
        ordered: list[ExperienceItem],
        provider_context: GenerationProviderContext | None,
    ) -> list[RouteLegFeasibility] | None:
        """The final-mode legs of `ordered`, or None unless every leg came
        back with a real provider distance and duration."""
        try:
            legs = self.route_feasibility_service.route_day_legs(ordered, provider_context)
        except Exception:
            logger.warning("Routing the repaired day failed; the day is left unchanged.", exc_info=True)
            return None
        if len(legs) != len(ordered) - 1 or any(
            leg.status != ProviderStatus.SUCCESS or leg.duration_seconds is None or leg.distance_meters is None
            for leg in legs
        ):
            return None
        return legs

    def _apply(
        self,
        planning_state: PlanningState,
        day: DailyPlan,
        new_order: list[ExperienceItem],
        legs: list[RouteLegFeasibility],
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
        self.route_feasibility_service.replace_day_legs(planning_state.route_feasibility_report, previous, legs)
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
