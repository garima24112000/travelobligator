"""Why a day is geographically dispersed (quality tuning corrections).

A day is DISPERSED when the straight-line path through its scheduled stops,
in their scheduled order, is longer than the validator's boundary
(`day_order_heuristics.day_spread_km` against
`GEOGRAPHIC_SPREAD_THRESHOLD_KM`) -- the one existing measure and the one
existing value. This module adds no threshold and no second measure; it is
not the day's extent (the largest distance between two stops).

Three different things are kept apart, and this module is only the first:

  * geographic dispersion -- where the day's places lie (a straight-line
    proxy; reported as `geographic_dispersion` when the day's routes are
    verified and within their limits, so that an acceptable drive never
    makes a regional day read as a compact one);
  * provider-verified route burden -- `LONG_TRAVEL_DAY` (`route_burden`);
  * unverified route feasibility -- `geographic_spread` / movement data,
    when a leg has no provider route.

`dispersion_cause` says, from stored state only, why a dispersed day is
dispersed. Pure: no provider, routing or model call, no place name, and
nothing here decides what is scheduled.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.common import GeoPoint
from app.models.planning_state import DailyPlan, PlanningState
from app.services import candidate_universe as universe
from app.services.day_order_heuristics import (
    AS_WELL_PLACED_KM,
    GEOGRAPHIC_SPREAD_THRESHOLD_KM,
    day_spread_km,
    keeps_day_within_spread,
    prospective_day_spread_km,
)
from app.services.must_visit_matching import must_visit_place_ids
from app.services.pace_targets import pace_targets_for
from app.services.usefulness_contract import _VIABLE_TIERS, is_meaningful_stop

CATEGORY = "geographic_dispersion"

# The distance is required by what the traveller asked for: the day's
# grounded must-visits are far apart by themselves, they cannot be shared
# out over the trip's days without a dispersed day, and the day's optional
# stops add nothing material. Only then is the finding a note. A must-visit
# merely being ON a dispersed day is never enough.
CAUSE_MANDATORY_DESTINATION = "mandatory_destination"
# A discretionary stop is far from the rest of its day, no unused viable
# candidate would have kept the day within the boundary, and taking the stop
# out would leave the plan below the minimum useful number of stops (R).
CAUSE_LIMITED_COMPATIBLE_INVENTORY = "limited_compatible_inventory"
# A discretionary stop is far from the rest of its day although a compatible
# candidate existed, or although the plan did not need the stop at all; or
# optional stops add materially to a day's spread; or requested places that
# could have had days of their own were grouped. A grounded semantic anchor
# is discretionary: a preference, never an intentional excursion.
CAUSE_DISCRETIONARY_DISPERSION = "discretionary_dispersion"
# The stored state does not hold what is needed to tell (no candidate
# quality report or destination context, or a stop without an identity).
CAUSE_UNVERIFIED = "cause_unverified"


@dataclass(frozen=True)
class DayDispersion:
    day_number: int
    # The validator's measure: straight-line path length in scheduled order.
    spread_km: float
    cause: str


def _within(points: list[GeoPoint | None]) -> bool:
    spread = day_spread_km(points)
    return spread is None or spread <= GEOGRAPHIC_SPREAD_THRESHOLD_KM


def _unused_viable_points(planning_state: PlanningState, scheduled_ids: set[str]) -> list[GeoPoint] | None:
    """Coordinates of every viable candidate that is not on the plan, or
    None when the stored state cannot say (no quality report / no pool)."""
    quality = planning_state.candidate_quality_report
    if quality is None or planning_state.destination_context is None:
        return None
    viable = {
        score.candidate_id
        for score in (*quality.attraction_scores, *quality.ai_directed_scores)
        if score.quality_tier in _VIABLE_TIERS and not score.low_value_object
    }
    points: dict[str, GeoPoint] = {}
    for poi in universe.schedulable_broad_pois(planning_state):
        place_id, coordinates = str(poi.get("place_id") or ""), poi.get("coordinates") or {}
        if place_id and coordinates.get("lat") is not None and coordinates.get("lng") is not None:
            points.setdefault(place_id, GeoPoint(lat=coordinates["lat"], lng=coordinates["lng"]))
    for promoted in universe.resolve_promoted_candidates(planning_state).accepted:
        if promoted.provider_place_id and promoted.coordinates is not None:
            points.setdefault(str(promoted.provider_place_id), promoted.coordinates)
    return [point for place_id, point in points.items() if place_id in viable and place_id not in scheduled_ids]


# The most grounded must-visit places for which "can they be kept on separate
# compact days?" is worked out exactly (set partitions; 4140 at eight). With
# more, the question is left open and the cause is reported as unverified.
_MAX_MANDATORY_FOR_PARTITION = 8


def mandatory_dispersion_unavoidable(points: list[GeoPoint | None], trip_days: int, per_day: int) -> bool | None:
    """Whether the trip's grounded must-visit places CANNOT be spread over
    its days without a dispersed day: True when every way of sharing them
    out (at most `per_day` to a day, at most `trip_days` days) leaves some
    day beyond the boundary; False when one compact way exists; None when
    that cannot be established (a must-visit without coordinates, or more
    places than are worked out exactly).

    Only True makes a day's dispersion a consequence of what the traveller
    asked for. The measure is the existing path spread and boundary."""
    if any(point is None for point in points):
        return None
    located = [point for point in points if point is not None]
    if len(located) <= max(1, trip_days):
        return False  # each on a day of its own
    if len(located) > _MAX_MANDATORY_FOR_PARTITION:
        return None
    groups: list[list[GeoPoint]] = []

    def place(index: int) -> bool:
        if index == len(located):
            return True
        for group in groups:
            if len(group) < per_day and prospective_day_spread_km([*group, located[index]]) <= GEOGRAPHIC_SPREAD_THRESHOLD_KM:
                group.append(located[index])
                if place(index + 1):
                    return True
                group.pop()
        if len(groups) < max(1, trip_days):
            groups.append([located[index]])
            if place(index + 1):
                return True
            groups.pop()
        return False

    return not place(0)


def dispersion_cause(
    day: DailyPlan,
    *,
    must_visit_ids: set[str],
    unused_points: list[GeoPoint] | None,
    meaningful_stops: int,
    minimum_stops: int,
    mandatory_unavoidable: bool | None = None,
) -> str:
    """Why `day` (already known to be dispersed) is dispersed.

    A must-visit on the day does not make the day a mandatory excursion.
    `mandatory_destination` is returned only when the grounded must-visits
    of the day are dispersed BY THEMSELVES, the trip's must-visits cannot be
    shared out over its days without a dispersed day
    (`mandatory_unavoidable is True`), and the discretionary stops do not
    add materially to the day's spread (the existing materiality tolerance,
    `AS_WELL_PLACED_KM`). When discretionary stops cause or materially
    increase the dispersion the finding stays a warning, and when the stored
    state cannot establish that the must-visits alone explain it, the cause
    is `cause_unverified` -- never downgraded."""
    stops = list(day.experiences)
    points = [stop.coordinates for stop in stops]
    mandatory = [index for index, stop in enumerate(stops) if stop.provider_place_id in must_visit_ids]
    discretionary = [index for index in range(len(stops)) if index not in mandatory]

    def without(removed: set[int]) -> list[GeoPoint | None]:
        return [point for index, point in enumerate(points) if index not in removed]

    # Discretionary stops whose removal ALONE brings the day back within the boundary.
    outliers = [index for index in discretionary if _within(without({index}))]
    mandatory_only = without(set(discretionary))
    if mandatory and not _within(mandatory_only):
        # The requested places are far apart by themselves.
        added_km = (day_spread_km(points) or 0.0) - (day_spread_km(mandatory_only) or 0.0)
        if discretionary and added_km > AS_WELL_PLACED_KM:
            return CAUSE_DISCRETIONARY_DISPERSION  # optional stops make it materially worse
        if mandatory_unavoidable is True:
            return CAUSE_MANDATORY_DESTINATION
        if mandatory_unavoidable is None:
            return CAUSE_UNVERIFIED
        return CAUSE_DISCRETIONARY_DISPERSION  # they could have had days of their own: a grouping choice
    if not discretionary or any(not stops[index].provider_place_id for index in discretionary):
        return CAUSE_UNVERIFIED
    if unused_points is None:
        return CAUSE_UNVERIFIED

    removable = set(outliers) if outliers else set(discretionary)
    kept = without(removable)
    if not any(point is not None for point in kept):
        kept = [points[0]]  # an all-discretionary day: what would sit with its first stop
    if any(keeps_day_within_spread(kept, candidate) for candidate in unused_points):
        return CAUSE_DISCRETIONARY_DISPERSION
    if meaningful_stops - len(removable) < minimum_stops:
        return CAUSE_LIMITED_COMPATIBLE_INVENTORY
    return CAUSE_DISCRETIONARY_DISPERSION


def assess_days(planning_state: PlanningState) -> list[DayDispersion]:
    """Every dispersed day of the stored plan with its cause, in day order."""
    plan = planning_state.experience_plan
    if plan is None:
        return []
    scheduled = [stop for day in plan.daily_plans for stop in day.experiences]
    dispersed = [
        (day, spread)
        for day in plan.daily_plans
        if (spread := day_spread_km([stop.coordinates for stop in day.experiences])) is not None
        and spread > GEOGRAPHIC_SPREAD_THRESHOLD_KM
    ]
    if not dispersed:
        return []
    must_visit_ids = must_visit_place_ids(planning_state)
    unused_points = _unused_viable_points(
        planning_state, {stop.provider_place_id for stop in scheduled if stop.provider_place_id}
    )
    meaningful = sum(1 for stop in scheduled if is_meaningful_stop(stop))
    targets = pace_targets_for(planning_state)
    minimum = targets.minimum_useful
    # one point per grounded must-visit PLACE on the plan (a place scheduled once counts once)
    mandatory_points = list(
        {
            stop.provider_place_id: stop.coordinates
            for stop in scheduled
            if stop.provider_place_id and stop.provider_place_id in must_visit_ids
        }.values()
    )
    unavoidable = mandatory_dispersion_unavoidable(mandatory_points, len(plan.daily_plans), targets.per_day)
    return [
        DayDispersion(
            day_number=day.day_number,
            spread_km=spread,
            cause=dispersion_cause(
                day,
                must_visit_ids=must_visit_ids,
                unused_points=unused_points,
                meaningful_stops=meaningful,
                minimum_stops=minimum,
                mandatory_unavoidable=unavoidable,
            ),
        )
        for day, spread in dispersed
    ]


def dispersion_by_day(planning_state: PlanningState) -> dict[int, DayDispersion]:
    """`assess_days` by day number. A state this cannot be worked out for
    gives no entry: the caller then reports the cause as unverified, and a
    reporting detail never fails validation."""
    try:
        return {entry.day_number: entry for entry in assess_days(planning_state)}
    except Exception:  # noqa: BLE001 - the cause is a detail of a finding, never a reason to fail
        return {}
