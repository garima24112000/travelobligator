"""Pace-derived itinerary targets (Section 203C.2B) -- the one definition of
T / R / H used by provider pool sizing, the inventory sufficiency gate and
the usefulness contract.

    T = trip_days * pace_target_per_day     ideal scheduled stops
    R = ceil(0.80 * T)                      minimum useful stops / inventory
    H = ceil(2.25 * T)                      healthy candidate buffer

`PACE_TARGET_PER_DAY` is the same per-day cap `ExperiencePlannerService`
schedules to (relaxed 2, balanced 3, packed 4); a test keeps the two equal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.planning_state import PlanningState, TripPace

PACE_TARGET_PER_DAY: dict[TripPace, int] = {
    TripPace.RELAXED: 2,
    TripPace.BALANCED: 3,
    TripPace.PACKED: 4,
}
MINIMUM_USEFUL_RATIO = 0.80
HEALTHY_BUFFER_RATIO = 2.25


@dataclass(frozen=True)
class PaceTargets:
    trip_days: int
    pace: TripPace
    per_day: int
    target_stops: int  # T
    minimum_useful: int  # R
    healthy_buffer: int  # H


def pace_targets(trip_days: int, pace: TripPace) -> PaceTargets:
    days = max(1, trip_days)
    per_day = PACE_TARGET_PER_DAY[pace]
    target = days * per_day
    return PaceTargets(
        trip_days=days,
        pace=pace,
        per_day=per_day,
        target_stops=target,
        minimum_useful=math.ceil(MINIMUM_USEFUL_RATIO * target),
        healthy_buffer=math.ceil(HEALTHY_BUFFER_RATIO * target),
    )


def trip_days_of(planning_state: PlanningState) -> int:
    trip_request = planning_state.trip_request
    return max(1, (trip_request.end_date - trip_request.start_date).days + 1)


def pace_of(planning_state: PlanningState) -> TripPace:
    profile = planning_state.traveler_profile
    return profile.pace if profile else planning_state.trip_request.pace


def pace_targets_for(planning_state: PlanningState) -> PaceTargets:
    return pace_targets(trip_days_of(planning_state), pace_of(planning_state))
