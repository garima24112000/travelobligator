"""Three bounded, deterministic planner passes over grouped days (M2 Phase 3;
Final Batch 1).

All run after the days are grouped and before the experience items, routing
and validation are built, so routing is still requested once, for the final
stops. All are pure: no provider, routing or model call, and no place name.
They never touch a plan with an active user lock (the caller checks).

1. **Known closures (Workstream B).** A stop whose OWN provider opening hours
   (`services/opening_hours`) say it is closed on its scheduled date is, in
   this order and at most once:

     * moved to a day on which those hours do not say it is closed, if that day
       has room and stays within the validator's spread boundary
       (`keeps_day_within_spread`);
     * swapped with a stop of such a day that is not closed on this date, when
       both days stay within the boundary;
     * replaced (not a must-visit) by the most useful unused candidate
       (`candidate_usefulness`) that is not closed on this date, not a suspected
       duplicate, keeps the day within the boundary and is at most one internal
       quality tier lower;
     * dropped (not a must-visit) when the day keeps another stop and the plan
       keeps its minimum of meaningful stops -- fewer worthwhile stops beat a
       visit that cannot happen;
     * otherwise kept, and the validator reports it (a must-visit is never
       removed).

   Only a CLOSED reading acts. `unknown` -- no hours listed, a value not
   understood, holiday data missing for a `PH` rule -- is never a reason to
   move or remove a stop: missing evidence is reported, not acted on.

2. **Remote days without a reason (Workstream D).** A day whose located stops
   ALL lie beyond the planner's own pool reach (`_pool_reach`, the boundary
   `services/remote_days` already reports with) costs a journey there and back
   that nothing has checked. When the plan's supply is healthy (the caller
   passes it) and nothing on the day is a reason for it -- a grounded
   must-visit, or the only scheduled cover of a requested interest -- the day is
   rebuilt from unused candidates WITHIN reach: most useful first, each keeping
   the day within the spread boundary, none more than one quality tier below
   the weakest stop it replaces, none closed on the date. A day that cannot be
   rebuilt with at least one such stop is left as it is, and reported.
   Final Batch 1: this applies to a plan the reasoning model chose as well
   (the caller decides), and a rebuilt day holds as many stops as it had or
   the pace's per-day minimum, whichever is more (never above the pace cap) --
   a single isolated stop is not rebuilt as another single-stop day.

3. **Thin days (Final Batch 1).** A day holding fewer stops than the pace's
   per-day minimum (`pace_targets.MIN_STOPS_PER_DAY`, the validator's own
   thin-day rule) while supply is healthy is topped up to that minimum from
   unused candidates within reach: most useful first, each keeping the day
   within the spread boundary and no more than one tier below the day's
   weakest stop, none closed on the date, none a duplicate or a record of a
   site already scheduled. Nothing below that standard is added -- a lighter
   day beats filler. A day whose stops all lie away from the trip's area (a
   requested excursion) is left as it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from app.services import opening_hours as hours
from app.services.day_order_heuristics import keeps_day_within_spread

Poi = dict[str, Any]

CLOSURE_MOVED = "moved"
CLOSURE_SWAPPED = "swapped"
CLOSURE_REPLACED = "replaced"
CLOSURE_DROPPED = "dropped"
CLOSURE_KEPT = "kept"
REMOTE_REBUILT = "rebuilt"
REMOTE_KEPT_REASON = "kept_with_reason"
REMOTE_KEPT_NO_ALTERNATIVE = "kept_no_alternative"
THIN_FILLED = "thin_filled"
THIN_PARTLY_FILLED = "thin_partly_filled"
THIN_KEPT_NO_CANDIDATE = "thin_kept_no_candidate"
THIN_KEPT_REMOTE = "thin_kept_remote"


@dataclass(frozen=True)
class Helpers:
    """The planner's own accessors (passed in, so this module stays pure)."""

    coordinates: Callable[[Poi], Any]
    tier_rank: Callable[[Poi], int]  # higher is better
    low_value: Callable[[Poi], bool]
    usefulness_key: Callable[[Poi], Any]  # sorts the most useful first
    must_visit: Callable[[Poi], bool]
    matched_interests: Callable[[Poi], frozenset[str]]
    suspected_duplicate: Callable[[Poi, list[Poi]], bool]
    # The schedule-diversity HARD rule (marketplaces per day): stops beyond the
    # cap. A pass never leaves a day with more of it than the day had before.
    market_excess: Callable[[list[Poi]], int] = lambda stops: 0
    # Final Batch 1 (C): whether a place's own listed hours rule a visit out on
    # a date. None: closed all day (`closed_on`); the planner passes
    # `visit_schedule.unvisitable_on`, which also counts hours that leave no
    # visit of its estimated length inside the pace's day window.
    closed: Callable[[Poi, date, frozenset[date] | None], bool] | None = None


def _closed(poi: Poi, day: date, holidays: frozenset[date] | None, helpers: Helpers) -> bool:
    return helpers.closed(poi, day, holidays) if helpers.closed is not None else closed_on(poi, day, holidays)


@dataclass
class PassReport:
    """What a pass did, as fixed labels and counts (diagnostics; nothing reads it to decide)."""

    actions: list[dict[str, Any]] = field(default_factory=list)

    def note(self, **entry: Any) -> None:
        self.actions.append(entry)


def day_hours(poi: Poi, day: date, holidays: frozenset[date] | None) -> hours.DayHours:
    return hours.hours_on((poi.get("venue_tags") or {}).get("opening_hours"), day, holidays)


def closed_on(poi: Poi, day: date, holidays: frozenset[date] | None) -> bool:
    return day_hours(poi, day, holidays).status == hours.CLOSED


def _points(stops: list[Poi], helpers: Helpers) -> list[Any]:
    return [helpers.coordinates(stop) for stop in stops]


def _fits(stops: list[Poi], helpers: Helpers, before: list[Poi] | None = None) -> bool:
    """A day (as it would be) within the spread boundary, and with no more
    hard marketplace excess than it had `before` (none when it is new)."""
    if helpers.market_excess(stops) > helpers.market_excess(before or []):
        return False
    if len(stops) < 2:
        return True
    return keeps_day_within_spread(_points(stops[:-1], helpers), helpers.coordinates(stops[-1]))


def resolve_known_closures(
    day_groups: list[list[Poi]],
    pool: list[Poi],
    helpers: Helpers,
    *,
    start_date: date,
    holidays: frozenset[date] | None,
    max_per_day: int,
    minimum_stops: int,
) -> tuple[list[list[Poi]], PassReport]:
    days = [list(group) for group in day_groups]
    report = PassReport()
    dates = [start_date + timedelta(days=index) for index in range(len(days))]
    used = {id(poi) for group in days for poi in group}

    def meaningful() -> int:
        return sum(1 for group in days for poi in group if not helpers.low_value(poi))

    for day_index in range(len(days)):
        position = 0
        while position < len(days[day_index]):
            stop = days[day_index][position]
            if not _closed(stop, dates[day_index], holidays, helpers):
                position += 1
                continue
            action = _relocate(days, day_index, position, dates, holidays, helpers, max_per_day)
            if action is None and not helpers.must_visit(stop):
                action = _replace(days, day_index, position, pool, used, dates[day_index], holidays, helpers)
            if action is None and not helpers.must_visit(stop):
                others = len(days[day_index]) > 1
                if others and meaningful() - (0 if helpers.low_value(stop) else 1) >= minimum_stops:
                    days[day_index].pop(position)
                    used.discard(id(stop))
                    action = CLOSURE_DROPPED
            report.note(day=day_index + 1, action=action or CLOSURE_KEPT, must_visit=helpers.must_visit(stop))
            if action in (CLOSURE_MOVED, CLOSURE_DROPPED):
                continue  # the list shifted; the same position holds the next stop
            position += 1
    return days, report


def _relocate(
    days: list[list[Poi]],
    day_index: int,
    position: int,
    dates: list[date],
    holidays: frozenset[date] | None,
    helpers: Helpers,
    max_per_day: int,
) -> str | None:
    stop = days[day_index][position]
    remaining = days[day_index][:position] + days[day_index][position + 1:]
    for other in range(len(days)):
        if other == day_index or _closed(stop, dates[other], holidays, helpers) or not remaining:
            continue  # a move never leaves its day empty (a swap may still help)
        if len(days[other]) < max_per_day and _fits([*days[other], stop], helpers, days[other]):
            days[day_index].pop(position)
            days[other].append(stop)
            return CLOSURE_MOVED
    for other in range(len(days)):
        if other == day_index or _closed(stop, dates[other], holidays, helpers):
            continue
        for index, partner in enumerate(days[other]):
            if _closed(partner, dates[day_index], holidays, helpers):
                continue
            target = days[other][:index] + days[other][index + 1:] + [stop]
            source = [*remaining, partner]
            if _fits(target, helpers, days[other]) and _fits(source, helpers, days[day_index]):
                days[other][index] = stop
                days[day_index][position] = partner
                return CLOSURE_SWAPPED
    return None


def _replace(
    days: list[list[Poi]],
    day_index: int,
    position: int,
    pool: list[Poi],
    used: set[int],
    day: date,
    holidays: frozenset[date] | None,
    helpers: Helpers,
) -> str | None:
    stop = days[day_index][position]
    remaining = days[day_index][:position] + days[day_index][position + 1:]
    scheduled = [poi for group in days for poi in group if poi is not stop]
    floor = helpers.tier_rank(stop) - 1
    candidates = sorted(
        (
            poi for poi in pool
            if id(poi) not in used
            and not helpers.low_value(poi)
            and helpers.coordinates(poi) is not None
            and helpers.tier_rank(poi) >= floor
            and not _closed(poi, day, holidays, helpers)
            and not helpers.suspected_duplicate(poi, scheduled)
        ),
        key=helpers.usefulness_key,
    )
    for candidate in candidates:
        if _fits([*remaining, candidate], helpers, days[day_index]):
            days[day_index][position] = candidate
            used.discard(id(stop))
            used.add(id(candidate))
            return CLOSURE_REPLACED
    return None


def resolve_unreasoned_remote_days(
    day_groups: list[list[Poi]],
    pool: list[Poi],
    helpers: Helpers,
    *,
    within_reach: Callable[[Poi], bool],
    requested_interests: frozenset[str],
    start_date: date,
    holidays: frozenset[date] | None,
    supply_healthy: bool,
    minimum_stops: int,
    minimum_per_day: int = 0,
    max_per_day: int | None = None,
) -> tuple[list[list[Poi]], PassReport]:
    days = [list(group) for group in day_groups]
    report = PassReport()
    if not supply_healthy:
        return days, report
    used = {id(poi) for group in days for poi in group}
    for day_index, group in enumerate(days):
        # As many stops as the day had, or the pace minimum -- never above the cap.
        target = max(len(group), minimum_per_day)
        if max_per_day is not None:
            target = min(target, max(max_per_day, len(group)))
        located = [poi for poi in group if helpers.coordinates(poi) is not None]
        if not located or any(within_reach(poi) for poi in located):
            continue
        others = [poi for other, stops in enumerate(days) if other != day_index for poi in stops]
        covered_elsewhere = frozenset().union(*(helpers.matched_interests(poi) for poi in others)) if others else frozenset()
        sole_cover = any((helpers.matched_interests(poi) & requested_interests) - covered_elsewhere for poi in group)
        if any(helpers.must_visit(poi) for poi in group) or sole_cover:
            report.note(day=day_index + 1, action=REMOTE_KEPT_REASON)
            continue
        floor = min(helpers.tier_rank(poi) for poi in group) - 1
        day = start_date + timedelta(days=day_index)
        scheduled = others
        candidates = sorted(
            (
                poi for poi in pool
                if id(poi) not in used
                and within_reach(poi)
                and not helpers.low_value(poi)
                and helpers.coordinates(poi) is not None
                and helpers.tier_rank(poi) >= floor
                and not _closed(poi, day, holidays, helpers)
            ),
            key=helpers.usefulness_key,
        )
        rebuilt: list[Poi] = []
        for candidate in candidates:
            if len(rebuilt) >= target:
                break
            if helpers.suspected_duplicate(candidate, [*scheduled, *rebuilt]):
                continue
            if _fits([*rebuilt, candidate], helpers, group):
                rebuilt.append(candidate)
        meaningful = sum(1 for stops in days for poi in stops if not helpers.low_value(poi))
        after = meaningful - sum(1 for poi in group if not helpers.low_value(poi)) + len(rebuilt)
        if not rebuilt or (len(rebuilt) < len(group) and after < minimum_stops):
            report.note(day=day_index + 1, action=REMOTE_KEPT_NO_ALTERNATIVE)
            continue
        for poi in group:
            used.discard(id(poi))
        for poi in rebuilt:
            used.add(id(poi))
        days[day_index] = rebuilt
        report.note(day=day_index + 1, action=REMOTE_REBUILT, replaced=len(group), stops=len(rebuilt))
    return days, report


def fill_thin_days(
    day_groups: list[list[Poi]],
    pool: list[Poi],
    helpers: Helpers,
    *,
    within_reach: Callable[[Poi], bool],
    start_date: date,
    holidays: frozenset[date] | None,
    supply_healthy: bool,
    minimum_per_day: int,
) -> tuple[list[list[Poi]], PassReport]:
    """Pass 3 (see the module docstring): tops a thin day up to the pace's
    per-day minimum with useful places within reach, or leaves it lighter."""
    days = [list(group) for group in day_groups]
    report = PassReport()
    if not supply_healthy:
        return days, report
    used = {id(poi) for group in days for poi in group}
    for day_index, group in enumerate(days):
        if not group or len(group) >= minimum_per_day:
            continue  # an empty day has its own fill; a full one needs nothing
        located = [poi for poi in group if helpers.coordinates(poi) is not None]
        if located and not any(within_reach(poi) for poi in located):
            report.note(day=day_index + 1, action=THIN_KEPT_REMOTE)
            continue
        floor = min(helpers.tier_rank(poi) for poi in group) - 1
        day = start_date + timedelta(days=day_index)
        candidates = sorted(
            (
                poi for poi in pool
                if id(poi) not in used
                and within_reach(poi)
                and not helpers.low_value(poi)
                and helpers.coordinates(poi) is not None
                and helpers.tier_rank(poi) >= floor
                and not _closed(poi, day, holidays, helpers)
            ),
            key=helpers.usefulness_key,
        )
        added = 0
        for candidate in candidates:
            if len(days[day_index]) >= minimum_per_day:
                break
            scheduled = [poi for stops in days for poi in stops]
            if helpers.suspected_duplicate(candidate, scheduled):
                continue
            if _fits([*days[day_index], candidate], helpers, days[day_index]):
                days[day_index].append(candidate)
                used.add(id(candidate))
                added += 1
        action = (
            THIN_FILLED if len(days[day_index]) >= minimum_per_day
            else THIN_PARTLY_FILLED if added
            else THIN_KEPT_NO_CANDIDATE
        )
        report.note(day=day_index + 1, action=action, added=added)
    return days, report
