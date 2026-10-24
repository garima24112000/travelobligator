"""M2 Phase 3: venue reliability, opening hours and visit days, remote days.

Synthetic fixtures only (no provider, no destination name). Covers the venue
lifecycle from the provider record to the validator: the evidence kept by the
adapters, the status read from it, exclusion before scheduling, the planner's
known-closure and remote-day passes, the validator's findings and per-stop
evidence, and backward compatibility of stored data. Fault injection: missing,
conflicting, malformed and stale evidence."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.models.candidate_quality import CandidateQualityTier, CandidateRejectReason
from app.models.common import DataStatus
from app.models.planning_state import (
    DestinationContext,
    Holiday,
    HolidayContext,
    PlanningState,
    TravelGroupType,
    TripPace,
    TripRequest,
    ValidationReport,
)
from app.models.providers import NormalizedPlace
from app.services import opening_hours as oh
from app.services import review_classification
from app.services import venue_evidence
from app.services import venue_status as vs
from app.services import visit_day_feasibility as feas
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.plan_validator_service import PlanValidatorService

_DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
START = date(2026, 8, 10)


def closed_on_weekday(day: date) -> str:
    """An hours value open every day except `day`'s weekday."""
    return "; ".join(f"{name} 10:00-18:00" for index, name in enumerate(_DAYS) if index != day.weekday())


# -- opening hours: the supported subset, and conservative refusal --------------------------


@pytest.mark.parametrize(
    ("value", "weekday", "status", "intervals"),
    [
        ("24/7", 0, oh.OPEN, ["00:00–24:00"]),
        ("Tu-Su 10:00-18:00", 0, oh.CLOSED, []),  # a weekly closure: days not named are closed
        ("Tu-Su 10:00-18:00", 2, oh.OPEN, ["10:00–18:00"]),
        ("Fr-Mo 09:00-17:00", 0, oh.OPEN, ["09:00–17:00"]),  # a wrapping range
        ("Mo-Fr 09:00-12:00,14:00-18:00", 1, oh.OPEN, ["09:00–12:00", "14:00–18:00"]),
        ("Mo-Su 09:00-17:00; We off", 2, oh.CLOSED, []),  # a later rule replaces
        ("Mo-Fr 09:00-17:00, Sa 10:00-14:00", 5, oh.OPEN, ["10:00–14:00"]),  # an additional rule adds
        ("Fr 22:00-03:00", 5, oh.OPEN, ["00:00–03:00"]),  # overnight: continues into Saturday
        ("Fr 22:00-03:00", 4, oh.OPEN, ["22:00–24:00"]),
        ("off", 3, oh.CLOSED, []),
    ],
)
def test_supported_hours(value: str, weekday: int, status: str, intervals: list[str]) -> None:
    day = START + timedelta(days=(weekday - START.weekday()) % 7)
    reading = oh.hours_on(value, day, frozenset())
    assert (reading.status, oh.format_intervals(reading.intervals)) == (status, intervals)


@pytest.mark.parametrize(
    "value",
    ["week 1-10 Mo-Fr 10:00-16:00", "Mo-Fr 09:00-17:00 || PH off", "sunrise-sunset", "Sa 10:00+",
     'Mo-Fr 09:00-17:00 "by appointment"', "SH off", "Mo[1] 10:00-12:00", "Mo-Fr open", "25:00-26:00",
     "Mo 10:00-12:00; " * 30],
)
def test_anything_not_fully_understood_says_nothing(value: str) -> None:
    reading = oh.hours_on(value, START, frozenset())
    assert reading.status == oh.UNKNOWN and reading.reason == "hours_not_understood"
    assert oh.hours_on(None, START, frozenset()).reason == "hours_not_listed"


def test_month_ranges_are_supported_since_the_first_live_run() -> None:
    # was unsupported in the first version; a month range is now read completely
    assert oh.hours_on("Jan-Mar Mo-Fr 10:00-16:00", START, frozenset()).status == oh.CLOSED  # August
    assert oh.hours_on("Jan-Mar Mo-Fr 10:00-16:00", date(2027, 2, 1), frozenset()).status == oh.OPEN


def test_a_public_holiday_rule_needs_holiday_data() -> None:
    value = "Mo-Su 10:00-18:00; PH off"
    assert oh.hours_on(value, START, None).reason == "holiday_data_unavailable"  # any date might be one
    assert oh.hours_on(value, START, frozenset()).status == oh.OPEN  # data: not a holiday
    assert oh.hours_on(value, START, frozenset({START})).status == oh.CLOSED  # data: a holiday


def test_holiday_dates_come_only_from_provider_holiday_data() -> None:
    state = _state([])
    assert oh.holiday_dates(state) is None
    state.holiday_context = HolidayContext(destination="x", start_date=START, end_date=START,
                                           holidays=[Holiday(date=START, name="h", local_name="h", country_code="ZZ",
                                                                   is_global=True, source="test",
                                                                   data_status=DataStatus.LIVE)],
                                           data_status=DataStatus.LIVE)
    assert oh.holiday_dates(state) == frozenset({START})
    state.holiday_context = state.holiday_context.model_copy(update={"data_status": DataStatus.UNAVAILABLE})
    assert oh.holiday_dates(state) is None


# -- venue status: what the record states, and nothing more ----------------------------------


@pytest.mark.parametrize(
    ("current", "venue", "status"),
    [
        ({}, {}, vs.UNKNOWN),  # a record existing is never evidence of being open
        ({"tourism": "museum"}, {}, vs.UNKNOWN),
        ({}, {"disused:tourism": "museum"}, vs.NOT_OPERATIONAL),
        ({}, {"was:amenity": "theatre"}, vs.NOT_OPERATIONAL),
        ({"building": "yes"}, {"demolished:tourism": "attraction"}, vs.NOT_OPERATIONAL),
        ({"amenity": "theatre"}, {"disused": "yes"}, vs.NOT_OPERATIONAL),
        ({}, {"construction:tourism": "museum"}, vs.NOT_YET_OPEN),
        ({"tourism": "museum"}, {"opening_hours": "closed"}, vs.LISTED_CLOSED),
        # conflicting evidence: a current use beside an ended one stays unknown
        ({"historic": "castle"}, {"disused:amenity": "museum"}, vs.UNKNOWN),
        # a disused flag on a sight often describes the sight itself
        ({"tourism": "attraction"}, {"disused": "yes"}, vs.UNKNOWN),
    ],
)
def test_venue_status(current: dict, venue: dict, status: str) -> None:
    assert vs.venue_status(current, venue).status == status


def test_only_venue_keys_are_kept_and_dates_must_be_dates() -> None:
    kept = vs.venue_evidence_tags({
        "name": "X", "phone": "1", "website": "w", "opening_hours": "Mo 10:00-12:00",
        "disused:tourism": "museum", "check_date": "2021-03", "check_date:opening_hours": "soon",
    })
    assert kept == {"opening_hours": "Mo 10:00-12:00", "disused:tourism": "museum", "check_date": "2021-03"}
    assert vs.venue_evidence_tags(None) == {}
    # stale evidence is kept with its date (never silently dropped, never acted on by age)
    assert vs.venue_status({}, kept).checked_on == "2021-03"


def _poi(pid: str, lat: float = 0.0, lng: float = 0.0, *, tags: dict | None = None, venue: dict | None = None,
         **extra: Any) -> dict[str, Any]:
    return {
        "place_id": pid, "name": f"Place {pid}", "category": "museum",
        "provider_tags": tags if tags is not None else {"tourism": "museum"},
        "venue_tags": venue, "coordinates": {"lat": lat, "lng": lng}, "address": None,
        "source": "openstreetmap_places", "data_status": "live", "confidence": 0.6, **extra,
    }


@pytest.mark.parametrize("venue", [{"disused:tourism": "museum"}, {"opening_hours": "off"},
                                   {"construction:tourism": "museum"}])
def test_a_record_stating_it_does_not_operate_is_rejected_before_scheduling(venue: dict) -> None:
    score = CandidateQualityService().score_attraction(_poi("x", tags={}, venue=venue))
    assert score.quality_tier == CandidateQualityTier.REJECTED
    assert CandidateRejectReason.VENUE_REPORTED_NOT_OPERATING in score.reject_reasons


def test_missing_venue_evidence_never_rejects() -> None:
    for venue in (None, {}, {"opening_hours": "gibberish"}, {"check_date": "2001"}):
        assert CandidateRejectReason.VENUE_REPORTED_NOT_OPERATING not in (
            CandidateQualityService().score_attraction(_poi("x", venue=venue)).reject_reasons
        )


# -- adapters keep the evidence --------------------------------------------------------------


def test_geoapify_keeps_the_raw_record_venue_keys_and_documented_hours() -> None:
    from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter

    adapter = GeoapifyPlacesAdapter.__new__(GeoapifyPlacesAdapter)
    adapter.provider_name = "geoapify_places"
    place = adapter._place_from_properties({
        "name": "A", "place_id": "p1", "lat": 1.0, "lon": 2.0, "categories": ["entertainment.museum"],
        "datasource": {"raw": {"tourism": "museum", "opening_hours": "Tu-Su 10:00-18:00", "phone": "1"}},
    })
    assert place.venue_tags == {"opening_hours": "Tu-Su 10:00-18:00"}
    assert "opening_hours" not in (place.provider_tags or {})  # the taxonomy tags are unchanged
    detailed = adapter._apply_details(place.model_copy(update={"venue_tags": None}),
                                      {"categories": ["entertainment.museum"], "opening_hours": "Mo-Fr 09:00-17:00"})
    assert detailed.venue_tags == {"opening_hours": "Mo-Fr 09:00-17:00"}


def test_overpass_keeps_venue_keys() -> None:
    from app.providers.places import openstreetmap_adapter as osm

    adapter = osm.OpenStreetMapPlacesAdapter.__new__(osm.OpenStreetMapPlacesAdapter)
    places = adapter._normalize([{"type": "node", "id": 1, "lat": 0.0, "lon": 0.0,
                                  "tags": {"name": "M", "tourism": "museum", "opening_hours": "24/7"}}])
    assert places[0].venue_tags == {"opening_hours": "24/7"}


def test_stored_places_and_reports_from_before_load_unchanged() -> None:
    assert NormalizedPlace(place_id="p", name="n", source="s", data_status=DataStatus.LIVE).venue_tags is None
    assert ValidationReport.model_validate({"readiness_status": "needs_review"}).venue_evidence is None


# -- the planner passes (pure) -----------------------------------------------------------------


def _helpers(tiers: dict[str, int] | None = None, must: set[str] = frozenset(), interests: dict | None = None,
             dup: set[frozenset[str]] = frozenset()) -> feas.Helpers:
    from app.models.common import GeoPoint

    return feas.Helpers(
        coordinates=lambda p: GeoPoint(**p["coordinates"]) if p.get("coordinates") else None,
        tier_rank=lambda p: (tiers or {}).get(p["place_id"], 3),
        low_value=lambda p: False,
        usefulness_key=lambda p: ((tiers or {}).get(p["place_id"], 3) * -1, p["place_id"]),
        must_visit=lambda p: p["place_id"] in must,
        matched_interests=lambda p: frozenset((interests or {}).get(p["place_id"], ())),
        suspected_duplicate=lambda p, others: any(frozenset({p["place_id"], o["place_id"]}) in dup for o in others),
    )


def _ids(days: list[list[dict]]) -> list[list[str]]:
    return [[p["place_id"] for p in day] for day in days]


def _closures(days, pool, helpers, *, minimum: int = 1, per_day: int = 3):
    return feas.resolve_known_closures(days, pool, helpers, start_date=START, holidays=frozenset(),
                                       max_per_day=per_day, minimum_stops=minimum)


def test_a_stop_closed_on_its_day_moves_to_a_day_it_is_open() -> None:
    closed = _poi("c", venue={"opening_hours": closed_on_weekday(START)})
    days, report = _closures([[closed, _poi("a")], [_poi("b")]], [], _helpers())
    assert _ids(days) == [["a"], ["b", "c"]] and report.actions[0]["action"] == feas.CLOSURE_MOVED


def test_it_swaps_when_the_other_day_is_full() -> None:
    closed = _poi("c", venue={"opening_hours": closed_on_weekday(START)})
    days, report = _closures([[closed, _poi("a")], [_poi("b"), _poi("d")]], [], _helpers(), per_day=2)
    assert _ids(days) == [["b", "a"], ["c", "d"]] and report.actions[0]["action"] == feas.CLOSURE_SWAPPED


def test_it_is_replaced_by_a_useful_open_candidate_within_one_tier() -> None:
    closed = _poi("c", venue={"opening_hours": "off"})  # closed every day: nowhere to move it
    weak, good, dup = _poi("weak"), _poi("good"), _poi("dup")
    helpers = _helpers(tiers={"c": 3, "weak": 1, "good": 2, "dup": 3}, dup={frozenset({"dup", "a"})})
    days, report = _closures([[closed, _poi("a")]], [weak, good, dup], helpers)
    assert _ids(days) == [["good", "a"]] and report.actions[0]["action"] == feas.CLOSURE_REPLACED


def test_with_no_replacement_it_is_dropped_only_above_the_minimum() -> None:
    closed = _poi("c", venue={"opening_hours": "off"})
    days, report = _closures([[closed, _poi("a")]], [], _helpers(), minimum=1)
    assert _ids(days) == [["a"]] and report.actions[0]["action"] == feas.CLOSURE_DROPPED
    days, report = _closures([[closed, _poi("a")]], [], _helpers(), minimum=2)
    assert _ids(days) == [["c", "a"]] and report.actions[0]["action"] == feas.CLOSURE_KEPT
    days, _ = _closures([[closed]], [], _helpers(), minimum=0)
    assert _ids(days) == [["c"]]  # a day is never emptied


def test_a_must_visit_is_moved_but_never_replaced_or_dropped() -> None:
    closed = _poi("c", venue={"opening_hours": "off"})
    days, report = _closures([[closed, _poi("a")]], [_poi("good")], _helpers(must={"c"}), minimum=0)
    assert _ids(days) == [["c", "a"]] and report.actions[0] == {"day": 1, "action": feas.CLOSURE_KEPT, "must_visit": True}


@pytest.mark.parametrize("venue", [None, {}, {"opening_hours": "Mo-Fr 09:00-17:00 || PH off"}])
def test_unknown_hours_never_move_a_stop(venue: Any) -> None:
    days, report = _closures([[_poi("u", venue=venue), _poi("a")], [_poi("b")]], [_poi("x")], _helpers())
    assert _ids(days) == [["u", "a"], ["b"]] and report.actions == []


def test_a_move_never_disperses_the_receiving_day() -> None:
    closed = _poi("c", venue={"opening_hours": closed_on_weekday(START)})
    far_day = [_poi("f1", 1.0, 1.0), _poi("f2", 1.0, 1.001)]  # ~150 km away
    days, report = _closures([[closed, _poi("a")], far_day], [], _helpers(), minimum=5)
    assert _ids(days)[0] == ["c", "a"] and report.actions[0]["action"] == feas.CLOSURE_KEPT


def _remote(days, pool, helpers, *, healthy: bool = True, minimum: int = 1, interests=frozenset()):
    return feas.resolve_unreasoned_remote_days(
        days, pool, helpers, within_reach=lambda p: p["coordinates"]["lat"] < 0.5,
        requested_interests=frozenset(interests), start_date=START, holidays=frozenset(),
        supply_healthy=healthy, minimum_stops=minimum,
    )


def test_a_remote_day_without_a_reason_is_rebuilt_from_central_places() -> None:
    remote = [_poi("r1", 1.0), _poi("r2", 1.0, 0.001)]
    central = [_poi("c1"), _poi("c2", 0.0, 0.001), _poi("c3", 0.0, 0.002)]
    days, report = _remote([[_poi("a")], remote], central, _helpers())
    assert _ids(days) == [["a"], ["c1", "c2"]] and report.actions[0]["action"] == feas.REMOTE_REBUILT


@pytest.mark.parametrize("reason", ["must_visit", "sole_interest"])
def test_a_remote_day_with_a_reason_is_kept(reason: str) -> None:
    remote = [_poi("r1", 1.0)]
    helpers = _helpers(must={"r1"}) if reason == "must_visit" else _helpers(interests={"r1": {"waterfront"}})
    days, report = _remote([[_poi("a")], remote], [_poi("c1")], helpers, interests={"waterfront"})
    assert _ids(days) == [["a"], ["r1"]] and report.actions[0]["action"] == feas.REMOTE_KEPT_REASON


def test_a_remote_day_is_kept_without_healthy_supply_or_a_good_enough_alternative() -> None:
    remote = [_poi("r1", 1.0), _poi("r2", 1.0)]
    assert _ids(_remote([[_poi("a")], remote], [_poi("c1")], _helpers(), healthy=False)[0])[1] == ["r1", "r2"]
    helpers = _helpers(tiers={"r1": 3, "r2": 3, "c1": 1})  # two tiers lower
    days, report = _remote([[_poi("a")], remote], [_poi("c1")], helpers)
    assert _ids(days)[1] == ["r1", "r2"] and report.actions[0]["action"] == feas.REMOTE_KEPT_NO_ALTERNATIVE
    days, _ = _remote([[_poi("a")], remote], [_poi("c1")], _helpers(), minimum=3)  # would fall below R
    assert _ids(days)[1] == ["r1", "r2"]


# -- planner and validator end to end ----------------------------------------------------------


def _state(pois: list[dict[str, Any]], *, days: int = 2, must_visit: list[str] | None = None) -> PlanningState:
    st = PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville", start_date=START, end_date=START + timedelta(days=days - 1),
            travelers_count=2, travel_group_type=TravelGroupType.COUPLE, pace=TripPace.RELAXED,
            must_visit=must_visit or [],
        )
    )
    st.destination_context = DestinationContext(destination_name="Testville", candidate_pois=pois)
    st.candidate_quality_report = CandidateQualityService().build_report(st)
    return st


def _schedule(state: PlanningState) -> dict[str, int]:
    ExperiencePlannerService().run(state)
    return {e.provider_place_id: d.day_number for d in state.experience_plan.daily_plans for e in d.experiences}


def test_the_planner_moves_a_museum_off_its_listed_closing_day(monkeypatch: pytest.MonkeyPatch) -> None:
    pois = [_poi(f"m{i}", i * 1e-3) for i in range(3)]
    pois.append(_poi("closed_day_one", 0.0, 1e-3, venue={"opening_hours": closed_on_weekday(START)}))
    state = _state(pois)
    where = _schedule(state)
    assert where.get("closed_day_one") != 1  # never kept on the day it is listed closed
    PlanValidatorService().run(state)
    report = state.validation_report
    assert not any(issue.category == "venue_closed_on_scheduled_day" for issue in report.warnings)
    entries = {e.experience_id: e for e in report.venue_evidence}
    assert len(entries) == sum(len(d.experiences) for d in state.experience_plan.daily_plans)


def test_the_rollback_switch_restores_the_previous_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    pois = [_poi(f"m{i}", i * 1e-3) for i in range(3)]
    pois.append(_poi("closed_day_one", 0.0, 1e-3, venue={"opening_hours": closed_on_weekday(START)}))
    monkeypatch.setenv("VISIT_DAY_FEASIBILITY_ENABLED", "false")
    get_settings.cache_clear()
    try:
        off = _schedule(_state(pois))
    finally:
        monkeypatch.delenv("VISIT_DAY_FEASIBILITY_ENABLED")
        get_settings.cache_clear()
    on = _schedule(_state(pois))
    assert set(off) == set(on)  # the same places; only the day may differ


def test_the_validator_reports_a_kept_must_visit_that_is_closed_or_not_operating() -> None:
    state = _state([])
    from app.models.planning_state import DailyPlan, ExperienceItem, ExperiencePlan

    pois = [_poi("mv", venue={"opening_hours": "off"}, must_visit_terms=["the place"], must_visit_term="the place"),
            _poi("ok", venue={"opening_hours": "24/7"})]
    state = _state(pois, must_visit=["the place"])
    state.experience_plan = ExperiencePlan(daily_plans=[DailyPlan(day_number=1, date=START, experiences=[
        ExperienceItem(name="Place mv", category="museum", provider_place_id="mv"),
        ExperienceItem(name="Place ok", category="museum", provider_place_id="ok"),
    ])])
    PlanValidatorService().run(state)
    report = state.validation_report
    categories = {issue.category for issue in report.warnings}
    assert "venue_closed_on_scheduled_day" in categories
    statuses = [e.status for e in report.venue_evidence]
    assert statuses == [venue_evidence.LISTED_CLOSED_ON_DAY, venue_evidence.LISTED_OPEN_ON_DAY]
    assert report.venue_evidence[1].hours_on_day == ["00:00–24:00"]
    message = next(i.message for i in report.warnings if i.category == "venue_closed_on_scheduled_day")
    assert "kept because you asked" in message
    for word in ("provider", "configured", "inventory", "OpenStreetMap"):
        assert word not in message  # traveller wording
    for code in ("VENUE_CLOSED_ON_SCHEDULED_DAY", "VENUE_REPORTED_NOT_OPERATING"):
        assert review_classification.classify(code) == review_classification.MATERIAL


# -- the schedule-diversity hard rule binds both passes (review fix) -----------------------------


def _market_helpers(markets: set[str], cap: int = 1, **kwargs: Any) -> feas.Helpers:
    import dataclasses

    base = _helpers(**kwargs)
    return dataclasses.replace(
        base, market_excess=lambda stops: max(0, sum(1 for p in stops if p["place_id"] in markets) - cap)
    )


def test_a_closure_replacement_never_adds_a_market_beyond_the_hard_cap() -> None:
    closed = _poi("c", venue={"opening_hours": "off"})
    helpers = _market_helpers({"mk1", "mk2"}, tiers={"c": 3, "mk2": 3, "museum": 2})
    days, report = _closures([[closed, _poi("mk1")]], [_poi("mk2"), _poi("museum")], helpers)
    assert _ids(days) == [["museum", "mk1"]] and report.actions[0]["action"] == feas.CLOSURE_REPLACED


def test_a_move_never_adds_a_market_beyond_the_hard_cap() -> None:
    closed_market = _poi("mk2", venue={"opening_hours": closed_on_weekday(START)})
    helpers = _market_helpers({"mk1", "mk2"})
    days, report = _closures([[closed_market, _poi("a")], [_poi("mk1")]], [], _helpers(), minimum=5)
    assert report.actions[0]["action"] == feas.CLOSURE_MOVED  # without the rule it moves...
    days, report = _closures([[closed_market, _poi("a")], [_poi("mk1")]], [], helpers, minimum=5)
    # ...with it, never onto a day that already holds a market: a swap keeps one market per day
    assert report.actions[0]["action"] == feas.CLOSURE_SWAPPED
    assert all(helpers.market_excess(day) == 0 for day in days) and "mk2" in _ids(days)[1]


def test_a_rebuilt_remote_day_never_exceeds_the_hard_cap() -> None:
    remote = [_poi("r1", 1.0), _poi("r2", 1.0, 0.001)]
    central = [_poi("mk1"), _poi("mk2", 0.0, 0.001), _poi("m1", 0.0, 0.002)]
    helpers = _market_helpers({"mk1", "mk2"})
    days, _ = _remote([[_poi("a")], remote], central, helpers)
    assert sum(1 for p in days[1] if p["place_id"].startswith("mk")) <= 1
