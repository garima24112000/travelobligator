"""Estimated visit times on the final routed days (Final Batch 1, C).

A day becomes a timeline: it starts at the pace's day window (a planning
assumption: the plan has no place to stay, so the day starts AT its first
stop), each stop takes an ESTIMATED visit length for its kind of place, and
between stops the routing provider's verified leg duration is added. A place
whose own listed hours open later is waited for; nothing is ever compressed to
fit. Times are local wall-clock times at the destination on the day's own
date -- the trip dates are destination dates; no time zone is stored or
converted.

What is evidence and what is not:

  * listed hours come only from the place's own record (`services/opening_hours`);
    unknown hours stay unknown -- such a stop is timed, never called open;
  * travel time comes only from a VERIFIED leg. After a leg with no verified
    route no further time is estimated for that day (`not_timed`): no travel
    time, mode or service is assumed;
  * the visit lengths and the day windows below are planning ESTIMATES, shown
    as such, never a fact about a place.

Repair (before the plan is final, once per day, never under an active user
lock): a stop whose listed hours leave no time for its visit at its place in
the timeline (`outside_listed_hours`, or `closed_on_date` for one kept on a
closing date) is first resolved by REORDERING the day -- the orders are
compared with the verified legs where known and a straight-line estimate
otherwise, which only ORDERS them; the chosen order is then routed (the
existing day-route allowance) and accepted only when every leg is verified, no
known conflict remains on the verified times, and the day's route burden is no
worse. Otherwise the stop is REMOVED -- never a must-visit, never below the
plan's minimum of meaningful stops (R), never a day's only stop; an interior
stop only when the leg that then joins its neighbours is verified.
Otherwise it is kept and the validator discloses it. A day that runs past its
window is disclosed, never trimmed: the lengths are estimates.

The pre-routing planner passes read `unvisitable_on` (hours that leave no
visit inside the day window at all), so most conflicts are prevented before
routing; what remains here is order-dependent.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.core import performance
from app.core.config import get_settings
from app.core.provider_usage import GenerationProviderContext
from app.models.common import ProviderStatus
from app.models.planning_state import DailyPlan, DayVisitSchedule, ExperienceItem, PlanningState, TripPace
from app.models.routing import RouteLegFeasibility
from app.services import opening_hours as hours
from app.services import place_taxonomy as taxonomy
from app.services import route_burden
from app.services.day_order_heuristics import path_length_km
from app.services.day_rationale import clear_day_rationale, deterministic_day_summary
from app.services.must_visit_matching import must_visit_place_ids
from app.services.pace_targets import pace_of, pace_targets_for
from app.services.venue_evidence import records_by_place_id

logger = logging.getLogger(__name__)

# The day window assumed for each pace (minutes from midnight, local time) --
# a planning assumption, shown as such.
DAY_WINDOWS: dict[TripPace, tuple[int, int]] = {
    TripPace.RELAXED: (10 * 60, 17 * 60 + 30),
    TripPace.BALANCED: (9 * 60 + 30, 18 * 60),
    TripPace.PACKED: (9 * 60, 19 * 60),
}
# Estimated visit length by kind of place (the taxonomy's primary category).
_VISIT_MINUTES: dict[str, int] = {
    taxonomy.MUSEUM: 120,
    taxonomy.ART_CULTURE: 90,
    taxonomy.LANDMARK: 90,
    taxonomy.ENTERTAINMENT: 120,
    taxonomy.PARK_NATURE: 75,
    taxonomy.HISTORIC: 60,
    taxonomy.WATERFRONT: 60,
    taxonomy.FOOD_MARKET: 60,
    taxonomy.CULINARY: 60,
    taxonomy.SHOPPING: 60,
    taxonomy.NEIGHBORHOOD_AREA: 60,
    taxonomy.GENERAL_ATTRACTION: 60,
    taxonomy.ARCHITECTURE: 45,
    taxonomy.RELIGIOUS: 45,
    taxonomy.VIEWPOINT: 30,
    taxonomy.LOW_VALUE_OBJECT: 15,
}
DEFAULT_VISIT_MINUTES = 60
# ORDERING estimate only (comparing candidate orders before one is routed):
# straight-line distance at a walking speed with a detour factor. Never a
# shown or accepted travel time.
_ORDERING_KMH = 4.5
_ORDERING_DETOUR = 1.3
# Days longer than this are not reordered (5! = 120 orders at most).
_MAX_REORDER_STOPS = 5
_LAST_MINUTE = 24 * 60 - 1

FITS = "fits_listed_hours"
UNKNOWN_HOURS = "hours_unknown"
OUTSIDE_HOURS = "outside_listed_hours"
CLOSED_ON_DATE = "closed_on_date"
NOT_TIMED = "not_timed"
CONFLICTS = frozenset({OUTSIDE_HOURS, CLOSED_ON_DATE})

TIMED = "timed"
PARTLY_TIMED = "partly_timed"
TRAVEL_NOT_VERIFIED = "travel_not_verified"
PAST_MIDNIGHT = "past_midnight"

ACTION_REORDERED = "reordered"
ACTION_REMOVED = "removed"
ACTION_KEPT = "kept"

_SEVERITY_RANK = {
    route_burden.SEVERITY_NORMAL: 0,
    route_burden.SEVERITY_ELEVATED: 1,
    route_burden.SEVERITY_UNVERIFIED: 2,
    route_burden.SEVERITY_SEVERE: 3,
}


@dataclass(frozen=True)
class StopTime:
    start: int | None
    end: int | None
    duration: int
    wait: int
    status: str


@dataclass(frozen=True)
class DayTimes:
    stops: tuple[StopTime, ...]
    ends_at: int | None
    exceeds_window: bool
    untimed_reason: str | None

    @property
    def conflicts(self) -> tuple[int, ...]:
        return tuple(index for index, stop in enumerate(self.stops) if stop.status in CONFLICTS)


def clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def visit_minutes(record: dict[str, Any] | None, item: ExperienceItem | None = None) -> int:
    """The ESTIMATED visit length of a place, by its kind (provider tags)."""
    tags = (record or {}).get("provider_tags")
    category = (record or {}).get("category") or (item.category if item is not None else None)
    if not tags and not category and item is not None and item.normalized_category:
        return _VISIT_MINUTES.get(item.normalized_category, DEFAULT_VISIT_MINUTES)
    primary = taxonomy.classify_place(tags, category).primary_category
    return _VISIT_MINUTES.get(primary, DEFAULT_VISIT_MINUTES)


def _fit(reading: hours.DayHours, arrival: int, duration: int) -> tuple[int, int, str]:
    """(start, wait, status) for a visit arriving at `arrival`."""
    if reading.status == hours.OPEN:
        for opens, closes in reading.intervals:
            start = max(arrival, opens)
            if start + duration <= closes:
                return start, start - arrival, FITS
        return arrival, 0, OUTSIDE_HOURS
    if reading.status == hours.CLOSED:
        return arrival, 0, CLOSED_ON_DATE
    return arrival, 0, UNKNOWN_HOURS


def day_times(
    readings: list[hours.DayHours],
    durations: list[int],
    travel_minutes: list[float | None],
    window: tuple[int, int],
) -> DayTimes:
    """The timeline of one day (see the module docstring). `travel_minutes[i]`
    is the leg before stop i+1; None when that leg has no verified route."""
    clock_now = window[0]
    stops: list[StopTime] = []
    untimed: str | None = None
    for index, (reading, duration) in enumerate(zip(readings, durations)):
        if index > 0 and untimed is None:
            minutes = travel_minutes[index - 1]
            if minutes is None:
                untimed = TRAVEL_NOT_VERIFIED
            else:
                clock_now += int(-(-minutes // 1))  # whole minutes, rounded up
        if untimed is None and clock_now + duration > _LAST_MINUTE:
            untimed = PAST_MIDNIGHT
        if untimed is not None:
            stops.append(StopTime(start=None, end=None, duration=duration, wait=0, status=NOT_TIMED))
            continue
        start, wait, status = _fit(reading, clock_now, duration)
        if start + duration > _LAST_MINUTE:
            untimed = PAST_MIDNIGHT
            stops.append(StopTime(start=None, end=None, duration=duration, wait=0, status=NOT_TIMED))
            continue
        stops.append(StopTime(start=start, end=start + duration, duration=duration, wait=wait, status=status))
        clock_now = start + duration
    ends = [stop.end for stop in stops if stop.end is not None]
    ends_at = max(ends) if ends else None
    return DayTimes(
        stops=tuple(stops),
        ends_at=ends_at,
        exceeds_window=ends_at is not None and ends_at > window[1],
        untimed_reason=untimed,
    )


def unvisitable_on(
    record: dict[str, Any], day: date, holidays: frozenset[date] | None, pace: TripPace
) -> bool:
    """True when the place's own listed hours leave NO visit of its estimated
    length inside the pace's day window that date (closed all day included).
    Order-independent; unknown hours are never unvisitable."""
    reading = hours.hours_on((record.get("venue_tags") or {}).get("opening_hours"), day, holidays)
    if reading.status == hours.CLOSED:
        return True
    if reading.status != hours.OPEN:
        return False
    window_start, window_end = DAY_WINDOWS[pace]
    duration = visit_minutes(record)
    return not any(
        min(closes, window_end) - max(opens, window_start) >= duration for opens, closes in reading.intervals
    )


def _verified(leg: RouteLegFeasibility | None) -> bool:
    return (
        leg is not None
        and leg.status == ProviderStatus.SUCCESS
        and leg.duration_seconds is not None
        and leg.distance_meters is not None
    )


class _Scheduler:
    def __init__(
        self,
        planning_state: PlanningState,
        route_feasibility_service: Any,
        provider_context: GenerationProviderContext | None,
    ) -> None:
        self.state = planning_state
        self.routing = route_feasibility_service
        self.context = provider_context
        self.pace = pace_of(planning_state)
        self.window = DAY_WINDOWS[self.pace]
        self.records = records_by_place_id(planning_state)
        self.holidays = hours.holiday_dates(planning_state)
        self.must_visit = must_visit_place_ids(planning_state)
        self.minimum_stops = pace_targets_for(planning_state).minimum_useful

    # -- reading -----------------------------------------------------------------

    def _record(self, item: ExperienceItem) -> dict[str, Any] | None:
        return self.records.get(str(item.provider_place_id)) if item.provider_place_id else None

    def _stored_legs(self, stops: list[ExperienceItem]) -> list[RouteLegFeasibility | None]:
        report = self.state.route_feasibility_report
        by_pair = {(leg.from_experience_id, leg.to_experience_id): leg for leg in (report.legs if report else [])}
        return [by_pair.get((a.experience_id, b.experience_id)) for a, b in zip(stops, stops[1:])]

    def _times(self, day: DailyPlan, stops: list[ExperienceItem], travel: list[float | None]) -> DayTimes:
        readings, durations = [], []
        for item in stops:
            record = self._record(item)
            readings.append(hours.hours_on(((record or {}).get("venue_tags") or {}).get("opening_hours"), day.date,
                                           self.holidays))
            durations.append(visit_minutes(record, item))
        return day_times(readings, durations, travel, self.window)

    @staticmethod
    def _verified_minutes(legs: list[RouteLegFeasibility | None]) -> list[float | None]:
        return [leg.duration_seconds / 60.0 if _verified(leg) else None for leg in legs]

    def _ordering_minutes(self, a: ExperienceItem, b: ExperienceItem) -> float:
        known = self._stored_legs([a, b])[0]
        if _verified(known):
            return known.duration_seconds / 60.0
        if a.coordinates is None or b.coordinates is None:
            return float(24 * 60)
        return path_length_km([a.coordinates, b.coordinates]) * _ORDERING_DETOUR / _ORDERING_KMH * 60.0

    # -- repair ------------------------------------------------------------------

    def _route(self, stops: list[ExperienceItem]) -> list[RouteLegFeasibility] | None:
        try:
            legs = self.routing.route_day_legs(stops, self.context)
        except Exception:  # noqa: BLE001 - an unverified order is simply not taken
            logger.warning("Routing during visit scheduling failed; the day is left as it is.")
            return None
        return legs if len(legs) == len(stops) - 1 and all(_verified(leg) for leg in legs) else None

    def _no_worse(self, day: DailyPlan, before: list[RouteLegFeasibility | None], after: list) -> bool:
        old = route_burden.assess_legs(day.day_number, before, self.pace)
        new = route_burden.assess_legs(day.day_number, after, self.pace)
        return new.severity != route_burden.SEVERITY_SEVERE and _SEVERITY_RANK[new.severity] <= _SEVERITY_RANK[old.severity]

    def _reorder(self, day: DailyPlan) -> bool:
        stops = list(day.experiences)
        if not 2 <= len(stops) <= _MAX_REORDER_STOPS:
            return False
        best: tuple[tuple[bool, float], list[ExperienceItem]] | None = None
        for order in itertools.permutations(range(len(stops))):
            if list(order) == list(range(len(stops))):
                continue
            ordered = [stops[index] for index in order]
            travel = [self._ordering_minutes(a, b) for a, b in zip(ordered, ordered[1:])]
            estimate = self._times(day, ordered, travel)
            if estimate.conflicts or estimate.untimed_reason:
                continue
            key = (estimate.exceeds_window, sum(travel))
            if best is None or key < best[0]:
                best = (key, ordered)
        if best is None:
            return False
        ordered = best[1]
        legs = self._route(ordered)
        if legs is None or not self._no_worse(day, self._stored_legs(stops), legs):
            return False
        if self._times(day, ordered, self._verified_minutes(legs)).conflicts:
            return False  # the verified times still rule a visit out
        self._apply(day, ordered, legs)
        return True

    def _meaningful(self) -> int:
        plan = self.state.experience_plan
        return sum(1 for d in plan.daily_plans for item in d.experiences if not item.low_value_object)

    def _remove(self, day: DailyPlan, conflicts: tuple[int, ...]) -> bool:
        stops = list(day.experiences)
        stored = self._stored_legs(stops)
        for index in conflicts:
            stop = stops[index]
            if len(stops) <= 1 or (stop.provider_place_id and str(stop.provider_place_id) in self.must_visit):
                continue
            if self._meaningful() - (0 if stop.low_value_object else 1) < self.minimum_stops:
                continue
            remaining = stops[:index] + stops[index + 1:]
            if index == 0:
                legs = stored[1:]
            elif index == len(stops) - 1:
                legs = stored[:-1]
            else:
                routed = self._route(remaining)
                if routed is None:
                    continue
                legs = routed
            self._apply(day, remaining, legs)
            return True
        return False

    def _apply(self, day: DailyPlan, stops: list[ExperienceItem], legs: list) -> None:
        previous = list(day.experiences)
        for position, item in enumerate(stops, start=1):
            item.day_number = day.day_number
            item.stop_order = position
        day.experiences = stops
        # The day changed: its summary is rebuilt and a rationale written for
        # the superseded day is dropped.
        day.goal = deterministic_day_summary([item.name for item in stops])
        clear_day_rationale(day)
        if self.state.route_feasibility_report is not None:
            self.routing.replace_day_legs(self.state.route_feasibility_report, previous, [leg for leg in legs if leg])
        sequencing = self.state.route_aware_sequencing_report
        if sequencing is not None:
            sequencing.suggestions = [s for s in sequencing.suggestions if s.day_index != day.day_number]

    # -- writing -----------------------------------------------------------------

    def write(self, day: DailyPlan) -> DayTimes:
        stops = list(day.experiences)
        times = self._times(day, stops, self._verified_minutes(self._stored_legs(stops)))
        for item, slot in zip(stops, times.stops):
            item.start_time = clock(slot.start) if slot.start is not None else None
            item.end_time = clock(slot.end) if slot.end is not None else None
            item.estimated_duration_minutes = slot.duration
            item.visit_time_status = slot.status
            item.arrival_wait_minutes = slot.wait if slot.start is not None else None
        day.visit_schedule = (
            DayVisitSchedule(
                window_start=clock(self.window[0]),
                window_end=clock(self.window[1]),
                status=PARTLY_TIMED if times.untimed_reason else TIMED,
                ends_at=clock(times.ends_at) if times.ends_at is not None else None,
                exceeds_window=times.exceeds_window,
                untimed_reason=times.untimed_reason,
            )
            if stops
            else None
        )
        return times

    def run(self, *, repair: bool, days: set[int] | None = None) -> None:
        for day in self.state.experience_plan.daily_plans:
            if days is not None and day.day_number not in days:
                continue
            stops = list(day.experiences)
            times = self._times(day, stops, self._verified_minutes(self._stored_legs(stops)))
            if repair and times.conflicts:
                if self._reorder(day):
                    action = ACTION_REORDERED
                elif self._remove(day, times.conflicts):
                    action = ACTION_REMOVED
                else:
                    action = ACTION_KEPT
                performance.count(f"visit_schedule_{action}")
            self.write(day)


def clear_visit_times(planning_state: PlanningState, days: set[int] | None = None) -> None:
    """Removes the estimated times (before a re-route of copied items) -- of
    every day, or of `days` only."""
    plan = planning_state.experience_plan
    for day in plan.daily_plans if plan is not None else []:
        if days is not None and day.day_number not in days:
            continue
        day.visit_schedule = None
        for item in day.experiences:
            item.start_time = item.end_time = None
            item.estimated_duration_minutes = None
            item.visit_time_status = None
            item.arrival_wait_minutes = None


def apply_visit_schedule_safely(
    planning_state: PlanningState,
    route_feasibility_service: Any,
    provider_context: GenerationProviderContext | None = None,
    *,
    repair: bool = True,
    days: set[int] | None = None,
) -> None:
    """Estimated visit times for the final days (or `days` only), with the
    bounded repair (see the module docstring). Never under an active user
    lock (times are still written; nothing moves), and never fails
    generation: on an unexpected error the estimated times are removed and
    the plan is as it was routed."""
    if not get_settings().visit_time_scheduling_enabled or planning_state.experience_plan is None:
        return
    locked = any(lock.is_active for lock in planning_state.user_locks)
    try:
        _Scheduler(planning_state, route_feasibility_service, provider_context).run(
            repair=repair and not locked, days=days
        )
    except Exception:  # noqa: BLE001 - an estimate never fails a plan
        logger.warning("Visit scheduling failed unexpectedly; no visit time is shown.", exc_info=True)
        clear_visit_times(planning_state, days)
