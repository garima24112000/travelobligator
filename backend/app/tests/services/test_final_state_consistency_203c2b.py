from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
    build_candidate_id,
)
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.planning_state import (
    DailyPlan,
    DestinationContext,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    RestaurantSuggestion,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.models.routing import RouteFeasibilityReport, RouteFeasibilityStatus, RouteLegFeasibility, RouteResult
from app.services import schedule_diversity as diversity
from app.services.candidate_quality_service import CandidateQualityService
from app.services.day_rationale import (
    RATIONALE_WARNING_PREFIX,
    current_day_rationale,
    deterministic_day_summary,
    finalize_day_explanations,
)
from app.services.entity_collisions import SUSPECT_COLLISION_KEY
from app.services.experience_planner_service import ExperiencePlannerService, recompute_food_suggestions
from app.services.interest_coverage import final_food_evidence, interest_coverage
from app.services.plan_validator_service import PlanValidatorService
from app.services.route_burden_repair_service import RouteBurdenRepairService
from app.services.route_feasibility_service import RouteFeasibilityService

# Section 203C.2B final state consistency: requested-interest coverage is
# judged on the FINAL user-visible itinerary (including final nearby food),
# and an AI day rationale is shown only for the exact day the model proposed.
# Every fixture is synthetic (invented names on a small coordinate grid).

_START = date(2026, 11, 10)


def _point(lat: float, lng: float) -> GeoPoint:
    return GeoPoint(lat=lat, lng=lng)


def _near(offset: int) -> GeoPoint:
    return _point(50.000 + 0.001 * offset, 10.000 + 0.001 * offset)


def _poi(key: str, name: str, point: GeoPoint, **extra: Any) -> dict[str, Any]:
    return {
        "place_id": f"geoapify/{key}", "name": name, "category": "museum",
        "coordinates": {"lat": point.lat, "lng": point.lng}, "source": "geoapify_places",
        "data_status": "live", "confidence": 0.6, "provider_tags": {"tourism": "museum"}, **extra,
    }


def _castle(key: str, name: str, point: GeoPoint, **extra: Any) -> dict[str, Any]:
    return _poi(key, name, point, category="castle", provider_tags={"historic": "castle"}, **extra)


def _market(key: str, name: str, point: GeoPoint, **extra: Any) -> dict[str, Any]:
    # a market the provider records a food trade for (the evidence that makes it a food experience)
    return _poi(
        key, name, point, category="marketplace", provider_tags={"amenity": "marketplace", "shop": "greengrocer"}, **extra
    )


def _state(pois: list[dict[str, Any]], days: int = 1, interests: list[str] | None = None, **trip: Any) -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Fixtureville, Fixtureland", start_date=_START,
            end_date=_START + timedelta(days=days - 1), travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE, pace=TripPace.BALANCED,
            interests=interests or ["architecture", "history", "food"], **trip,
        )
    )
    state.destination_context = DestinationContext(
        destination_name="Fixtureville, Fixtureland", candidate_pois=pois, candidate_restaurants=[]
    )
    state.candidate_quality_report = CandidateQualityService().build_report(state)
    return state


def _stop(poi: dict[str, Any], **extra: Any) -> ExperienceItem:
    return ExperienceItem(
        experience_id=f"exp-{poi['place_id']}", name=poi["name"], category=poi["category"],
        coordinates=GeoPoint(**poi["coordinates"]), provider_place_id=poi["place_id"],
        provider_source="geoapify_places", **extra,
    )


def _food(name: str, point: GeoPoint | None, category: str = "restaurant", source: str | None = "geoapify_places") -> RestaurantSuggestion:
    return RestaurantSuggestion(
        name=name, category=category, coordinates=point, source=source,
        data_status=DataStatus.LIVE, confidence=0.6, why_suggested="Nearby.",
    )


def _with_day(state: PlanningState, stops: list[ExperienceItem], food: list[RestaurantSuggestion]) -> PlanningState:
    state.experience_plan = ExperiencePlan(
        daily_plans=[DailyPlan(day_number=1, date=_START, experiences=stops, restaurant_suggestions=food)]
    )
    return state


def _codes(state: PlanningState) -> list[str]:
    return list(PlanValidatorService().run(state).validation_report.review_codes)


# Supply that matches every interest, so an uncovered interest is a real
# under-coverage finding (not a supply limit).
_CASTLE = _castle("c", "Old Castle", _near(0))
_MUSEUM = _poi("m", "Town Museum", _near(1))
_MARKET = _market("k", "Covered Market", _near(2))
_POOL = [_CASTLE, _MUSEUM, _MARKET]


# =====================================================================================
# 1. Food interest coverage from final user-visible evidence
# =====================================================================================


def test_a_scheduled_factual_food_experience_covers_food() -> None:
    state = _with_day(_state(_POOL), [_stop(_CASTLE, matched_interests=["history"]),
                                      _stop(_MARKET, matched_interests=["food"])], [])
    assert interest_coverage(state)["food"] is True
    report = PlanValidatorService().run(state).validation_report
    undercovered = [issue.message for issue in report.warnings if issue.category == "interest_undercoverage"]
    assert not any("'food'" in message for message in undercovered)
    assert any("'architecture'" in message for message in undercovered)  # genuinely missing here

    # with every requested interest served, no under-coverage code at all
    stops = [_stop(_CASTLE, matched_interests=["history", "architecture"]), _stop(_MARKET, matched_interests=["food"])]
    assert "INTEREST_UNDERCOVERAGE" not in _codes(_with_day(_state(_POOL), stops, []))


def test_valid_nearby_food_covers_food_without_any_food_attraction() -> None:
    stops = [_stop(_CASTLE, matched_interests=["history"]), _stop(_MUSEUM, matched_interests=["history"])]
    food = [_food("Corner Cafe", _point(50.0005, 10.0005), category="cafe"),
            _food("Station Diner", _point(50.0015, 10.0015))]
    state = _with_day(_state(_POOL), stops, food)

    assert final_food_evidence(state) == ["Corner Cafe", "Station Diner"]
    coverage = interest_coverage(state)
    assert coverage == {"architecture": False, "history": True, "food": True}
    report = PlanValidatorService().run(state).validation_report
    undercovered = [issue.message for issue in report.warnings if issue.category == "interest_undercoverage"]
    assert not any("'food'" in message for message in undercovered)


def test_food_without_valid_final_food_evidence_is_still_under_covered() -> None:
    stops = [_stop(_CASTLE, matched_interests=["history"]), _stop(_MUSEUM, matched_interests=["history"])]
    for food in (
        [],                                                              # nothing suggested
        [_food("Far Diner", _point(50.10, 10.10))],                       # beyond the nearby radius
        [_food("Unverified Diner", _point(50.0005, 10.0005), source=None)],  # no provider source
        [_food("Located Nowhere", None)],                                 # no coordinates
        [_food("Hotel Lobby", _point(50.0005, 10.0005), category="hotel")],  # not a food place
    ):
        state = _with_day(_state(_POOL), stops, food)
        assert interest_coverage(state)["food"] is False and final_food_evidence(state) == []
        report = PlanValidatorService().run(state).validation_report
        assert any(
            issue.category == "interest_undercoverage" and "'food'" in issue.message for issue in report.warnings
        )


def test_a_food_suggestion_made_for_a_superseded_day_does_not_count() -> None:
    old_stop = _stop(_CASTLE, matched_interests=["history"])
    state = _with_day(_state(_POOL), [old_stop], [_food("Corner Cafe", _point(50.0005, 10.0005))])
    assert interest_coverage(state)["food"] is True

    # the day's stops change to a far-away place; the old suggestion is still attached
    far = _castle("far", "Ridge Fort", _point(50.20, 10.20))
    state.experience_plan.daily_plans[0].experiences = [_stop(far, matched_interests=["history"])]
    assert interest_coverage(state)["food"] is False  # judged against the FINAL stops, so it never counts

    # and the final recomputation removes it from what the traveller is shown
    state.destination_context.candidate_restaurants = [
        {"place_id": "geoapify/r1", "name": "Corner Cafe", "category": "restaurant",
         "coordinates": {"lat": 50.0005, "lng": 10.0005}, "source": "geoapify_places", "data_status": "live"}
    ]
    recompute_food_suggestions(state)
    assert state.experience_plan.daily_plans[0].restaurant_suggestions == []
    assert final_food_evidence(state) == []


def test_architecture_and_history_coverage_are_unchanged_by_food_evidence() -> None:
    food = [_food("Corner Cafe", _point(50.0005, 10.0005))]
    state = _with_day(_state(_POOL), [_stop(_MUSEUM, matched_interests=["history"])], food)
    coverage = interest_coverage(state)
    assert coverage["history"] is True and coverage["architecture"] is False  # food never covers anything else
    report = PlanValidatorService().run(state).validation_report
    assert any(
        issue.category == "interest_undercoverage" and "'architecture'" in issue.message for issue in report.warnings
    )

    # nightlife-like or other interests are never satisfied by food suggestions either
    state = _with_day(_state(_POOL, interests=["nightlife"]), [_stop(_MUSEUM)], food)
    assert interest_coverage(state) == {"nightlife": False}


def test_a_food_interest_does_not_relax_the_marketplace_rule() -> None:
    assert not diversity.markets_explicitly_requested(["food"])
    assert diversity.hard_excess([diversity.MARKETPLACE] * 2 + [diversity.MUSEUM_CULTURE], False) == {
        diversity.MARKETPLACE: 1
    }
    markets = [_market(f"k{i}", f"Market K{i}", _near(i)) for i in range(4)]
    others = [_castle(f"c{i}", f"Castle C{i}", _near(i + 4)) for i in range(3)]
    state = _state([*markets, *others], interests=["food"])
    ExperiencePlannerService().run(state)
    classes = [diversity.coarse_class(stop.normalized_category) for stop in state.experience_plan.daily_plans[0].experiences]
    assert classes.count(diversity.MARKETPLACE) <= 1


# =====================================================================================
# 2. One authoritative explanation per final day
# =====================================================================================


def _reasoning(days: list[list[dict[str, Any]]]) -> AIItineraryReasoningResult:
    return AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.COMPLETED,
        strategy=ItineraryReasoningStrategy(summary="A balanced plan.", pace="balanced", reason="Matches the request."),
        days=[
            ItineraryReasoningDayPlan(
                day_index=index,
                candidate_ids=[build_candidate_id(poi["source"], poi["place_id"]) for poi in day],
                rationale=f"RATIONALE_FOR_PROPOSED_DAY_{index}",
            )
            for index, day in enumerate(days, start=1)
        ],
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        confidence=0.8,
        provider_name="fake_provider",
    )


def _rationales(day: DailyPlan) -> list[str]:
    return [warning for warning in day.warnings if "RATIONALE_FOR_PROPOSED_DAY" in warning]


def _assert_only_the_factual_summary(state: PlanningState, day: DailyPlan) -> None:
    assert _rationales(day) == [] and day.ai_rationale_warning is None
    assert current_day_rationale(state, day) is None
    assert day.goal == deterministic_day_summary([stop.name for stop in day.experiences])


_CASTLES = [_castle(f"h{i}", f"Castle H{i}", _near(i)) for i in range(3)]


def test_an_unchanged_ai_day_keeps_its_rationale_with_provenance() -> None:
    state = _state([*_CASTLES, _MUSEUM])
    state.ai_itinerary_reasoning_result = _reasoning([_CASTLES])
    ExperiencePlannerService().run(state)

    day = state.experience_plan.daily_plans[0]
    assert [stop.name for stop in day.experiences] == ["Castle H0", "Castle H1", "Castle H2"]
    expected = f"{RATIONALE_WARNING_PREFIX}RATIONALE_FOR_PROPOSED_DAY_1"
    assert _rationales(day) == [expected] and day.ai_rationale_warning == expected
    finalize_day_explanations(state)  # idempotent
    assert _rationales(day) == [expected]


def test_a_day_the_pace_cap_trimmed_does_not_keep_the_rationale_for_the_longer_day() -> None:
    # the model proposed four stops ending at a market; a balanced day holds three
    state = _state([*_CASTLES, _MARKET])
    state.ai_itinerary_reasoning_result = _reasoning([[*_CASTLES, _MARKET]])
    ExperiencePlannerService().run(state)

    day = state.experience_plan.daily_plans[0]
    assert "Covered Market" not in [stop.name for stop in day.experiences]
    _assert_only_the_factual_summary(state, day)


def test_a_diversity_replacement_invalidates_the_rationale() -> None:
    markets = [_market(f"k{i}", f"Market K{i}", _near(i)) for i in range(2)]
    state = _state([*markets, _CASTLES[0], _MUSEUM])
    state.ai_itinerary_reasoning_result = _reasoning([[markets[0], markets[1], _CASTLES[0]]])
    ExperiencePlannerService().run(state)

    day = state.experience_plan.daily_plans[0]
    assert state.experience_plan.schedule_diversity[0].replacements  # a market was replaced
    _assert_only_the_factual_summary(state, day)


def test_a_suspected_duplicate_replacement_invalidates_the_rationale() -> None:
    twin_a = _poi("hall-en", "Grand Hall Museum", _near(1), **{SUSPECT_COLLISION_KEY: "collision-1"})
    twin_b = _poi("hall-local", "Grand Hall", _near(1), **{SUSPECT_COLLISION_KEY: "collision-1"})
    spare = _castle("spare", "Spare Castle", _near(3))
    state = _state([twin_a, twin_b, _CASTLES[0], spare])
    state.ai_itinerary_reasoning_result = _reasoning([[twin_a, twin_b, _CASTLES[0]]])
    ExperiencePlannerService().run(state)

    day = state.experience_plan.daily_plans[0]
    assert state.experience_plan.collision_separations  # one of the pair was replaced
    _assert_only_the_factual_summary(state, day)


class _Routing:
    provider_name = "fixture_routing"


class _Gateway:
    """Walking routes at 60000 s per degree of fixture separation."""

    routing = _Routing()

    def get_route_sequence(self, points: list[tuple[float, float]], provider_context: Any = None) -> list[RouteResult]:
        return [
            RouteResult(
                provider="fixture_routing", status=ProviderStatus.SUCCESS, source="fixture_routing", mode="walk",
                duration_seconds=round((abs(a[0] - b[0]) + abs(a[1] - b[1])) * 60000.0, 3),
                distance_meters=round((abs(a[0] - b[0]) + abs(a[1] - b[1])) * 72000.0, 3), confidence=0.9,
            )
            for a, b in zip(points, points[1:])
        ]


def test_a_route_burden_replacement_invalidates_the_rationale_and_reload_stays_clean() -> None:
    # an isolated viewpoint (not a primary anchor, so the repair may replace it) and a nearby one
    viewpoint = {"category": "viewpoint", "provider_tags": {"tourism": "viewpoint"}}
    far = _poi("far", "Ridge Lookout", _point(50.000, 10.100), **viewpoint)
    near = [_poi("a", "Museum Alpha", _near(0)), _poi("b", "Museum Beta", _near(2))]
    spare = _poi("good", "Garden Lookout", _near(3), **viewpoint)
    state = _state([*near, far, spare], interests=["museums"])
    state.ai_itinerary_reasoning_result = _reasoning([[*near, far]])
    ExperiencePlannerService().run(state)
    day = state.experience_plan.daily_plans[0]
    assert [stop.name for stop in day.experiences] == ["Museum Alpha", "Museum Beta", "Ridge Lookout"]
    assert _rationales(day)  # the model's own day: its rationale is shown

    feasibility = RouteFeasibilityService(gateway=_Gateway())
    state.route_feasibility_report = feasibility.build_report(state)
    report = RouteBurdenRepairService(feasibility).repair(state)
    assert report is not None and report.attempts[0].accepted
    finalize_day_explanations(state)  # the route stage's final pass
    _assert_only_the_factual_summary(state, day)

    reloaded = PlanningState.model_validate_json(state.model_dump_json())
    reloaded_day = reloaded.experience_plan.daily_plans[0]
    assert _rationales(reloaded_day) == [] and reloaded_day.ai_rationale_warning is None
    assert reloaded_day.goal == day.goal


def test_a_changed_day_never_shows_both_the_old_rationale_and_the_new_summary() -> None:
    state = _state([*_CASTLES, _MUSEUM])
    state.ai_itinerary_reasoning_result = _reasoning([_CASTLES])
    ExperiencePlannerService().run(state)
    day = state.experience_plan.daily_plans[0]
    assert _rationales(day)

    # any later step that changes the day's places: the final pass resolves it
    day.experiences[2] = _stop(_MUSEUM)
    finalize_day_explanations(state)
    _assert_only_the_factual_summary(state, day)
    assert "Town Museum" in (day.goal or "")


def test_a_state_stored_before_rationale_provenance_is_cleaned_the_same_way() -> None:
    state = _state([*_CASTLES, _MUSEUM])
    state.ai_itinerary_reasoning_result = _reasoning([_CASTLES])
    stops = [_stop(_CASTLES[0]), _stop(_CASTLES[1]), _stop(_MUSEUM)]  # not the model's day
    stored = _with_day(state, stops, []).model_dump(mode="json")
    stored["experience_plan"]["daily_plans"][0].pop("ai_rationale_warning")
    stored["experience_plan"]["daily_plans"][0]["warnings"] = [
        "A feasibility note.", f"{RATIONALE_WARNING_PREFIX}RATIONALE_FOR_PROPOSED_DAY_1",
    ]

    loaded = PlanningState.model_validate(stored)
    finalize_day_explanations(loaded)
    day = loaded.experience_plan.daily_plans[0]
    assert day.warnings == ["A feasibility note."]
    _assert_only_the_factual_summary(loaded, day)
