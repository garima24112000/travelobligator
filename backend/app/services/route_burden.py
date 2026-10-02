"""Route-burden quality signals (Section 203C.2B, canary correction).

Full routing coverage only says the routing provider answered. It does not
say the day is a sensible amount of walking. This module reads the routing
provider's OWN leg results (`route_feasibility_report`) and reports, per
day: total walking duration and distance, and the longest single leg.

Nothing here estimates anything: a leg without provider distance/duration
is simply not counted, and no transit, taxi or driving time is ever
substituted for a long walk. A day beyond its pace's total, or with a leg
beyond the leg limit, is flagged so the plan carries an honest long-travel
warning. Thresholds are settings (`ROUTE_BURDEN_*`).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.models.planning_state import PlanningState, TripPace
from app.services.pace_targets import pace_of

LONG_TRAVEL_DAY = "LONG_TRAVEL_DAY"


@dataclass(frozen=True)
class DayRouteBurden:
    day_number: int
    required_legs: int
    routed_legs: int
    total_duration_seconds: float
    total_distance_meters: float
    max_leg_duration_seconds: float
    max_leg_distance_meters: float
    over_day_limit: bool
    over_leg_limit: bool

    @property
    def long_route(self) -> bool:
        return self.over_day_limit or self.over_leg_limit


def max_day_seconds(pace: TripPace, settings: Settings | None = None) -> int:
    resolved = settings or get_settings()
    return {
        TripPace.RELAXED: resolved.route_burden_max_day_seconds_relaxed,
        TripPace.BALANCED: resolved.route_burden_max_day_seconds_balanced,
        TripPace.PACKED: resolved.route_burden_max_day_seconds_packed,
    }[pace]


def day_route_burdens(planning_state: PlanningState) -> list[DayRouteBurden]:
    plan = planning_state.experience_plan
    if plan is None:
        return []
    settings = get_settings()
    day_limit = max_day_seconds(pace_of(planning_state), settings)
    leg_limit = settings.route_burden_max_leg_seconds

    report = planning_state.route_feasibility_report
    legs = {(leg.from_experience_id, leg.to_experience_id): leg for leg in (report.legs if report else [])}

    burdens: list[DayRouteBurden] = []
    for day in plan.daily_plans:
        stops = day.experiences
        routed = [
            leg
            for a, b in zip(stops, stops[1:])
            if (leg := legs.get((a.experience_id, b.experience_id))) is not None
            and leg.duration_seconds is not None
            and leg.distance_meters is not None
        ]
        total_duration = sum(leg.duration_seconds for leg in routed)
        max_leg_duration = max((leg.duration_seconds for leg in routed), default=0.0)
        burdens.append(
            DayRouteBurden(
                day_number=day.day_number,
                required_legs=max(0, len(stops) - 1),
                routed_legs=len(routed),
                total_duration_seconds=total_duration,
                total_distance_meters=sum(leg.distance_meters for leg in routed),
                max_leg_duration_seconds=max_leg_duration,
                max_leg_distance_meters=max((leg.distance_meters for leg in routed), default=0.0),
                over_day_limit=total_duration > day_limit,
                over_leg_limit=max_leg_duration > leg_limit,
            )
        )
    return burdens
