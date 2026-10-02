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
