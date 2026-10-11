"""Route-aware recomposition (Phase Q4).

Q3 composes days from a straight-line proxy. A day it calls compact can
still be impractical: the routing provider's own legs may show a walk far
beyond the limits (a water barrier, a missing crossing). This stage makes
that evidence actionable. It runs once, after routing, mode adaptation and
the routability repair, and replaces the single-attempt route-burden repair
(`ROUTE_RECOMPOSITION_ENABLED=false` restores that one exactly).

Evidence is the provider's, leg by leg
--------------------------------------
A day is SEVERE when its VERIFIED legs alone exceed a route-burden limit
(`route_burden.assess_legs`). A leg nobody could route is not evidence of
anything: it neither hides a verified long leg of the same day nor proves
the day feasible. So a partially routed day with a verified offending leg is
repaired like any other, its unverified legs are left exactly as they are,
and such a day is never reported as fully verified.

One composition engine
----------------------
The moves and the hard rules are Q3's (`day_composition.focused_moves`):
replace a discretionary stop of an offending leg, move it to another day, or
swap it with another day's stop -- never a grounded must-visit replaced,
never a requested interest uncovered, no duplicate / sub-feature conflict, no
extra marketplace excess, no new dispersed day, at most one quality tier
lost, pace caps kept. This stage adds reorders of the day itself, the user
locks (a locked stop is neither moved nor replaced) and the verdict: what a
move is worth is decided ONLY by provider routes. A grounded semantic anchor
is a preference here as in Q3 and may give way under the same guard.

Bounded, deterministic, never partly verified
---------------------------------------------
Per severe day, worst first:

  1. PROPOSE every admissible move that changes an offending leg.
  2. SHORTLIST at most `SHORTLIST_SIZE` (3). Moves whose legs are all
     already known are first; then a straight-line estimate ORDERS the rest
     (it never accepts one, and a move is dropped only when the estimate is
     materially worse). When more than one kind of move is available, two
     kinds are represented before the remaining place is filled by rank.
  3. VERIFY in shortlist order. Only the legs a move changes are routed;
     legs with existing evidence are reused. Before anything is dispatched
     the whole move must fit what is left: this stage's own request cap, the
     generation's shared route-request allowance and the provider-credit cap.
     A move that does not fit is skipped whole -- never verified in part.
  4. ACCEPT the best verified move. A move that RESOLVES the day (its
     verified legs are no longer severe) is preferred, a fully verified one
     first; otherwise a still-severe day is changed only for a material
     provider-measured reduction with no leg, walking total or vehicle
     transfer made worse. No other day may become severe, and a move that
     meets a definitive routing failure is rejected.

Routing requests here are ordinary ones: each also takes one of the
generation's `route_requests_left` and its credits, and a driving route for
an over-long walking leg comes from the unchanged alternate-mode cap.
`recomposition_requests_left` (at most 8) only LIMITS this stage further.
A cached or memoised leg costs nothing and is not counted. A transient
provider failure is no evidence about any stop: the move is left unverified,
and two in a row end the stage.

Nothing is estimated and nothing is invented: no route, duration, ferry,
timetable or access arrangement. A day that stays severe keeps its
long-travel finding; the validator judges the final days and final legs.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.core.config import get_settings
from app.core.provider_usage import GenerationProviderContext
from app.models.common import GeoPoint
from app.models.planning_state import DailyPlan, ExperienceItem, PlanningState
from app.models.route_burden_repair import RouteBurdenRepairAttempt, RouteBurdenRepairReport
from app.models.routing import RouteLegFeasibility
from app.services import candidate_universe as universe
from app.services import candidate_usefulness as usefulness
from app.services import day_composition as composition
from app.services import schedule_diversity as diversity
from app.services.day_order_heuristics import AS_WELL_PLACED_KM, path_length_km
from app.services.day_rationale import clear_day_rationale, deterministic_day_summary
from app.services.entity_collisions import SUSPECT_COLLISION_KEY
from app.services.experience_planner_service import (
    ReplacementOption,
    _candidate_profile,
    _composition_key,
    build_replacement_experience,
    quality_tier_rank,
    recompute_food_suggestions,
    unused_replacement_options,
)
from app.services.interest_coverage import FOOD
from app.services.must_visit_matching import must_visit_place_ids
from app.services.pace_targets import PACE_TARGET_PER_DAY, pace_of
from app.services.route_burden import (
    DayRouteAssessment,
    assess_days,
    assess_legs,
    is_definitive_failure,
    is_verified_leg,
)
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.usefulness_contract import is_meaningful_stop

logger = logging.getLogger(__name__)

SHORTLIST_SIZE = 3
# Reorders of one day proposed at most (the shortest by straight line).
_MAX_REORDERS = 2
# A day longer than this is only reordered, never permuted exhaustively.
_MAX_PERMUTED_STOPS = 5
# A proposal whose straight-line estimate is this much LONGER than the
# current arrangement is not worth a routing request (the Q3 materiality
# tolerance). The estimate never accepts anything.
_PROXY_WORSE_M = round(AS_WELL_PLACED_KM * 1000)
# Transient provider failures in a row that end the stage.
_MAX_CONSECUTIVE_TRANSIENT = 2

REORDER, RELOCATE, SWAP, REPLACE = "reorder", "relocate", "swap", "replace"
_CROSS_DAY = "cross_day"
_KIND_GROUP = {REORDER: REORDER, RELOCATE: _CROSS_DAY, SWAP: _CROSS_DAY, REPLACE: REPLACE}

# The report keeps the fixed reason codes of the single-attempt repair, so
# nothing that reads it changes meaning. Whether an ACCEPTED change resolved
# the day is in `severity_after` (a day that is still severe keeps its
# long-travel finding). One code is new: only must-visits or locked stops are
# on the offending legs.
ACCEPTED = "accepted"
NO_ADMISSIBLE_ALTERNATIVE = "no_suitable_candidate"
PROTECTED_STOPS_ONLY = "protected_stops_only"
ALTERNATIVES_NOT_BETTER = "no_material_improvement"
INSUFFICIENT_BUDGET = "route_budget_exhausted"
PROVIDER_UNAVAILABLE = "replacement_route_unavailable"

_REPLACEABLE = "replaceable"


@dataclass(frozen=True)
class _Node:
    """A place in a proposed day: a scheduled stop, or an unused candidate."""

    key: str
    item: ExperienceItem | None = None
    option: ReplacementOption | None = None

    @property
    def coordinates(self) -> GeoPoint | None:
        return self.item.coordinates if self.item is not None else self.option.coordinates

    @property
    def name(self) -> str:
        return self.item.name if self.item is not None else self.option.name


@dataclass
class _Candidate:
    kind: str
    # day number -> that day's proposed order (only the days the move changes)
    orders: dict[int, list[_Node]]
    quality: tuple[Any, ...] = ()
    proxy_delta_m: int = 0
    unknown_legs: int = 0
    outgoing: _Node | None = None
    incoming: _Node | None = None
    moved: _Node | None = None

    @property
    def ident(self) -> tuple[Any, ...]:
        return (self.kind, tuple((day, tuple(node.key for node in nodes)) for day, nodes in sorted(self.orders.items())))


@dataclass
class _Verified:
    candidate: _Candidate
    # day number -> its legs in the proposed order (None: no evidence at all)
    legs: dict[int, list[RouteLegFeasibility | None]]
    after: dict[int, DayRouteAssessment]
    outcome: int  # 0 resolved and fully verified, 1 resolved, 2 improved, still severe
    items: dict[int, list[ExperienceItem]] = field(default_factory=dict)


class RouteRecompositionService:
    def __init__(self, route_feasibility_service: RouteFeasibilityService | None = None) -> None:
        self.route_feasibility_service = route_feasibility_service or RouteFeasibilityService()
        self.gateway = self.route_feasibility_service.gateway

    # -- entry point ----------------------------------------------------------------------

    def recompose(
        self,
        planning_state: PlanningState,
        provider_context: GenerationProviderContext | None = None,
    ) -> RouteBurdenRepairReport | None:
        """Runs the stage and returns its report (None when no day is severe
        on verified legs -- then no request is made at all). Mutates
        `planning_state` only for an accepted move."""
        plan = planning_state.experience_plan
        if plan is None or planning_state.route_feasibility_report is None:
            return None
        severe = [assessment for assessment in assess_days(planning_state) if assessment.verified_severe]
        if not severe:
            return None

        settings = get_settings()
        self._state = planning_state
        self._context = provider_context
        self._cap = settings.route_recomposition_max_requests_per_generation
        self._local_left = self._cap  # used only when there is no generation context
        self._requests_used = 0
        self._consecutive_transient = 0
        self._pace = pace_of(planning_state)
        self._built: dict[str, ExperienceItem] = {}
        self._fresh: dict[tuple[str, str], RouteLegFeasibility] = {}
        self._prepare_composition()

        attempts: list[RouteBurdenRepairAttempt] = []
        changed = False
        for target in sorted(severe, key=lambda item: (-item.burden.total_duration_seconds, item.day_number)):
            day = next(day for day in plan.daily_plans if day.day_number == target.day_number)
            # An earlier cross-day change may already have relieved this day.
            current = self._assess(day.day_number, self._nodes_of(day))
            if not current.verified_severe:
                continue
            attempt, accepted = self._recompose_day(day, current)
            attempts.append(attempt)
            changed = changed or accepted
        if changed:
            # Food is judged against the stops the traveller is shown.
            recompute_food_suggestions(planning_state)
        return RouteBurdenRepairReport(
            attempts=attempts, recomposition_requests_used=self._requests_used, recomposition_requests_cap=self._cap
        )

    # -- composition view of the plan -----------------------------------------------------------

    def _prepare_composition(self) -> None:
        state = self._state
        interests = usefulness.requested_canonical_interests(state)
        requested = frozenset(interests)
        assessed = usefulness.usefulness_by_place_id(state)
        must_visit_ids = must_visit_place_ids(state)
        pool = {_composition_key(poi): poi for poi in universe.schedulable_broad_pois(state)}
        self._locked = {lock.locked_item_id for lock in state.user_locks if lock.is_active}
        self._must_visit_ids = must_visit_ids
        self._options = {_composition_key(option.poi): option for option in unused_replacement_options(state)}
        self._policy = composition.Policy(
            per_day=PACE_TARGET_PER_DAY[self._pace],
            requested_interests=requested,
            coverage_exempt=frozenset({FOOD}),
            markets_requested=diversity.markets_requested_for(state),
            justified=diversity.justified_classes_for(state),
        )

        def conflicts(poi: dict[str, Any] | None, cluster: str | None) -> frozenset[str]:
            found = set()
            if cluster is not None:
                found.add(f"sub_feature:{cluster}")
            if poi is not None and poi.get(SUSPECT_COLLISION_KEY):
                found.add(f"suspected_duplicate:{poi[SUSPECT_COLLISION_KEY]}")
            return frozenset(found)

        def scheduled(stop: ExperienceItem) -> composition.Stop:
            key = self._key_of(stop)
            known = assessed.get(stop.provider_place_id or "")
            tier = quality_tier_rank(stop.quality_tier)
            poi = pool.get(stop.provider_place_id or "")
            cluster = _candidate_profile(poi, None, interests).sub_feature_cluster if poi is not None else None
            return composition.Stop(
                key=key,
                point=stop.coordinates,
                preference=known.preference if known else (1, 0, 1, 1, -tier),
                tail=known.tail if known else (0.0, " ".join(stop.name.casefold().split()), key),
                evidence_band=known.evidence_band if known else 0,
                tier_rank=known.tier_rank if known else tier,
                must_visit=bool(stop.provider_place_id) and stop.provider_place_id in must_visit_ids,
                coarse_class=diversity.coarse_class(stop.normalized_category),
                interests=frozenset(stop.matched_interests) & requested,
                conflicts=conflicts(poi, cluster),
            )

        def unused(key: str, option: ReplacementOption) -> composition.Stop:
            known = assessed.get(key) or option.profile.usefulness
            return composition.Stop(
                key=key,
                point=option.coordinates,
                preference=known.preference if known else (1, 0, 1, 1, -option.tier_rank),
                tail=known.tail if known else (-option.profile.score, " ".join(option.name.casefold().split()), key),
                evidence_band=known.evidence_band if known else 0,
                tier_rank=option.tier_rank,
                must_visit=False,
                coarse_class=diversity.coarse_class(option.profile.primary),
                interests=option.matched_interests & requested,
                conflicts=conflicts(option.poi, option.profile.sub_feature_cluster),
                may_enter=not (option.profile.commercial_gallery and "art" not in interests),
            )

        self._scheduled_stop = scheduled
        self._unused_stops = [unused(key, option) for key, option in self._options.items()]

    @staticmethod
    def _key_of(stop: ExperienceItem) -> str:
        return stop.provider_place_id or stop.experience_id

    def _nodes_of(self, day: DailyPlan) -> list[_Node]:
        return [_Node(self._key_of(stop), item=stop) for stop in day.experiences]

    def _protection(self, stop: ExperienceItem) -> str:
        if stop.provider_place_id and stop.provider_place_id in self._must_visit_ids:
            return "must_visit"
        if stop.experience_id in self._locked:
            return "user_lock"
        if stop.coordinates is None:
            return "no_coordinates"
        return _REPLACEABLE

    # -- evidence ---------------------------------------------------------------------------

    def _evidence(self, origin: _Node, destination: _Node) -> RouteLegFeasibility | None:
        """The leg already known for this pair: one verified in this stage,
        or the stored report's leg for two scheduled stops (verified or not)."""
        fresh = self._fresh.get((origin.key, destination.key))
        if fresh is not None:
            return fresh
        if origin.item is None or destination.item is None:
            return None
        pair = (origin.item.experience_id, destination.item.experience_id)
        return next(
            (
                leg
                for leg in self._state.route_feasibility_report.legs
                if (leg.from_experience_id, leg.to_experience_id) == pair
            ),
            None,
        )

    def _legs(self, nodes: Sequence[_Node]) -> list[RouteLegFeasibility | None]:
        return [self._evidence(a, b) for a, b in zip(nodes, nodes[1:])]

    def _assess(self, day_number: int, nodes: Sequence[_Node]) -> DayRouteAssessment:
        return assess_legs(day_number, self._legs(nodes), self._pace)

    def _unknown_runs(self, nodes: Sequence[_Node]) -> list[list[_Node]]:
        """The maximal runs of consecutive legs without any evidence, each as
        the stops to route in one request."""
        runs: list[list[_Node]] = []
        current: list[_Node] = []
        for origin, destination in zip(nodes, nodes[1:]):
            if self._evidence(origin, destination) is None:
                current = current or [origin]
                current.append(destination)
            elif current:
                runs.append(current)
                current = []
        if current:
            runs.append(current)
        return runs

    # -- one severe day -----------------------------------------------------------------------

    def _recompose_day(self, day: DailyPlan, before: DayRouteAssessment) -> tuple[RouteBurdenRepairAttempt, bool]:
        stops = day.experiences
        nodes = self._nodes_of(day)
        protections = [self._protection(stop) for stop in stops]

        def outcome(reason: str, **fields: Any) -> RouteBurdenRepairAttempt:
            return RouteBurdenRepairAttempt(
                day_number=day.day_number,
                reason=reason,
                stop_protections=protections,
                before_duration_seconds=before.burden.total_duration_seconds,
                before_distance_meters=before.burden.total_distance_meters,
                severity_before=before.severity,
                **{"severity_after": before.severity, "unverified_legs_after": before.unverified_legs, **fields},
            )

        if self._consecutive_transient >= _MAX_CONSECUTIVE_TRANSIENT:
            return outcome(PROVIDER_UNAVAILABLE), False

        offending = {
            self._key_of(stop)
            for index in before.offending_legs
            for stop in (stops[index], stops[index + 1])
        }
        candidates = self._propose(day, nodes, offending)
        if not candidates:
            on_offending_legs = [
                protection for stop, protection in zip(stops, protections) if self._key_of(stop) in offending
            ]
            protected_only = all(protection in ("must_visit", "user_lock") for protection in on_offending_legs)
            return outcome(PROTECTED_STOPS_ONLY if protected_only else NO_ADMISSIBLE_ALTERNATIVE), False

        shortlist = self._shortlist(candidates)
        verified: list[_Verified] = []
        skipped = attempted = 0
        requests_before = self._requests_used
        chosen: _Verified | None = None
        for candidate in shortlist:
            if self._consecutive_transient >= _MAX_CONSECUTIVE_TRANSIENT:
                break
            if not self._fits_budget(candidate):
                skipped += 1
                continue
            attempted += 1
            result = self._verify(candidate, day.day_number, before)
            if result is None:
                continue
            verified.append(result)
            if result.outcome == 0:
                chosen = result  # resolved and fully verified: nothing can beat it
                break
        if chosen is None and verified:
            chosen = min(
                verified,
                key=lambda item: (
                    item.outcome,
                    sum(after.burden.total_duration_seconds for after in item.after.values()),
                    item.candidate.quality,
                    item.candidate.ident,
                ),
            )
        counts = {
            "candidates_shortlisted": len(shortlist),
            "candidates_verified": attempted,
            "candidates_skipped_for_budget": skipped,
            "routing_requests": self._requests_used - requests_before,
            "route_burden_repair_attempted": attempted > 0,
        }
        if chosen is None:
            if attempted == 0 and skipped:
                reason = INSUFFICIENT_BUDGET
            elif self._consecutive_transient >= _MAX_CONSECUTIVE_TRANSIENT or (attempted and not verified and self._consecutive_transient):
                reason = PROVIDER_UNAVAILABLE
            else:
                reason = ALTERNATIVES_NOT_BETTER
            return outcome(reason, **counts), False

        self._apply(chosen)
        after = chosen.after[day.day_number]
        candidate = chosen.candidate
        other_days = [number for number in candidate.orders if number != day.day_number]
        return (
            outcome(
                ACCEPTED,
                accepted=True,
                operation=candidate.kind,
                other_day_number=other_days[0] if other_days else None,
                replaced_place=candidate.outgoing.name if candidate.outgoing else None,
                replacement_place=candidate.incoming.name if candidate.incoming else None,
                moved_place=candidate.moved.name if candidate.moved else None,
                after_duration_seconds=after.burden.total_duration_seconds,
                after_distance_meters=after.burden.total_distance_meters,
                severity_after=after.severity,
                unverified_legs_after=after.unverified_legs,
                hard_walking_violation_remains=after.burden.excessive_walking,
                **counts,
            ),
            True,
        )

    # -- proposing --------------------------------------------------------------------------

    def _propose(self, day: DailyPlan, nodes: list[_Node], offending: set[str]) -> list[_Candidate]:
        plan_days = self._state.experience_plan.daily_plans
        current_orders = {plan_day.day_number: self._nodes_of(plan_day) for plan_day in plan_days}
        candidates: list[_Candidate] = []
        seen: set[tuple[Any, ...]] = set()

        def add(candidate: _Candidate) -> None:
            if candidate.ident in seen:
                return
            seen.add(candidate.ident)
            # a changed leg with an unlocated end could never be verified
            if any(self._unknown_touches_unlocated(order) for order in candidate.orders.values()):
                return
            candidate.proxy_delta_m = sum(
                _path_m(order) - _path_m(current_orders[number]) for number, order in candidate.orders.items()
            )
            candidate.unknown_legs = sum(
                len(run) - 1 for order in candidate.orders.values() for run in self._unknown_runs(order)
            )
            candidates.append(candidate)

        # 1. Reorders of the day itself: the orders that do not keep the same
        #    neighbours, shortest by straight line first.
        current_pairs = _neighbour_pairs(nodes)
        orders = (
            itertools.permutations(nodes)
            if len(nodes) <= _MAX_PERMUTED_STOPS
            else [tuple(reversed(nodes))]
        )
        reorders = sorted(
            (list(order) for order in orders if _neighbour_pairs(order) != current_pairs),
            key=lambda order: (_path_m(order), tuple(node.key for node in order)),
        )
        for order in reorders[:_MAX_REORDERS]:
            add(_Candidate(REORDER, {day.day_number: order}))

        # 2. Q3's moves for the stops of the offending legs.
        stops_by_day = [[self._scheduled_stop(stop) for stop in plan_day.experiences] for plan_day in plan_days]
        working = composition.working_set(stops_by_day, self._unused_stops, self._policy)
        by_key: dict[str, _Node] = {
            node.key: node for day_nodes in current_orders.values() for node in day_nodes
        }
        immovable = frozenset(
            self._key_of(stop)
            for plan_day in plan_days
            for stop in plan_day.experiences
            if stop.experience_id in self._locked
        )
        for move in composition.focused_moves(stops_by_day, working, self._policy, sorted(offending), immovable):
            orders_after: dict[int, list[_Node]] = {}
            for index in move.changed_days:
                number = plan_days[index].day_number
                kept = [by_key[key] for key in move.days[index] if key in by_key]
                if move.kind == REPLACE:
                    option = self._options[move.incoming[0]]
                    incoming = _Node(move.incoming[0], option=option)
                    order = [incoming if key == move.incoming[0] else by_key[key] for key in move.days[index]]
                elif move.kind == RELOCATE and len(move.days[index]) > len(current_orders[number]):
                    # the receiving day: the stop joins where it lengthens the day least
                    arriving = by_key[move.moved[0]]
                    rest = [node for node in kept if node.key != arriving.key]
                    order = min(
                        (rest[:slot] + [arriving] + rest[slot:] for slot in range(len(rest) + 1)),
                        key=lambda proposed: (_path_m(proposed), [node.key for node in proposed]),
                    )
                else:
                    order = kept
                orders_after[number] = order
            if move.kind == REPLACE:
                outgoing = by_key[move.outgoing[0]]
                incoming_node = _Node(move.incoming[0], option=self._options[move.incoming[0]])
                if is_meaningful_stop(outgoing.item) and not self._meaningful(incoming_node):
                    continue
                add(_Candidate(REPLACE, orders_after, move.quality, outgoing=outgoing, incoming=incoming_node))
            else:
                add(_Candidate(move.kind, orders_after, move.quality, moved=by_key[move.moved[0]]))
        return candidates

    def _unknown_touches_unlocated(self, nodes: Sequence[_Node]) -> bool:
        return any(node.coordinates is None for run in self._unknown_runs(nodes) for node in run)

    def _meaningful(self, node: _Node) -> bool:
        return is_meaningful_stop(self._item(node))

    def _item(self, node: _Node) -> ExperienceItem:
        """The scheduled stop for a node; a replacement is built once, as the
        planner builds any stop (same provider identity, same stable id)."""
        if node.item is not None:
            return node.item
        if node.key not in self._built:
            self._built[node.key] = build_replacement_experience(self._state, node.option)
        return self._built[node.key]

    # -- shortlisting -------------------------------------------------------------------------

    def _shortlist(self, candidates: list[_Candidate]) -> list[_Candidate]:
        """At most `SHORTLIST_SIZE` proposals. Known provider evidence first
        (a move with no leg left to route), then the straight-line estimate
        in 100 m steps, then Q3's own ranking. The best overall is taken,
        then the best of ONE other kind of move when there is one, then the
        remaining places by rank -- a kind is never represented by a proposal
        whose estimate is materially worse than today's arrangement."""
        worth_routing = [
            candidate
            for candidate in candidates
            if candidate.unknown_legs == 0 or candidate.proxy_delta_m < _PROXY_WORSE_M
        ]
        ranked = sorted(
            worth_routing,
            key=lambda candidate: (
                candidate.unknown_legs > 0,
                round(candidate.proxy_delta_m / 100),
                candidate.quality,
                candidate.ident,
            ),
        )
        if not ranked:
            return []
        chosen = [ranked[0]]
        other_kind = next(
            (candidate for candidate in ranked if _KIND_GROUP[candidate.kind] != _KIND_GROUP[ranked[0].kind]), None
        )
        if other_kind is not None:
            chosen.append(other_kind)
        for candidate in ranked:
            if len(chosen) >= SHORTLIST_SIZE:
                break
            if all(candidate is not taken for taken in chosen):
                chosen.append(candidate)
        position = {id(candidate): index for index, candidate in enumerate(ranked)}
        return sorted(chosen, key=lambda candidate: position[id(candidate)])

    # -- budget -----------------------------------------------------------------------------

    def _requests_left(self) -> int:
        """What this stage may still dispatch: its own cap AND the
        generation's shared route-request allowance."""
        if self._context is None:
            return self._local_left
        return max(0, min(self._context.recomposition_requests_left, self._context.route_requests_left))

    def _fits_budget(self, candidate: _Candidate) -> bool:
        """Whether EVERY changed leg of the move can be verified with what is
        left: one request per run of unknown legs, one credit per leg. A leg
        with existing evidence needs neither. Counted before anything is
        dispatched, so a move is never verified in part."""
        runs = [run for order in candidate.orders.values() for run in self._unknown_runs(order)]
        if not runs:
            return True
        if len(runs) > self._requests_left():
            return False
        tracker = self._context.usage_tracker if self._context is not None else None
        return tracker is None or tracker.can_afford(sum(len(run) - 1 for run in runs))

    def _route_run(self, run: list[_Node]) -> list[RouteLegFeasibility] | None:
        """The provider's legs for one run (one request, or none when the
        provider already knows them), charged to this stage by what the
        shared allowance actually lost. None when the call raised."""
        items = [self._item(node) for node in run]
        before = self._context.route_requests_left if self._context is not None else None
        try:
            legs = self.route_feasibility_service.route_day_legs(items, self._context)
        except Exception:
            logger.warning("Routing a proposed day failed; the proposal is left unverified.", exc_info=True)
            legs = None
        used = (before - self._context.route_requests_left) if before is not None else 1
        used = max(0, used)
        self._requests_used += used
        if self._context is not None:
            self._context.recomposition_requests_left = max(0, self._context.recomposition_requests_left - used)
        else:
            self._local_left = max(0, self._local_left - used)
        return legs if legs is not None and len(legs) == len(run) - 1 else None

    # -- verifying ---------------------------------------------------------------------------

    def _verify(self, candidate: _Candidate, target_day: int, before: DayRouteAssessment) -> _Verified | None:
        """Routes the legs the move changes and judges the result. None when
        the move is rejected or could not be verified."""
        before_by_day = {
            number: self._assess(
                number, self._nodes_of(next(d for d in self._state.experience_plan.daily_plans if d.day_number == number))
            )
            for number in candidate.orders
        }
        fresh: dict[tuple[str, str], RouteLegFeasibility] = {}
        for order in candidate.orders.values():
            for run in self._unknown_runs(order):
                legs = self._route_run(run)
                if legs is None or any(not is_verified_leg(leg) and not is_definitive_failure(leg) for leg in legs):
                    # no answer, or a transient one: no evidence about any stop
                    self._consecutive_transient += 1
                    return None
                if any(is_definitive_failure(leg) for leg in legs):
                    # the provider cannot route the proposed day: never introduced
                    self._consecutive_transient = 0
                    return None
                for (origin, destination), leg in zip(zip(run, run[1:]), legs):
                    fresh[(origin.key, destination.key)] = leg
        self._consecutive_transient = 0
        # Verified legs are kept for the rest of the stage (a later proposal
        # that needs the same leg reuses it at no cost).
        self._fresh.update(fresh)

        legs_by_day = {number: self._legs(order) for number, order in candidate.orders.items()}
        after = {number: assess_legs(number, legs, self._pace) for number, legs in legs_by_day.items()}
        outcome = self._judge(target_day, before_by_day, after)
        if outcome is None:
            return None
        return _Verified(candidate, legs_by_day, after, outcome)

    @staticmethod
    def _judge(
        target_day: int, before: dict[int, DayRouteAssessment], after: dict[int, DayRouteAssessment]
    ) -> int | None:
        for number, assessment in after.items():
            previous = before[number]
            if assessment.unverified_legs > previous.unverified_legs:
                return None
            if number == target_day or not assessment.verified_severe:
                continue
            # another day may not become severe, nor a severe one get worse
            if not previous.verified_severe or _worse(previous, assessment):
                return None
        target_before, target_after = before[target_day], after[target_day]
        if not target_after.verified_severe:
            return 0 if all(assessment.fully_verified for assessment in after.values()) else 1
        min_ratio = get_settings().route_burden_repair_min_improvement_ratio
        material = (
            target_after.burden.total_duration_seconds
            <= target_before.burden.total_duration_seconds * (1.0 - min_ratio)
        )
        return 2 if material and not _worse(target_before, target_after) else None

    # -- applying ----------------------------------------------------------------------------

    def _apply(self, chosen: _Verified) -> None:
        state = self._state
        report = state.route_feasibility_report
        for number, order in chosen.candidate.orders.items():
            day = next(day for day in state.experience_plan.daily_plans if day.day_number == number)
            previous = list(day.experiences)
            new_order = [self._item(node) for node in order]
            for stop_index, stop in enumerate(new_order, start=1):
                stop.day_number = number
                stop.stop_order = stop_index
            day.experiences = new_order
            # The day changed: its summary is rebuilt and a rationale written
            # for the superseded day is dropped.
            day.goal = deterministic_day_summary([stop.name for stop in new_order])
            clear_day_rationale(day)
            # The day's stale legs give way to the legs of its FINAL order:
            # the ones just verified and the unchanged ones already held.
            self.route_feasibility_service.replace_day_legs(
                report, previous, [leg for leg in chosen.legs[number] if leg is not None]
            )
            sequencing = state.route_aware_sequencing_report
            if sequencing is not None:
                # A reorder suggestion computed for the superseded day is stale.
                sequencing.suggestions = [
                    suggestion for suggestion in sequencing.suggestions if suggestion.day_index != number
                ]
        if chosen.candidate.incoming is not None:
            self._options.pop(chosen.candidate.incoming.key, None)
            self._unused_stops = [stop for stop in self._unused_stops if stop.key != chosen.candidate.incoming.key]


def _path_m(nodes: Sequence[_Node]) -> int:
    """Straight-line length of an order, in metres -- an ESTIMATE that only
    orders proposals. Never a route length and never shown."""
    length = path_length_km([node.coordinates for node in nodes])
    return 10**9 if length == float("inf") else round(length * 1000)


def _neighbour_pairs(nodes: Sequence[_Node]) -> frozenset[frozenset[str]]:
    return frozenset(frozenset((a.key, b.key)) for a, b in zip(nodes, nodes[1:]))


def _worse(before: DayRouteAssessment, after: DayRouteAssessment) -> bool:
    """Whether any route dimension of a still-severe day got worse: a longer
    leg, more walking where walking is already excessive, a longer vehicle
    transfer, or more travel in total."""
    old, new = before.burden, after.burden
    return (
        new.max_leg_duration_seconds > old.max_leg_duration_seconds
        or new.max_drive_leg_duration_seconds > old.max_drive_leg_duration_seconds
        or new.total_duration_seconds > old.total_duration_seconds
        or (new.walking_duration_seconds > old.walking_duration_seconds and new.excessive_walking)
        or (new.over_transfer_limit and not old.over_transfer_limit)
    )
