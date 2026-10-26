"""Final Batch 1 (C, E): estimated visit times and traveller-facing wording.

Synthetic fixtures only (a Monday in August 2026; no destination name, no
provider call -- a fake routing service answers re-route requests). Times are
labelled estimates: listed hours come from the place's own record, travel only
from verified legs, unknown hours stay unknown, and a visit its listed hours
rule out is reordered, removed or disclosed -- never silently kept."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.models.common import GeoPoint, ProviderStatus
from app.models.planning_state import (
    DailyPlan,
    DestinationContext,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripPace,
    TripRequest,
    UserLock,
)
from app.models.routing import RouteFeasibilityReport, RouteFeasibilityStatus, RouteLegFeasibility
from app.services import opening_hours as oh
from app.services import visit_schedule as vs
from app.services.plan_validator_service import PlanValidatorService

MONDAY = date(2026, 8, 10)
WINDOW = vs.DAY_WINDOWS[TripPace.BALANCED]  # 09:30-18:00


def _reading(value: str | None) -> oh.DayHours:
    return oh.hours_on(value, MONDAY, frozenset())


# -- the timeline ---------------------------------------------------------------------------------


def test_a_day_starts_at_its_window_and_adds_only_verified_travel() -> None:
    times = vs.day_times([_reading("24/7"), _reading("24/7")], [120, 60], [15.2], WINDOW)
    assert [(s.start, s.end) for s in times.stops] == [(570, 690), (706, 766)]  # travel rounded up
    assert times.untimed_reason is None and not times.exceeds_window


def test_a_late_opening_is_waited_for_never_compressed() -> None:
    times = vs.day_times([_reading("Mo 11:00-17:00")], [120], [], WINDOW)
    assert (times.stops[0].start, times.stops[0].wait, times.stops[0].status) == (660, 90, vs.FITS)


def test_after_an_unverified_leg_nothing_is_timed() -> None:
    times = vs.day_times([_reading("24/7")] * 3, [60, 60, 60], [10.0, None], WINDOW)
    assert [s.status for s in times.stops] == [vs.FITS, vs.FITS, vs.NOT_TIMED]
    assert times.stops[2].start is None and times.untimed_reason == vs.TRAVEL_NOT_VERIFIED


def test_unknown_hours_are_timed_but_never_called_open() -> None:
    times = vs.day_times([_reading(None), _reading("Mo-Fr 09:00-17:00 || PH off")], [60, 60], [5.0], WINDOW)
    assert [s.status for s in times.stops] == [vs.UNKNOWN_HOURS, vs.UNKNOWN_HOURS]


def test_listed_hours_that_leave_no_time_are_a_conflict() -> None:
    times = vs.day_times([_reading("24/7"), _reading("Mo 09:00-12:00")], [120, 120], [10.0], WINDOW)
    assert times.stops[1].status == vs.OUTSIDE_HOURS and times.conflicts == (1,)
    closed = vs.day_times([_reading("Tu-Su 10:00-18:00")], [60], [], WINDOW)
    assert closed.stops[0].status == vs.CLOSED_ON_DATE


def test_a_long_day_is_flagged_never_trimmed() -> None:
    times = vs.day_times([_reading("24/7")] * 4, [120] * 4, [30.0] * 3, WINDOW)
    assert times.exceeds_window and times.ends_at == 570 + 480 + 90 and len(times.stops) == 4


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Mo 06:00-09:00", True),  # only open before the day starts
        ("Mo 10:00-11:00", True),  # one hour: not enough for a museum (120 min, estimate)
        ("Mo 10:00-18:00", False),
        ("Tu-Su 10:00-18:00", True),  # closed that date
        (None, False),  # unknown is never a reason to act
    ],
)
def test_unvisitable_on_reads_the_pace_window_and_the_estimate(value: str | None, expected: bool) -> None:
    record = {"provider_tags": {"tourism": "museum"}, "venue_tags": {"opening_hours": value} if value else {}}
    assert vs.unvisitable_on(record, MONDAY, frozenset(), TripPace.BALANCED) is expected


# -- the repair on the final routed days -----------------------------------------------------------


def _record(pid: str, hours: str | None, lat: float, *, tags: dict | None = None, **extra: Any) -> dict[str, Any]:
    return {"place_id": pid, "name": pid, "provider_tags": tags or {"tourism": "museum"},
            "venue_tags": {"opening_hours": hours} if hours else {}, "coordinates": {"lat": lat, "lng": 0.0},
            "source": "fixture", "data_status": "live", "confidence": 0.6, **extra}


def _leg(a: ExperienceItem, b: ExperienceItem, seconds: float | None) -> RouteLegFeasibility:
    return RouteLegFeasibility(
        from_experience_id=a.experience_id, from_experience_name=a.name,
        to_experience_id=b.experience_id, to_experience_name=b.name, provider="fixture",
        status=ProviderStatus.SUCCESS if seconds is not None else ProviderStatus.UNAVAILABLE,
        distance_meters=seconds * 1.2 if seconds is not None else None, duration_seconds=seconds,
        feasibility_status=RouteFeasibilityStatus.FEASIBLE if seconds is not None else RouteFeasibilityStatus.NEEDS_REVIEW,
    )


class _Routing:
    """Re-routes a day: every leg verified at `seconds` (None: not verified)."""

    def __init__(self, seconds: float | None = 600.0) -> None:
        self.seconds, self.requests = seconds, 0

    def route_day_legs(self, stops: list[ExperienceItem], provider_context: Any = None) -> list[RouteLegFeasibility]:
        self.requests += 1
        return [_leg(a, b, self.seconds) for a, b in zip(stops, stops[1:])]

    def replace_day_legs(self, report: RouteFeasibilityReport, previous: list, fresh: list) -> None:
        stale = {(a.experience_id, b.experience_id) for a, b in zip(previous, previous[1:])}
        report.legs = [leg for leg in report.legs if (leg.from_experience_id, leg.to_experience_id) not in stale] + fresh


def _state(records: list[dict], *, must_visit: list[str] | None = None, legs_seconds: float | None = 600.0,
           pace: TripPace = TripPace.BALANCED) -> PlanningState:
    state = PlanningState(trip_request=TripRequest(
        primary_destination="Testville", start_date=MONDAY, end_date=MONDAY, travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE, pace=pace, must_visit=must_visit or [],
    ))
    state.destination_context = DestinationContext(destination_name="Testville", candidate_pois=records,
                                                   must_visit_grounding_version=1)
    items = [ExperienceItem(experience_id=f"e-{r['place_id']}", name=r["place_id"], category="museum",
                            coordinates=GeoPoint(lat=r["coordinates"]["lat"], lng=0.0),
                            provider_place_id=r["place_id"], provider_source="fixture", stop_order=i + 1,
                            day_number=1)
             for i, r in enumerate(records)]
    state.experience_plan = ExperiencePlan(daily_plans=[DailyPlan(day_number=1, date=MONDAY, experiences=items)])
    state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.SUCCESS, provider="fixture", route_data_source="fixture",
        legs=[_leg(a, b, legs_seconds) for a, b in zip(items, items[1:])],
    )
    return state


def _order(state: PlanningState) -> list[str]:
    return [item.provider_place_id for item in state.experience_plan.daily_plans[0].experiences]


def test_a_visit_its_hours_rule_out_is_fixed_by_reordering_the_day() -> None:
    # B closes at 11:30: second in the day it cannot be visited; first it can.
    state = _state([_record("A", "Mo 09:00-18:00", 0.0), _record("B", "Mo 09:00-11:30", 0.001)])
    routing = _Routing()
    vs.apply_visit_schedule_safely(state, routing)
    assert _order(state) == ["B", "A"] and routing.requests == 1
    items = state.experience_plan.daily_plans[0].experiences
    assert [(i.start_time, i.end_time, i.visit_time_status) for i in items] == [
        ("09:30", "11:30", vs.FITS), ("11:40", "13:40", vs.FITS)]
    assert [i.stop_order for i in items] == [1, 2]
    legs = state.route_feasibility_report.legs
    assert [(leg.from_experience_id, leg.to_experience_id) for leg in legs] == [("e-B", "e-A")]  # no stale leg


def test_an_unverified_reorder_is_not_taken_and_a_last_stop_is_removed_without_routing() -> None:
    # C opens only 09:00-10:00: no visit fits anywhere. As the LAST stop it is removed
    # (relaxed: R = 2 for one day, so the plan keeps its minimum).
    records = [_record("A", "24/7", 0.0), _record("B", "24/7", 0.001), _record("C", "Mo 09:00-10:00", 0.002)]
    state = _state(records, pace=TripPace.RELAXED)
    routing = _Routing(seconds=None)  # nothing can be re-routed
    vs.apply_visit_schedule_safely(state, routing)
    assert _order(state) == ["A", "B"]
    assert [(leg.from_experience_id, leg.to_experience_id) for leg in state.route_feasibility_report.legs] == [
        ("e-A", "e-B")]


def test_a_removal_never_takes_the_plan_below_its_minimum() -> None:
    records = [_record("A", "24/7", 0.0), _record("B", "24/7", 0.001), _record("C", "Mo 09:00-10:00", 0.002)]
    state = _state(records)  # balanced, one day: R = 3
    vs.apply_visit_schedule_safely(state, _Routing(seconds=None))
    assert _order(state) == ["A", "B", "C"]  # kept and disclosed
    assert state.experience_plan.daily_plans[0].experiences[2].visit_time_status == vs.OUTSIDE_HOURS


def test_a_must_visit_is_never_removed_and_is_disclosed() -> None:
    records = [_record("A", "24/7", 0.0),
               _record("M", "Mo 09:00-10:00", 0.001, must_visit_terms=["M"], must_visit_term="M")]
    state = _state(records, must_visit=["M"])
    vs.apply_visit_schedule_safely(state, _Routing(seconds=None))
    assert _order(state) == ["A", "M"]
    assert state.experience_plan.daily_plans[0].experiences[1].visit_time_status == vs.OUTSIDE_HOURS
    PlanValidatorService().run(state)
    (finding,) = [i for i in state.validation_report.warnings if i.category == "visit_outside_listed_hours"]
    assert "kept because you asked" in finding.message and "09:00–10:00" in finding.message
    assert "VISIT_OUTSIDE_LISTED_HOURS" in state.validation_report.review_codes
    assert state.validation_report.review_code_classification["VISIT_OUTSIDE_LISTED_HOURS"] == "material"


def test_nothing_moves_under_an_active_lock_but_times_are_shown() -> None:
    state = _state([_record("A", "Mo 09:00-18:00", 0.0), _record("B", "Mo 09:00-11:30", 0.001)])
    state.user_locks = [UserLock(locked_item_type="experience", locked_item_id="anything")]
    routing = _Routing()
    vs.apply_visit_schedule_safely(state, routing)
    assert _order(state) == ["A", "B"] and routing.requests == 0
    assert state.experience_plan.daily_plans[0].experiences[1].visit_time_status == vs.OUTSIDE_HOURS


def test_a_day_past_its_window_is_disclosed() -> None:
    records = [_record(p, "24/7", i * 0.001) for i, p in enumerate("ABCD")]
    state = _state(records, legs_seconds=3600.0)
    vs.apply_visit_schedule_safely(state, _Routing())
    schedule = state.experience_plan.daily_plans[0].visit_schedule
    assert schedule.exceeds_window and schedule.window_end == "18:00"
    PlanValidatorService().run(state)
    (finding,) = [i for i in state.validation_report.warnings if i.category == "day_exceeds_time_window"]
    assert f"until about {schedule.ends_at}" in finding.message
    assert _order(state) == list("ABCD")  # estimates never trim a day


def test_the_rollback_switch_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("VISIT_TIME_SCHEDULING_ENABLED", "false")
    get_settings.cache_clear()
    try:
        state = _state([_record("A", "24/7", 0.0)])
        vs.apply_visit_schedule_safely(state, _Routing())
    finally:
        monkeypatch.delenv("VISIT_TIME_SCHEDULING_ENABLED")
        get_settings.cache_clear()
    item = state.experience_plan.daily_plans[0].experiences[0]
    assert item.start_time is None and state.experience_plan.daily_plans[0].visit_schedule is None


def test_clearing_is_scoped_to_the_days_named() -> None:
    state = _state([_record("A", "24/7", 0.0)])
    vs.apply_visit_schedule_safely(state, _Routing())
    vs.clear_visit_times(state, {2})
    assert state.experience_plan.daily_plans[0].experiences[0].start_time == "09:30"
    vs.clear_visit_times(state, {1})
    assert state.experience_plan.daily_plans[0].experiences[0].start_time is None


def test_saved_itineraries_without_times_still_load() -> None:
    assert ExperienceItem(name="x", category="museum").visit_time_status is None
    assert DailyPlan(day_number=1, date=MONDAY).visit_schedule is None


# -- the pre-routing pass reads the same rule ------------------------------------------------------


def test_the_planner_moves_a_stop_its_hours_rule_out_on_that_date() -> None:
    from app.services import visit_day_feasibility as feas
    from app.tests.services.test_venue_reliability_m2p3 import _helpers, _ids, _poi

    morning_only = _poi("m", venue={"opening_hours": "Mo 06:00-09:00; Tu-Su 10:00-18:00"})
    helpers = _helpers()
    import dataclasses

    helpers = dataclasses.replace(
        helpers, closed=lambda poi, day, holidays: vs.unvisitable_on(poi, day, holidays, TripPace.BALANCED)
    )
    days, report = feas.resolve_known_closures(
        [[morning_only, _poi("a")], [_poi("b")]], [], helpers, start_date=MONDAY, holidays=frozenset(),
        max_per_day=3, minimum_stops=1,
    )
    assert _ids(days) == [["a"], ["b", "m"]] and report.actions[0]["action"] == feas.CLOSURE_MOVED


# -- E: traveller-facing wording ------------------------------------------------------------------


def _rationale_state(rationale: str, interests: list[str], matched: list[str], food_nearby: bool = False):
    from app.models.ai_itinerary_reasoning import (
        AIItineraryReasoningResult,
        AIItineraryReasoningStatus,
        ItineraryReasoningDayPlan,
        build_candidate_id,
    )
    from app.models.planning_state import RestaurantSuggestion
    from app.services.day_rationale import current_day_rationale

    state = PlanningState(trip_request=TripRequest(
        primary_destination="Testville", start_date=MONDAY, end_date=MONDAY, travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE, interests=interests,
    ))
    item = ExperienceItem(name="x", category="museum", provider_place_id="p1", provider_source="fixture",
                          matched_interests=matched)
    day = DailyPlan(day_number=1, date=MONDAY, experiences=[item], restaurant_suggestions=[
        RestaurantSuggestion(name="r", data_status="live", why_suggested="near")] if food_nearby else [])
    state.ai_itinerary_reasoning_result = AIItineraryReasoningResult.model_construct(
        status=AIItineraryReasoningStatus.COMPLETED,
        days=[ItineraryReasoningDayPlan.model_construct(
            day_index=1, candidate_ids=[build_candidate_id("fixture", "p1")], rationale=rationale)],
    )
    return current_day_rationale(state, day)


@pytest.mark.parametrize(
    ("rationale", "interests", "matched", "food", "shown"),
    [
        ("A morning among the old collections.", ["history"], ["history"], False, True),
        ("Expanded the schedule with a third stop to match the expected pace.", [], [], False, False),
        ("Focuses on historical and culinary experiences.", ["food", "history"], ["history"], False, False),
        ("Focuses on historical and culinary experiences.", ["food", "history"], ["history"], True, True),
    ],
)
def test_a_rationale_is_shown_only_when_it_is_a_supported_reason(
    rationale: str, interests: list[str], matched: list[str], food: bool, shown: bool
) -> None:
    assert (_rationale_state(rationale, interests, matched, food) == rationale) is shown


def test_the_holiday_note_no_longer_says_hours_are_never_checked() -> None:
    from app.models.common import DataStatus
    from app.models.planning_state import HolidayContext
    from app.services.plan_validator_service import _build_holiday_warning

    state = _state([_record("A", "24/7", 0.0)])
    state.holiday_context = HolidayContext(destination="x", start_date=MONDAY, end_date=MONDAY, holidays=[],
                                           data_status=DataStatus.LIVE)
    message = _build_holiday_warning(state).message
    assert "not implemented" not in message and "has not been checked against venue closures" not in message
    assert "only where a place lists them" in message


def test_an_english_name_from_the_record_is_kept_for_display() -> None:
    from app.providers.places.entity_identity import english_name
    from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter

    assert english_name({"name:en": "Old Gallery"}, "Galleria Vecchia") == "Old Gallery"
    assert english_name({"name:en": "Old Gallery"}, "Old Gallery") is None  # the same name: nothing to add
    assert english_name({}, "x") is None and english_name(None, "x") is None
    adapter = GeoapifyPlacesAdapter.__new__(GeoapifyPlacesAdapter)
    adapter.provider_name = "geoapify_places"
    place = adapter._place_from_properties({
        "name": "Galleria Vecchia", "place_id": "p1", "lat": 1.0, "lon": 2.0, "categories": ["entertainment.museum"],
        "datasource": {"raw": {"tourism": "museum", "name:en": "Old Gallery"}},
    })
    assert place.name == "Galleria Vecchia" and place.english_name == "Old Gallery"  # identity unchanged


def test_dates_are_the_trip_dates() -> None:
    state = _state([_record("A", "Tu-Su 10:00-18:00", 0.0)])
    state.experience_plan.daily_plans[0].date = MONDAY + timedelta(days=1)  # a Tuesday: open
    vs.apply_visit_schedule_safely(state, _Routing())
    assert state.experience_plan.daily_plans[0].experiences[0].visit_time_status == vs.FITS
