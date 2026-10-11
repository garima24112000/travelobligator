"""Day composition (Phase Q3): which eligible places share a day.

Candidate usefulness (`candidate_usefulness`, Q2) answers "which eligible
place deserves a limited slot?". This module answers a different question:
"do the places of a day belong together?". It owns the ONE composition
objective that both the deterministic planner and a plan the reasoning model
chose are improved against, and the coarse geographic groups ("areas") the
bounded reasoning request shows the model. It never changes the usefulness
ordering -- it only reads each candidate's `preference` / `tail`.

Pure: no provider, routing or model call, no settings beyond the existing
schedule-diversity ones, and no place name or destination anywhere.

GEOGRAPHY IS A STRAIGHT-LINE PROXY. Every distance here is a great-circle
distance between provider coordinates. None is a route length, a walking
distance or a travel duration, none is shown, stored or validated, and a day
this module calls compact can still be a hard day to travel: actual
provider-route burden, and any recomposition driven by it, belongs to Q4.

Areas
-----
`build_areas` groups located candidates by complete linkage: every two
members of an area lie within `AREA_MAX_DIAMETER_KM` of each other (so there
is no chaining along a corridor). That value is the planner's existing
"near the day's other stops" proxy (`NEAR_DAY_STOPS_KM`) -- not a
neighbourhood definition, and not derived from the validator's spread
warning. Areas are opaque ids (`a1`, `a2`, ...). They are a coarse label
only: the objective below reads DISTANCES, never an area id, so two places a
few metres apart on opposite sides of an area edge are simply close.

Objective
---------
A day's *extent* is the largest straight-line distance between two of its
located stops (order-independent). Its band is

  * `dispersed` -- the validator's own check would warn: the straight-line
    length of the day, visited nearest-next from its best-ranked stop,
    exceeds `GEOGRAPHIC_SPREAD_THRESHOLD_KM`;
  * `extended`  -- otherwise, extent beyond `NEAR_DAY_STOPS_KM`;
  * `compact`   -- otherwise;
  * `unmeasured` -- fewer than two located stops (never called compact).

A move is INADMISSIBLE when it breaks a hard rule: it would remove a grounded
must-visit, bring in a place that may not enter (low-value object, commercial
gallery on a non-art trip, unlocated, beyond the pool's reach), schedule two
places of one conflict group (an unresolved suspected duplicate pair, two
sub-features of one complex), exceed a plan-level class cap, add to a day's
hard marketplace excess, take away the only stop covering a requested
interest (any interest, food included), or create a dispersed day. An admissible move is then an improvement, in this order of precedence:

  1. COVERAGE  -- a requested interest gains a scheduled stop;
  2. GEOGRAPHY -- the plan's total extent falls by at least
     `EXTENT_MATERIALITY_KM` (the materiality tolerance). A smaller
     difference, including one that merely crosses a band boundary, is NOT a
     geographic improvement and can never outweigh usefulness;
  3. VARIETY   -- for a move that keeps the same places: less unjustified
     class concentration, then more requested interests served per day --
     and only while the plan's total extent stays within the tolerance of
     where the last coverage/geography move left it (no drift).

Usefulness is protected by guards on every replacement, not by a weight:
a replacement that removes a dispersed day (or covers an interest) may sit at
most one quality tier below the stop it replaces; any other geographic
replacement may lose at most one usefulness evidence band and no tier. Among
the improvements of one pass the best is taken: bands first, then whole
tolerance steps of extent gained, then the Q2 usefulness of the scheduled
set, then variety, then the fewest places replaced, then the remaining
extent, then a neutral identity key.

Search
------
Best-improvement local search over the COMPLETE multi-day assignment: swap
two stops between days, move a stop to a lighter day, replace a
discretionary stop with an unused candidate, or rebuild a day around one
kept stop (several replacements judged as one move, so a day spread over
three places is not stuck). Every intermediate plan is valid. The search
ends when no move improves, after as many moves as there are scheduled
stops, or when `MAX_EVALUATIONS` candidate plans have been judged -- it then
returns the best plan found so far and never fails a generation.
"""

from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Sequence

from app.models.common import GeoPoint
from app.services import schedule_diversity as diversity
from app.services.day_order_heuristics import (
    AS_WELL_PLACED_KM,
    GEOGRAPHIC_SPREAD_THRESHOLD_KM,
    NEAR_DAY_STOPS_KM,
    day_spread_km,
    nearest_next_order,
)
from app.utils.geo import haversine_distance_km

# Two existing proxies, each in its existing meaning (see the module text).
AREA_MAX_DIAMETER_KM = NEAR_DAY_STOPS_KM
EXTENT_MATERIALITY_KM = AS_WELL_PLACED_KM

# Computational bounds. Complete linkage runs on at most this many located
# candidates (the rest attach to an area they fit entirely, or stand alone).
MAX_AREA_CANDIDATES = 300
# Scheduled stops plus the unused candidates the search may bring in.
MAX_WORKING_SET = 120
# Candidate plans judged per composition (the objective-evaluation budget).
MAX_EVALUATIONS = 30_000
# Unused candidates kept per requested interest when the working set is bounded.
COVERAGE_CANDIDATES_PER_INTEREST = 2

COMPACT = "compact"
EXTENDED = "extended"
DISPERSED = "dispersed"
UNMEASURED = "unmeasured"

REASON_MUST_VISIT = "must_visit"
REASON_COVERAGE = "coverage"
REASON_GEOGRAPHY = "geography"
REASON_VARIETY = "variety"

DECLINED_DUPLICATE_IDENTITY = "duplicate_identity"
DECLINED_OVER_BOUND = "scheduled_stops_exceed_working_set_bound"

_NEAR_M = round(NEAR_DAY_STOPS_KM * 1000)
_AREA_M = round(AREA_MAX_DIAMETER_KM * 1000)
_MATERIAL_M = round(EXTENT_MATERIALITY_KM * 1000)


def _meters(a: GeoPoint | None, b: GeoPoint | None) -> int | None:
    """Straight-line distance in whole metres (rounded so float noise never
    decides a comparison); None when either point is missing."""
    distance_km = haversine_distance_km(a, b)
    return None if distance_km is None else round(distance_km * 1000)


def geographic_band(extent_km: float | None, spread_km: float | None) -> str:
    """The band of one day from its extent and its straight-line path length
    (`day_order_heuristics.day_extent_km` / `day_spread_km`)."""
    if extent_km is None:
        return UNMEASURED
    if spread_km is not None and spread_km > GEOGRAPHIC_SPREAD_THRESHOLD_KM:
        return DISPERSED
    return EXTENDED if round(extent_km * 1000) > _NEAR_M else COMPACT


# =====================================================================================
# Areas
# =====================================================================================


@dataclass(frozen=True)
class Located:
    """One candidate as the grouping sees it. `order` is the caller's
    canonical ordering (the Q2 usefulness sort key); it only decides which
    candidates are clustered first when there are too many, and how areas
    are numbered."""

    key: str
    point: GeoPoint | None
    order: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Areas:
    area_of: dict[str, str] = field(default_factory=dict)
    # area id -> member keys, best-ordered first
    members: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # area id -> the areas with a member within `NEAR_DAY_STOPS_KM`, nearest first
    near: dict[str, tuple[str, ...]] = field(default_factory=dict)


def build_areas(candidates: Sequence[Located]) -> Areas:
    """Deterministic complete-linkage areas of the located candidates.

    The result depends only on the SET of candidates: input is sorted by
    key, distances are whole metres, and equal distances merge the pair with
    the smaller keys first. A candidate without coordinates gets no area.
    """
    by_key: dict[str, Located] = {}
    for candidate in sorted(candidates, key=lambda item: item.key):
        if candidate.point is not None:
            by_key.setdefault(candidate.key, candidate)
    located = list(by_key.values())
    if not located:
        return Areas()

    ranked = sorted(located, key=lambda item: (item.order, item.key))
    core_keys = {item.key for item in ranked[:MAX_AREA_CANDIDATES]}
    core = [item for item in located if item.key in core_keys]  # key order
    surplus = [item for item in located if item.key not in core_keys]

    count = len(core)
    distance = [[0] * count for _ in range(count)]
    heap: list[tuple[int, int, int, int, int]] = []
    for i in range(count):
        for j in range(i + 1, count):
            meters = _meters(core[i].point, core[j].point) or 0
            distance[i][j] = distance[j][i] = meters
            if meters <= _AREA_M:
                heap.append((meters, i, j, 0, 0))
    heapq.heapify(heap)

    # A group is labelled by its smallest member index (= smallest key).
    link = [row[:] for row in distance]
    groups: dict[int, list[int]] = {index: [index] for index in range(count)}
    version = [0] * count
    while heap:
        _, a, b, version_a, version_b = heapq.heappop(heap)
        if a not in groups or b not in groups or version[a] != version_a or version[b] != version_b:
            continue
        groups[a].extend(groups.pop(b))
        version[a] += 1
        for other in groups:
            if other == a:
                continue
            farthest = max(link[a][other], link[b][other])
            link[a][other] = link[other][a] = farthest
            if farthest <= _AREA_M:
                low, high = (a, other) if a < other else (other, a)
                heapq.heappush(heap, (farthest, low, high, version[low], version[high]))

    clusters: list[list[Located]] = [[core[index] for index in sorted(members)] for _, members in sorted(groups.items())]
    core_cluster_of = {member.key: index for index, members in enumerate(clusters) for member in members}
    core_members = [list(members) for members in clusters]
    for candidate in surplus:
        best: tuple[int, int] | None = None
        for index, members in enumerate(core_members):
            reach = max(_meters(candidate.point, member.point) or 0 for member in members)
            if reach <= _AREA_M and (best is None or (reach, index) < best):
                best = (reach, index)
        if best is not None:
            clusters[best[1]].append(candidate)
        else:
            clusters.append([candidate])

    def rank(members: list[Located]) -> tuple[Any, ...]:
        return min((member.order, member.key) for member in members)

    ordered = sorted(clusters, key=rank)
    area_ids = {id(members): f"a{index + 1}" for index, members in enumerate(ordered)}
    area_of: dict[str, str] = {}
    members_of: dict[str, tuple[str, ...]] = {}
    for members in ordered:
        area_id = area_ids[id(members)]
        ranked_members = sorted(members, key=lambda item: (item.order, item.key))
        members_of[area_id] = tuple(member.key for member in ranked_members)
        for member in members:
            area_of[member.key] = area_id

    # Nearness between areas, from the clustered candidates' own distances.
    closest: dict[tuple[str, str], int] = {}
    core_area = [area_of[item.key] for item in core]
    for i in range(count):
        for j in range(i + 1, count):
            if core_cluster_of[core[i].key] == core_cluster_of[core[j].key] or distance[i][j] > _NEAR_M:
                continue
            pair = (core_area[i], core_area[j]) if core_area[i] < core_area[j] else (core_area[j], core_area[i])
            if pair not in closest or distance[i][j] < closest[pair]:
                closest[pair] = distance[i][j]
    neighbours: dict[str, list[tuple[int, int, str]]] = {area_id: [] for area_id in members_of}
    for (first, second), meters in closest.items():
        neighbours[first].append((meters, int(second[1:]), second))
        neighbours[second].append((meters, int(first[1:]), first))
    near = {area_id: tuple(other for _, _, other in sorted(found)) for area_id, found in neighbours.items()}
    return Areas(area_of=area_of, members=members_of, near=near)


# =====================================================================================
# Composition
# =====================================================================================


@dataclass(frozen=True)
class Stop:
    """One eligible candidate as the composition sees it. Every field is a
    stored fact or a Q2 value the caller already computed."""

    key: str  # provider place id: the scheduling identity
    point: GeoPoint | None
    preference: tuple[int, ...]  # `CandidateUsefulness.preference`
    tail: tuple[Any, ...]  # `CandidateUsefulness.tail`
    evidence_band: int
    tier_rank: int
    must_visit: bool
    coarse_class: str
    # requested interests this place serves (food left out by the caller)
    interests: frozenset[str] = frozenset()
    # groups of which at most one member may be scheduled
    conflicts: frozenset[str] = frozenset()
    # may be brought INTO a plan by a replacement
    may_enter: bool = True

    @property
    def order(self) -> tuple[Any, ...]:
        """The Q2 canonical ordering, completed by the identity."""
        return (*self.preference, *self.tail, self.key)


@dataclass(frozen=True)
class Policy:
    per_day: int
    requested_interests: frozenset[str] = frozenset()
    # Requested interests a place is never brought in FOR (food: it is served
    # by real nearby food). A stop that covers one is still never taken out.
    coverage_exempt: frozenset[str] = frozenset()
    markets_requested: bool = False
    justified: frozenset[str] = frozenset()
    # most stops of a coarse class in the whole plan (None = uncapped)
    plan_class_cap: Callable[[str], int | None] | None = None
    # swaps and moves between days
    allow_regrouping: bool = True
    # bringing an unused candidate in (also needed to add a missing must-visit)
    allow_replacement: bool = True
    # most discretionary stops replaced (None = bounded only by the search)
    max_replacements: int | None = None
    max_evaluations: int = MAX_EVALUATIONS


@dataclass(frozen=True)
class Move:
    kind: str  # swap | relocate | replace | rebuild | add
    reason: str
    outgoing: tuple[str, ...] = ()
    incoming: tuple[str, ...] = ()


@dataclass
class Composition:
    days: list[list[str]]
    moves: list[Move] = field(default_factory=list)
    evaluations: int = 0
    budget_exhausted: bool = False
    working_set_size: int = 0
    # Set when composition did not run; `days` is then the input, unchanged.
    declined: str | None = None
    # Diagnostic only: aggregate counts (fixed labels) of what the search
    # could draw on and why moves were not generated or not accepted. Never
    # read by a decision; see `_Search.counts`.
    diagnostics: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.moves)


@dataclass(frozen=True)
class _DayFacts:
    extent_m: int
    band: str
    excess: int
    hard: int
    interests: frozenset[str]


@dataclass(frozen=True)
class _PlanFacts:
    covered: frozenset[str]
    uncovered: int
    dispersed: int
    extended: int
    extent_m: int
    excess: int
    variety: int
    hard_by_day: tuple[int, ...]


@dataclass(frozen=True)
class _Proposal:
    kind: str
    days: list[list[str]]
    # (outgoing, incoming) for every stop the move replaces
    pairs: tuple[tuple[str, str], ...] = ()
    moved: tuple[str, ...] = ()


def _tier_guard(outgoing: Stop, incoming: Stop) -> bool:
    return incoming.tier_rank >= outgoing.tier_rank - 1


def _band_guard(outgoing: Stop, incoming: Stop) -> bool:
    return incoming.tier_rank >= outgoing.tier_rank and incoming.evidence_band >= outgoing.evidence_band - 1


class _Search:
    def __init__(self, stops: dict[str, Stop], policy: Policy) -> None:
        self.stops = stops
        self.policy = policy
        self.evaluations = 0
        # Diagnostic only: aggregate counts of why moves were not generated or
        # not accepted (fixed labels). Nothing reads them to decide anything,
        # and counting changes neither which moves exist nor their order.
        self.counts: dict[str, int] = {}
        self._distance: dict[tuple[str, str], int | None] = {}
        self._day_facts: dict[tuple[str, ...], _DayFacts] = {}
        self._neighbours: dict[str, tuple[str, ...]] = {}

    def _note(self, label: str, amount: int = 1) -> None:
        self.counts[label] = self.counts.get(label, 0) + amount

    # -- geometry (cached pairwise straight-line distances) -----------------------------

    def meters(self, a: str, b: str) -> int | None:
        if a == b:
            return 0
        pair = (a, b) if a < b else (b, a)
        if pair not in self._distance:
            self._distance[pair] = _meters(self.stops[a].point, self.stops[b].point)
        return self._distance[pair]

    def within(self, a: str, b: str, limit_m: int) -> bool:
        meters = self.meters(a, b)
        return meters is not None and meters <= limit_m

    def neighbours(self, key: str) -> tuple[str, ...]:
        """Every working-set stop within the near proxy of `key`, best first."""
        if key not in self._neighbours:
            found = [other for other in self.stops if other != key and self.within(key, other, _NEAR_M)]
            self._neighbours[key] = tuple(sorted(found, key=lambda other: self.stops[other].order))
        return self._neighbours[key]

    # -- facts --------------------------------------------------------------------------

    def day_facts(self, day: Sequence[str]) -> _DayFacts:
        cache_key = tuple(sorted(day))
        facts = self._day_facts.get(cache_key)
        if facts is not None:
            return facts
        located = sorted((key for key in day if self.stops[key].point is not None), key=lambda key: self.stops[key].order)
        extent_m = max(
            (self.meters(a, b) or 0 for index, a in enumerate(located) for b in located[index + 1 :]), default=0
        )
        if len(located) < 2:
            band = UNMEASURED
        else:
            points = [self.stops[key].point for key in located]
            ordered = nearest_next_order(points, points)
            spread_km = day_spread_km(ordered)
            if spread_km is not None and spread_km > GEOGRAPHIC_SPREAD_THRESHOLD_KM:
                band = DISPERSED
            else:
                band = EXTENDED if extent_m > _NEAR_M else COMPACT
        classes = [self.stops[key].coarse_class for key in day]
        policy = self.policy
        facts = _DayFacts(
            extent_m=extent_m,
            band=band,
            excess=sum(diversity.relievable_excess(classes, policy.markets_requested, policy.justified).values()),
            hard=sum(diversity.hard_excess(classes, policy.markets_requested).values()),
            interests=frozenset().union(*(self.stops[key].interests for key in day)) & policy.requested_interests,
        )
        self._day_facts[cache_key] = facts
        return facts

    def plan_facts(self, days: Sequence[Sequence[str]]) -> _PlanFacts:
        facts = [self.day_facts(day) for day in days]
        covered = frozenset().union(*(item.interests for item in facts)) if facts else frozenset()
        return _PlanFacts(
            covered=covered,
            uncovered=len(self.policy.requested_interests - self.policy.coverage_exempt - covered),
            dispersed=sum(1 for item in facts if item.band == DISPERSED),
            extended=sum(1 for item in facts if item.band == EXTENDED),
            extent_m=sum(item.extent_m for item in facts),
            excess=sum(item.excess for item in facts),
            variety=sum(len(item.interests) for item in facts),
            hard_by_day=tuple(item.hard for item in facts),
        )

    def usefulness(self, days: Sequence[Sequence[str]]) -> list[tuple[int, ...]]:
        """The Q2 preference of every scheduled stop, best first."""
        return sorted(self.stops[key].preference for day in days for key in day)

    # -- hard rules for a place entering the plan -----------------------------------------

    def can_enter(
        self, incoming: str, days: Sequence[Sequence[str]], leaving: Sequence[str], entering: Sequence[str]
    ) -> bool:
        stop = self.stops[incoming]
        if not stop.may_enter or stop.point is None:
            self._note("entry_refused_not_eligible_or_unlocated")
            return False
        staying = [key for day in days for key in day if key not in leaving] + list(entering)
        if stop.conflicts and any(stop.conflicts & self.stops[key].conflicts for key in staying):
            self._note("entry_refused_conflict_group")
            return False
        cap = self.policy.plan_class_cap(stop.coarse_class) if self.policy.plan_class_cap else None
        if cap is not None:
            before = sum(1 for day in days for key in day if self.stops[key].coarse_class == stop.coarse_class)
            after = 1 + sum(1 for key in staying if self.stops[key].coarse_class == stop.coarse_class)
            if after > max(cap, before):
                self._note("entry_refused_plan_class_cap")
                return False
        return True

    # -- judging one candidate plan ----------------------------------------------------------

    def judge(
        self, current: _PlanFacts, proposal: _Proposal, baseline_m: int
    ) -> tuple[str, _PlanFacts, int] | None:
        """`(reason, facts, extent gained)` when `proposal` is an admissible
        improvement of the current plan; None otherwise."""
        self.evaluations += 1
        self._note(f"judged_{proposal.kind}")
        new = self.plan_facts(proposal.days)
        # No requested interest a stop covers today may lose its cover -- judged
        # interest by interest, so covering one never pays for uncovering another.
        if not current.covered <= new.covered or new.dispersed > current.dispersed:
            self._note(
                "rejected_hard_interest_cover_lost"
                if not current.covered <= new.covered
                else "rejected_hard_new_dispersed_day"
            )
            return None
        if any(after > before for after, before in zip(new.hard_by_day, current.hard_by_day)):
            self._note("rejected_hard_market_excess")
            return None
        pairs = [(self.stops[outgoing], self.stops[incoming]) for outgoing, incoming in proposal.pairs]
        gain_m = current.extent_m - new.extent_m
        if new.uncovered < current.uncovered:
            if all(_tier_guard(*pair) for pair in pairs):
                self._note(f"improving_{proposal.kind}")
                return REASON_COVERAGE, new, gain_m
            self._note("rejected_usefulness_guard")
            return None
        if gain_m >= _MATERIAL_M:
            guard = _tier_guard if new.dispersed < current.dispersed else _band_guard
            if all(guard(*pair) for pair in pairs):
                self._note(f"improving_{proposal.kind}")
                return REASON_GEOGRAPHY, new, gain_m
            self._note("rejected_usefulness_guard")
            return None
        if (
            not pairs
            and new.extent_m < baseline_m + _MATERIAL_M
            and (new.excess, -new.variety) < (current.excess, -current.variety)
        ):
            self._note(f"improving_{proposal.kind}")
            return REASON_VARIETY, new, gain_m
        self._note("admissible_not_improving")
        return None

    # -- moves -----------------------------------------------------------------------------

    def proposals(self, days: list[list[str]], unused: set[str], replacements_left: int | None) -> Iterator[_Proposal]:
        policy = self.policy
        if policy.allow_regrouping:
            for first in range(len(days)):
                for second in range(first + 1, len(days)):
                    for i, a in enumerate(days[first]):
                        for j, b in enumerate(days[second]):
                            swapped = [list(day) for day in days]
                            swapped[first][i], swapped[second][j] = b, a
                            yield _Proposal("swap", swapped, moved=(a, b))
            for source, day in enumerate(days):
                if len(day) < 2:
                    continue
                for target, other in enumerate(days):
                    # Only towards a day at least two stops lighter: balanced
                    # day sizes are never merely permuted.
                    if target == source or len(other) + 1 >= len(day) or len(other) >= policy.per_day:
                        continue
                    for key in day:
                        moved = [list(item) for item in days]
                        moved[source].remove(key)
                        moved[target].append(key)
                        yield _Proposal("relocate", moved, moved=(key,))

        if not policy.allow_replacement or (replacements_left is not None and replacements_left <= 0):
            self._note(
                "replacement_pass_skipped_not_allowed"
                if not policy.allow_replacement
                else "replacement_pass_skipped_allowance_used"
            )
            return
        covered = frozenset().union(*(self.stops[key].interests for day in days for key in day))
        missing = policy.requested_interests - policy.coverage_exempt - covered
        for_coverage = [key for key in unused if self.stops[key].interests & missing]
        for index, day in enumerate(days):
            for position, outgoing in enumerate(day):
                if self.stops[outgoing].must_visit:
                    self._note("replace_blocked_must_visit_stop")
                    continue
                options = set(for_coverage)
                for other in day:
                    if other != outgoing:
                        options.update(key for key in self.neighbours(other) if key in unused)
                if not options:
                    # nothing unused lies near the day's other stops (and nothing covers a missing interest)
                    self._note("replace_no_candidate_near_day")
                for incoming in sorted(options, key=lambda key: self.stops[key].order):
                    if not self.can_enter(incoming, days, [outgoing], []):
                        continue
                    replaced = [list(item) for item in days]
                    replaced[index][position] = incoming
                    yield _Proposal("replace", replaced, pairs=((outgoing, incoming),))

        if replacements_left is not None and replacements_left < 2:
            self._note("rebuild_pass_skipped_allowance_below_two")
            return
        for index, day in enumerate(days):
            if len(day) < 3:
                self._note("rebuild_skipped_day_under_three_stops")
                continue
            guard = _tier_guard if self.day_facts(day).band == DISPERSED else _band_guard
            for keep in day:
                if self.stops[keep].point is None:
                    continue
                fixed = [key for key in day if key == keep or self.stops[key].must_visit]
                leaving = sorted((key for key in day if key not in fixed), key=lambda key: self.stops[key].order)
                if len(leaving) < 2:
                    self._note("rebuild_skipped_fewer_than_two_replaceable_stops")
                    continue
                located_fixed = [key for key in fixed if self.stops[key].point is not None]
                pool = [
                    key
                    for key in self.neighbours(keep)
                    if key in unused and all(self.within(key, other, _NEAR_M) for other in located_fixed)
                ]
                if not pool:
                    self._note("rebuild_no_candidate_near_kept_stops")
                pairs: list[tuple[str, str]] = []
                for outgoing in leaving:
                    if replacements_left is not None and len(pairs) >= replacements_left:
                        break
                    taken = [incoming for _, incoming in pairs]
                    gone = [*(old for old, _ in pairs), outgoing]
                    incoming = next(
                        (
                            key
                            for key in pool
                            if key not in taken
                            and guard(self.stops[outgoing], self.stops[key])
                            and self.can_enter(key, days, gone, taken)
                        ),
                        None,
                    )
                    if incoming is not None:
                        pairs.append((outgoing, incoming))
                if len(pairs) < 2:
                    if pool:
                        # candidates lay near the kept stops, yet fewer than two passed the guard and the entry rules
                        self._note("rebuild_candidates_failed_guard_or_entry")
                    continue
                mapping = dict(pairs)
                rebuilt = [list(item) for item in days]
                rebuilt[index] = [mapping.get(key, key) for key in day]
                yield _Proposal("rebuild", rebuilt, pairs=tuple(pairs))


def _working_set(days: Sequence[Sequence[Stop]], unused: Sequence[Stop], policy: Policy) -> list[Stop]:
    """The unused candidates the search may bring in, bounded so that every
    scheduled stop and these fit `MAX_WORKING_SET`. Never simply the best
    ranked: the bound is filled, in this order and without repeats, by

      1. candidates serving each requested interest;
      2. candidates near each scheduled grounded must-visit;
      3. candidates near every other scheduled stop, round-robin;
      4. the best candidates of every geographic area, round-robin;
      5. whatever is left, in usefulness order.

    Each list is in the Q2 canonical order, so the selection depends only on
    the set of candidates, never on provider order."""
    scheduled = [stop for day in days for stop in day]
    room = MAX_WORKING_SET - len(scheduled)
    eligible = sorted((stop for stop in unused if stop.may_enter and stop.point is not None), key=lambda stop: stop.order)
    if len(eligible) <= room:
        return eligible
    by_key = {stop.key: stop for stop in eligible}
    chosen: dict[str, Stop] = {}

    def take(stop: Stop) -> None:
        if len(chosen) < room:
            chosen.setdefault(stop.key, stop)

    for interest in sorted(policy.requested_interests - policy.coverage_exempt):
        for stop in [stop for stop in eligible if interest in stop.interests][:COVERAGE_CANDIDATES_PER_INTEREST]:
            take(stop)

    def near(anchor: Stop) -> list[Stop]:
        return [stop for stop in eligible if (_meters(anchor.point, stop.point) or 0) <= _NEAR_M] if anchor.point else []

    located = sorted((stop for stop in scheduled if stop.point is not None), key=lambda stop: stop.order)
    for anchor in located:
        if anchor.must_visit:
            for stop in near(anchor)[: policy.per_day]:
                take(stop)
    companions = [near(anchor) for anchor in located]
    for depth in range(max(1, policy.per_day)):
        for found in companions:
            if depth < len(found):
                take(found[depth])

    areas = build_areas([Located(stop.key, stop.point, stop.order) for stop in (*scheduled, *eligible)])
    for depth in range(max(1, policy.per_day)):
        for area_id in sorted(areas.members, key=lambda name: int(name[1:])):
            members = [key for key in areas.members[area_id] if key in by_key]
            if depth < len(members):
                take(by_key[members[depth]])

    for stop in eligible:
        take(stop)
    return sorted(chosen.values(), key=lambda stop: stop.order)


def working_set(days: Sequence[Sequence[Stop]], unused: Sequence[Stop], policy: Policy) -> list[Stop]:
    """The bounded set of unused candidates (see `_working_set`), for a
    caller outside this module that brings its own evidence (Q4)."""
    return _working_set(days, unused, policy)


@dataclass(frozen=True)
class FocusedMove:
    """One admissible change of a plan that touches a given stop (Q4)."""

    kind: str  # swap | relocate | replace
    days: list[list[str]]
    changed_days: tuple[int, ...]  # indexes into `days`
    outgoing: tuple[str, ...] = ()  # stops leaving the plan
    incoming: tuple[str, ...] = ()  # stops entering it
    moved: tuple[str, ...] = ()  # stops changing day
    # This module's own judgement of the resulting plan (lower is better):
    # dispersed days, extended days, the Q2 usefulness of the scheduled set,
    # unjustified concentration, variety, total extent. A straight-line
    # proxy -- it orders proposals and never establishes that one is feasible.
    quality: tuple[Any, ...] = ()


def focused_moves(
    days: Sequence[Sequence[Stop]],
    unused: Sequence[Stop],
    policy: Policy,
    focus: Sequence[str],
    immovable: frozenset[str] = frozenset(),
) -> list[FocusedMove]:
    """Every admissible move that takes a `focus` stop out of its day: a
    replacement by an unused candidate, a move to another day with room, or
    a swap with another day's stop. The SAME hard rules as `compose` apply
    (a grounded must-visit is never replaced, `can_enter`, no requested
    interest loses its only cover, no day gains hard marketplace excess, no
    day becomes dispersed) plus the one-tier usefulness guard, and no day
    may become more concentrated in one class. `immovable` stops (active
    user locks) are neither moved nor replaced nor used as a swap partner.

    Q4 calls this for the stops of a leg the routing provider reports as too
    long. What a move is worth is decided there, from provider routes; this
    function only says which moves are allowed and how composition ranks
    them. Deterministic: the result is in a fixed order and depends only on
    the sets given. Empty when two candidates share one key."""
    scheduled = [stop for day in days for stop in day]
    keys = [stop.key for stop in (*scheduled, *unused)]
    if len(set(keys)) != len(keys):
        return []
    search = _Search({stop.key: stop for stop in (*scheduled, *unused)}, policy)
    start = [[stop.key for stop in day] for day in days]
    current = search.plan_facts(start)
    current_usefulness = search.usefulness(start)
    entering = sorted((stop.key for stop in unused), key=lambda key: search.stops[key].order)
    moves: list[FocusedMove] = []

    def consider(kind: str, new_days: list[list[str]], changed: tuple[int, ...], **fields: tuple[str, ...]) -> None:
        facts = search.plan_facts(new_days)
        if not current.covered <= facts.covered or facts.dispersed > current.dispersed:
            return
        if facts.excess > current.excess:
            return
        if any(after > before for after, before in zip(facts.hard_by_day, current.hard_by_day)):
            return
        replaced = bool(fields.get("outgoing"))
        moves.append(
            FocusedMove(
                kind=kind,
                days=new_days,
                changed_days=changed,
                quality=(
                    facts.dispersed,
                    facts.extended,
                    search.usefulness(new_days) if replaced else current_usefulness,
                    facts.excess,
                    -facts.variety,
                    facts.extent_m,
                ),
                **fields,
            )
        )

    for index, day in enumerate(start):
        for position, key in enumerate(day):
            if key not in focus or key in immovable:
                continue
            stop = search.stops[key]
            if not stop.must_visit:
                for incoming in entering:
                    if not _tier_guard(stop, search.stops[incoming]) or not search.can_enter(incoming, start, [key], []):
                        continue
                    replaced = [list(item) for item in start]
                    replaced[index][position] = incoming
                    consider("replace", replaced, (index,), outgoing=(key,), incoming=(incoming,))
            for target, other in enumerate(start):
                if target == index:
                    continue
                if len(day) >= 2 and len(other) < policy.per_day:
                    relocated = [list(item) for item in start]
                    relocated[index].remove(key)
                    relocated[target].append(key)
                    consider("relocate", relocated, (index, target), moved=(key,))
                for other_position, partner in enumerate(other):
                    if partner in immovable:
                        continue
                    swapped = [list(item) for item in start]
                    swapped[index][position], swapped[target][other_position] = partner, key
                    consider("swap", swapped, (index, target), moved=(key, partner))
    return moves


def _include_missing_must_visits(
    search: _Search, days: list[list[str]], unused: set[str], moves: list[Move]
) -> list[list[str]]:
    """Hard rule: a grounded must-visit is scheduled while the pace allows.
    Each one left out takes a free slot of a day below the pace cap, or else
    the place of a discretionary stop -- whichever leaves the better plan.
    With neither (every stop is a must-visit and every day is full) it stays
    out: the pace cap is never broken."""
    missing = sorted((key for key in unused if search.stops[key].must_visit), key=lambda key: search.stops[key].order)
    for key in missing:
        options: list[tuple[tuple[Any, ...], list[list[str]], Move]] = []
        for index, day in enumerate(days):
            if len(day) < search.policy.per_day:
                added = [list(item) for item in days]
                added[index].append(key)
                options.append(((0, index, ""), added, Move("add", REASON_MUST_VISIT, incoming=(key,))))
            for position, outgoing in enumerate(day):
                if search.stops[outgoing].must_visit:
                    continue
                replaced = [list(item) for item in days]
                replaced[index][position] = key
                options.append(
                    ((1, index, outgoing), replaced, Move("replace", REASON_MUST_VISIT, (outgoing,), (key,)))
                )
        if not options:
            continue

        def rank(option: tuple[tuple[Any, ...], list[list[str]], Move]) -> tuple[Any, ...]:
            facts = search.plan_facts(option[1])
            return (facts.uncovered, facts.dispersed, facts.extended, option[0][0], facts.extent_m, option[0][1:])

        _, days, move = min(options, key=rank)
        unused.discard(key)
        unused.update(move.outgoing)
        moves.append(move)
    return days


def compose(days: Sequence[Sequence[Stop]], unused: Sequence[Stop], policy: Policy) -> Composition:
    """Improves a complete multi-day assignment against the composition
    objective. `days` is the plan to start from (the deterministic
    selection, or the reasoning model's own days), `unused` every other
    eligible candidate. Returns the improved days as stop keys; the input
    order of a day is kept, and an incoming stop takes the place of the stop
    it replaces.

    Declines (returns the input unchanged, `declined` set) rather than work
    on a plan it cannot bound or identify: two candidates sharing one key, or
    more scheduled stops than `MAX_WORKING_SET`. The caller then uses its
    previous behaviour; nothing is ever dropped to fit the bound."""
    start = [[stop.key for stop in day] for day in days]
    scheduled = [stop for day in days for stop in day]
    keys = [stop.key for stop in (*scheduled, *unused)]
    if len(set(keys)) != len(keys):
        return Composition(days=start, declined=DECLINED_DUPLICATE_IDENTITY)
    if len(scheduled) > MAX_WORKING_SET:
        return Composition(days=start, declined=DECLINED_OVER_BOUND)

    # A must-visit left out is always within reach of the search, whatever the bound.
    left_out = [stop for stop in unused if stop.must_visit]
    working = _working_set(days, [stop for stop in unused if not stop.must_visit], policy)
    stops = {stop.key: stop for stop in (*scheduled, *left_out, *working)}
    search = _Search(stops, policy)
    current_days = [list(day) for day in start]
    unused_keys = {stop.key for stop in (*left_out, *working)}
    moves: list[Move] = []
    if policy.allow_replacement:
        current_days = _include_missing_must_visits(search, current_days, unused_keys, moves)

    current = search.plan_facts(current_days)
    baseline_m = current.extent_m
    replaced = 0
    exhausted = False
    move_limit = sum(len(day) for day in current_days)
    applied = 0
    while applied < move_limit and not exhausted:
        left = None if policy.max_replacements is None else policy.max_replacements - replaced
        current_usefulness = search.usefulness(current_days)
        best: tuple[tuple[Any, ...], _Proposal, str, _PlanFacts] | None = None
        for proposal in search.proposals(current_days, unused_keys, left):
            if search.evaluations >= policy.max_evaluations:
                exhausted = True
                break
            verdict = search.judge(current, proposal, baseline_m)
            if verdict is None:
                continue
            reason, facts, gain_m = verdict
            rank = (
                facts.uncovered,
                facts.dispersed,
                facts.extended,
                -(gain_m // _MATERIAL_M) if reason == REASON_GEOGRAPHY else 0,
                search.usefulness(proposal.days) if proposal.pairs else current_usefulness,
                facts.excess,
                -facts.variety,
                len(proposal.pairs),  # the smaller change to the chosen places
                facts.extent_m,
                proposal.kind,
                tuple(sorted(outgoing for outgoing, _ in proposal.pairs)),
                tuple(sorted(incoming for _, incoming in proposal.pairs)),
                tuple(sorted(proposal.moved)),
                [tuple(day) for day in proposal.days],
            )
            if best is None or rank < best[0]:
                best = (rank, proposal, reason, facts)
        if best is None:
            break
        _, proposal, reason, facts = best
        current_days, current = proposal.days, facts
        if reason != REASON_VARIETY:
            baseline_m = facts.extent_m
        for outgoing, incoming in proposal.pairs:
            unused_keys.discard(incoming)
            unused_keys.add(outgoing)
        replaced += len(proposal.pairs)
        applied += 1
        moves.append(
            Move(
                proposal.kind,
                reason,
                outgoing=tuple(outgoing for outgoing, _ in proposal.pairs) or proposal.moved,
                incoming=tuple(incoming for _, incoming in proposal.pairs),
            )
        )
    return Composition(
        days=current_days,
        moves=moves,
        evaluations=search.evaluations,
        budget_exhausted=exhausted,
        working_set_size=len(stops),
        diagnostics={
            **search.counts,
            # what the search could draw on (unused candidates other than left-out must-visits)
            "unused_candidates": len(unused) - len(left_out),
            "unused_not_eligible_to_enter": sum(
                1 for stop in unused if not stop.must_visit and not (stop.may_enter and stop.point is not None)
            ),
            "unused_eligible_outside_working_set": sum(
                1 for stop in unused if not stop.must_visit and stop.may_enter and stop.point is not None
            )
            - len(working),
            "unused_in_working_set": len(working),
            "move_limit": move_limit,
            "moves_applied": applied,
            "move_limit_reached": int(applied >= move_limit),
            "evaluation_budget_exhausted": int(exhausted),
        },
    )
