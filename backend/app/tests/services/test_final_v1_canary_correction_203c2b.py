from __future__ import annotations

import importlib.util
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalFailureKind,
    AICandidateProposalRequest,
    AICandidateProposalStatus,
    AICandidateProposalTask,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
    build_candidate_id,
)
from app.models.candidate_quality import CandidateQualityTier
from app.models.common import GeoPoint, ProviderStatus, ReadinessStatus
from app.models.planning_state import (
    DailyPlan,
    DestinationContext,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.models.routing import RouteFeasibilityReport, RouteFeasibilityStatus, RouteLegFeasibility
from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider
from app.providers.ai_candidate_proposal.proposal_validation import validate_proposals
from app.providers.weather import open_meteo_adapter
from app.providers.weather.open_meteo_adapter import FORECAST_NOT_YET_AVAILABLE, OpenMeteoWeatherAdapter
from app.services import experience_planner_service as planner_module
from app.services.candidate_quality_service import CandidateQualityService
from app.services.day_order_heuristics import balanced_day_sizes, balanced_spatial_clusters, grouping_length_km
from app.services.day_rationale import RATIONALE_WARNING_PREFIX, current_day_rationale, deterministic_day_summary
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.plan_validator_service import PlanValidatorService
from app.services.route_burden import LONG_TRAVEL_DAY, day_route_burdens
from app.storage.provider_cache_store import ProviderCacheStore

# Section 203C.2B canary correction: generic planner-quality fixes exposed by
# the first live canary. Every fixture below is synthetic (invented names and
# coordinates on a small grid); nothing here is city-specific.

_START = date(2026, 11, 10)


# =====================================================================================
# 1. Anchor proposal robustness
# =====================================================================================


def _raw_proposal(index: int, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "proposal_id": f"proposal_{index:03d}",
        "proposal_type": "named_place",
        "candidate_name": f"Old Place {index}",
        "search_query": f"Old Place {index}",
        "candidate_type": "landmark",
        "priority_hint": "high",
        "suggested_area": None,
        "why_consider": "A well-known historic site illustrating the city's past.",
        "fit_with_user_preferences": ["history"],
        "verification_requirements": ["must_ground_by_name_and_location"],
        "confidence": 0.8,
    }
    fields.update(overrides)
    return fields


def _proposal_request(max_candidates: int = 17) -> AICandidateProposalRequest:
    return AICandidateProposalRequest(
        task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        trip_id="trip_1",
        destination_name="Fixtureville, Fixtureland",
        trip_duration_days=3,
        max_candidates=max_candidates,
    )


class _Client:
    def __init__(self, *outputs: Any) -> None:
        self._outputs = list(outputs)
        self.calls = 0

    def invoke(self, prompt: str) -> Any:
        self.calls += 1
        output = self._outputs[min(self.calls - 1, len(self._outputs) - 1)]
        if isinstance(output, Exception):
            raise output
        return output


def _batch(*proposals: dict[str, Any]) -> dict[str, Any]:
    return {"proposals": list(proposals), "rejected_raw_items": [], "confidence": 0.7}


@pytest.mark.parametrize(
    "text",
    [
        "A historic site illustrating the city's past.",
        "A museum celebrating local craft, operating as a cultural venue.",
        "A priceless collection of tiles.",
        "Decorating traditions on display.",
    ],
)
def test_ordinary_words_are_not_mistaken_for_factual_claims(text: str) -> None:
    proposal = AICandidateProposal(**_raw_proposal(1, why_consider=text))
    assert proposal.why_consider == text


@pytest.mark.parametrize(
    "text",
    ["It has a 4.8 rating.", "Great ratings online.", "Low price tickets.", "Highly rated by visitors.", "Cheap entry."],
)
def test_real_factual_claims_are_still_blocked(text: str) -> None:
    with pytest.raises(ValueError):
        AICandidateProposal(**_raw_proposal(1, why_consider=text))


def test_one_invalid_proposal_is_dropped_without_discarding_the_valid_ones() -> None:
    secret_text = "MODEL_OUTPUT_SENTINEL costs a low price"
    raw = [
        _raw_proposal(1),
        _raw_proposal(2, why_consider=secret_text),  # a real forbidden claim
        _raw_proposal(3, candidate_name=None),  # named_place without a name
        "not an object",
        _raw_proposal(4),
    ]
    valid, dropped, summary = validate_proposals(raw)
    assert [p.proposal_id for p in valid] == ["proposal_001", "proposal_004"]
    assert dropped == 3
    assert "MODEL_OUTPUT_SENTINEL" not in summary and "price" not in summary  # field names + error types only
    assert "why_consider" in summary and "entry:not_an_object" in summary


def test_groq_proposal_keeps_valid_anchors_and_records_what_it_dropped() -> None:
    client = _Client(_batch(_raw_proposal(1), _raw_proposal(2, why_consider="Top rating in town"), _raw_proposal(3)))
    result = GroqAICandidateProposalProvider(client=client).propose(_proposal_request())

    assert result.status == AICandidateProposalStatus.COMPLETED
    assert [p.candidate_name for p in result.proposals] == ["Old Place 1", "Old Place 3"]
    assert result.dropped_proposal_count == 1 and result.failure_kind is None
    assert {p.candidate_type for p in result.proposals} == {AICandidateType.LANDMARK}
    # a proposal is a name + type hypothesis only
    assert not {"coordinates", "provider_place_id", "rating", "price", "opening_hours"} & set(
        result.proposals[0].model_dump()
    )


class _ProviderError(Exception):
    def __init__(self, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__("RAW_PROVIDER_TEXT_SENTINEL https://api.example.test/secret")
        self.status_code = status_code
        self.code = code


@pytest.mark.parametrize(
    ("outputs", "kind", "calls"),
    [
        ((_batch(),), AICandidateProposalFailureKind.EMPTY_RESPONSE, 1),
        ((_batch(_raw_proposal(1, why_consider="Best price guaranteed")),), AICandidateProposalFailureKind.CANDIDATE_VALIDATION, 1),
        ((None, None), AICandidateProposalFailureKind.PARSE_FAILURE, 2),  # one bounded structural retry
        ((_ProviderError(code="json_validate_failed"),) * 2, AICandidateProposalFailureKind.SCHEMA_VALIDATION, 2),
        # Section 1C: a transient transport failure gets the stage's ONE recovery
        # attempt (made here, not hidden in the SDK) -- never a third request
        ((_ProviderError(status_code=429),) * 3, AICandidateProposalFailureKind.PROVIDER_FAILURE, 2),
        ((_ProviderError(status_code=401),), AICandidateProposalFailureKind.PROVIDER_FAILURE, 1),  # never retried
    ],
)
def test_a_failed_proposal_carries_a_safe_machine_readable_failure_kind(
    outputs: tuple[Any, ...], kind: AICandidateProposalFailureKind, calls: int
) -> None:
    client = _Client(*outputs)
    result = GroqAICandidateProposalProvider(client=client).propose(_proposal_request())

    assert result.status == AICandidateProposalStatus.REJECTED and result.proposals == []
    assert result.failure_kind == kind
    assert client.calls == calls
    reason = " ".join(result.guardrail_report.blocked_reasons)
    for leaked in ("RAW_PROVIDER_TEXT_SENTINEL", "api.example.test", "Best price guaranteed"):
        assert leaked not in reason


def test_a_structural_failure_is_retried_once_with_a_smaller_batch_and_can_recover() -> None:
    client = _Client(_ProviderError(code="json_validate_failed"), _batch(_raw_proposal(1), _raw_proposal(2)))
    result = GroqAICandidateProposalProvider(client=client).propose(_proposal_request())
    assert result.status == AICandidateProposalStatus.COMPLETED and client.calls == 2


def test_the_completion_budget_grows_with_the_anchor_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    budgets: list[int | None] = []

    def _build(self: Any, max_tokens: int | None = None, timeout: float | None = None) -> Any:
        budgets.append(max_tokens)
        return _Client(_batch(_raw_proposal(1)))

    monkeypatch.setattr(GroqAICandidateProposalProvider, "_build_client", _build)
    provider = GroqAICandidateProposalProvider(api_key="SENTINEL_KEY_NOT_REAL")
    provider.propose(_proposal_request(max_candidates=20))
    provider.propose(_proposal_request(max_candidates=8))
    assert budgets == [1500 + 250 * 20, None]  # 6500 for 20 anchors; the 4000 default covers 8


# =====================================================================================
# 2. Geographic day clustering
# =====================================================================================


def _point(lat: float, lng: float) -> GeoPoint:
    return GeoPoint(lat=lat, lng=lng)


# three compact areas ~4-5 km apart, three places each
_AREAS = {"west": (50.00, 10.00), "centre": (50.00, 10.06), "east": (50.00, 10.12)}


def _area_points(order: list[str]) -> tuple[list[str], list[GeoPoint]]:
    counters = {name: 0 for name in _AREAS}
    names: list[str] = []
    points: list[GeoPoint] = []
    for area in order:
        index = counters[area]
        counters[area] += 1
        lat, lng = _AREAS[area]
        names.append(f"{area}-{index}")
        points.append(_point(lat + index * 0.002, lng + index * 0.001))
    return names, points


def test_balanced_spatial_clusters_put_neighbours_on_the_same_day() -> None:
    # priority order deliberately interleaves the three areas
    names, points = _area_points(["west", "east", "centre", "west", "east", "centre", "west", "east", "centre"])
    sizes = balanced_day_sizes(len(names), 3, 3)
    days = balanced_spatial_clusters(names, points, sizes)

    assert sorted(len(day) for day in days) == [3, 3, 3]
    for day in days:
        assert len({name.split("-")[0] for name in day}) == 1  # one area per day
    assert days[0][0] == "west-0"  # the best-ranked place leads the first day
    assert days == balanced_spatial_clusters(names, points, sizes)  # deterministic

    by_name = dict(zip(names, points))
    clustered_km = grouping_length_km([[by_name[n] for n in day] for day in days])
    rank_order_km = grouping_length_km([points[0:3], points[3:6], points[6:9]])  # take-the-next-three grouping
    assert clustered_km < 0.2 * rank_order_km


def test_clusters_stay_balanced_and_place_items_without_coordinates() -> None:
    names, points = _area_points(["west", "west", "west", "west", "east", "east", "centre"])
    days = balanced_spatial_clusters(names, points, balanced_day_sizes(7, 3, 3))
    assert sorted((len(day) for day in days), reverse=True) == [3, 2, 2]  # no one-stop day next to a full one
    assert sorted(name for day in days for name in day) == sorted(names)  # nothing lost or duplicated

    with_unlocated = balanced_spatial_clusters(["a", "b", "c", "d"], [_point(50, 10), None, _point(50, 10.1), None], [2, 2])
    assert sorted(len(day) for day in with_unlocated) == [2, 2]
    assert balanced_spatial_clusters([], [], [3, 3]) == [[], []]


def _poi(key: str, name: str, point: GeoPoint, **extra: Any) -> dict[str, Any]:
    return {
        "place_id": f"geoapify/{key}", "name": name, "category": "museum",
        "coordinates": {"lat": point.lat, "lng": point.lng}, "source": "geoapify_places",
        "data_status": "live", "confidence": 0.6, "provider_tags": {"tourism": "museum"}, **extra,
    }


def _planner_state(pois: list[dict[str, Any]], restaurants: list[dict[str, Any]] | None = None, days: int = 3, **trip: Any) -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Fixtureville, Fixtureland", start_date=_START,
            end_date=_START + timedelta(days=days - 1), travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE, pace=TripPace.BALANCED, **trip,
        )
    )
    state.destination_context = DestinationContext(
        destination_name="Fixtureville, Fixtureland", candidate_pois=pois, candidate_restaurants=restaurants or []
    )
    state.candidate_quality_report = CandidateQualityService().build_report(state)
    return state


def _area_pois(order: list[str]) -> list[dict[str, Any]]:
    names, points = _area_points(order)
    return [_poi(name, f"Museum {name}", point) for name, point in zip(names, points)]


def _areas_by_day(state: PlanningState) -> list[set[str]]:
    return [
        {experience.name.split(" ")[1].split("-")[0] for experience in day.experiences}
        for day in state.experience_plan.daily_plans
    ]


def test_the_deterministic_planner_groups_each_day_geographically() -> None:
    state = _planner_state(_area_pois(["west", "east", "centre"] * 3))
    ExperiencePlannerService().run(state)
    assert [len(day.experiences) for day in state.experience_plan.daily_plans] == [3, 3, 3]
    assert all(len(areas) == 1 for areas in _areas_by_day(state))  # never west -> east -> west


def test_neighbouring_must_visits_share_a_day_instead_of_being_spread_out() -> None:
    pois = _area_pois(["west", "east", "centre"] * 3)
    pois[0]["name"], pois[3]["name"] = "Museum west-0 Tower", "Museum west-1 Abbey"  # two neighbours in the west
    state = _planner_state(pois, must_visit=["Tower", "Abbey"])
    ExperiencePlannerService().run(state)

    days = state.experience_plan.daily_plans
    day_of = {experience.name: day.day_number for day in days for experience in day.experiences}
    assert day_of["Museum west-0 Tower"] == day_of["Museum west-1 Abbey"]
    assert all(len(areas) == 1 for areas in _areas_by_day(state))


# =====================================================================================
# 3. Rationale must match the final plan
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


def _rationale_warnings(day: DailyPlan) -> list[str]:
    return [warning for warning in day.warnings if warning.startswith(RATIONALE_WARNING_PREFIX)]


def test_a_geographically_poor_ai_grouping_is_regrouped_and_its_rationale_dropped() -> None:
    pois = _area_pois(["west", "east", "centre"] * 3)
    state = _planner_state(pois)
    # the model spreads every day across all three areas (west -> east -> centre)
    state.ai_itinerary_reasoning_result = _reasoning([pois[0:3], pois[3:6], pois[6:9]])
    ExperiencePlannerService().run(state)

    assert all(len(areas) == 1 for areas in _areas_by_day(state))  # regrouped by geography
    scheduled = {e.name for day in state.experience_plan.daily_plans for e in day.experiences}
    assert scheduled == {poi["name"] for poi in pois}  # the model's SELECTION is kept
    for day in state.experience_plan.daily_plans:
        assert _rationale_warnings(day) == []  # no rationale from the superseded days
        assert current_day_rationale(state, day) is None
        assert day.goal == deterministic_day_summary([e.name for e in day.experiences])
        assert "RATIONALE_FOR_PROPOSED_DAY" not in (day.goal or "")


def test_a_geographically_sound_ai_grouping_is_kept_with_its_rationale() -> None:
    pois = _area_pois(["west", "west", "west", "east", "east", "east", "centre", "centre", "centre"])
    state = _planner_state(pois)
    state.ai_itinerary_reasoning_result = _reasoning([pois[0:3], pois[3:6], pois[6:9]])
    ExperiencePlannerService().run(state)

    for index, day in enumerate(state.experience_plan.daily_plans):
        assert [e.name for e in day.experiences] == [poi["name"] for poi in pois[index * 3 : index * 3 + 3]]
        assert _rationale_warnings(day) == [f"{RATIONALE_WARNING_PREFIX}RATIONALE_FOR_PROPOSED_DAY_{index + 1}"]
        assert current_day_rationale(state, day) == f"RATIONALE_FOR_PROPOSED_DAY_{index + 1}"


def test_the_fallback_top_up_changes_a_day_and_its_old_rationale_is_not_retained() -> None:
    pois = _area_pois(["west", "west", "west", "east", "east", "east", "centre", "centre", "centre"])
    state = _planner_state(pois)
    # the model under-fills: one stop on day 1, two on day 2, three on day 3
    state.ai_itinerary_reasoning_result = _reasoning([pois[0:1], pois[3:5], pois[6:9]])
    state.usefulness_fallback_applied = True  # the single deterministic top-up pass
    ExperiencePlannerService().run(state)

    days = state.experience_plan.daily_plans
    assert [len(day.experiences) for day in days] == [3, 3, 3]  # topped up from unused verified candidates
    for day in days[:2]:  # changed days: the rationale described a different day
        assert _rationale_warnings(day) == [] and current_day_rationale(state, day) is None
    assert current_day_rationale(state, days[2]) == "RATIONALE_FOR_PROPOSED_DAY_3"  # untouched day keeps its own

    # the narrator is only ever given a rationale that still matches the final day
    from app.services.itinerary_narrative_request_builder import ItineraryNarrativeRequestBuilder

    request = ItineraryNarrativeRequestBuilder().build_request(state)
    assert [day.reasoning_rationale for day in request.days] == [None, None, "RATIONALE_FOR_PROPOSED_DAY_3"]


def test_a_route_aware_reorder_also_drops_the_rationale_for_the_old_order() -> None:
    from app.services.route_aware_sequencing_service import _apply_day_order

    day = DailyPlan(
        day_number=1, date=_START,
        experiences=[ExperienceItem(experience_id=f"e{i}", name=f"Place {i}", category="museum") for i in range(3)],
        warnings=["A feasibility note.", f"{RATIONALE_WARNING_PREFIX}Start at Place 0, then Place 1."],
        goal="old summary",
    )
    _apply_day_order(day, ["e0", "e2", "e1"])
    assert day.warnings == ["A feasibility note."]
    assert day.goal == "This day's stops, grouped by location: Place 0, Place 2 and Place 1."


# =====================================================================================
# 4. Food local to the day
# =====================================================================================


def _stops(*points: GeoPoint) -> list[ExperienceItem]:
    return [
        ExperienceItem(experience_id=f"s{i}", name=f"Stop {i}", category="museum", coordinates=point)
        for i, point in enumerate(points)
    ]


def _restaurant(key: str, point: GeoPoint | None) -> dict[str, Any]:
    return {
        "place_id": f"geoapify/{key}", "name": f"Restaurant {key}", "category": "restaurant",
        "coordinates": {"lat": point.lat, "lng": point.lng} if point else None,
        "source": "geoapify_places", "data_status": "live", "confidence": 0.6,
    }


def test_food_is_chosen_near_the_days_own_stops_and_not_repeated_across_days() -> None:
    west, east = _point(50.0, 10.00), _point(50.0, 10.12)
    restaurants = [
        _restaurant("w1", _point(50.001, 10.001)), _restaurant("w2", _point(50.002, 10.002)),
        _restaurant("w3", _point(50.003, 10.003)), _restaurant("e1", _point(50.001, 10.121)),
        _restaurant("far", _point(51.0, 11.0)),
    ]
    used: set[str] = set()

    def suggest(stops: list[ExperienceItem]) -> tuple[list[str], list[str]]:
        warnings: list[str] = []
        names = [
            s.name for s in planner_module._suggest_nearby_restaurants(stops, restaurants, warnings, already_suggested=used)
        ]
        return names, warnings

    assert suggest(_stops(west))[0] == ["Restaurant w1", "Restaurant w2"]
    assert suggest(_stops(east))[0] == ["Restaurant e1"]  # only what is near THIS day; never the west ones
    # a second west day gets only the unused west restaurant -- never a repeat
    assert suggest(_stops(west))[0] == ["Restaurant w3"]
    # and a third west day gets nothing at all rather than a repeat
    names, warnings = suggest(_stops(west))
    assert names == [] and planner_module._NO_NEARBY_RESTAURANTS_WARNING in warnings

    # proximity is to the NEAREST stop of the day, not just the first one
    used.clear()
    assert suggest(_stops(_point(51.5, 12.0), east))[0] == ["Restaurant e1"]

    # nothing within reach: no suggestion, and the day says so (never a far-away "nearby" place)
    names, warnings = suggest(_stops(_point(52.0, 13.0)))
    assert names == [] and warnings == [planner_module._NO_NEARBY_RESTAURANTS_WARNING]
    assert len(suggest(_stops(west))[0]) <= planner_module._MAX_RESTAURANT_SUGGESTIONS_PER_DAY


def test_planned_days_get_different_local_food_and_food_never_takes_an_attraction_slot() -> None:
    pois = _area_pois(["west", "east", "centre"] * 3)
    restaurants = [
        _restaurant(f"{area}{i}", _point(lat + 0.001 * (i + 1), lng + 0.002))
        for area, (lat, lng) in _AREAS.items() for i in range(3)
    ]
    state = _planner_state(pois, restaurants)
    ExperiencePlannerService().run(state)

    suggestions = [[s.name for s in day.restaurant_suggestions] for day in state.experience_plan.daily_plans]
    assert all(len(day) == 2 for day in suggestions)
    flat = [name for day in suggestions for name in day]
    assert len(flat) == len(set(flat))  # no restaurant repeated across the three days
    scheduled = {e.name for day in state.experience_plan.daily_plans for e in day.experiences}
    assert not scheduled & set(flat)
    for suggestion in state.experience_plan.daily_plans[0].restaurant_suggestions:  # no invented facts
        assert not {"rating", "price", "opening_hours", "availability"} & set(suggestion.model_dump())


# =====================================================================================
# 5. Route burden + readiness
# =====================================================================================


def _routed_state(leg_seconds: list[list[float]], pace: TripPace = TripPace.BALANCED) -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Fixtureville, Fixtureland", start_date=_START,
            end_date=_START + timedelta(days=len(leg_seconds) - 1), travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE, pace=pace,
        )
    )
    days: list[DailyPlan] = []
    legs: list[RouteLegFeasibility] = []
    for day_index, durations in enumerate(leg_seconds, start=1):
        stops = [
            ExperienceItem(
                experience_id=f"d{day_index}s{i}", name=f"Place {day_index}-{i}", category="museum",
                coordinates=_point(50.0 + i * 0.001, 10.0), provider_place_id=f"geoapify/{day_index}{i}",
                provider_source="geoapify_places",
            )
            for i in range(len(durations) + 1)
        ]
        days.append(DailyPlan(day_number=day_index, date=_START + timedelta(days=day_index - 1), experiences=stops))
        for (a, b), seconds in zip(zip(stops, stops[1:]), durations):
            legs.append(
                RouteLegFeasibility(
                    from_experience_id=a.experience_id, from_experience_name=a.name,
                    to_experience_id=b.experience_id, to_experience_name=b.name,
                    provider="geoapify_routing", status=ProviderStatus.SUCCESS,
                    distance_meters=seconds * 1.2, duration_seconds=seconds,
                    feasibility_status=RouteFeasibilityStatus.FEASIBLE,
                )
            )
    state.experience_plan = ExperiencePlan(daily_plans=days)
    state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.SUCCESS, legs=legs, provider="geoapify_routing", route_data_source="geoapify_routing"
    )
    return state


def test_route_burden_flags_a_long_walking_day_and_a_single_long_leg() -> None:
    state = _routed_state([[600, 900], [4000, 4600], [3000, 300], [200]])
    burdens = {burden.day_number: burden for burden in day_route_burdens(state)}

    assert (burdens[1].total_duration_seconds, burdens[1].long_route) == (1500, False)
    assert burdens[2].over_day_limit and burdens[2].total_duration_seconds == 8600  # > 90 min for a balanced day
    assert burdens[2].total_distance_meters == pytest.approx(8600 * 1.2)
    assert burdens[3].over_leg_limit and not burdens[3].over_day_limit  # one 50-minute leg
    assert burdens[3].max_leg_duration_seconds == 3000
    assert not burdens[4].long_route

    # thresholds follow the pace and are configurable
    defaults = Settings(_env_file=None)
    assert (
        defaults.route_burden_max_day_seconds_relaxed
        < defaults.route_burden_max_day_seconds_balanced
        < defaults.route_burden_max_day_seconds_packed
    )
    assert [b.long_route for b in day_route_burdens(_routed_state([[2000, 2000, 2000]], TripPace.PACKED))] == [False]
    assert [b.long_route for b in day_route_burdens(_routed_state([[2000, 2000]], TripPace.RELAXED))] == [True]


def test_a_long_travel_day_gets_a_user_facing_warning_with_real_walking_figures_only() -> None:
    state = _routed_state([[4000, 4600], [600]])
    report = PlanValidatorService().run(state).validation_report

    long_travel = [issue for issue in report.warnings if issue.category == "long_travel_day"]
    assert len(long_travel) == 1 and long_travel[0].affected_section == "experience_plan.daily_plans[1]"
    assert "143 minutes of walking" in long_travel[0].message and "10.3 km" in long_travel[0].message
    assert "no public-transport or taxi time is available" in long_travel[0].message  # nothing invented
    assert LONG_TRAVEL_DAY in report.review_codes
    assert report.readiness_status == ReadinessStatus.NEEDS_REVIEW


def test_unrouted_legs_are_never_estimated_into_a_burden() -> None:
    state = _routed_state([[600, 700]])
    state.route_feasibility_report.legs[1].duration_seconds = None
    burden = day_route_burdens(state)[0]
    assert (burden.required_legs, burden.routed_legs, burden.total_duration_seconds) == (2, 1, 600)


def test_readiness_is_ready_without_review_codes_and_never_needs_review_without_one() -> None:
    calm = PlanValidatorService().run(_routed_state([[600, 700], [500, 400]])).validation_report
    warning_codes = {issue.category.upper() for issue in calm.warnings if issue.severity.value == "warning"}
    assert set(calm.review_codes) == warning_codes
    assert calm.readiness_status == (ReadinessStatus.NEEDS_REVIEW if warning_codes else ReadinessStatus.READY)

    burdened = PlanValidatorService().run(_routed_state([[4000, 4600]])).validation_report
    assert burdened.readiness_status == ReadinessStatus.NEEDS_REVIEW and burdened.review_codes


# =====================================================================================
# 6. Weather forecast horizon
# =====================================================================================


class _WeatherResponse:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast?latitude=50.0&SECRET_URL_SENTINEL=1")
            raise httpx.HTTPStatusError("bad", request=request, response=httpx.Response(self.status_code, request=request))

    def json(self) -> Any:
        return self._payload


class _WeatherClient:
    def __init__(self, *responses: _WeatherResponse) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __enter__(self) -> "_WeatherClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, url: str, params: dict[str, Any] | None = None) -> _WeatherResponse:
        self.requests.append(dict(params or {}))
        return self._responses[min(len(self.requests) - 1, len(self._responses) - 1)]


_TODAY = date(2026, 10, 2)
_FORECAST = {
    "daily": {
        "time": ["2026-10-05", "2026-10-06"], "temperature_2m_max": [20.0, 21.0], "temperature_2m_min": [12.0, 13.0],
        "precipitation_probability_max": [10, 20], "precipitation_sum": [0.0, 0.1], "weathercode": [1, 2],
        "wind_speed_10m_max": [10.0, 12.0],
    }
}


def _weather(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: _WeatherClient) -> OpenMeteoWeatherAdapter:
    monkeypatch.setattr(open_meteo_adapter, "_today", lambda: _TODAY)
    monkeypatch.setattr(open_meteo_adapter.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(open_meteo_adapter.time, "sleep", lambda seconds: None)
    return OpenMeteoWeatherAdapter(cache_store=ProviderCacheStore(tmp_path / "weather.sqlite3"))


def _dates(start: date, end: date) -> dict[str, str]:
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}


def test_a_trip_beyond_the_forecast_horizon_is_not_requested(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _WeatherClient(_WeatherResponse(400))
    adapter = _weather(monkeypatch, tmp_path, client)
    response = adapter.get_weather_forecast(
        "Fixtureville", _dates(_TODAY + timedelta(days=150), _TODAY + timedelta(days=152)), _point(50, 10)
    )

    assert client.requests == []  # the endpoint is never called
    assert response.status == ProviderStatus.UNAVAILABLE and response.data is None
    assert response.failure_reason == FORECAST_NOT_YET_AVAILABLE
    assert "not available yet" in response.message and "16 days" in response.message


def test_an_in_horizon_trip_is_requested_and_a_partly_covered_one_is_clamped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _WeatherClient(_WeatherResponse(200, _FORECAST))
    adapter = _weather(monkeypatch, tmp_path, client)
    response = adapter.get_weather_forecast(
        "Fixtureville", _dates(_TODAY + timedelta(days=3), _TODAY + timedelta(days=4)), _point(50, 10)
    )
    assert response.status == ProviderStatus.SUCCESS and len(response.data) == 2
    assert client.requests[0]["end_date"] == (_TODAY + timedelta(days=4)).isoformat()

    adapter.get_weather_forecast(
        "Fixtureville", _dates(_TODAY + timedelta(days=14), _TODAY + timedelta(days=30)), _point(50, 11)
    )
    assert client.requests[1]["start_date"] == (_TODAY + timedelta(days=14)).isoformat()
    assert client.requests[1]["end_date"] == (_TODAY + timedelta(days=16)).isoformat()  # only up to the horizon


@pytest.mark.parametrize(("status", "expected_requests"), [(400, 1), (404, 1), (422, 1), (429, 2), (500, 2), (503, 2)])
def test_a_deterministic_client_error_is_not_retried_and_logs_carry_no_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, status: int, expected_requests: int
) -> None:
    caplog.set_level(logging.DEBUG, logger="app")
    client = _WeatherClient(_WeatherResponse(status))
    adapter = _weather(monkeypatch, tmp_path, client)
    response = adapter.get_weather_forecast(
        "Fixtureville", _dates(_TODAY + timedelta(days=3), _TODAY + timedelta(days=4)), _point(50, 10)
    )

    assert response.status == ProviderStatus.FAILED and response.data is None
    assert len(client.requests) == expected_requests
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert f"HTTP {status}" in logged
    assert "SECRET_URL_SENTINEL" not in logged and "open-meteo.com" not in logged and "latitude" not in logged
    assert "SECRET_URL_SENTINEL" not in (response.message or "")


# =====================================================================================
# 7. Canary acceptance measures product quality
# =====================================================================================


def _canary() -> Any:
    path = Path(__file__).resolve().parents[3] / "scripts" / "canary_city.py"
    spec = importlib.util.spec_from_file_location("canary_city_correction_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _clean_report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "destination": {"geocode_success": True},
        "inventory": {"viable_meaningful_candidate_count": 36, "R": 8},
        "anchors": {"proposal_status": "completed", "failure_kind": None, "proposed": 17, "grounded": 12},
        "quality": {
            "duplicates": [], "meaningful_scheduled_stops": 9, "empty_days": [], "blocking_codes": [],
            "review_codes": [], "readiness": "ready",
        },
        "routing": {"coverage_percentage": 100.0},
        "must_visits": {"grounded": ["A"], "scheduled": ["A"]},
        "daily_travel_burden": [{"day": 1, "long_route": False}],
        "food_locality": {"repeated_suggestion_count": 0},
        "rationale_consistency": {"stale_rationale_detected": False},
        "factual_safety": {"fabricated_or_unverified_scheduled_identities": [], "unsupported_factual_claims_in_stored_narrative": 0},
        "persistence": {"save_succeeded": True, "reload_succeeded": True},
        "provider_usage": {"geoapify_credit_budget": 100, "total_geoapify_credits": 40},
    }
    for key, value in overrides.items():
        report[key] = {**report[key], **value} if isinstance(value, dict) else value
    return report


def test_a_clean_canary_passes_and_accepted_data_coverage_codes_do_not_fail_it() -> None:
    canary = _canary()
    assert canary._acceptance(_clean_report())["outcome"] == "PASS"
    accepted = _clean_report(quality={"readiness": "needs_review", "review_codes": ["FLIGHT_INVENTORY", "WEATHER"]})
    assert canary._acceptance(accepted)["outcome"] == "PASS"


@pytest.mark.parametrize(
    ("overrides", "stage"),
    [
        ({"anchors": {"proposal_status": "rejected", "failure_kind": "candidate_validation", "proposed": 0}}, "AI anchor proposal"),
        ({"quality": {"readiness": "needs_review", "review_codes": []}}, "validation / readiness"),
        ({"quality": {"readiness": "needs_review", "review_codes": ["LONG_TRAVEL_DAY"]}}, "validation / readiness"),
        ({"quality": {"readiness": "needs_review", "review_codes": ["SOME_UNKNOWN_CODE"]}}, "validation / readiness"),
        ({"daily_travel_burden": [{"day": 1, "long_route": True}]}, "day clustering / route burden"),
        ({"food_locality": {"repeated_suggestion_count": 4}}, "food locality"),
        ({"rationale_consistency": {"stale_rationale_detected": True}}, "day rationale"),
    ],
)
def test_a_product_quality_defect_fails_the_canary_and_names_the_stage(overrides: dict[str, Any], stage: str) -> None:
    acceptance = _canary()._acceptance(_clean_report(**overrides))
    assert acceptance["outcome"] == "FAIL"
    assert stage in acceptance["failed_stages"]


def test_an_unreachable_optional_ai_provider_is_not_reported_as_a_rejection() -> None:
    # AI anchor discovery simply not connected: the app degrades gracefully and the canary does not
    # invent an "anchor rejected" failure for it.
    not_connected = _clean_report(anchors={"proposal_status": "not_connected", "failure_kind": "not_connected", "proposed": 0})
    assert "AI anchor proposal" not in _canary()._acceptance(not_connected)["failed_stages"]
