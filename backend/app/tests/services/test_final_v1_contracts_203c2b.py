from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.provider_usage import GenerationProviderContext, ProviderUsageTracker, route_request_allowance
from app.graphs.planning_graph_nodes import _underfill_fallback_or_finish, route_after_repair, route_after_validation
from app.models.ai_itinerary_repair import RepairableIssueType
from app.models.candidate_quality import (
    CandidateQualityReport,
    CandidateQualityScore,
    CandidateRejectReason,
    CandidateQualityTier,
    CandidateUseCase,
)
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.inventory_sufficiency import (
    INSUFFICIENT_VERIFIED_INVENTORY,
    UNDERFILLED_PLAN,
    InventorySufficiencyStatus,
)
from app.models.planning_state import (
    DailyPlan,
    DestinationContext,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    ReadinessStatus,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.models.providers import NormalizedPlace, ProviderResponse
from app.models.routing import RouteRequest, RouteResult
from app.providers.base import PlacesProvider
from app.providers.gateway import ProviderGateway, provider_gateway
from app.providers.routing.base import RoutingProvider
from app.services import experience_planner_service as planner_module
from app.services.ai_itinerary_repair_request_builder import classify_repairable_issues
from app.services.inventory_sufficiency_service import InventorySufficiencyService
from app.services.pace_targets import PACE_TARGET_PER_DAY, pace_targets, pace_targets_for
from app.services.plan_validator_service import PlanValidatorService
from app.services.planning_orchestrator import planning_orchestrator
from app.services.route_aware_sequencing_service import RouteAwareSequencingService
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.travel_time_buffer_service import TravelTimeBufferService
from app.services.usefulness_contract import (
    count_viable_candidates,
    evaluate_usefulness,
    inventory_status,
    usefulness_findings,
)

# Section 203C.2B: T / R / H inventory semantics, the usefulness contract,
# bounded routing, and failure semantics. Hermetic: no provider, no AI.

_START = date(2026, 11, 10)
_PACES = {"relaxed": TripPace.RELAXED, "balanced": TripPace.BALANCED, "packed": TripPace.PACKED}


def _state(days: int = 3, pace: TripPace = TripPace.BALANCED, **trip: Any) -> PlanningState:
    return PlanningState(
        trip_request=TripRequest(
            primary_destination="Fixtureville, Fixtureland",
            start_date=_START,
            end_date=_START + timedelta(days=days - 1),
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
            pace=pace,
            **trip,
        )
    )


def _score(index: int, tier: CandidateQualityTier = CandidateQualityTier.GOOD_CANDIDATE, low_value: bool = False) -> CandidateQualityScore:
    return CandidateQualityScore(
        candidate_id=f"geoapify/c{index}",
        candidate_name=f"Place {index}",
        use_case=CandidateUseCase.ATTRACTION,
        quality_tier=tier,
        total_score=0.6,
        low_value_object=low_value,
        reject_reasons=(
            [CandidateRejectReason.UNSUITABLE_PLACE_TYPE] if tier == CandidateQualityTier.REJECTED else []
        ),
    )


def _with_inventory(state: PlanningState, viable: int) -> PlanningState:
    state.destination_context = DestinationContext(destination_name=state.trip_request.primary_destination)
    state.candidate_quality_report = CandidateQualityReport(
        destination_name=state.trip_request.primary_destination,
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[_score(index) for index in range(viable)],
    )
    state.inventory_sufficiency_report = InventorySufficiencyService().evaluate(state)
    return state


def _stop(index: int, *, low_value: bool = False, grounded: bool = True, lng: float | None = None) -> ExperienceItem:
    return ExperienceItem(
        experience_id=f"exp-{index}",
        name=f"Place {index}",
        category="museum",
        coordinates=GeoPoint(lat=50.0, lng=10.0 + index * 0.001 if lng is None else lng),
        provider_place_id=f"geoapify/c{index}" if grounded else None,
        provider_source="geoapify_places" if grounded else None,
        low_value_object=low_value,
    )


def _with_schedule(state: PlanningState, per_day: list[int]) -> PlanningState:
    counter = iter(range(1000))
    state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(
                day_number=day + 1,
                date=_START + timedelta(days=day),
                experiences=[_stop(next(counter)) for _ in range(count)],
            )
            for day, count in enumerate(per_day)
        ]
    )
    return state


def _spread(total: int, days: int, cap: int) -> list[int]:
    """`total` stops over `days`, never above `cap`, as evenly as possible."""
    counts = [0] * days
    for index in range(total):
        counts[index % days] += 1
    assert max(counts, default=0) <= cap
    return counts


# -- T / R / H ---------------------------------------------------------------------------------------

_EXPECTED_T_R = {
    (1, "relaxed"): (2, 2), (1, "balanced"): (3, 3), (1, "packed"): (4, 4),
    (2, "relaxed"): (4, 4), (2, "balanced"): (6, 5), (2, "packed"): (8, 7),
    (3, "relaxed"): (6, 5), (3, "balanced"): (9, 8), (3, "packed"): (12, 10),
    (5, "relaxed"): (10, 8), (5, "balanced"): (15, 12), (5, "packed"): (20, 16),
}


@pytest.mark.parametrize(("days", "pace"), sorted(_EXPECTED_T_R))
def test_pace_targets_use_ceil_for_every_trip_shape(days: int, pace: str) -> None:
    targets = pace_targets(days, _PACES[pace])
    assert (targets.target_stops, targets.minimum_useful) == _EXPECTED_T_R[(days, pace)]
    assert targets.minimum_useful == math.ceil(0.8 * targets.target_stops)
    assert targets.healthy_buffer == math.ceil(2.25 * targets.target_stops)


def test_pace_targets_are_the_scheduler_s_own_per_day_caps() -> None:
    assert PACE_TARGET_PER_DAY == planner_module._MAX_ATTRACTIONS_PER_DAY
    assert pace_targets(3, TripPace.BALANCED).healthy_buffer == 21


@pytest.mark.parametrize(
    ("viable", "expected"),
    [
        (24, InventorySufficiencyStatus.HEALTHY),
        (21, InventorySufficiencyStatus.HEALTHY),
        (20, InventorySufficiencyStatus.SUFFICIENT),
        (12, InventorySufficiencyStatus.SUFFICIENT),
        (9, InventorySufficiencyStatus.SUFFICIENT),
        (8, InventorySufficiencyStatus.THIN_BUT_USABLE),
        (7, InventorySufficiencyStatus.INSUFFICIENT),
        (0, InventorySufficiencyStatus.INSUFFICIENT),
    ],
)
def test_inventory_semantics_for_a_three_day_balanced_trip(viable: int, expected: InventorySufficiencyStatus) -> None:
    targets = pace_targets(3, TripPace.BALANCED)  # T=9, R=8, H=21
    assert inventory_status(viable, targets) == expected
    report = _with_inventory(_state(), viable).inventory_sufficiency_report
    assert (report.status, report.target_stops, report.minimum_useful, report.healthy_buffer) == (expected, 9, 8, 21)
    assert report.viable_candidates == viable


def test_viable_inventory_counts_unique_quality_approved_non_low_value_candidates() -> None:
    state = _state()
    state.candidate_quality_report = CandidateQualityReport(
        destination_name="x",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[
            _score(1),
            _score(1),  # the same place twice counts once
            _score(2, CandidateQualityTier.SECONDARY_CANDIDATE),
            _score(3, CandidateQualityTier.LOW_PRIORITY),
            _score(4, CandidateQualityTier.REJECTED),
            _score(5, low_value=True),  # a statue is not meaningful inventory
        ],
        ai_directed_scores=[_score(6, CandidateQualityTier.PRIMARY_ANCHOR)],
    )
    assert count_viable_candidates(state) == 3


# -- usefulness contract ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("days", "pace"), sorted(_EXPECTED_T_R))
def test_usefulness_requires_r_meaningful_stops_for_every_trip_shape(days: int, pace: str) -> None:
    target, minimum = _EXPECTED_T_R[(days, pace)]
    cap = PACE_TARGET_PER_DAY[_PACES[pace]]

    passing = evaluate_usefulness(
        _with_schedule(_with_inventory(_state(days, _PACES[pace]), 40), _spread(minimum, days, cap))
    )
    assert passing.enforced and passing.passed and not passing.underfilled
    assert passing.scheduled_meaningful_stops == minimum
    assert passing.target_attainment_ratio == round(minimum / target, 3)

    failing = evaluate_usefulness(
        _with_schedule(_with_inventory(_state(days, _PACES[pace]), 40), _spread(minimum - 1, days, cap))
    )
    assert failing.enforced and not failing.passed and failing.underfilled


def test_three_day_balanced_seven_stops_fails_and_eight_may_pass() -> None:
    seven = evaluate_usefulness(_with_schedule(_with_inventory(_state(), 24), [3, 2, 2]))
    assert seven.scheduled_meaningful_stops == 7 and seven.underfilled

    eight = evaluate_usefulness(_with_schedule(_with_inventory(_state(), 24), [3, 3, 2]))
    assert eight.passed

    # two stops on every day is NOT enough: the total is what counts
    six = evaluate_usefulness(_with_schedule(_with_inventory(_state(), 24), [2, 2, 2]))
    assert six.underfilled and not six.empty_days


def test_an_empty_day_fails_even_when_the_total_is_met() -> None:
    state = _with_schedule(_with_inventory(_state(5, TripPace.RELAXED), 30), [2, 2, 2, 2, 0])  # R = 8
    verdict = evaluate_usefulness(state)
    assert verdict.meets_total and verdict.empty_days == (5,) and verdict.underfilled


def test_low_value_and_ungrounded_stops_are_not_meaningful() -> None:
    state = _with_inventory(_state(1, TripPace.BALANCED), 10)  # R = 3
    state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(
                day_number=1,
                date=_START,
                experiences=[_stop(1), _stop(2), _stop(3, low_value=True), _stop(4, grounded=False)],
            )
        ]
    )
    verdict = evaluate_usefulness(state)
    assert verdict.scheduled_meaningful_stops == 2 and verdict.underfilled


def test_thin_but_usable_inventory_is_still_held_to_the_contract() -> None:
    # viable = 8 = R for a 3-day balanced trip: not blocked, and expected to schedule >= 8
    state = _with_schedule(_with_inventory(_state(), 8), [3, 2, 2])
    verdict = evaluate_usefulness(state)
    assert verdict.inventory == InventorySufficiencyStatus.THIN_BUT_USABLE
    assert verdict.enforced and verdict.underfilled
    critical, warnings, blocking, review = usefulness_findings(verdict)
    assert critical == [] and blocking == []
    assert review == [UNDERFILLED_PLAN] and warnings[0].category == "underfilled_plan"


def test_only_inventory_below_r_blocks() -> None:
    blocked = usefulness_findings(evaluate_usefulness(_with_schedule(_with_inventory(_state(), 7), [3, 2, 2])))
    assert blocked[2] == [INSUFFICIENT_VERIFIED_INVENTORY] and blocked[3] == []
    assert blocked[0][0].category == "insufficient_verified_inventory"
    assert "not padded" in blocked[0][0].message

    usable = usefulness_findings(evaluate_usefulness(_with_schedule(_with_inventory(_state(), 8), [3, 3, 2])))
    assert usable == ([], [], [], [])  # viable < T but >= R: never blocked for that alone


def test_validator_reports_blocking_and_review_codes_only_when_the_gate_ran() -> None:
    validator = PlanValidatorService()

    insufficient = validator.run(_with_schedule(_with_inventory(_state(), 7), [3, 2, 2])).validation_report
    assert insufficient.readiness_status == ReadinessStatus.BLOCKED
    assert insufficient.blocking_codes == [INSUFFICIENT_VERIFIED_INVENTORY]

    underfilled = validator.run(_with_schedule(_with_inventory(_state(), 24), [3, 2, 2])).validation_report
    assert UNDERFILLED_PLAN in underfilled.review_codes
    assert underfilled.readiness_status == ReadinessStatus.NEEDS_REVIEW
    assert INSUFFICIENT_VERIFIED_INVENTORY not in underfilled.blocking_codes

    useful = validator.run(_with_schedule(_with_inventory(_state(), 24), [3, 3, 3])).validation_report
    assert useful.blocking_codes == [] and UNDERFILLED_PLAN not in useful.review_codes

    # no inventory report (gate off / a plan stored before this section): contract not asserted
    legacy_state = _with_schedule(_state(), [1, 0, 0])
    legacy = validator.run(legacy_state).validation_report
    assert legacy.blocking_codes == [] and UNDERFILLED_PLAN not in legacy.review_codes

    # readiness always matches its machine-readable reasons
    for report in (insufficient, underfilled, useful, legacy):
        expected = (
            ReadinessStatus.BLOCKED if report.critical_issues
            else ReadinessStatus.NEEDS_REVIEW if report.review_codes
            else ReadinessStatus.READY
        )
        assert report.readiness_status == expected
        assert set(report.review_codes) == {
            issue.category.upper() for issue in report.warnings if issue.severity.value == "warning"
        }


# -- sufficiency gate: bounded expansion --------------------------------------------------------------------


class _SizingPlaces(PlacesProvider):
    provider_name = "geoapify_places"
    supports_inventory_sizing = True

    def __init__(self, extra: int) -> None:
        self.extra = extra
        self.calls: list[dict[str, Any] | None] = []

    def search_attractions(self, destination: str, filters: dict[str, Any] | None = None) -> ProviderResponse[Any]:
        self.calls.append(filters)
        places = [
            NormalizedPlace(
                place_id=f"geoapify/new{index}",
                name=f"New Museum {index}",
                category="museum",
                coordinates=GeoPoint(lat=50.0 + index * 0.001, lng=10.0),
                source=self.provider_name,
                data_status=DataStatus.LIVE,
                confidence=0.6,
                provider_tags={"tourism": "museum"},
            )
            for index in range(self.extra)
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name, provider_type=self.provider_type, status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE, data=places, confidence=0.6,
        )


def _gate_state(pool: int) -> PlanningState:
    state = _state()
    state.destination_context = DestinationContext(
        destination_name="Fixtureville, Fixtureland",
        candidate_pois=[
            {
                "place_id": f"geoapify/p{index}", "name": f"Museum {index}", "category": "museum",
                "coordinates": {"lat": 50.0, "lng": 10.0 + index * 0.001}, "source": "geoapify_places",
                "data_status": "live", "confidence": 0.6, "provider_tags": {"tourism": "museum"},
            }
            for index in range(pool)
        ],
    )
    state.candidate_quality_report = InventorySufficiencyService().quality_service.build_report(state)
    return state


def test_the_gate_expands_once_when_below_the_healthy_buffer_and_never_again() -> None:
    places = _SizingPlaces(extra=20)
    service = InventorySufficiencyService(gateway=ProviderGateway(places=places))
    state = _gate_state(pool=6)  # below R = 8
    state.usefulness_fallback_applied = True

    service.run(state)
    report = state.inventory_sufficiency_report
    assert places.calls == [{"pool_size": 60, "page": 1}]  # exactly one further page
    assert report.expansion_attempted and report.viable_before_expansion == 6
    assert report.viable_candidates == 26 and report.status == InventorySufficiencyStatus.HEALTHY
    assert len(state.destination_context.candidate_pois) == 26
    assert state.usefulness_fallback_applied is False  # each generation starts with its fallback unused


def test_the_gate_does_not_expand_a_healthy_pool_or_a_provider_that_cannot_page() -> None:
    healthy_places = _SizingPlaces(extra=20)
    healthy = _gate_state(pool=22)
    InventorySufficiencyService(gateway=ProviderGateway(places=healthy_places)).run(healthy)
    assert healthy_places.calls == [] and healthy.inventory_sufficiency_report.expansion_attempted is False

    class _NoSizing(PlacesProvider):
        provider_name = "openstreetmap_places"

    thin = _gate_state(pool=6)
    InventorySufficiencyService(gateway=ProviderGateway(places=_NoSizing())).run(thin)
    assert thin.inventory_sufficiency_report.status == InventorySufficiencyStatus.INSUFFICIENT
    assert thin.inventory_sufficiency_report.expansion_attempted is False
    assert len(thin.destination_context.candidate_pois) == 6  # nothing invented


# -- insufficient inventory is a completed, blocked outcome -- not a failure ------------------------------------


@pytest.mark.parametrize("engine", ["langgraph", "legacy"])
def test_insufficient_inventory_completes_normally_with_blocked_readiness(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch, inventory_sufficiency_gate_enabled: None, engine: str
) -> None:
    monkeypatch.setenv("PLANNING_ENGINE_MODE", engine)
    get_settings.cache_clear()

    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200 and response.json()["success"] is True  # not an error envelope

    # the truthful partial state was persisted and reloads
    state = client.get(f"/trips/{created_trip_id}").json()["data"]["planning_state"]
    report = state["inventory_sufficiency_report"]
    assert report["status"] == "insufficient"
    assert (report["target_stops"], report["minimum_useful"], report["healthy_buffer"]) == (9, 8, 21)
    assert report["viable_candidates"] < report["minimum_useful"]
    validation = state["validation_report"]
    assert validation["readiness_status"] == "blocked"
    assert validation["blocking_codes"] == [INSUFFICIENT_VERIFIED_INVENTORY]
    assert state["generation_progress"]["status"] != "failed"
    # the real places that were found are still shown; nothing was added to pad the days
    scheduled = [e["name"] for day in state["experience_plan"]["daily_plans"] for e in day["experiences"]]
    assert set(scheduled) <= {"Test Fixture Attraction One", "Test Fixture Attraction Two"}
    assert state["provider_usage_report"]["credits_used"] == 0


def test_async_job_succeeds_for_insufficient_inventory_and_fails_for_a_technical_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, inventory_sufficiency_gate_enabled: None
) -> None:
    from app.tests.conftest import create_trip_payload

    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()

    trip_id = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    started = client.post(f"/trips/{trip_id}/generate")
    assert started.status_code == 202
    job = client.get(f"/trips/{trip_id}/jobs/{started.json()['data']['job_id']}").json()["data"]
    assert job["status"] == "succeeded"  # a domain outcome, not a job failure
    state = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    assert state["validation_report"]["readiness_status"] == "blocked"
    assert state["validation_report"]["blocking_codes"] == [INSUFFICIENT_VERIFIED_INVENTORY]

    # a genuine execution failure is still a FAILED job
    def _explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated execution failure")

    monkeypatch.setattr(planning_orchestrator.versioning_service, "create_initial_version", _explode)
    other_trip = client.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    failed_start = client.post(f"/trips/{other_trip}/generate")
    assert failed_start.status_code == 202
    failed_job = client.get(f"/trips/{other_trip}/jobs/{failed_start.json()['data']['job_id']}").json()["data"]
    assert failed_job["status"] == "failed"


# -- underfilled plan: one repair, then the deterministic fallback ------------------------------------------------


def test_underfilled_days_are_repairable_only_when_inventory_could_fill_them() -> None:
    underfilled = _with_schedule(_with_inventory(_state(), 24), [3, 1, 0])
    issues = [i for i in classify_repairable_issues(underfilled) if i.issue_type == RepairableIssueType.UNDERFILLED_DAY]
    assert [issue.day_index for issue in issues] == [2, 3]

    insufficient = _with_schedule(_with_inventory(_state(), 5), [3, 1, 0])
    assert classify_repairable_issues(insufficient) == []  # a supply limit is not repairable

    no_gate = _with_schedule(_state(), [3, 1, 0])
    assert classify_repairable_issues(no_gate) == []


def test_router_gives_an_underfilled_plan_exactly_one_fallback_pass() -> None:
    state = _with_schedule(_with_inventory(_state(), 24), [3, 2, 2])
    graph_state = {"planning_state": state}
    # AI repair is not enabled in this suite, so validation routes straight to the fallback
    assert route_after_validation(graph_state) == "underfill_fallback"
    assert route_after_repair(graph_state) == "underfill_fallback"  # a repair that did not complete

    state.usefulness_fallback_applied = True
    assert _underfill_fallback_or_finish(state) == "provider_coverage"  # never a second pass
    assert route_after_validation(graph_state) == "provider_coverage"

    useful = _with_schedule(_with_inventory(_state(), 24), [3, 3, 3])
    assert route_after_validation({"planning_state": useful}) == "provider_coverage"
    blocked = _with_schedule(_with_inventory(_state(), 5), [2, 2, 1])
    assert route_after_validation({"planning_state": blocked}) == "provider_coverage"


def test_the_deterministic_fallback_tops_days_up_from_unused_verified_candidates_only() -> None:
    def _poi(index: int, lng: float) -> dict[str, Any]:
        return {"place_id": f"geoapify/c{index}", "name": f"Place {index}", "coordinates": {"lat": 50.0, "lng": lng}}

    pool = [_poi(index, 10.0 + index * 0.01) for index in range(8)]
    statue = _poi(90, 10.001)
    no_coordinates = {"place_id": "geoapify/c91", "name": "Place 91"}
    profiles = {
        id(poi): planner_module._CandidateProfile(
            score=0.6, tier_rank=2, primary="museum", categories=frozenset({"museum"}), low_value=poi is statue,
            commercial_gallery=False, sub_feature_cluster=None, matched_interests=[], tier="good_candidate",
        )
        for poi in [*pool, statue, no_coordinates]
    }
    day_groups = [[pool[0], pool[1], pool[2]], [pool[7]], []]

    filled = planner_module._top_up_underfilled_days(
        day_groups, [*pool, statue, no_coordinates], profiles, max_per_day=3
    )
    scheduled = [poi["place_id"] for day in filled for poi in day]
    assert len(scheduled) == len(set(scheduled)) == 8  # every eligible candidate, none twice
    assert "geoapify/c90" not in scheduled and "geoapify/c91" not in scheduled  # no statue, no coordinate-less place
    assert set(scheduled) <= {poi["place_id"] for poi in pool}  # nothing outside the verified pool
    assert [len(day) for day in filled] == [3, 3, 2]
    assert pool[6] in filled[1]  # the neighbour of day 2's stop joins day 2


# -- bounded routing ---------------------------------------------------------------------------------------------


class _SequenceCountingRouting(RoutingProvider):
    """Duration proportional to the longitude gap; counts sequence requests."""

    provider_name = "counting_routing_provider"

    def __init__(self) -> None:
        self.sequence_requests: list[int] = []
        self.single_requests = 0

    def _leg(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        gap = abs(origin[1] - destination[1])
        return RouteResult(
            provider=self.provider_name, status=ProviderStatus.SUCCESS, distance_meters=gap * 100_000,
            duration_seconds=gap * 1000, source=self.provider_name, confidence=0.9,
        )

    def get_route(self, request: RouteRequest) -> RouteResult:
        self.single_requests += 1
        return self._leg((request.origin_lat, request.origin_lon), (request.destination_lat, request.destination_lon))

    def get_route_sequence(self, points: list[tuple[float, float]], profile: Any = None) -> list[RouteResult]:
        self.sequence_requests.append(len(points))
        return [self._leg(a, b) for a, b in zip(points, points[1:])]


def _routed_state(days: list[list[float]]) -> PlanningState:
    state = _state(len(days), TripPace.PACKED)
    counter = iter(range(1000))
    state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(
                day_number=index + 1,
                date=_START + timedelta(days=index),
                experiences=[_stop(next(counter), lng=lng) for lng in longitudes],
            )
            for index, longitudes in enumerate(days)
        ]
    )
    return state


def test_a_normal_day_is_routed_with_exactly_one_request() -> None:
    routing = _SequenceCountingRouting()
    gateway = ProviderGateway(routing=routing)
    state = _routed_state([[10.0, 10.1, 10.2], [11.0, 11.1, 11.2, 11.3], [12.0, 12.1]])

    state.route_feasibility_report = RouteFeasibilityService(gateway=gateway).build_report(state)
    assert routing.sequence_requests == [3, 4, 2]  # one request per day, all of that day's stops
    assert len(state.route_feasibility_report.legs) == 2 + 3 + 1
    assert routing.single_requests == 0  # never pair by pair

    report = RouteAwareSequencingService(gateway=gateway).build_report(state)
    # geographically consistent days: the order is routed (once more here, because this fake
    # provider has no per-generation memo) and no alternative is ever requested
    assert routing.sequence_requests == [3, 4, 2, 3, 4, 2]
    assert all(s.suggested_order == s.original_order and s.improvement_seconds == 0.0 for s in report.suggestions)


def test_a_clearly_poor_order_gets_at_most_one_alternative_and_real_route_data_decides() -> None:
    routing = _SequenceCountingRouting()
    gateway = ProviderGateway(routing=routing)
    state = _routed_state([[10.0, 10.9, 10.1, 10.8]])  # west, east, west, east
    sequencing = RouteAwareSequencingService(gateway=gateway)

    report = sequencing.build_report(state)
    assert routing.sequence_requests == [4, 4]  # the current order + ONE alternative
    suggestion = report.suggestions[0]
    assert suggestion.status == ProviderStatus.SUCCESS
    names = {e.experience_id: e.coordinates.lng for e in state.experience_plan.daily_plans[0].experiences}
    assert [names[i] for i in suggestion.suggested_order] == [10.0, 10.1, 10.8, 10.9]
    # every figure is the provider's: original 900+800+700 s, alternative 100+700+100 s
    assert suggestion.route_duration_seconds == pytest.approx(900.0)
    assert suggestion.improvement_seconds == pytest.approx(1500.0)

    assert sequencing.apply_report(state, report, min_improvement_seconds=60.0) is True
    assert [e.coordinates.lng for e in state.experience_plan.daily_plans[0].experiences] == [10.0, 10.1, 10.8, 10.9]
    assert [e.stop_order for e in state.experience_plan.daily_plans[0].experiences] == [1, 2, 3, 4]


def test_two_stop_days_never_get_an_alternative() -> None:
    routing = _SequenceCountingRouting()
    RouteAwareSequencingService(gateway=ProviderGateway(routing=routing)).build_report(_routed_state([[10.0, 10.9]]))
    assert routing.sequence_requests == [2]


@pytest.mark.parametrize(
    ("days", "pace", "requests", "credits"),
    [(1, "relaxed", 2, 2), (3, "balanced", 10, 20), (5, "packed", 14, 42)],
)
def test_worst_case_routing_fits_the_generation_budget(days: int, pace: str, requests: int, credits: int) -> None:
    stops = PACE_TARGET_PER_DAY[_PACES[pace]]
    alternates = days if stops >= 3 else 0
    swap_and_repair = 4 if days > 1 else 1  # one swap evaluation (2 day-routes) + 2 post-repair re-routes
    worst_requests = days + alternates + swap_and_repair
    assert worst_requests == requests
    assert worst_requests * (stops - 1) == credits
    assert worst_requests <= route_request_allowance(days)
    assert credits <= Settings(_env_file=None).geoapify_max_credits_per_generation


# -- accounting: targeted regeneration and repair re-routes ------------------------------------------------------------


def test_targeted_regeneration_and_its_reroute_charge_only_their_own_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.providers.routing import geoapify_adapter as routing_module
    from app.providers.routing.geoapify_adapter import GeoapifyRoutingAdapter
    from app.services.targeted_regeneration_executor import TargetedRegenerationExecutor
    from app.storage.provider_cache_store import ProviderCacheStore

    real_client = httpx.Client
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        legs = [{"distance": 500.0, "time": 300.0} for _ in request.url.params["waypoints"].split("|")[1:]]
        return httpx.Response(200, json={"features": [{"properties": {"legs": legs}}]})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(_handler), **kwargs))
    monkeypatch.setattr(
        routing_module, "get_settings", lambda: Settings(_env_file=None, GEOAPIFY_API_KEY="SENTINEL_203C2B_KEY_0001")
    )
    gateway = ProviderGateway(routing=GeoapifyRoutingAdapter(cache_store=ProviderCacheStore(tmp_path / "c.sqlite3")))
    executor = TargetedRegenerationExecutor(
        route_feasibility_svc=RouteFeasibilityService(gateway=gateway),
        route_aware_sequencing_svc=RouteAwareSequencingService(gateway=gateway),
        travel_time_buffer_svc=TravelTimeBufferService(gateway=gateway),
        gateway=gateway,
    )
    regeneration = GenerationProviderContext(ProviderUsageTracker(100), route_requests_left=route_request_allowance(1))
    unrelated = GenerationProviderContext(ProviderUsageTracker(100), route_requests_left=route_request_allowance(1))
    working_state = _routed_state([[10.0, 10.1, 10.2]])

    executor._rerun_routing_reports(working_state, set(), regeneration)
    assert len(requests) == 1  # one day, one request; feasibility + sequencing + buffers share it
    assert regeneration.usage_tracker.credits_used("routing") == 2
    assert unrelated.usage_tracker.credits_used() == 0
    assert working_state.travel_time_buffer_report.buffers[0].route_duration_seconds == 300.0

    # a bounded repair inside the SAME regeneration re-routes under the same context:
    # the unchanged day is read back from the generation's own results at zero cost
    executor._rerun_routing_reports(working_state, set(), regeneration)
    assert len(requests) == 1 and regeneration.usage_tracker.credits_used("routing") == 2

    # ... and an execution started without a context creates its own isolated one
    import inspect

    assert "GenerationProviderContext.new" in inspect.getsource(TargetedRegenerationExecutor.execute)


# -- compatibility -----------------------------------------------------------------------------------------------------


def test_a_state_stored_before_this_section_still_loads() -> None:
    state = _with_schedule(_with_inventory(_state(), 24), [3, 3, 2])
    PlanValidatorService().run(state)
    stored = json.loads(state.model_dump_json())
    for key in ("inventory_sufficiency_report", "provider_usage_report", "usefulness_fallback_applied"):
        stored.pop(key)
    for key in ("blocking_codes", "review_codes"):
        stored["validation_report"].pop(key)

    loaded = PlanningState.model_validate(stored)
    assert loaded.inventory_sufficiency_report is None and loaded.provider_usage_report is None
    assert loaded.usefulness_fallback_applied is False
    assert loaded.validation_report.blocking_codes == [] and loaded.validation_report.review_codes == []
    assert len(loaded.experience_plan.daily_plans) == 3
    assert pace_targets_for(loaded).target_stops == 9


# -- no city-specific production logic -------------------------------------------------------------------------------------

_APP_DIR = Path(__file__).resolve().parents[2]  # backend/app
_BACKEND_DIR = _APP_DIR.parent
_NEW_PRODUCTION_MODULES = (
    "core/provider_usage.py",
    "providers/errors.py",
    "providers/geoapify_client.py",
    "providers/places/geoapify_places_adapter.py",
    "providers/places/geoapify_categories.py",
    "providers/places/factory.py",
    "providers/routing/geoapify_adapter.py",
    "providers/ai_candidate_proposal/anchor_guidance.py",
    "services/pace_targets.py",
    "services/usefulness_contract.py",
    "services/inventory_sufficiency_service.py",
    "services/day_order_heuristics.py",
    "models/inventory_sufficiency.py",
)


def test_no_benchmark_city_or_landmark_name_appears_in_the_new_production_code() -> None:
    data = json.loads((_BACKEND_DIR / "scripts" / "benchmark" / "cities.json").read_text())
    assert len(data["tuning"]) == 18 and len(data["holdout"]) == 10 and len(data["holdout_stress"]) == 8
    assert not set(data["tuning"]) & set(data["holdout"])
    assert {s["destination"] for s in data["holdout_stress"]} <= set(data["holdout"])  # stress runs on holdout only

    names = {destination.split(",")[0].strip() for destination in (*data["tuning"], *data["holdout"])}
    names |= {name for scenario in data["holdout_stress"] for name in scenario.get("must_visit", [])}
    for relative in _NEW_PRODUCTION_MODULES:
        source = (_APP_DIR / relative).read_text()
        for name in names:
            assert not re.search(rf"\b{re.escape(name)}\b", source), f"{name!r} appears in {relative}"
        assert "benchmark" not in source.lower().replace("benchmark-only", "")


# -- must-visits: grounded before ranking, pinned, and disclosed when ungroundable ------------------------------------------


class _MustVisitPlaces(_SizingPlaces):
    def __init__(self) -> None:
        super().__init__(extra=3)
        self.lookups: list[str] = []

    def search_must_visit_place(self, must_visit_term: str, primary_destination: str, filters: Any = None) -> ProviderResponse[Any]:
        self.lookups.append(must_visit_term)
        if must_visit_term != "the big tower":
            return self.not_connected(unavailable_fields=["must_visit_place"])
        place = NormalizedPlace(
            place_id="geoapify/tower", name="Torre Grande", category="tower",
            coordinates=GeoPoint(lat=50.02, lng=10.02), source="geoapify", data_status=DataStatus.LIVE, confidence=0.5,
        )
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name, provider_type=self.provider_type, status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE, data=[place], confidence=0.5,
        )


def test_must_visits_are_grounded_pinned_by_the_users_term_and_disclosed_when_ungroundable() -> None:
    from app.providers.base import CurrencyProvider, HolidayProvider, WeatherProvider
    from app.services.candidate_quality_service import CandidateQualityService
    from app.services.destination_context_service import DestinationContextService

    places = _MustVisitPlaces()
    gateway = ProviderGateway(
        places=places, weather=WeatherProvider(), holiday=HolidayProvider(), currency=CurrencyProvider()
    )
    state = _state(must_visit=["the big tower", "an invented palace"])
    DestinationContextService(gateway=gateway).run(state)

    # bounded, trip-derived pool sizes were passed to the sizing provider (T = 9 -> max(60, 45))
    # ... and, the trip having must-visits, the provider is asked to hold back part of its local share
    assert places.calls[0] == {"pool_size": 60, "hold_back_for_must_visits": True}
    assert places.lookups == ["the big tower", "an invented palace"]

    pois = state.destination_context.candidate_pois
    tower = next(poi for poi in pois if poi["place_id"] == "geoapify/tower")
    # provider identity, name and coordinates are the provider's; only the user's TERM is attached
    assert (tower["name"], tower["source"], tower["must_visit_term"]) == ("Torre Grande", "geoapify", "the big tower")
    assert not any("invented palace" in str(poi.get("name", "")).lower() for poi in pois)  # never fabricated
    assert any(
        "'an invented palace' could not be matched to a verified place" in text
        for text in state.destination_context.assumptions
    )

    # the grounded must-visit is ranked as a primary anchor although its name differs from the term
    report = CandidateQualityService().build_report(state)
    by_id = {score.candidate_id: score for score in report.attraction_scores}
    assert by_id["geoapify/tower"].quality_tier == CandidateQualityTier.PRIMARY_ANCHOR
    assert by_id["geoapify/tower"].total_score > max(
        score.total_score for candidate_id, score in by_id.items() if candidate_id != "geoapify/tower"
    )
    assert planner_module._matches_must_visit(tower, ["the big tower"]) is True


# -- end to end: the production provider wiring through the real pipeline ---------------------------------------------------


def _production_like_network() -> tuple[Any, dict[str, list[httpx.Request]]]:
    """Geoapify geocoding + places + routing answered in-process; every other host gets a 404."""
    seen: dict[str, list[httpx.Request]] = {"geocode": [], "places": [], "routing": [], "other": []}
    families = {
        "tourism.sights": ("tourism.sights.castle", "Castle"),
        "tourism.attraction": ("tourism.attraction.viewpoint", "Lookout"),
        "entertainment.museum": ("entertainment.museum", "Museum"),
        "leisure.park": ("leisure.park.garden", "Garden"),
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if "geoapify" not in request.url.host:
            seen["other"].append(request)
            return httpx.Response(404)
        path = request.url.path
        if path == "/v1/geocode/search":
            seen["geocode"].append(request)
            if request.url.params["text"] != "Testville, Testland":
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": [{
                "city": "Testville", "country": "Testland", "lat": 50.0, "lon": 10.0, "result_type": "city",
                "formatted": "Testville, Testland", "place_id": "dest01",
                "rank": {"confidence": 1, "match_type": "full_match"},
                "bbox": {"lon1": 9.8, "lat1": 49.8, "lon2": 10.2, "lat2": 50.2},
            }]})
        if path == "/v2/places":
            seen["places"].append(request)
            categories = request.url.params["categories"]
            if int(request.url.params["offset"]) > 0:
                return httpx.Response(200, json={"features": []})
            if "catering" in categories:
                # three cafes next to each attraction family's area
                return httpx.Response(200, json={"features": [
                    {"properties": {"place_id": f"food{area}_{i}", "name": f"Cafe {area}-{i}",
                                    "categories": ["catering.cafe"],
                                    "lat": 50.0 + area * 0.01 + i * 0.001, "lon": 10.0 + area * 0.01 + 0.001}}
                    for area in range(len(families)) for i in range(3)
                ]})
            for index, (key, (leaf, label)) in enumerate(families.items()):
                if key in categories:
                    return httpx.Response(200, json={"features": [
                        {"properties": {
                            "place_id": f"{label.lower()}{i}", "name": f"{label} {i}", "categories": [leaf],
                            "lat": 50.0 + index * 0.01 + i * 0.001, "lon": 10.0 + index * 0.01,
                            "wiki_and_media": {"wikipedia": f"en:{label} {i}"},
                        }} for i in range(7)
                    ]})
            return httpx.Response(200, json={"features": []})
        if path == "/v1/routing":
            seen["routing"].append(request)
            legs = [{"distance": 400.0, "time": 300.0} for _ in request.url.params["waypoints"].split("|")[1:]]
            return httpx.Response(200, json={"features": [{"properties": {"legs": legs}}]})
        return httpx.Response(404)

    return _handler, seen


@pytest.mark.parametrize("engine", ["langgraph", "legacy"])
def test_production_wiring_builds_a_useful_grounded_plan_within_the_credit_budget(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    inventory_sufficiency_gate_enabled: None, engine: str,
) -> None:
    from app.providers.base import CurrencyProvider, HolidayProvider, WeatherProvider
    from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
    from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter
    from app.providers.routing.geoapify_adapter import GeoapifyRoutingAdapter
    from app.storage.provider_cache_store import ProviderCacheStore

    monkeypatch.setenv("PLANNING_ENGINE_MODE", engine)
    monkeypatch.setenv("GEOAPIFY_API_KEY", "SENTINEL_203C2B_KEY_E2E_0001")
    monkeypatch.setenv("GEOCODING_PROVIDER", "geoapify")
    get_settings.cache_clear()

    handler, seen = _production_like_network()
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    monkeypatch.setattr(provider_gateway, "places", GeoapifyPlacesAdapter(cache_store=store, geocoder=GeoapifyGeocoder()))
    monkeypatch.setattr(provider_gateway, "routing", GeoapifyRoutingAdapter(cache_store=store))
    for slot, stub in (("weather", WeatherProvider()), ("holiday", HolidayProvider()), ("currency", CurrencyProvider())):
        monkeypatch.setattr(provider_gateway, slot, stub)

    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200
    state = client.get(f"/trips/{created_trip_id}").json()["data"]["planning_state"]

    inventory = state["inventory_sufficiency_report"]
    assert inventory["status"] == "healthy" and inventory["viable_candidates"] >= inventory["healthy_buffer"] == 21

    days = state["experience_plan"]["daily_plans"]
    scheduled = [experience for day in days for experience in day["experiences"]]
    assert all(len(day["experiences"]) >= 1 for day in days)  # no empty day
    assert len(scheduled) >= inventory["minimum_useful"] == 8
    assert len({e["provider_place_id"] for e in scheduled}) == len(scheduled)  # no duplicate
    for experience in scheduled:  # grounded identities only
        assert experience["provider_source"] == "geoapify_places"
        assert experience["provider_place_id"].startswith("geoapify/") and experience["coordinates"]
    assert all(day["restaurant_suggestions"] for day in days)  # food stays a nearby suggestion, not a stop
    suggested = [s["name"] for day in days for s in day["restaurant_suggestions"]]
    assert len(suggested) == len(set(suggested))  # local to each day: no restaurant repeated across days
    assert not any("Cafe" in experience["name"] for experience in scheduled)

    validation = state["validation_report"]
    assert validation["blocking_codes"] == [] and validation["review_codes"] == []
    # useful, grounded, fully routed, nothing to review: the plan is ready
    assert validation["readiness_status"] == "ready"

    # routing: real provider legs for every consecutive pair, never more than 2 requests per day
    legs = state["route_feasibility_report"]["legs"]
    assert len(legs) == sum(len(day["experiences"]) - 1 for day in days)
    assert all(leg["status"] == "success" and leg["duration_seconds"] == 300.0 for leg in legs)
    assert len(days) <= len(seen["routing"]) <= 2 * len(days)
    assert state["travel_time_buffer_report"]["movement_data_provenance"] == "provider_backed"

    # accounting: one generation, one isolated budget, never exceeded
    usage = state["provider_usage_report"]
    assert usage["credits_by_api"]["geocoding"] == len(seen["geocode"]) == 1
    # 4 attraction groups and food, each as a broad and a local request, + accommodation
    assert usage["calls_by_api"]["places"] == len(seen["places"]) == 11
    assert usage["calls_by_api"]["routing"] == len(seen["routing"])
    assert usage["credits_used"] == sum(usage["credits_by_api"].values()) <= usage["budget"] == 100
    assert usage["refused_calls"] == 0
    assert seen["other"] == []  # nothing fell through to Overpass or any other host
