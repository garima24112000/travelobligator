"""Bounded routability repair (Section 3C.2).

A day is routed with ONE multi-waypoint request, so when the routing
provider rejects that request every leg of the day fails together -- and
which stop is responsible cannot be read from the failed legs alone. The
usual cause is a single provider-backed stop whose coordinate is not on the
provider's routing network (the centre of a large enclosed area, for
example): a well-formed request the provider answers with a 4xx.

This runs once, after the initial routing pass and before the route-burden
repair, and only when the plan's factual routing coverage is below the
release threshold. For each day with unrouted legs, at most ONE replacement:

  1. LOCALISE. Each failed leg of the day is asked for again on its own
     (one request per failed leg). Legs that route are kept -- that alone is
     a factual improvement -- and the stop all of whose legs still fail is
     the stop that cannot be routed. A stop with even one routed leg is
     proven routable and is never a suspect.
  2. REPLACE. The suspect gives way to the best unused provider-grounded
     candidate: never a must-visit or a user-locked stop; quality tier at
     most one below; the plan's interest coverage and meaningful-stop count
     preserved; the day no more concentrated in one class; near the rest of
     the day. A grounded anchor MAY be the stop replaced here -- and only
     here -- because a place the provider cannot route to outranks anchor
     preference.
  3. VERIFY. Only the legs next to the replacement are routed (one request).
     The replacement is kept only when every one of them comes back with a
     real provider route and the day does not become a long-travel day;
     otherwise nothing is changed.

Two bounded exceptions, each still at most ONE accepted change per day:

  * AMBIGUOUS END STOPS. A failed leg between two end stops that are both
    unproven (a two-stop day) does not say which of them is at fault. When
    the first replacement cannot be routed -- it was verified against the
    other unproven stop -- that other stop gets one attempt of its own: at
    most two verification requests for such a day, never more.
  * NO REPLACEMENT. When exactly one stop is the suspect and no candidate
    fits, the stop is removed instead -- only while the plan keeps at least R
    meaningful stops, the day stays non-empty and every requested interest
    the scheduled stops cover stays covered by a scheduled stop. An interior
    stop is removed only when the provider routes the leg that then joins
    its neighbours. Never a must-visit or a user-locked stop, and never one
    of two ambiguous end stops.

Nothing is estimated: no route is inferred, no coordinate is moved, and a
leg the provider did not route stays unrouted and keeps its review finding.
Requests are bounded by the generation's route-request allowance and the
provider credit cap. A transient failure (timeout, rate limit, server
error) is not evidence about any stop and never triggers a replacement.
"""

from __future__ import annotations

import logging
from typing import Callable

from app.core import generation_diagnostics
from app.core.provider_usage import GenerationProviderContext
from app.models.common import GeoPoint, ProviderStatus
from app.models.planning_state import DailyPlan, ExperienceItem, PlanningState
from app.models.route_burden_repair import RoutabilityRepairAttempt, RoutabilityRepairReport
from app.models.routing import RouteLegFeasibility
from app.providers.errors import REASON_MALFORMED_REQUEST
from app.services import schedule_diversity as diversity
from app.services.day_order_heuristics import centroid, path_length_km
from app.services.day_rationale import clear_day_rationale, deterministic_day_summary
from app.services.experience_planner_service import (
    ReplacementOption,
    build_replacement_experience,
    quality_tier_rank,
    recompute_food_suggestions,
    unused_replacement_options,
)
from app.services.grounded_anchors import grounded_anchor_place_ids
from app.services.interest_coverage import FOOD
from app.services.must_visit_matching import must_visit_place_ids
from app.services.pace_targets import pace_of, pace_targets, trip_days_of
from app.services.route_burden import burden_of_legs
from app.services.route_burden_repair_service import REPLACEABLE, RouteBurdenRepairService
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.usefulness_contract import is_meaningful_stop

logger = logging.getLogger(__name__)

# The release threshold for factual routing coverage (routed / required legs).
ROUTING_COVERAGE_RELEASE_THRESHOLD = 0.90
# A replacement may sit at most one quality tier below the stop it replaces.
_MAX_TIER_DROP = 1
# ... and must stay near the rest of its day: within `_NEAR_KM` of the day's
# other stops, or no more than `_DISTANCE_FACTOR` times as far from them as
# the stop it replaces (the diversity pass's own bounds).
_NEAR_KM = 3.0
_DISTANCE_FACTOR = 1.5
# Failures that say nothing about a stop: the request never got a real answer.
_TRANSIENT_REASONS = frozenset(
    {
        "timeout", "network", "server", "rate_limited", "auth", "malformed", "not_connected", "budget_exhausted",
        "no_generation_context",
    }
)
_ANCHOR = "grounded_anchor"
# Fixed reason code: the one proven suspect was removed (no replacement fitted).
_REMOVED = "suspect_removed"


def _routed(leg: RouteLegFeasibility | None) -> bool:
    return (
        leg is not None
        and leg.status == ProviderStatus.SUCCESS
        and leg.distance_meters is not None
        and leg.duration_seconds is not None
    )


def _km(a: GeoPoint | None, b: GeoPoint | None) -> float:
    return path_length_km([a, b])


def routing_coverage(planning_state: PlanningState) -> float | None:
    """Routed legs / required legs across the plan (None with no leg required)."""
    plan, report = planning_state.experience_plan, planning_state.route_feasibility_report
    if plan is None or report is None:
        return None
    legs = {(leg.from_experience_id, leg.to_experience_id): leg for leg in report.legs}
    required = routed = 0
    for day in plan.daily_plans:
        for a, b in zip(day.experiences, day.experiences[1:]):
            required += 1
            routed += _routed(legs.get((a.experience_id, b.experience_id)))
    return routed / required if required else None


class RoutabilityRepairService:
    def __init__(self, route_feasibility_service: RouteFeasibilityService | None = None) -> None:
        self.route_feasibility_service = route_feasibility_service or RouteFeasibilityService()

    def repair(
        self,
        planning_state: PlanningState,
        provider_context: GenerationProviderContext | None = None,
    ) -> RoutabilityRepairReport | None:
        """Runs the bounded repair and returns its report, or None when the
        plan's factual routing coverage already meets the release threshold.
        Mutates `planning_state` only for a leg that now routes or an
        accepted replacement."""
        before = routing_coverage(planning_state)
        if before is None or before >= ROUTING_COVERAGE_RELEASE_THRESHOLD:
            return None

        must_visit_ids = must_visit_place_ids(planning_state)
        locked_ids = {lock.locked_item_id for lock in planning_state.user_locks if lock.is_active}
        anchor_ids = grounded_anchor_place_ids(planning_state)
        options = unused_replacement_options(planning_state)

        attempts: list[RoutabilityRepairAttempt] = []
        replaced = False
        for day in planning_state.experience_plan.daily_plans:
            if not self._failed_pairs(planning_state, day):
                continue
            attempt, used = self._repair_day(
                planning_state, day, options, must_visit_ids, locked_ids, anchor_ids, provider_context
            )
            attempts.append(attempt)
            if used is not None:
                options = [option for option in options if option is not used]
                replaced = True
            elif attempt.reason == _REMOVED:
                replaced = True
        if replaced:
            # Food is judged against the stops the traveller is shown.
            recompute_food_suggestions(planning_state)
        return RoutabilityRepairReport(
            attempts=attempts, coverage_before=before, coverage_after=routing_coverage(planning_state)
        )

    # -- one day ------------------------------------------------------------------

    def _legs(self, planning_state: PlanningState) -> dict[tuple[str, str], RouteLegFeasibility]:
        report = planning_state.route_feasibility_report
        return {(leg.from_experience_id, leg.to_experience_id): leg for leg in report.legs}

    def _failed_pairs(self, planning_state: PlanningState, day: DailyPlan) -> list[int]:
        """Indexes `i` of the day's legs (stop i -> stop i+1) without a factual route."""
        legs = self._legs(planning_state)
        stops = day.experiences
        return [
            index
            for index, (a, b) in enumerate(zip(stops, stops[1:]))
            if not _routed(legs.get((a.experience_id, b.experience_id)))
        ]

    def _repair_day(
        self,
        planning_state: PlanningState,
        day: DailyPlan,
        options: list[ReplacementOption],
        must_visit_ids: set[str],
        locked_ids: set[str],
        anchor_ids: set[str],
        provider_context: GenerationProviderContext | None,
    ) -> tuple[RoutabilityRepairAttempt, ReplacementOption | None]:
        stops = day.experiences
        failed = self._failed_pairs(planning_state, day)
        fields: dict[str, object] = {"day_number": day.day_number, "failed_legs_before": len(failed)}

        def outcome(reason: str, **extra: object) -> RoutabilityRepairAttempt:
            return RoutabilityRepairAttempt(
                reason=reason,
                failed_legs_after=len(self._failed_pairs(planning_state, day)),
                **{**fields, **extra},
            )

        legs = self._legs(planning_state)
        reasons = {
            leg.failure_reason
            for index in failed
            if (leg := legs.get((stops[index].experience_id, stops[index + 1].experience_id))) is not None
        }
        statuses = {
            leg.status
            for index in failed
            if (leg := legs.get((stops[index].experience_id, stops[index + 1].experience_id))) is not None
        }
        # Only a request the provider actually answered is evidence about a stop.
        if reasons & _TRANSIENT_REASONS or statuses - {ProviderStatus.FAILED}:
            return outcome("transient_failure"), None
        if REASON_MALFORMED_REQUEST in reasons:
            # The request itself was wrong: replacing a place cannot fix that.
            return outcome("malformed_request"), None

        # 1. Localise: ask for each failed leg on its own, in order.
        if len(failed) > 1:
            relocalized = 0
            for index in failed:
                if provider_context is not None and provider_context.route_requests_left <= 0:
                    return outcome("route_budget_exhausted", relocalized_legs=relocalized), None
                fresh = self._route([stops[index], stops[index + 1]], provider_context)
                relocalized += 1
                if fresh is not None:
                    self._store_legs(planning_state, day, {index: fresh[0]})
            fields["relocalized_legs"] = relocalized
            failed = self._failed_pairs(planning_state, day)
            if not failed:
                return outcome("localized", accepted=True), None

        # 2. The suspect: a stop ALL of whose legs still fail. A stop with a
        #    routed leg is proven routable.
        failed_set = set(failed)
        suspects = [
            position
            for position in range(len(stops))
            if all(index in failed_set for index in (position - 1, position) if 0 <= index < len(stops) - 1)
            and len(stops) > 1
        ]
        if not suspects:
            return outcome("no_suspect_stop"), None
        protections = {
            position: RouteBurdenRepairService._protection(stops[position], must_visit_ids, locked_ids, anchor_ids)
            for position in suspects
        }
        # An interior stop with BOTH adjacent legs failed is the strongest
        # evidence: when there is one, its neighbours' failed legs are
        # explained by it and they are not suspects in their own right (so a
        # protected interior stop is never worked around by replacing a
        # neighbour). Otherwise an ordinary stop is tried before a grounded
        # anchor, then stop order.
        interior = [position for position in suspects if 0 < position < len(stops) - 1]
        ranked = sorted(
            interior or suspects,
            key=lambda position: (1 if protections[position] == _ANCHOR else 0, position),
        )
        replaceable = [position for position in ranked if protections[position] in (REPLACEABLE, _ANCHOR)]
        if not replaceable:
            first = ranked[0]
            return outcome(
                "suspect_protected", suspect_place=stops[first].name, suspect_protection=protections[first]
            ), None
        # Exactly one stop is the suspect: an end stop whose neighbour has a
        # routed leg, or the interior stop the rule above singles out.
        sole_suspect = len(ranked) == 1
        # A failed leg between two END stops that are both unproven suspects
        # (in practice a two-stop day) gives no evidence which of them is at
        # fault. Only then, when the first replacement's verification fails
        # -- it was routed to the other unproven stop -- that other stop gets
        # the one further attempt. Never for an interior suspect, and never
        # for a stop that is not next to the first one.
        attempts = replaceable[:1]
        if not interior and len(replaceable) > 1 and abs(replaceable[1] - replaceable[0]) == 1:
            attempts = replaceable[:2]

        unroutable: RoutabilityRepairAttempt | None = None
        for position in attempts:
            suspect = stops[position]
            # After a failed first verification the day is reported as that
            # attempt left it unless this one gets as far as its own request.
            suspect_fields = {
                "suspect_place": suspect.name,
                "suspect_protection": REPLACEABLE,
                "suspect_was_grounded_anchor": protections[position] == _ANCHOR,
            }

            # 3. The best unused candidate that would not make the plan worse.
            best = self._best_option(planning_state, day, suspect, options)
            replacement = build_replacement_experience(planning_state, best) if best is not None else None
            if replacement is None or (is_meaningful_stop(suspect) and not is_meaningful_stop(replacement)):
                if unroutable is not None:
                    return unroutable, None
                fields.update(suspect_fields)
                if sole_suspect:
                    return self._remove_suspect(planning_state, day, position, provider_context, fields, outcome), None
                return outcome("no_suitable_candidate"), None
            fields.update(suspect_fields)
            named = {"replaced_place": suspect.name, "replacement_place": replacement.name}

            # 4. Route ONLY the legs next to the replacement (one request).
            if provider_context is not None and provider_context.route_requests_left <= 0:
                return outcome("route_budget_exhausted", **named), None
            start, end = max(0, position - 1), min(len(stops) - 1, position + 1)
            affected = [replacement if index == position else stops[index] for index in range(start, end + 1)]
            fresh = self._route(affected, provider_context)
            fields["verification_attempts"] = int(fields.get("verification_attempts", 0)) + 1
            if fresh is None:
                unroutable = outcome("replacement_unroutable", **named)
                continue

            new_stops = [replacement if index == position else stop for index, stop in enumerate(stops)]
            new_legs = {start + offset: leg for offset, leg in enumerate(fresh)}
            day_legs = self._day_legs(planning_state, stops, new_stops, new_legs)
            burden = burden_of_legs(day.day_number, len(new_stops) - 1, day_legs, pace_of(planning_state))
            if burden.long_route:
                # A routable day that is an unreasonable amount of travel is not an improvement.
                return outcome("replacement_route_burden", **named), None

            self._apply(planning_state, day, new_stops, day_legs)
            return outcome("accepted", accepted=True, **named), best
        return unroutable, None

    # -- removal ----------------------------------------------------------------------

    def _remove_suspect(
        self,
        planning_state: PlanningState,
        day: DailyPlan,
        position: int,
        provider_context: GenerationProviderContext | None,
        fields: dict[str, object],
        outcome: Callable[..., RoutabilityRepairAttempt],
    ) -> RoutabilityRepairAttempt:
        """The day's ONE proven suspect has no suitable replacement: it is
        taken out of its day, but only when the plan stays useful without it
        -- at least R meaningful stops, no empty day, and every requested
        interest the scheduled stops cover still covered by a scheduled stop
        (food included: a nearby-food suggestion is not counted on here).
        The caller has already excluded a must-visit and a user-locked stop.
        Removing an end stop needs no request; removing an interior stop is
        kept only when the provider routes the leg that now joins its
        neighbours and the day does not become a long-travel day. Otherwise
        nothing is changed and the day keeps its failure signal."""
        stops = day.experiences
        suspect = stops[position]
        plan = planning_state.experience_plan
        others = [stop for other_day in plan.daily_plans for stop in other_day.experiences if stop is not suspect]
        targets = pace_targets(trip_days_of(planning_state), pace_of(planning_state))
        still_covered = {interest for stop in others for interest in stop.matched_interests}
        if (
            len(stops) < 2
            or sum(1 for stop in others if is_meaningful_stop(stop)) < targets.minimum_useful
            or set(suspect.matched_interests) - still_covered
        ):
            return outcome("no_suitable_candidate")

        new_stops = [stop for stop in stops if stop is not suspect]
        bridge: RouteLegFeasibility | None = None
        if 0 < position < len(stops) - 1:
            if provider_context is not None and provider_context.route_requests_left <= 0:
                return outcome("route_budget_exhausted")
            fresh = self._route([stops[position - 1], stops[position + 1]], provider_context)
            fields["verification_attempts"] = int(fields.get("verification_attempts", 0)) + 1
            if fresh is None:
                return outcome("no_suitable_candidate")
            bridge = fresh[0]

        # The day's legs for the stops that remain: the bridging leg where
        # one was needed, otherwise the leg already stored for that pair.
        stored = self._legs(planning_state)
        day_legs: list[RouteLegFeasibility] = []
        for index, (a, b) in enumerate(zip(new_stops, new_stops[1:])):
            leg = bridge if bridge is not None and index == position - 1 else stored.get((a.experience_id, b.experience_id))
            if leg is not None:
                day_legs.append(leg)
        if bridge is not None and burden_of_legs(
            day.day_number, len(new_stops) - 1, day_legs, pace_of(planning_state)
        ).long_route:
            return outcome("no_suitable_candidate")

        self._apply(planning_state, day, new_stops, day_legs)
        return outcome(_REMOVED, accepted=True, replaced_place=suspect.name)

    # -- candidates -----------------------------------------------------------------

    def _best_option(
        self,
        planning_state: PlanningState,
        day: DailyPlan,
        suspect: ExperienceItem,
        options: list[ReplacementOption],
    ) -> ReplacementOption | None:
        stops = day.experiences
        plan = planning_state.experience_plan
        covered_without = {
            interest
            for other_day in plan.daily_plans
            for stop in other_day.experiences
            if stop is not suspect
            for interest in stop.matched_interests
        }
        must_cover = set(suspect.matched_interests) - covered_without
        if FOOD in must_cover and RouteBurdenRepairService._food_evidence_on_another_day(planning_state, day):
            must_cover.discard(FOOD)
        floor_rank = quality_tier_rank(suspect.quality_tier) - _MAX_TIER_DROP

        markets_requested = diversity.markets_requested_for(planning_state)
        justified = diversity.justified_classes_for(planning_state)
        rest = [stop for stop in stops if stop is not suspect]
        rest_classes = [diversity.coarse_class(stop.normalized_category) for stop in rest]
        suspect_class = diversity.coarse_class(suspect.normalized_category)

        def excess(classes: list[str]) -> int:
            return sum(diversity.relievable_excess(classes, markets_requested, justified).values())

        current_excess = excess([*rest_classes, suspect_class])
        centre = centroid([stop.coordinates for stop in rest])
        reach_km = max(_NEAR_KM, _DISTANCE_FACTOR * _km(suspect.coordinates, centre)) if centre is not None else None

        suitable = [
            option
            for option in options
            if option.tier_rank >= floor_rank
            and must_cover <= option.matched_interests
            and excess([*rest_classes, diversity.coarse_class(option.profile.primary)]) <= current_excess
            and (reach_km is None or _km(option.coordinates, centre) <= reach_km)
        ]
        if not suitable:
            return None
        # Same class first, then quality, then the nearer place; the options'
        # own (pool) order breaks any remaining tie, so the choice is stable.
        return min(
            suitable,
            key=lambda option: (
                0 if diversity.coarse_class(option.profile.primary) == suspect_class else 1,
                -option.tier_rank,
                round(_km(option.coordinates, centre), 3) if centre is not None else 0.0,
                -option.profile.score,
            ),
        )

    # -- routing and state -------------------------------------------------------------

    def _route(
        self,
        ordered: list[ExperienceItem],
        provider_context: GenerationProviderContext | None,
    ) -> list[RouteLegFeasibility] | None:
        """The legs of `ordered`, or None unless EVERY leg came back with a
        real provider distance and duration."""
        try:
            legs = self.route_feasibility_service.route_day_legs(ordered, provider_context)
        except Exception:
            logger.warning("Routing during the routability repair failed; nothing is changed.", exc_info=True)
            return None
        if len(legs) != len(ordered) - 1 or not all(_routed(leg) for leg in legs):
            return None
        return legs

    def _day_legs(
        self,
        planning_state: PlanningState,
        previous: list[ExperienceItem],
        current: list[ExperienceItem],
        fresh: dict[int, RouteLegFeasibility],
    ) -> list[RouteLegFeasibility]:
        """The legs of the day as `current`: a freshly routed leg where one
        was obtained, otherwise the leg already stored for that pair (only
        when both of its stops are unchanged)."""
        stored = self._legs(planning_state)
        day_legs: list[RouteLegFeasibility] = []
        for index, (a, b) in enumerate(zip(current, current[1:])):
            if index in fresh:
                day_legs.append(fresh[index])
            elif a is previous[index] and b is previous[index + 1]:
                existing = stored.get((a.experience_id, b.experience_id))
                if existing is not None:
                    day_legs.append(existing)
        return day_legs

    def _store_legs(self, planning_state: PlanningState, day: DailyPlan, fresh: dict[int, RouteLegFeasibility]) -> None:
        stops = list(day.experiences)
        self.route_feasibility_service.replace_day_legs(
            planning_state.route_feasibility_report, stops, self._day_legs(planning_state, stops, stops, fresh)
        )

    def _apply(
        self,
        planning_state: PlanningState,
        day: DailyPlan,
        new_stops: list[ExperienceItem],
        day_legs: list[RouteLegFeasibility],
    ) -> None:
        previous = list(day.experiences)
        for stop_index, stop in enumerate(new_stops, start=1):
            stop.day_number = day.day_number
            stop.stop_order = stop_index
        day.experiences = new_stops
        # The day changed: its summary is rebuilt and a rationale written for
        # the superseded day is dropped.
        day.goal = deterministic_day_summary([stop.name for stop in new_stops])
        clear_day_rationale(day)
        self.route_feasibility_service.replace_day_legs(planning_state.route_feasibility_report, previous, day_legs)
        sequencing = planning_state.route_aware_sequencing_report
        if sequencing is not None:
            # A reorder suggestion computed for the superseded day is stale.
            sequencing.suggestions = [
                suggestion for suggestion in sequencing.suggestions if suggestion.day_index != day.day_number
            ]


def apply_routability_repair_safely(
    planning_state: PlanningState,
    route_feasibility_service: RouteFeasibilityService,
    provider_context: GenerationProviderContext | None = None,
) -> None:
    """Runs the bounded routability repair and stores its report. Never lets
    a repair problem fail generation: on an unexpected error the report is
    cleared and the plan keeps its movement-data findings."""
    planning_state.routability_repair_report = None
    if planning_state.experience_plan is None or planning_state.route_feasibility_report is None:
        return
    # Diagnostic only (a no-op unless the evaluation tooling is recording).
    generation_diagnostics.route_checkpoint(
        generation_diagnostics.ROUTE_STAGE_ORDER_FINAL, planning_state, provider_context
    )
    try:
        planning_state.routability_repair_report = RoutabilityRepairService(route_feasibility_service).repair(
            planning_state, provider_context
        )
    except Exception:
        logger.warning("Routability repair failed unexpectedly; leaving the plan unchanged.", exc_info=True)
    finally:
        # Diagnostic only (a no-op unless the evaluation tooling is recording).
        generation_diagnostics.schedule_from_state("routability_repair", planning_state)
        generation_diagnostics.allowances("routability_repair", provider_context)
        generation_diagnostics.route_checkpoint(
            generation_diagnostics.ROUTE_STAGE_ROUTABILITY_REPAIR, planning_state, provider_context
        )
