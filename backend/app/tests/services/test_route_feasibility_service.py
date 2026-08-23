from __future__ import annotations

from app.models.common import GeoPoint, ProviderStatus
from app.models.planning_state import (
    DailyPlan,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripRequest,
)
from app.models.routing import RouteFeasibilityStatus, RouteRequest, RouteResult
from app.providers.gateway import ProviderGateway
from app.providers.routing import NotConnectedRoutingProvider, RoutingProvider
from app.services.route_feasibility_service import RouteFeasibilityService

# Step 165E: RouteFeasibilityService tests. Every test here injects a
# ProviderGateway with either the default NotConnectedRoutingProvider or a
# deterministic in-memory fake RoutingProvider double -- never a real OSRM
# instance, matching backend/app/tests/providers/test_osrm_adapter.py and
# test_provider_gateway_routing.py's own no-network guarantee.


class _FakeRoutingProvider(RoutingProvider):
    """Deterministic test double. Records every request it's called with so
    tests can prove the gateway is (or isn't) actually invoked."""

    provider_name = "fake_routing_provider"

    def __init__(self, result: RouteResult | None = None) -> None:
        self._result = result
        self.calls: list[RouteRequest] = []

    def get_route(self, request: RouteRequest) -> RouteResult:
        self.calls.append(request)
        if self._result is not None:
            return self._result
        return RouteResult(
            provider=self.provider_name,
            status=ProviderStatus.SUCCESS,
            distance_meters=1500.0,
            duration_seconds=900.0,
            geometry=None,
            source=self.provider_name,
            confidence=0.9,
            message="Fake route for test purposes only.",
        )


class _AssertNeverCalledRoutingProvider(RoutingProvider):
    """Fails the test loudly if the routing provider is ever invoked --
    used to prove a leg with missing coordinates never reaches the
    provider at all."""

    provider_name = "assert_never_called"

    def get_route(self, request: RouteRequest) -> RouteResult:
        raise AssertionError("Routing provider must not be called for an incomplete leg.")


def _experience(name: str, *, lat: float | None, lng: float | None) -> ExperienceItem:
    coordinates = GeoPoint(lat=lat, lng=lng) if lat is not None and lng is not None else None
    return ExperienceItem(name=name, category="attraction", coordinates=coordinates)


def _planning_state_with_experiences(*experience_rows: list[ExperienceItem]) -> PlanningState:
    """Builds a PlanningState with one DailyPlan per row of experiences,
    bypassing ExperiencePlannerService entirely for precise control over
    scheduled order and coordinates.
    """
    trip_request = TripRequest(
        primary_destination="Testville, Testland",
        start_date="2026-08-10",
        end_date="2026-08-10",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    planning_state = PlanningState(trip_request=trip_request)
    daily_plans = [
        DailyPlan(day_number=index + 1, date=trip_request.start_date, experiences=experiences)
        for index, experiences in enumerate(experience_rows)
    ]
    planning_state.experience_plan = ExperiencePlan(daily_plans=daily_plans)
    return planning_state


# ---------------------------------------------------------------------------
# Default/not-connected behavior -- no network call, honest needs_review.
# ---------------------------------------------------------------------------


def test_not_connected_routing_provider_produces_needs_review_legs_without_network() -> None:
    gateway = ProviderGateway(routing=NotConnectedRoutingProvider())
    service = RouteFeasibilityService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=38.7169, lng=-9.1399),
        ]
    )

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.NOT_CONNECTED
    assert len(report.legs) == 1
    leg = report.legs[0]
    assert leg.status == ProviderStatus.NOT_CONNECTED
    assert leg.feasibility_status == RouteFeasibilityStatus.NEEDS_REVIEW
    assert leg.distance_meters is None
    assert leg.duration_seconds is None
    assert report.route_data_source == "not_connected"


def test_empty_experience_plan_produces_not_connected_report_with_no_legs() -> None:
    gateway = ProviderGateway(routing=NotConnectedRoutingProvider())
    service = RouteFeasibilityService(gateway=gateway)
    planning_state = _planning_state_with_experiences([])

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.NOT_CONNECTED
    assert report.legs == []


def test_no_experience_plan_at_all_produces_not_connected_report_with_no_legs() -> None:
    trip_request = TripRequest(
        primary_destination="Testville, Testland",
        start_date="2026-08-10",
        end_date="2026-08-10",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    planning_state = PlanningState(trip_request=trip_request)
    service = RouteFeasibilityService(gateway=ProviderGateway(routing=NotConnectedRoutingProvider()))

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.NOT_CONNECTED
    assert report.legs == []


# ---------------------------------------------------------------------------
# Missing coordinates -- never guessed, never calls the provider.
# ---------------------------------------------------------------------------


def test_missing_coordinates_leg_is_unavailable_and_provider_never_called() -> None:
    gateway = ProviderGateway(routing=_AssertNeverCalledRoutingProvider())
    service = RouteFeasibilityService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=None, lng=None),
        ]
    )

    report = service.build_report(planning_state)

    assert len(report.legs) == 1
    leg = report.legs[0]
    assert leg.status == ProviderStatus.UNAVAILABLE
    assert leg.feasibility_status == RouteFeasibilityStatus.UNAVAILABLE
    assert leg.distance_meters is None
    assert leg.duration_seconds is None
    assert leg.to_lat is None
    assert leg.to_lon is None
    assert leg.from_lat == 38.7223


# ---------------------------------------------------------------------------
# Successful, provider-backed routing -- real distance/duration, feasible.
# ---------------------------------------------------------------------------


def test_successful_routing_provider_creates_feasible_legs_with_distance_and_duration() -> None:
    fake_provider = _FakeRoutingProvider()
    gateway = ProviderGateway(routing=fake_provider)
    service = RouteFeasibilityService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=38.7169, lng=-9.1399),
        ]
    )

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.SUCCESS
    assert report.route_data_source == fake_provider.provider_name
    assert len(fake_provider.calls) == 1
    leg = report.legs[0]
    assert leg.status == ProviderStatus.SUCCESS
    assert leg.feasibility_status == RouteFeasibilityStatus.FEASIBLE
    assert leg.distance_meters == 1500.0
    assert leg.duration_seconds == 900.0
    assert leg.provider == fake_provider.provider_name
    assert leg.from_experience_name == "A"
    assert leg.to_experience_name == "B"


def test_legs_built_for_every_consecutive_pair_within_a_day() -> None:
    fake_provider = _FakeRoutingProvider()
    service = RouteFeasibilityService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
            _experience("C", lat=0.0, lng=2.0),
        ]
    )

    report = service.build_report(planning_state)

    assert len(report.legs) == 2
    assert [leg.from_experience_name for leg in report.legs] == ["A", "B"]
    assert [leg.to_experience_name for leg in report.legs] == ["B", "C"]
    assert len(fake_provider.calls) == 2


# ---------------------------------------------------------------------------
# Aggregate status is honest, never optimistic.
# ---------------------------------------------------------------------------


def test_aggregate_status_is_partial_when_some_legs_succeed_and_others_do_not() -> None:
    fake_provider = _FakeRoutingProvider()
    service = RouteFeasibilityService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
            _experience("C", lat=None, lng=None),
        ]
    )

    report = service.build_report(planning_state)

    assert len(report.legs) == 2
    assert report.legs[0].status == ProviderStatus.SUCCESS
    assert report.legs[1].status == ProviderStatus.UNAVAILABLE
    assert report.status == ProviderStatus.PARTIAL


def test_aggregate_status_is_unavailable_when_no_leg_succeeds_and_none_failed() -> None:
    service = RouteFeasibilityService(gateway=ProviderGateway(routing=_AssertNeverCalledRoutingProvider()))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=None, lng=None),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )

    report = service.build_report(planning_state)

    assert report.legs[0].status == ProviderStatus.UNAVAILABLE
    assert report.status == ProviderStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# Never reorders/drops scheduled experiences -- feasibility reporting only.
# ---------------------------------------------------------------------------


def test_route_feasibility_never_reorders_or_drops_scheduled_experiences() -> None:
    fake_provider = _FakeRoutingProvider()
    service = RouteFeasibilityService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("C", lat=0.0, lng=2.0),
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )
    original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]

    service.build_report(planning_state)

    assert [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ] == original_order == ["C", "A", "B"]


# ---------------------------------------------------------------------------
# No disallowed vendor import, no direct network client.
# ---------------------------------------------------------------------------


def test_route_feasibility_service_module_has_no_disallowed_imports() -> None:
    import ast
    import inspect

    from app.services import route_feasibility_service as module

    source = inspect.getsource(module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "groq",
        "anthropic",
        "openai",
        "gemini",
        "kiwi",
        "mcp",
        "httpx",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


def test_route_feasibility_service_only_reaches_routing_through_the_gateway() -> None:
    """Must never import an adapter (OSRM or otherwise) directly -- only
    `ProviderGateway`, matching the "call providers through the gateway"
    rule (docs/12_provider_architecture.md, docs/14_backend_architecture.md
    section 18)."""
    import inspect

    from app.services import route_feasibility_service as module

    source = inspect.getsource(module)
    assert "osrm_adapter" not in source
    assert "OSRMRoutingAdapter" not in source
