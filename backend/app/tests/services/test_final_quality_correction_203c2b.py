from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.core.provider_usage import GenerationProviderContext
from app.models.ai_candidate_promotion import PromotedAICandidate
from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.ai_provider_discovery import AIProviderDiscoveryAttemptStatus
from app.models.candidate_grounding import (
    CandidateGroundingConfidenceTier,
    CandidateGroundingEvidence,
    CandidateGroundingMatchType,
    GroundedCandidate,
)
from app.models.common import DataStatus, GeoPoint, ProviderStatus
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
from app.models.providers import NormalizedPlace, ProviderResponse
from app.models.routing import RouteFeasibilityReport, RouteFeasibilityStatus, RouteLegFeasibility, RouteResult
from app.providers.base import PlacesProvider
from app.providers.gateway import ProviderGateway
from app.services import experience_planner_service as planner_module
from app.services.ai_candidate_promotion_service import _same_promoted_place
from app.services.ai_directed_provider_discovery_service import AIDirectedProviderDiscoveryService
from app.services.anchor_category_compatibility import anchor_category_compatible
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService, recompute_food_suggestions
from app.services.must_visit_matching import must_visit_place_ids, resolve_must_visits
from app.services.plan_validator_service import PlanValidatorService
from app.services.route_burden import LONG_TRAVEL_DAY, day_route_burdens
from app.services.route_burden_repair_service import RouteBurdenRepairService, apply_route_burden_repair_safely
from app.services.route_feasibility_service import RouteFeasibilityService

# Section 203C.2B final quality correction: bounded route repair, the food
# locality hard contract, must-visit identity matching and grounded-anchor
# category hygiene. Every fixture is synthetic (invented names on a small
# coordinate grid); nothing here is city-specific.

_START = date(2026, 11, 10)
_RADIUS_KM = planner_module._MAX_FOOD_SUGGESTION_KM


def _point(lat: float, lng: float) -> GeoPoint:
    return GeoPoint(lat=lat, lng=lng)


def _poi(key: str, name: str, point: GeoPoint, **extra: Any) -> dict[str, Any]:
    return {
        "place_id": f"geoapify/{key}", "name": name, "category": "museum",
        "coordinates": {"lat": point.lat, "lng": point.lng}, "source": "geoapify_places",
        "data_status": "live", "confidence": 0.6, "provider_tags": {"tourism": "museum"}, **extra,
    }


def _restaurant(key: str, point: GeoPoint) -> dict[str, Any]:
    return {
        "place_id": f"geoapify/r-{key}", "name": f"Restaurant {key}", "category": "restaurant",
        "coordinates": {"lat": point.lat, "lng": point.lng}, "source": "geoapify_places",
        "data_status": "live", "confidence": 0.6,
    }


def _stop(poi: dict[str, Any]) -> ExperienceItem:
    return ExperienceItem(
        experience_id=f"exp-{poi['place_id']}", name=poi["name"], category="museum",
        coordinates=GeoPoint(**poi["coordinates"]), provider_place_id=poi["place_id"],
        provider_source="geoapify_places",
    )


def _state(
    pois: list[dict[str, Any]],
    days: list[list[dict[str, Any]]],
    restaurants: list[dict[str, Any]] | None = None,
    **trip: Any,
) -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Fixtureville, Fixtureland", start_date=_START,
            end_date=_START + timedelta(days=max(1, len(days)) - 1), travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE, pace=TripPace.BALANCED, **trip,
        )
    )
    state.destination_context = DestinationContext(
        destination_name="Fixtureville, Fixtureland", candidate_pois=pois, candidate_restaurants=restaurants or []
    )
    state.candidate_quality_report = CandidateQualityService().build_report(state)
    state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(
                day_number=index, date=_START + timedelta(days=index - 1), experiences=[_stop(poi) for poi in day]
            )
            for index, day in enumerate(days, start=1)
        ]
    )
    return state


# =====================================================================================
# 1. Bounded post-routing day repair
# =====================================================================================


class _Routing:
    provider_name = "fixture_routing"


class _Gateway:
    """Answers one multi-waypoint request per call with a fixed time per km
    of straight-line fixture distance -- a stand-in for the routing provider."""

    routing = _Routing()

    def __init__(self, seconds_per_degree: float = 60000.0) -> None:
        self.calls: list[list[tuple[float, float]]] = []
        self._rate = seconds_per_degree

    def get_route_sequence(self, points: list[tuple[float, float]], provider_context: Any = None) -> list[RouteResult]:
        self.calls.append(points)
        if provider_context is not None:
            provider_context.route_requests_left -= 1
        results = []
        for (lat_a, lng_a), (lat_b, lng_b) in zip(points, points[1:]):
            seconds = (abs(lat_a - lat_b) + abs(lng_a - lng_b)) * self._rate
            results.append(
                RouteResult(
                    provider="fixture_routing", status=ProviderStatus.SUCCESS, distance_meters=seconds * 1.2,
                    duration_seconds=seconds, source="fixture_routing", confidence=0.9,
                )
            )
        return results


def _with_routes(state: PlanningState, gateway: _Gateway) -> PlanningState:
    legs: list[RouteLegFeasibility] = []
    for day in state.experience_plan.daily_plans:
        stops = day.experiences
        if len(stops) < 2:
            continue
        results = gateway.get_route_sequence([(s.coordinates.lat, s.coordinates.lng) for s in stops])
        for (a, b), result in zip(zip(stops, stops[1:]), results):
            legs.append(
                RouteLegFeasibility(
                    from_experience_id=a.experience_id, from_experience_name=a.name,
                    to_experience_id=b.experience_id, to_experience_name=b.name,
                    provider="fixture_routing", status=ProviderStatus.SUCCESS,
                    distance_meters=result.distance_meters, duration_seconds=result.duration_seconds,
                    feasibility_status=RouteFeasibilityStatus.FEASIBLE,
                )
            )
    gateway.calls.clear()
    state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.SUCCESS, legs=legs, provider="fixture_routing", route_data_source="fixture_routing"
    )
    return state


# Two compact stops, one stop ~7 km east, and unused candidates.
_NEAR_A = _poi("a", "Museum Alpha", _point(50.000, 10.000))
_NEAR_B = _poi("b", "Museum Beta", _point(50.002, 10.002))
_FAR = _poi("far", "Museum Faraway", _point(50.000, 10.100))
_GOOD_NEARBY = _poi("good", "Museum Gamma", _point(50.003, 10.003))
_EVEN_FARTHER = _poi("farther", "Museum Remote", _point(50.000, 10.300))


def _repair(state: PlanningState, gateway: _Gateway, context: GenerationProviderContext | None = None):
    service = RouteBurdenRepairService(RouteFeasibilityService(gateway=gateway))
    return service.repair(state, context)


def _day_names(state: PlanningState, day: int = 0) -> list[str]:
    return [stop.name for stop in state.experience_plan.daily_plans[day].experiences]


def test_a_long_day_with_a_good_nearby_candidate_is_repaired_with_one_route_request() -> None:
    gateway = _Gateway()
    restaurants = [_restaurant("near", _point(50.001, 10.001)), _restaurant("by-far", _point(50.000, 10.101))]
    state = _with_routes(
        _state([_NEAR_A, _NEAR_B, _FAR, _GOOD_NEARBY], [[_NEAR_A, _NEAR_B, _FAR]], restaurants), gateway
    )
    assert day_route_burdens(state)[0].long_route

    report = _repair(state, gateway)

    attempt = report.attempts[0]
    assert attempt.route_burden_repair_attempted and attempt.accepted and attempt.reason == "accepted"
    assert (attempt.replaced_place, attempt.replacement_place) == ("Museum Faraway", "Museum Gamma")
    assert attempt.after_duration_seconds < attempt.before_duration_seconds
    assert attempt.after_distance_meters < attempt.before_distance_meters
    assert len(gateway.calls) == 1  # the changed day is routed exactly once
    assert sorted(_day_names(state)) == ["Museum Alpha", "Museum Beta", "Museum Gamma"]
    # the stored legs describe the new day, and the day is no longer long
    burden = day_route_burdens(state)[0]
    assert burden.routed_legs == burden.required_legs == 2 and not burden.long_route
    assert burden.total_duration_seconds == attempt.after_duration_seconds
    day = state.experience_plan.daily_plans[0]
    assert [stop.stop_order for stop in day.experiences] == [1, 2, 3]
    assert "Museum Gamma" in day.goal and "Museum Faraway" not in day.goal
    # the replacement carries a real provider identity, and food follows the final stops
    replacement = next(stop for stop in day.experiences if stop.name == "Museum Gamma")
    assert replacement.provider_place_id == "geoapify/good" and replacement.provider_source == "geoapify_places"
    assert [s.name for s in day.restaurant_suggestions] == ["Restaurant near"]


def test_only_worse_candidates_leave_the_day_unchanged_and_the_warning_in_place() -> None:
    gateway = _Gateway()
    state = _with_routes(_state([_NEAR_A, _NEAR_B, _FAR, _EVEN_FARTHER], [[_NEAR_A, _NEAR_B, _FAR]]), gateway)
    before = _day_names(state)

    report = _repair(state, gateway)

    attempt = report.attempts[0]
    assert not attempt.accepted and not attempt.route_burden_repair_attempted
    assert attempt.reason == "no_suitable_candidate" and attempt.replacement_place is None
    assert gateway.calls == [] and _day_names(state) == before
    validation = PlanValidatorService().run(state).validation_report
    assert LONG_TRAVEL_DAY in validation.review_codes


def test_a_replacement_that_does_not_materially_improve_the_route_is_discarded() -> None:
    class _NoBetter(_Gateway):
        def get_route_sequence(self, points, provider_context=None):
            results = super().get_route_sequence(points, provider_context)
            return [result.model_copy(update={"duration_seconds": 3000.0}) for result in results]

    gateway = _Gateway()
    state = _with_routes(_state([_NEAR_A, _NEAR_B, _FAR, _GOOD_NEARBY], [[_NEAR_A, _NEAR_B, _FAR]]), gateway)
    before = _day_names(state)
    slow = _NoBetter()

    attempt = _repair(state, slow).attempts[0]

    assert attempt.route_burden_repair_attempted and not attempt.accepted
    assert attempt.reason == "no_material_improvement" and len(slow.calls) == 1
    assert _day_names(state) == before and day_route_burdens(state)[0].long_route


def test_a_must_visit_or_locked_stop_is_never_replaced() -> None:
    gateway = _Gateway()
    state = _with_routes(
        _state(
            [_NEAR_A, _NEAR_B, {**_FAR, "must_visit_term": "Torre Distante"}, _GOOD_NEARBY],
            [[_NEAR_A, _NEAR_B, _FAR]],
            must_visit=["Torre Distante"],
        ),
        gateway,
    )
    assert must_visit_place_ids(state) == {"geoapify/far"}

    attempt = _repair(state, gateway).attempts[0]

    assert "Museum Faraway" in _day_names(state)
    assert attempt.replaced_place != "Museum Faraway" and attempt.replacement_place != "Museum Faraway"

    locked_gateway = _Gateway()
    locked = _with_routes(_state([_NEAR_A, _NEAR_B, _FAR, _GOOD_NEARBY], [[_NEAR_A, _NEAR_B, _FAR]]), locked_gateway)
    locked.user_locks = [
        UserLock(locked_item_type="experience", locked_item_id=stop.experience_id)
        for stop in locked.experience_plan.daily_plans[0].experiences
    ]
    attempt = _repair(locked, locked_gateway).attempts[0]
    assert attempt.reason == "no_replaceable_stop" and locked_gateway.calls == []
    assert _day_names(locked) == ["Museum Alpha", "Museum Beta", "Museum Faraway"]


def test_repair_stays_inside_the_route_request_budget_and_makes_one_attempt_per_day() -> None:
    far_two = _poi("far2", "Museum Outpost", _point(50.100, 10.000))
    near_c = _poi("c", "Museum Delta", _point(50.050, 10.050))
    near_d = _poi("d", "Museum Epsilon", _point(50.052, 10.052))
    good_two = _poi("good2", "Museum Zeta", _point(50.053, 10.053))
    pois = [_NEAR_A, _NEAR_B, _FAR, _GOOD_NEARBY, near_c, near_d, far_two, good_two]
    days = [[_NEAR_A, _NEAR_B, _FAR], [near_c, near_d, far_two]]

    gateway = _Gateway()
    state = _with_routes(_state(pois, days), gateway)
    context = GenerationProviderContext.new(trip_days=2)
    context.route_requests_left = 5
    report = _repair(state, gateway, context)
    assert [attempt.accepted for attempt in report.attempts] == [True, True]
    assert len(gateway.calls) == 2 and context.route_requests_left == 3  # one request per repaired day

    # no route requests left: nothing is routed and nothing changes
    exhausted_gateway = _Gateway()
    exhausted = _with_routes(_state(pois, days), exhausted_gateway)
    context = GenerationProviderContext.new(trip_days=2)
    context.route_requests_left = 0
    report = _repair(exhausted, exhausted_gateway, context)
    assert {attempt.reason for attempt in report.attempts} == {"route_budget_exhausted"}
    assert exhausted_gateway.calls == [] and _day_names(exhausted) == ["Museum Alpha", "Museum Beta", "Museum Faraway"]


def test_a_plan_without_a_long_day_is_not_touched_and_a_failure_never_breaks_generation() -> None:
    gateway = _Gateway()
    calm = _with_routes(_state([_NEAR_A, _NEAR_B, _GOOD_NEARBY], [[_NEAR_A, _NEAR_B]]), gateway)
    apply_route_burden_repair_safely(calm, RouteFeasibilityService(gateway=gateway))
    assert calm.route_burden_repair_report is None and gateway.calls == []

    class _Broken(_Gateway):
        def get_route_sequence(self, points, provider_context=None):
            raise RuntimeError("routing is down")

    state = _with_routes(_state([_NEAR_A, _NEAR_B, _FAR, _GOOD_NEARBY], [[_NEAR_A, _NEAR_B, _FAR]]), gateway)
    apply_route_burden_repair_safely(state, RouteFeasibilityService(gateway=_Broken()))
    assert state.route_burden_repair_report.attempts[0].reason == "replacement_route_unavailable"
    assert _day_names(state) == ["Museum Alpha", "Museum Beta", "Museum Faraway"]


# =====================================================================================
# 2. Food locality hard contract
# =====================================================================================


def _suggest(stops: list[ExperienceItem], restaurants: list[dict[str, Any]], used: set[str], **kwargs: Any):
    warnings: list[str] = []
    suggestions = planner_module._suggest_nearby_restaurants(
        stops, restaurants, warnings, already_suggested=used, **kwargs
    )
    return [suggestion.name for suggestion in suggestions], warnings


def test_a_restaurant_beyond_the_radius_is_never_shown_even_when_the_model_picked_it() -> None:
    stops = [_stop(_NEAR_A)]
    far = _restaurant("far", _point(50.05, 10.05))  # ~6.6 km away
    names, warnings = _suggest(stops, [far], set(), preferred=[far])
    assert names == [] and warnings == [planner_module._NO_NEARBY_RESTAURANTS_WARNING]

    near = _restaurant("near", _point(50.001, 10.001))
    names, _ = _suggest(stops, [far, near], set(), preferred=[far])
    assert names == ["Restaurant near"]


def test_the_same_restaurant_is_never_repeated_across_days() -> None:
    stops = [_stop(_NEAR_A)]
    restaurants = [_restaurant("one", _point(50.001, 10.001)), _restaurant("two", _point(50.002, 10.002))]
    used: set[str] = set()
    assert _suggest(stops, restaurants, used)[0] == ["Restaurant one", "Restaurant two"]
    # a later day near the same places gets nothing rather than a repeat -- also for a model pick
    names, warnings = _suggest(stops, restaurants, used, preferred=[restaurants[0]])
    assert names == [] and warnings == [planner_module._NO_NEARBY_RESTAURANTS_WARNING]


def test_one_nearby_unused_restaurant_gives_exactly_one_suggestion() -> None:
    stops = [_stop(_NEAR_A)]
    restaurants = [
        _restaurant("used", _point(50.001, 10.001)), _restaurant("fresh", _point(50.002, 10.002)),
        _restaurant("far", _point(50.2, 10.2)),
    ]
    names, warnings = _suggest(stops, restaurants, {"geoapify/r-used"})
    assert names == ["Restaurant fresh"] and warnings == []


def test_the_planner_never_shows_a_distant_or_repeated_restaurant() -> None:
    pois = [_poi(f"p{i}", f"Museum P{i}", _point(50.0 + 0.001 * i, 10.0)) for i in range(9)]
    restaurants = [_restaurant("only", _point(50.004, 10.001)), _restaurant("far", _point(50.3, 10.3))]
    state = _state(pois, [])
    state.destination_context.candidate_restaurants = restaurants
    ExperiencePlannerService().run(state)

    shown = [s for day in state.experience_plan.daily_plans for s in day.restaurant_suggestions]
    assert [s.name for s in shown] == ["Restaurant only"]  # one place, shown once, on one day only


def test_food_is_recomputed_against_the_final_schedule_after_a_day_changes() -> None:
    west = _restaurant("west", _point(50.001, 10.001))
    east = _restaurant("east", _point(50.000, 10.101))
    state = _state([_NEAR_A, _NEAR_B, _FAR], [[_NEAR_A, _NEAR_B], [_FAR]], [west, east])
    recompute_food_suggestions(state)
    days = state.experience_plan.daily_plans
    assert [[s.name for s in day.restaurant_suggestions] for day in days] == [["Restaurant west"], ["Restaurant east"]]

    # the far stop is swapped out of day 2: its old suggestion is no longer near any stop
    days[1].experiences = [_stop(_GOOD_NEARBY)]
    recompute_food_suggestions(state)
    assert [[s.name for s in day.restaurant_suggestions] for day in days] == [["Restaurant west"], []]
    assert planner_module._NO_NEARBY_RESTAURANTS_WARNING in days[1].warnings
    recompute_food_suggestions(state)  # idempotent: the warning is not stacked
    assert days[1].warnings.count(planner_module._NO_NEARBY_RESTAURANTS_WARNING) == 1
    assert _RADIUS_KM == 1.5


# =====================================================================================
# 3. MUST_VISIT review code
# =====================================================================================


def _must_visit_issues(state: PlanningState) -> list[Any]:
    report = PlanValidatorService().run(state).validation_report
    return [issue for issue in report.warnings if issue.category == "must_visit"]


def test_a_grounded_and_scheduled_must_visit_creates_no_review_code_even_under_another_name() -> None:
    # the provider's name for the place differs from the user's wording
    tower = _poi("tower", "High Tower", _point(50.0, 10.0), must_visit_term="Torre Alta")
    state = _state([tower, _NEAR_B], [[tower, _NEAR_B]], must_visit=["Torre Alta"])

    resolution = resolve_must_visits(state)[0]
    assert resolution.grounded and resolution.scheduled
    assert _must_visit_issues(state) == []
    assert "MUST_VISIT" not in state.validation_report.review_codes


def test_an_ungroundable_must_visit_is_a_warning() -> None:
    state = _state([_NEAR_A, _NEAR_B], [[_NEAR_A, _NEAR_B]], must_visit=["An Invented Palace"])
    resolution = resolve_must_visits(state)[0]
    assert not resolution.grounded and not resolution.scheduled
    issues = _must_visit_issues(state)
    assert len(issues) == 1 and "An Invented Palace" in issues[0].message
    assert "MUST_VISIT" in state.validation_report.review_codes


def test_a_grounded_but_unscheduled_must_visit_is_a_warning() -> None:
    tower = _poi("tower", "High Tower", _point(50.0, 10.0), must_visit_term="Torre Alta")
    state = _state([tower, _NEAR_A, _NEAR_B], [[_NEAR_A, _NEAR_B]], must_visit=["Torre Alta"])
    resolution = resolve_must_visits(state)[0]
    assert resolution.grounded and not resolution.scheduled
    issues = _must_visit_issues(state)
    assert len(issues) == 1 and "Torre Alta" in issues[0].message
    assert "Found by the provider but not scheduled" in issues[0].message
    assert "MUST_VISIT" in state.validation_report.review_codes


# =====================================================================================
# 4. Grounded anchor category hygiene
# =====================================================================================


class _Places(PlacesProvider):
    provider_name = "fixture_places"

    def __init__(self, places: dict[str, NormalizedPlace]) -> None:
        self._places = places

    def search_must_visit_place(self, must_visit_term, primary_destination, filters=None):
        return ProviderResponse[list[NormalizedPlace]](
            provider_name="geoapify_places", provider_type=PlacesProvider.provider_type,
            status=ProviderStatus.SUCCESS, data_status=DataStatus.LIVE,
            data=[self._places[must_visit_term]], confidence=0.6, message="found",
        )


def _place(key: str, name: str, category: str, tags: dict[str, str], point: GeoPoint | None = None) -> NormalizedPlace:
    return NormalizedPlace(
        place_id=f"geoapify/{key}", name=name, category=category, coordinates=point or _point(50.0, 10.0),
        source="geoapify_places", data_status=DataStatus.LIVE, confidence=0.6, provider_tags=tags,
    )


def _anchor(proposal_id: str, name: str, candidate_type: AICandidateType) -> AICandidateProposal:
    return AICandidateProposal(
        proposal_id=proposal_id, candidate_name=name, candidate_type=candidate_type,
        why_consider="A well-known place worth checking.",
        verification_requirements=[AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION],
        confidence=0.6,
    )


def _discover(proposals: list[AICandidateProposal], places: dict[str, NormalizedPlace]):
    service = AIDirectedProviderDiscoveryService(gateway=ProviderGateway(places=_Places(places)))
    state = _state([], [])
    return service.discover(state, proposals, provider_candidates=[])


def test_an_apartment_grounded_for_a_neighbourhood_anchor_is_rejected_by_category() -> None:
    apartment = _place(
        "apt", "Old Quarter Apartments", "apartment",
        {"tourism": "apartment", "category_path": "accommodation.apartment"},
    )
    result = _discover(
        [_anchor("proposal_001", "Old Quarter", AICandidateType.NEIGHBORHOOD)], {"Old Quarter": apartment}
    )
    assert result.matched_count == 0 and result.matches_by_proposal_id() == {}
    attempt = result.attempts[0]
    assert attempt.status == AIProviderDiscoveryAttemptStatus.CATEGORY_MISMATCH
    assert attempt.status.value == "grounding_category_mismatch" and attempt.match is None
    assert "Old Quarter Apartments" not in (attempt.message or "")  # the wrong place is not exposed

    # decided by provider category only: accommodation and single restaurants are never attraction anchors
    assert not anchor_category_compatible(AICandidateType.LANDMARK, {"tourism": "hotel"}, "hotel")
    assert not anchor_category_compatible(AICandidateType.ATTRACTION, {"amenity": "restaurant"}, "restaurant")
    assert not anchor_category_compatible(AICandidateType.MUSEUM, {}, None)  # unclassified: cannot be verified


def test_a_museum_grounded_for_a_museum_anchor_is_accepted() -> None:
    museum = _place("m", "City History Museum", "museum", {"tourism": "museum"})
    result = _discover(
        [_anchor("proposal_001", "City History Museum", AICandidateType.MUSEUM)], {"City History Museum": museum}
    )
    assert result.matched_count == 1
    assert result.matches_by_proposal_id()["proposal_001"].provider_place_id == "geoapify/m"


def test_duplicate_grounded_anchors_produce_one_match_and_one_promotion() -> None:
    museum = _place("m", "City History Museum", "museum", {"tourism": "museum"})
    # same normalised name ~40 m away under another provider id: the same real place
    twin = _place("m-twin", "City History  Museum", "museum", {"tourism": "museum"}, _point(50.0003, 10.0003))
    result = _discover(
        [
            _anchor("proposal_001", "City History Museum", AICandidateType.MUSEUM),
            _anchor("proposal_002", "The City History Museum", AICandidateType.MUSEUM),
            _anchor("proposal_003", "History Museum of the City", AICandidateType.MUSEUM),
        ],
        {"City History Museum": museum, "The City History Museum": museum, "History Museum of the City": twin},
    )
    assert result.matched_count == 1 and list(result.matches_by_proposal_id()) == ["proposal_001"]
    assert [attempt.status for attempt in result.attempts[1:]] == [
        AIProviderDiscoveryAttemptStatus.DUPLICATE_ANCHOR
    ] * 2

    # promotion applies the same rule: provider identity, then name + proximity
    promoted = PromotedAICandidate(
        candidate_id="promoted_proposal_001", name="City History Museum", category="museum",
        source="ai_candidate_promotion", provider_place_id="geoapify/m", provider_source="geoapify_places",
        original_ai_candidate_id="proposal_001", quality_bucket="strong", grounding_status="grounded",
        coordinates=_point(50.0, 10.0), promoted=True,
    )

    def grounded(place_id: str, point: GeoPoint) -> GroundedCandidate:
        return GroundedCandidate(
            grounding_id="grounding_x", proposal_id="proposal_009", candidate_name="City History Museum",
            candidate_type=AICandidateType.MUSEUM, matched_name="City History Museum",
            confidence_tier=CandidateGroundingConfidenceTier.HIGH, confidence=0.8,
            evidence=CandidateGroundingEvidence(
                provider_name="geoapify_places", provider_place_id=place_id, matched_name="City History Museum",
                matched_category="museum", match_type=CandidateGroundingMatchType.EXACT_NAME,
                coordinates=point, data_status=DataStatus.LIVE, confidence=0.8,
            ),
            verification_requirements_satisfied=[AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION],
        )

    assert _same_promoted_place(promoted, "Another Name", grounded("geoapify/m", _point(51.0, 11.0)))
    assert _same_promoted_place(promoted, "City History Muséum", grounded("geoapify/other", _point(50.0003, 10.0003)))
    assert not _same_promoted_place(promoted, "City History Museum", grounded("geoapify/other", _point(50.1, 10.1)))
    assert not _same_promoted_place(promoted, "Harbour Museum", grounded("geoapify/other", _point(50.0, 10.0)))


def test_a_category_mismatch_never_affects_must_visit_grounding() -> None:
    # a user may name any real place as a must-visit, including one an AI anchor could never be
    hotel = _poi(
        "hotel", "Grand Palace Hotel", _point(50.0, 10.0), category="hotel",
        provider_tags={"tourism": "hotel"}, must_visit_term="Grand Palace Hotel",
    )
    assert not anchor_category_compatible(AICandidateType.LANDMARK, hotel["provider_tags"], hotel["category"])
    state = _state([hotel, _NEAR_B], [[hotel, _NEAR_B]], must_visit=["Grand Palace Hotel"])
    resolution = resolve_must_visits(state)[0]
    assert resolution.grounded and resolution.scheduled
    assert must_visit_place_ids(state) == {"geoapify/hotel"}
    assert _must_visit_issues(state) == []
