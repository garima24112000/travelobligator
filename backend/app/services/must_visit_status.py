"""What became of each requested must-visit in the FINAL itinerary (Final
Batch 1, A).

One status per requested term, reconciled against what is scheduled -- never
against the first lookup alone (`must_visit_matching.resolve_must_visits`
decides "scheduled" by identity, including a scheduled record of the same
entity), and combined with how the destination-context stage found the term
(`DestinationContext.must_visit_lookups`):

  * `scheduled`              -- found and on the plan;
  * `scheduled_excursion`    -- found OUTSIDE the destination, a driving route
                                within the existing limit was verified, and it
                                is on the plan (how to travel there is not
                                arranged or checked);
  * `found_inaccessible`     -- on the plan because you asked, but its own
                                record states it does not operate, is not yet
                                open, is closed on its date, or is not open to
                                the public;
  * `found_not_scheduled`    -- found, but not on the final plan;
  * `outside_destination_not_feasible` -- found outside the destination; the
                                verified driving route exceeds the limit;
  * `outside_destination_unverified`   -- found outside the destination; no
                                route could be verified, so it was not planned;
  * `unresolved`             -- no matching place was found.

Presentation only: nothing that plans reads it, and it never changes
readiness or a code. Pure: no provider, routing or model call.
"""

from __future__ import annotations

from app.models.planning_state import MustVisitStatusEntry, PlanningState
from app.services import must_visit_matching as matching
from app.services import venue_evidence
from app.services.place_taxonomy import explicit_no_public_access

SCHEDULED = "scheduled"
SCHEDULED_EXCURSION = "scheduled_excursion"
FOUND_INACCESSIBLE = "found_inaccessible"
FOUND_NOT_SCHEDULED = "found_not_scheduled"
OUTSIDE_NOT_FEASIBLE = "outside_destination_not_feasible"
OUTSIDE_UNVERIFIED = "outside_destination_unverified"
UNRESOLVED = "unresolved"

# The statuses that mean "requested, not on the plan".
NOT_ON_PLAN = frozenset({FOUND_NOT_SCHEDULED, OUTSIDE_NOT_FEASIBLE, OUTSIDE_UNVERIFIED, UNRESOLVED})

_INACCESSIBLE_EVIDENCE = frozenset(
    {
        venue_evidence.REPORTED_NOT_OPERATING,
        venue_evidence.REPORTED_NOT_YET_OPEN,
        venue_evidence.LISTED_CLOSED_ON_DAY,
    }
)


def must_visit_statuses(planning_state: PlanningState) -> list[MustVisitStatusEntry] | None:
    """One entry per requested must-visit, in the traveller's order; None
    without a plan (unknown, never "fine")."""
    plan = planning_state.experience_plan
    context = planning_state.destination_context
    if plan is None:
        return None
    lookups = {lookup.term: lookup for lookup in (context.must_visit_lookups if context is not None else [])}
    pool = {
        str(poi["place_id"]): poi for poi in (context.candidate_pois if context is not None else []) if poi.get("place_id")
    }
    stops = {
        str(stop.provider_place_id): (day.day_number, stop)
        for day in plan.daily_plans
        for stop in day.experiences
        if stop.provider_place_id
    }
    try:
        evidence = {entry.experience_id: entry.status for entry in venue_evidence.stop_evidence(planning_state)}
    except Exception:  # noqa: BLE001 - without venue evidence nothing is called inaccessible from it
        evidence = {}

    entries: list[MustVisitStatusEntry] = []
    for resolution in matching.resolve_must_visits(planning_state):
        lookup = lookups.get(resolution.term)
        drive_minutes = (
            round(lookup.excursion_drive_seconds / 60)
            if lookup is not None and lookup.excursion_drive_seconds is not None
            else None
        )
        distance_km = lookup.distance_km if lookup is not None else None
        if resolution.scheduled:
            place_id = sorted(resolution.scheduled_place_ids)[0]
            day_number, stop = stops[place_id]
            record = pool.get(place_id) or {}
            access = explicit_no_public_access(record.get("provider_tags"))
            venue = evidence.get(stop.experience_id)
            if access is not None or venue in _INACCESSIBLE_EVIDENCE:
                status, reason = FOUND_INACCESSIBLE, (f"access_{access}" if access else venue)
            elif record.get(matching.OUTSIDE_DESTINATION_KEY):
                status, reason = SCHEDULED_EXCURSION, None
            else:
                status, reason = SCHEDULED, None
            entries.append(MustVisitStatusEntry(
                term=resolution.term, status=status, place_name=stop.name, day_number=day_number,
                distance_km=distance_km if status == SCHEDULED_EXCURSION else None,
                drive_minutes=drive_minutes if status == SCHEDULED_EXCURSION else None, reason=reason,
            ))
            continue
        outcome = lookup.outcome if lookup is not None else None
        if outcome == matching.EXCURSION_TOO_FAR:
            status = OUTSIDE_NOT_FEASIBLE
        elif outcome == matching.EXCURSION_ROUTE_UNVERIFIED:
            status = OUTSIDE_UNVERIFIED
        elif resolution.grounded:
            status = FOUND_NOT_SCHEDULED
        else:
            status = UNRESOLVED
        entries.append(MustVisitStatusEntry(
            term=resolution.term, status=status,
            place_name=resolution.grounded_names[0] if resolution.grounded_names else None,
            distance_km=distance_km if status in (OUTSIDE_NOT_FEASIBLE, OUTSIDE_UNVERIFIED) else None,
            drive_minutes=drive_minutes if status == OUTSIDE_NOT_FEASIBLE else None,
            reason=lookup.rejection if lookup is not None and status == UNRESOLVED else None,
        ))
    return entries


def must_visit_statuses_safely(planning_state: PlanningState) -> list[MustVisitStatusEntry] | None:
    """`must_visit_statuses`, or None when the stored state cannot be read: a
    disclosure detail never fails validation."""
    try:
        return must_visit_statuses(planning_state)
    except Exception:  # noqa: BLE001 - disclosure only
        return None
