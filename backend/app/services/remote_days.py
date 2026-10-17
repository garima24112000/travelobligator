"""Days away from where the trip's places are (Phase 1, travel feasibility).

A day's legs connect only its own stops. No day has a starting point
(accommodation is not connected), so the journey to a day's first stop and
back from its last is never part of the plan's travel figures. For an
ordinary day that is the general, disclosed gap. A day whose stops ALL lie
away from where the destination's places are is different: its figures can
read as a short, compact day ("21 minutes") while reaching the area at all
is a journey of its own that nothing has checked.

`remote_days` names those days from stored state only. The boundary is the
planner's existing one, not a new threshold: the isolated-outlier rule
(`experience_planner_service._pool_reach` -- within the larger of its
minimum distance and a multiple of the pool's median distance from its own
median centre), measured against the same scheduling pool the planner
measures it against (the schedulable broad pool and the promoted candidates
that join it). It adapts to how spread out the destination's own places are,
so there is no per-city radius, and it is a straight-line proxy: never a
route, a distance travelled or a duration, and never a statement about where
the traveller stays.

Nothing here changes a plan. A day the traveller's own must-visits put there
is kept exactly as it is and reported with those places named, because the
journey there and back is unchecked whatever the reason for the day. Pure: no
provider, routing or model call, and no place name.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.planning_state import PlanningState
from app.services import candidate_universe as universe
from app.services.experience_planner_service import _build_promoted_candidate_pois, _pool_reach
from app.services.must_visit_matching import must_visit_place_ids


@dataclass(frozen=True)
class RemoteDay:
    day_number: int
    # The day's grounded must-visits (by provider identity), in stop order.
    requested_places: tuple[str, ...]
    # Located stops on the day (all of them beyond the pool's reach).
    located_stops: int


def remote_days(planning_state: PlanningState) -> list[RemoteDay]:
    """Every scheduled day whose located stops ALL lie beyond the reach of
    the scheduling pool, in day order. A day without a located stop says
    nothing and is never reported; with no located candidate in the pool no
    day is."""
    plan = planning_state.experience_plan
    if plan is None:
        return []
    pool = universe.schedulable_broad_pois(planning_state) + _build_promoted_candidate_pois(planning_state)[0]
    if not any(poi.get("coordinates") for poi in pool):
        return []
    within = _pool_reach(pool)
    must_visit_ids = must_visit_place_ids(planning_state)
    found: list[RemoteDay] = []
    for day in plan.daily_plans:
        located = [stop for stop in day.experiences if stop.coordinates is not None]
        if not located:
            continue
        if any(
            within({"coordinates": {"lat": stop.coordinates.lat, "lng": stop.coordinates.lng}}) for stop in located
        ):
            continue
        found.append(
            RemoteDay(
                day_number=day.day_number,
                requested_places=tuple(
                    stop.name
                    for stop in day.experiences
                    if stop.provider_place_id and str(stop.provider_place_id) in must_visit_ids
                ),
                located_stops=len(located),
            )
        )
    return found


def remote_days_safely(planning_state: PlanningState) -> list[RemoteDay]:
    """`remote_days`, or nothing when the stored state cannot be read: a
    disclosure detail never fails validation."""
    try:
        return remote_days(planning_state)
    except Exception:  # noqa: BLE001 - disclosure only
        return []
