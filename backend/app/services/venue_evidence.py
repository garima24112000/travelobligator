"""Per-stop venue evidence of a final plan (M2 Phase 3, Workstreams A and B).

For every scheduled stop: what its own provider record says about the place
operating (`services/venue_status`) and about its opening hours on the stop's
scheduled date (`services/opening_hours`). Computed by the validator from the
stored candidate pool and the final days, so it describes the plan as it is,
whichever stage changed it last. Pure: no provider, routing or model call.

Statuses, from strongest statement to none:

  * `reported_not_operating` / `reported_not_yet_open` -- the record states it;
  * `listed_closed_on_day`   -- the listed hours close it on that date;
  * `listed_open_on_day`     -- the listed hours open it at some time that date
                                (never a claim that it is open: hours change);
  * `hours_need_holiday_data`-- the hours have a public-holiday rule and the
                                trip has no holiday data;
  * `hours_not_understood`   -- listed in a form this reader does not support;
  * `hours_not_listed`       -- the record lists none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.planning_state import PlanningState
from app.services import opening_hours as hours
from app.services import venue_status

REPORTED_NOT_OPERATING = "reported_not_operating"
REPORTED_NOT_YET_OPEN = "reported_not_yet_open"
LISTED_CLOSED_ON_DAY = "listed_closed_on_day"
LISTED_OPEN_ON_DAY = "listed_open_on_day"
HOURS_NEED_HOLIDAY_DATA = "hours_need_holiday_data"
HOURS_NOT_UNDERSTOOD = "hours_not_understood"
HOURS_NOT_LISTED = "hours_not_listed"

# Where a stop's record was found (diagnostics: why hours may be missing).
RECORD_POOL = "pool"
RECORD_PROMOTED = "promoted_anchor"
RECORD_NONE = "no_record"

_FROM_STATUS = {
    venue_status.NOT_OPERATIONAL: REPORTED_NOT_OPERATING,
    venue_status.NOT_YET_OPEN: REPORTED_NOT_YET_OPEN,
    venue_status.LISTED_CLOSED: LISTED_CLOSED_ON_DAY,
}
_FROM_REASON = {
    "holiday_data_unavailable": HOURS_NEED_HOLIDAY_DATA,
    "hours_not_understood": HOURS_NOT_UNDERSTOOD,
    "hours_not_listed": HOURS_NOT_LISTED,
}


@dataclass(frozen=True)
class StopEvidence:
    experience_id: str
    day_number: int
    name: str
    must_visit: bool
    status: str
    hours_on_day: tuple[str, ...]
    checked_on: str | None
    source: str | None
    # Diagnostics (fixed labels): where the record came from, and for unknown
    # hours, why -- `no_record`, `record_without_venue_evidence`,
    # `no_hours_in_record`, `holiday_data_unavailable` or `unsupported:<feature>`.
    record: str = RECORD_NONE
    unknown_cause: str | None = None


def _pool_by_place_id(planning_state: PlanningState) -> dict[str, dict[str, Any]]:
    """Every record a scheduled stop can come from, by provider place id: the
    broad pool, then the promoted (grounded AI-proposed) candidates, which
    are not in the pool -- their venue evidence came with their grounding."""
    context = planning_state.destination_context
    pool: dict[str, dict[str, Any]] = {}
    for poi in (context.candidate_pois if context is not None else []):
        if poi.get("place_id"):
            pool.setdefault(str(poi["place_id"]), {**poi, "_record": RECORD_POOL})
    promotion = planning_state.ai_candidate_promotion_report
    for promoted in (promotion.promoted_candidates if promotion is not None else []):
        if promoted.provider_place_id:
            pool.setdefault(str(promoted.provider_place_id), {
                "provider_tags": promoted.provider_tags,
                "venue_tags": promoted.venue_tags,
                "source": promoted.provider_source,
                "_record": RECORD_PROMOTED,
            })
    return pool


# Final Batch 1 (C): the same records, read by the visit scheduler.
records_by_place_id = _pool_by_place_id


def _unknown_cause(poi: dict[str, Any] | None, tags: dict[str, Any], reading: hours.DayHours) -> str:
    if poi is None:
        return "no_record"
    if reading.reason == "holiday_data_unavailable":
        return "holiday_data_unavailable"
    if reading.reason == "hours_not_understood":
        return f"unsupported:{reading.feature or hours.FEATURE_OTHER}"
    if poi.get("venue_tags") is None:
        # nothing was read into the record: a source that returns no raw record
        # (the geocoder), a record stored before Phase 3, or a lost hand-over
        return "record_without_venue_evidence"
    return "no_hours_in_record"  # the record was read; it lists no hours


def stop_evidence(planning_state: PlanningState) -> list[StopEvidence]:
    """One entry per scheduled stop, in day and stop order."""
    from app.services.must_visit_matching import must_visit_place_ids

    plan = planning_state.experience_plan
    if plan is None:
        return []
    pool = _pool_by_place_id(planning_state)
    holidays = hours.holiday_dates(planning_state)
    must_visit_ids = must_visit_place_ids(planning_state)
    entries: list[StopEvidence] = []
    for day in plan.daily_plans:
        for stop in day.experiences:
            place_id = str(stop.provider_place_id) if stop.provider_place_id else None
            poi = pool.get(place_id) if place_id else None
            tags = (poi or {}).get("venue_tags") or {}
            status = venue_status.venue_status((poi or {}).get("provider_tags"), tags)
            listed: tuple[str, ...] = ()
            cause: str | None = None
            if status.status in _FROM_STATUS:
                label = _FROM_STATUS[status.status]
                checked = status.checked_on
            else:
                reading = hours.hours_on(tags.get("opening_hours"), day.date, holidays)
                checked = venue_status.checked_on(tags, hours=True)
                if reading.status == hours.OPEN:
                    label, listed = LISTED_OPEN_ON_DAY, tuple(hours.format_intervals(reading.intervals))
                elif reading.status == hours.CLOSED:
                    label = LISTED_CLOSED_ON_DAY
                else:
                    label = _FROM_REASON.get(reading.reason or "", HOURS_NOT_LISTED)
                    cause = _unknown_cause(poi, tags, reading)
            entries.append(
                StopEvidence(
                    experience_id=stop.experience_id,
                    day_number=day.day_number,
                    name=stop.name,
                    must_visit=bool(place_id and place_id in must_visit_ids),
                    status=label,
                    hours_on_day=listed,
                    checked_on=checked,
                    source=(poi or {}).get("source") or stop.provider_source,
                    record=(poi or {}).get("_record", RECORD_NONE),
                    unknown_cause=cause,
                )
            )
    return entries
