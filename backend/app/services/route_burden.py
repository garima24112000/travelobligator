"""Route-burden quality signals (Section 203C.2B).

Full routing coverage only says the routing provider answered. It does not
say the day is a sensible amount of travel. This module reads the routing
provider's OWN leg results (`route_feasibility_report`), after the bounded
per-leg mode adaptation, and reports per day:

  * the WALKING burden -- walk legs only (duration, distance, longest leg);
  * the total TRANSFER time and distance across every mode, for information;
  * the longest vehicle transfer.

A day is a long-travel day when its walking exceeds the pace's walking total
or one walk leg exceeds the walking-leg limit, or when its transfers are
unreasonable in their own right (one vehicle transfer beyond the drive-leg
limit, or all transfers together beyond the day transfer limit). A leg that
was too long to walk but has a reasonable factual driving route does NOT
make its day a long-travel day.

Nothing here estimates anything: a leg without provider distance/duration
is simply not counted, and a leg's mode is the mode the provider routed it
in. Thresholds are settings (`ROUTE_BURDEN_*`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.core.config import Settings, get_settings
from app.models.planning_state import PlanningState, TripPace
from app.models.routing import TRANSFER_MODE_WALK, RouteLegFeasibility, leg_mode
from app.services.pace_targets import pace_of

LONG_TRAVEL_DAY = "LONG_TRAVEL_DAY"


@dataclass(frozen=True)
class DayRouteBurden:
    day_number: int
    required_legs: int
    routed_legs: int
    # Every mode together (information, and the day transfer limit).
    total_duration_seconds: float
    total_distance_meters: float
    max_leg_duration_seconds: float
    max_leg_distance_meters: float
    # Walk legs only.
    walking_duration_seconds: float
    walking_distance_meters: float
    max_walk_leg_duration_seconds: float
    max_walk_leg_distance_meters: float
    # Vehicle transfers.
    drive_legs: int
    max_drive_leg_duration_seconds: float
    over_day_limit: bool  # walking total beyond the pace's walking limit
    over_leg_limit: bool  # one walk leg beyond the walking-leg limit
    over_transfer_limit: bool  # transfers unreasonable regardless of mode

    @property
    def excessive_walking(self) -> bool:
        return self.over_day_limit or self.over_leg_limit

    @property
    def long_route(self) -> bool:
        return self.excessive_walking or self.over_transfer_limit


def max_day_seconds(pace: TripPace, settings: Settings | None = None) -> int:
    resolved = settings or get_settings()
    return {
        TripPace.RELAXED: resolved.route_burden_max_day_seconds_relaxed,
        TripPace.BALANCED: resolved.route_burden_max_day_seconds_balanced,
        TripPace.PACKED: resolved.route_burden_max_day_seconds_packed,
    }[pace]


def burden_of_legs(
    day_number: int,
    required_legs: int,
    legs: Sequence[RouteLegFeasibility],
    pace: TripPace,
    settings: Settings | None = None,
) -> DayRouteBurden:
    """The burden of one day given its (final-mode) legs."""
    resolved = settings or get_settings()
    routed = [leg for leg in legs if leg.duration_seconds is not None and leg.distance_meters is not None]
    walk = [leg for leg in routed if leg_mode(leg.mode) == TRANSFER_MODE_WALK]
    drive = [leg for leg in routed if leg_mode(leg.mode) != TRANSFER_MODE_WALK]

    total_duration = sum(leg.duration_seconds for leg in routed)
    walking_duration = sum(leg.duration_seconds for leg in walk)
    max_walk_leg = max((leg.duration_seconds for leg in walk), default=0.0)
    max_drive_leg = max((leg.duration_seconds for leg in drive), default=0.0)
    return DayRouteBurden(
        day_number=day_number,
        required_legs=required_legs,
        routed_legs=len(routed),
        total_duration_seconds=total_duration,
        total_distance_meters=sum(leg.distance_meters for leg in routed),
        max_leg_duration_seconds=max((leg.duration_seconds for leg in routed), default=0.0),
        max_leg_distance_meters=max((leg.distance_meters for leg in routed), default=0.0),
        walking_duration_seconds=walking_duration,
        walking_distance_meters=sum(leg.distance_meters for leg in walk),
        max_walk_leg_duration_seconds=max_walk_leg,
        max_walk_leg_distance_meters=max((leg.distance_meters for leg in walk), default=0.0),
        drive_legs=len(drive),
        max_drive_leg_duration_seconds=max_drive_leg,
        over_day_limit=walking_duration > max_day_seconds(pace, resolved),
        over_leg_limit=max_walk_leg > resolved.route_burden_max_leg_seconds,
        over_transfer_limit=(
            max_drive_leg > resolved.route_burden_max_drive_leg_seconds
            or (bool(drive) and total_duration > resolved.route_burden_max_day_transfer_seconds)
        ),
    )


def day_route_burdens(planning_state: PlanningState) -> list[DayRouteBurden]:
    plan = planning_state.experience_plan
    if plan is None:
        return []
    settings = get_settings()
    pace = pace_of(planning_state)

    report = planning_state.route_feasibility_report
    legs = {(leg.from_experience_id, leg.to_experience_id): leg for leg in (report.legs if report else [])}

    burdens: list[DayRouteBurden] = []
    for day in plan.daily_plans:
        stops = day.experiences
        day_legs = [
            leg
            for a, b in zip(stops, stops[1:])
            if (leg := legs.get((a.experience_id, b.experience_id))) is not None
        ]
        burdens.append(burden_of_legs(day.day_number, max(0, len(stops) - 1), day_legs, pace, settings))
    return burdens


# -- Q4: route severity, judged leg by leg ------------------------------------------------
#
# A day is classified from the routing provider's own legs, with the limits
# above and nothing else. Evidence is used at the LEG level: an unavailable
# leg never erases the fact that another leg of the same day is verified and
# too long, and it is never read as proof that the day is feasible or not.

SEVERITY_NORMAL = "normal"
# Verified and within the limits, but the day needs a vehicle transfer or
# more walking than the most relaxed pace allows. Reported, never repaired.
SEVERITY_ELEVATED = "elevated"
# The VERIFIED legs alone already exceed a limit (`DayRouteBurden.long_route`).
SEVERITY_SEVERE = "severe"
# Not severe on the verified legs, and at least one required leg has no
# provider route: feasibility is unknown.
SEVERITY_UNVERIFIED = "unverified"

# The provider's definitive "this cannot be routed" answers (fixed codes).
_DEFINITIVE_FAILURES = frozenset({"no_route", "unroutable_endpoint"})


def is_verified_leg(leg: RouteLegFeasibility | None) -> bool:
    """A leg with the provider's own distance and duration."""
    return leg is not None and leg.duration_seconds is not None and leg.distance_meters is not None


def is_definitive_failure(leg: RouteLegFeasibility | None) -> bool:
    return leg is not None and not is_verified_leg(leg) and leg.failure_reason in _DEFINITIVE_FAILURES


@dataclass(frozen=True)
class DayRouteAssessment:
    day_number: int
    severity: str
    burden: DayRouteBurden
    # Required legs without a provider route (missing, failed or unavailable).
    unverified_legs: int
    # ... of which the provider definitively refused (no route by the
    # supported modes), as opposed to an unavailable or failed request.
    definitive_failures: int
    # Verified legs whose provider route includes a ferry.
    ferry_legs: int
    # Positions (in day order) of the verified legs that make the day severe.
    offending_legs: tuple[int, ...]

    @property
    def verified_severe(self) -> bool:
        return self.burden.long_route

    @property
    def fully_verified(self) -> bool:
        return self.unverified_legs == 0


def assess_legs(
    day_number: int,
    legs: Sequence[RouteLegFeasibility | None],
    pace: TripPace,
    settings: Settings | None = None,
) -> DayRouteAssessment:
    """The assessment of one day from its required legs in day order (None
    for a leg nobody has routed). The burden counts verified legs only, so
    on a partially routed day it is a lower bound: a day it calls severe is
    severe whatever the missing legs turn out to be."""
    resolved = settings or get_settings()
    verified = [leg for leg in legs if is_verified_leg(leg)]
    burden = burden_of_legs(day_number, len(legs), verified, pace, resolved)
    unverified = len(legs) - len(verified)

    offending: list[int] = []
    for index, leg in enumerate(legs):
        if not is_verified_leg(leg):
            continue
        limit = (
            resolved.route_burden_max_leg_seconds
            if leg_mode(leg.mode) == TRANSFER_MODE_WALK
            else resolved.route_burden_max_drive_leg_seconds
        )
        if leg.duration_seconds > limit:
            offending.append(index)
    if burden.long_route and not offending:
        # The day's total is over a limit with no single leg over one: the
        # longest verified leg is where a change helps most.
        offending.append(
            max(
                (index for index, leg in enumerate(legs) if is_verified_leg(leg)),
                key=lambda index: (legs[index].duration_seconds, -index),
            )
        )

    if burden.long_route:
        severity = SEVERITY_SEVERE
    elif unverified:
        severity = SEVERITY_UNVERIFIED
    elif burden.drive_legs or burden.walking_duration_seconds > resolved.route_burden_max_day_seconds_relaxed:
        severity = SEVERITY_ELEVATED
    else:
        severity = SEVERITY_NORMAL
    return DayRouteAssessment(
        day_number=day_number,
        severity=severity,
        burden=burden,
        unverified_legs=unverified,
        definitive_failures=sum(1 for leg in legs if is_definitive_failure(leg)),
        ferry_legs=sum(1 for leg in verified if leg.includes_ferry is True),
        offending_legs=tuple(offending),
    )


def assess_days(planning_state: PlanningState) -> list[DayRouteAssessment]:
    """`assess_legs` for every day of the stored plan, from the stored route
    report. Read-only; a day with fewer than two stops has no legs and is
    normal."""
    plan = planning_state.experience_plan
    if plan is None:
        return []
    settings = get_settings()
    pace = pace_of(planning_state)
    report = planning_state.route_feasibility_report
    known = {(leg.from_experience_id, leg.to_experience_id): leg for leg in (report.legs if report else [])}
    return [
        assess_legs(
            day.day_number,
            [known.get((a.experience_id, b.experience_id)) for a, b in zip(day.experiences, day.experiences[1:])],
            pace,
            settings,
        )
        for day in plan.daily_plans
    ]
