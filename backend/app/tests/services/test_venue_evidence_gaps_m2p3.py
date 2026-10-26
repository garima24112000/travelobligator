"""M2 Phase 3, after the first development run: the observed evidence gaps.

The run's own stored records showed (only the SHAPES are used here; no place
name or destination): four hours values the reader refused although they are
well-formed (`Mo-Fr, Su ...`, `PH,Tu-Su ...`, a seasonal month-day schedule, and
`closed "<comment>"`), and four stops without hours because grounded AI anchors
lost the venue evidence their Place Details answer carried. A day reported
`elevated` was within every limit and only needed vehicle transfers."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.models.ai_candidate_promotion import PromotedAICandidate
from app.models.candidate_grounding import CandidateGroundingEvidence, ProviderCandidateForGrounding
from app.models.candidate_quality import CandidateQualityTier, CandidateRejectReason
from app.models.common import DataStatus, GeoPoint
from app.services import opening_hours as oh
from app.services import venue_evidence
from app.services import venue_status as vs
from app.services.candidate_quality_service import CandidateQualityService

MON, TUE, WED, SAT, SUN = date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 21), date(2026, 11, 22)


def status(value: str, day: date, holidays: frozenset[date] | None = frozenset()) -> tuple[str, list[str]]:
    reading = oh.hours_on(value, day, holidays)
    return reading.status, oh.format_intervals(reading.intervals)


# -- the four observed shapes --------------------------------------------------------------


def test_a_comma_space_weekday_list_is_one_list() -> None:
    value = "Mo-Fr, Su 10:00-18:00"
    assert status(value, MON) == (oh.OPEN, ["10:00–18:00"])
    assert status(value, SAT) == (oh.CLOSED, [])
    assert status(value, SUN) == (oh.OPEN, ["10:00–18:00"])
    two_rules = "Mo-Tu, Th-Fr 09:00-14:00; We 09:00-20:00"
    assert status(two_rules, MON) == (oh.OPEN, ["09:00–14:00"])
    assert status(two_rules, WED) == (oh.OPEN, ["09:00–20:00"])
    assert status(two_rules, SAT) == (oh.CLOSED, [])


def test_a_holiday_first_list_is_read_and_closes_the_unnamed_weekday() -> None:
    value = "PH,Tu-Su 09:00-18:00"
    assert status(value, MON) == (oh.CLOSED, [])  # the weekly closing day
    assert status(value, TUE) == (oh.OPEN, ["09:00–18:00"])
    assert status(value, MON, frozenset({MON})) == (oh.OPEN, ["09:00–18:00"])  # a holiday Monday opens
    assert oh.hours_on(value, MON, None).reason == "holiday_data_unavailable"


def test_an_additional_rule_needs_a_space_after_its_comma() -> None:
    # `,Tu` is a list item, `, Tu` an additional rule: both read the same here
    assert status("Mo,We 10:00-12:00", WED) == (oh.OPEN, ["10:00–12:00"])
    assert status("Mo 10:00-12:00, We 14:00-16:00", WED) == (oh.OPEN, ["14:00–16:00"])
    assert status("Mo 10:00-12:00, We 14:00-16:00", MON) == (oh.OPEN, ["10:00–12:00"])


def test_seasonal_month_day_ranges_including_the_year_end() -> None:
    value = "Apr 1-Jun 30,Sep 1-Oct 31 08:00-17:30; Jul 1-Aug 31 08:00-18:30; Nov 1-Mar 31 08:00-17:00"
    assert status(value, date(2026, 5, 4)) == (oh.OPEN, ["08:00–17:30"])
    assert status(value, date(2026, 7, 15)) == (oh.OPEN, ["08:00–18:30"])
    assert status(value, date(2026, 11, 16)) == (oh.OPEN, ["08:00–17:00"])
    assert status(value, date(2027, 1, 10)) == (oh.OPEN, ["08:00–17:00"])  # across the year end
    assert status(value, date(2027, 3, 31)) == (oh.OPEN, ["08:00–17:00"])
    assert status(value, date(2027, 4, 1)) == (oh.OPEN, ["08:00–17:30"])


@pytest.mark.parametrize(
    ("value", "day", "expected"),
    [
        ("Mo-Su 10:00-18:00; Dec 24-26 off", date(2026, 12, 25), (oh.CLOSED, [])),  # a later rule replaces
        ("Mo-Su 10:00-18:00; Dec 24-26 off", date(2026, 12, 27), (oh.OPEN, ["10:00–18:00"])),
        ("Jan-Mar Sa,Su 10:00-16:00", date(2027, 2, 6), (oh.OPEN, ["10:00–16:00"])),
        ("Jan-Mar Sa,Su 10:00-16:00", date(2027, 2, 8), (oh.CLOSED, [])),
        ("Jan-Mar Sa,Su 10:00-16:00", date(2027, 4, 3), (oh.CLOSED, [])),  # dates not covered are closed
        ("Nov-Feb Mo off; Mo-Su 09:00-17:00", MON, (oh.OPEN, ["09:00–17:00"])),  # order decides
        ("Mo-Su 09:00-17:00; Nov-Feb Mo off", MON, (oh.CLOSED, [])),
    ],
)
def test_month_precedence(value: str, day: date, expected: tuple) -> None:
    assert status(value, day) == expected


def test_a_closure_comment_keeps_the_place_closed() -> None:
    value = 'closed "Vorübergehend geschlossen"'
    assert all(status(value, day)[0] == oh.CLOSED for day in (MON, SAT, SUN))
    assert vs.venue_status({"tourism": "museum"}, {"opening_hours": value}).status == vs.LISTED_CLOSED
    score = CandidateQualityService().score_attraction({
        "place_id": "x", "name": "x", "category": "museum", "provider_tags": {"tourism": "museum"},
        "venue_tags": {"opening_hours": value}, "coordinates": {"lat": 0, "lng": 0}, "source": "s",
        "data_status": "live", "confidence": 0.6,
    })
    assert score.quality_tier == CandidateQualityTier.REJECTED
    assert CandidateRejectReason.VENUE_REPORTED_NOT_OPERATING in score.reject_reasons
    assert status('Mo off "staff training"; Tu-Su 10:00-18:00', MON) == (oh.CLOSED, [])


@pytest.mark.parametrize(
    ("value", "feature"),
    [
        ('Mo-Fr 09:00-17:00 "by appointment"', oh.FEATURE_COMMENT),  # a comment on an OPEN rule changes meaning
        ("Mo-Fr 09:00-17:00 || PH off", oh.FEATURE_FALLBACK),
        ("Mo-Su sunrise-sunset", oh.FEATURE_SUN_TIMES),
        ("Sa 10:00+", oh.FEATURE_OPEN_END),
        ("SH off; Mo-Fr 09:00-17:00", oh.FEATURE_SCHOOL_HOLIDAY),
        ("2026 Mo-Fr 09:00-17:00", oh.FEATURE_YEAR_OR_WEEK),
        ("Su[-1] -1 day 10:00-12:00", oh.FEATURE_NTH_WEEKDAY),  # Final Batch 1: `Mo[1]` alone is now read
        ("Mo[1] 10:00-12:00", None),
        ("Mo-Fr, Su", oh.FEATURE_OTHER),
        ("Mo-Fr 09:00-17:00", None),
        ("", None),
    ],
)
def test_unsupported_values_are_named_by_a_fixed_label(value: str, feature: str | None) -> None:
    assert oh.unsupported_feature(value) == feature
    if feature:
        reading = oh.hours_on(value, MON, frozenset())
        assert reading.status == oh.UNKNOWN and reading.feature == feature


# -- evidence carried through the grounded-anchor pipeline ---------------------------------------


def test_grounding_and_promotion_carry_only_venue_keys() -> None:
    venue = {"opening_hours": "Tu-Su 10:00-18:00", "check_date": "2026-06-03"}
    common = {"provider_name": "geoapify", "provider_place_id": "geoapify/x", "coordinates": GeoPoint(lat=0, lng=0),
              "data_status": DataStatus.LIVE, "confidence": 0.6}
    assert ProviderCandidateForGrounding(name="X", venue_tags=venue, **common).venue_tags == venue
    assert CandidateGroundingEvidence(matched_name="X", match_type="exact_name", venue_tags=venue,
                                      **common).venue_tags == venue
    with pytest.raises(ValueError):
        ProviderCandidateForGrounding(name="X", venue_tags={"phone": "1"}, **common)
    assert PromotedAICandidate(candidate_id="c", name="X", source="s", venue_tags=venue).venue_tags == venue


def test_the_promoted_candidate_reaches_the_planner_with_its_evidence() -> None:
    from app.services.experience_planner_service import _promoted_candidate_to_poi_dict

    promoted = PromotedAICandidate(candidate_id="c", name="X", source="s", provider_place_id="geoapify/x",
                                   coordinates=GeoPoint(lat=0, lng=0), venue_tags={"opening_hours": "24/7"})
    assert _promoted_candidate_to_poi_dict(promoted)["venue_tags"] == {"opening_hours": "24/7"}


def _state_with(stops: list[tuple[str, dict | None, str]]) -> Any:
    from app.models.ai_candidate_promotion import AICandidatePromotionReport
    from app.models.planning_state import (
        DailyPlan,
        DestinationContext,
        ExperienceItem,
        ExperiencePlan,
        PlanningState,
        TravelGroupType,
        TripRequest,
    )

    state = PlanningState(trip_request=TripRequest(primary_destination="Testville", start_date=MON, end_date=MON,
                                                   travelers_count=1, travel_group_type=TravelGroupType.SOLO))
    pool = [{"place_id": pid, "name": pid, "provider_tags": {"tourism": "museum"}, "venue_tags": venue,
             "coordinates": {"lat": 0, "lng": 0}, "source": "s", "data_status": "live"}
            for pid, venue, where in stops if where == "pool"]
    state.destination_context = DestinationContext(destination_name="Testville", candidate_pois=pool)
    promoted = [PromotedAICandidate(candidate_id=pid, name=pid, source="s", provider_place_id=pid, venue_tags=venue,
                                    provider_tags={"tourism": "museum"})
                for pid, venue, where in stops if where == "promoted"]
    state.ai_candidate_promotion_report = AICandidatePromotionReport(
        trip_id=state.trip_id, status="promoted" if promoted else "no_eligible_candidates",
        promoted_candidates=promoted, promoted_count=len(promoted), total_reviewed_candidates=len(promoted),
        generated_at="2026-10-24T00:00:00Z",
    )
    state.experience_plan = ExperiencePlan(daily_plans=[DailyPlan(day_number=1, date=MON, experiences=[
        ExperienceItem(name=pid, category="museum", provider_place_id=pid) for pid, _, _ in stops
    ])])
    return state


def test_unknown_hours_name_their_cause_and_record() -> None:
    state = _state_with([
        ("anchor_with_hours", {"opening_hours": "Mo-Su 10:00-18:00"}, "promoted"),
        ("anchor_without", None, "promoted"),
        ("pool_no_hours", {"check_date": "2025-01-01"}, "pool"),
        ("pool_odd", {"opening_hours": "Mo-Su sunrise-sunset"}, "pool"),
        ("nowhere", None, "missing"),
    ])
    by_id = {e.name: e for e in venue_evidence.stop_evidence(state)}
    assert (by_id["anchor_with_hours"].status, by_id["anchor_with_hours"].record) == (
        venue_evidence.LISTED_OPEN_ON_DAY, venue_evidence.RECORD_PROMOTED)
    assert by_id["anchor_without"].unknown_cause == "record_without_venue_evidence"
    assert by_id["pool_no_hours"].unknown_cause == "no_hours_in_record"
    assert by_id["pool_odd"].unknown_cause == "unsupported:sunrise_sunset"
    assert (by_id["nowhere"].record, by_id["nowhere"].unknown_cause) == (venue_evidence.RECORD_NONE, "no_record")


def test_the_metrics_report_causes_and_elevated_reasons() -> None:
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "quality_metrics.py"
    spec = importlib.util.spec_from_file_location("quality_metrics_gaps_under_test", path)
    metrics = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = metrics
    spec.loader.exec_module(metrics)
    state = _state_with([("a", None, "promoted"), ("b", {"opening_hours": "Mo-Su sunrise-sunset"}, "pool")])
    venue = metrics.venue_metrics(state)
    assert venue["hours_unknown_by_cause"] == {"record_without_venue_evidence": 1, "unsupported:sunrise_sunset": 1}
    assert venue["stops_by_record"] == {"pool": 1, "promoted_anchor": 1}

    from app.services.route_burden import DayRouteAssessment, DayRouteBurden

    def assessment(drive_legs: int, walking: float) -> DayRouteAssessment:
        burden = DayRouteBurden(day_number=1, required_legs=2, routed_legs=2, total_duration_seconds=1200,
                                total_distance_meters=11000, max_leg_duration_seconds=700, max_leg_distance_meters=5800,
                                walking_duration_seconds=walking, walking_distance_meters=0,
                                max_walk_leg_duration_seconds=0, max_walk_leg_distance_meters=0, drive_legs=drive_legs,
                                max_drive_leg_duration_seconds=700, over_day_limit=False, over_leg_limit=False,
                                over_transfer_limit=False)
        return DayRouteAssessment(day_number=1, severity="elevated", burden=burden, unverified_legs=0,
                                  definitive_failures=0, ferry_legs=0, offending_legs=())

    assert metrics._elevated_because(assessment(2, 0)) == ["needs_a_vehicle_transfer"]
    assert metrics._elevated_because(assessment(0, 10**6)) == ["walking_beyond_the_relaxed_pace_total"]


def test_a_record_read_without_venue_keys_is_told_apart_from_no_record() -> None:
    from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter

    adapter = GeoapifyPlacesAdapter.__new__(GeoapifyPlacesAdapter)
    adapter.provider_name = "geoapify_places"
    base = {"name": "A", "place_id": "p1", "lat": 1.0, "lon": 2.0, "categories": ["entertainment.museum"]}
    read_empty = adapter._place_from_properties({**base, "datasource": {"raw": {"tourism": "museum"}}})
    not_read = adapter._place_from_properties(base)
    assert read_empty.venue_tags == {} and not_read.venue_tags is None
    # Place Details on a place that had no raw record: read now, even when empty
    detailed = adapter._apply_details(not_read, {"categories": ["entertainment.museum"],
                                                 "datasource": {"raw": {"tourism": "museum"}}})
    assert detailed.venue_tags == {}
