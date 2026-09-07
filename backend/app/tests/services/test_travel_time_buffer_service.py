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
from app.models.routing import (
    BufferSufficiencyStatus,
    MovementDataProvenance,
    RoutePathPoint,
    RouteRequest,
    RouteResult,
    TravelTimeBufferStatus,
)
from app.providers.gateway import ProviderGateway
from app.providers.routing import NotConnectedRoutingProvider, RoutingProvider
from app.services.travel_time_buffer_service import TravelTimeBufferService

# Step 166C: TravelTimeBufferService tests. Every test here injects a
# ProviderGateway with either the default NotConnectedRoutingProvider or a
# deterministic in-memory fake RoutingProvider double -- never a real OSRM
# instance, matching test_route_feasibility_service.py's own no-network
# guarantee. This service never reorders/drops a scheduled experience and
# never fabricates a duration/distance/buffer.


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


def _experience(
    name: str,
    *,
    lat: float | None,
    lng: float | None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> ExperienceItem:
    coordinates = GeoPoint(lat=lat, lng=lng) if lat is not None and lng is not None else None
    return ExperienceItem(
        name=name,
        category="attraction",
        coordinates=coordinates,
        start_time=start_time,
        end_time=end_time,
    )


def _planning_state_with_experiences(*experience_rows: list[ExperienceItem]) -> PlanningState:
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
# Default/not-connected behavior -- no network call, honest not_connected.
# ---------------------------------------------------------------------------


def test_not_connected_routing_provider_produces_not_connected_buffer_without_network() -> None:
    gateway = ProviderGateway(routing=NotConnectedRoutingProvider())
    service = TravelTimeBufferService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=38.7169, lng=-9.1399),
        ]
    )

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.NOT_CONNECTED
    assert len(report.buffers) == 1
    buffer = report.buffers[0]
    assert buffer.status == TravelTimeBufferStatus.NOT_CONNECTED
    assert buffer.buffer_status == BufferSufficiencyStatus.UNAVAILABLE
    assert buffer.route_duration_seconds is None
    assert buffer.route_distance_meters is None
    assert buffer.recommended_buffer_seconds is None
    assert report.uses_provider_backed_routes is True


def test_empty_experience_plan_produces_not_connected_report_with_no_buffers() -> None:
    gateway = ProviderGateway(routing=NotConnectedRoutingProvider())
    service = TravelTimeBufferService(gateway=gateway)
    planning_state = _planning_state_with_experiences([])

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.NOT_CONNECTED
    assert report.buffers == []


def test_no_experience_plan_at_all_produces_not_connected_report_with_no_buffers() -> None:
    trip_request = TripRequest(
        primary_destination="Testville, Testland",
        start_date="2026-08-10",
        end_date="2026-08-10",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    planning_state = PlanningState(trip_request=trip_request)
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=NotConnectedRoutingProvider()))

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.NOT_CONNECTED
    assert report.buffers == []


# ---------------------------------------------------------------------------
# Missing coordinates -- never guessed, never calls the provider.
# ---------------------------------------------------------------------------


def test_missing_coordinates_leg_is_not_computable_and_provider_never_called() -> None:
    gateway = ProviderGateway(routing=_AssertNeverCalledRoutingProvider())
    service = TravelTimeBufferService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=None, lng=None),
        ]
    )

    report = service.build_report(planning_state)

    assert len(report.buffers) == 1
    buffer = report.buffers[0]
    assert buffer.status == TravelTimeBufferStatus.NOT_COMPUTABLE
    assert buffer.buffer_status == BufferSufficiencyStatus.UNAVAILABLE
    assert buffer.route_duration_seconds is None
    assert buffer.route_distance_meters is None
    assert buffer.recommended_buffer_seconds is None


# ---------------------------------------------------------------------------
# Successful, provider-backed routing -- real duration/distance, buffer
# recommendation mirrors it exactly.
# ---------------------------------------------------------------------------


def test_successful_routing_provider_creates_buffer_with_duration_and_recommendation() -> None:
    fake_provider = _FakeRoutingProvider()
    gateway = ProviderGateway(routing=fake_provider)
    service = TravelTimeBufferService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=38.7169, lng=-9.1399),
        ]
    )

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.SUCCESS
    assert len(fake_provider.calls) == 1
    buffer = report.buffers[0]
    assert buffer.status == TravelTimeBufferStatus.SUCCESS
    assert buffer.route_duration_seconds == 900.0
    assert buffer.route_distance_meters == 1500.0
    # recommended_buffer_seconds is never anything but an exact restatement.
    assert buffer.recommended_buffer_seconds == buffer.route_duration_seconds == 900.0
    assert buffer.provider == fake_provider.provider_name
    # No schedule timestamps exist on these fixtures -- sufficiency is
    # never guessed.
    assert buffer.available_gap_seconds is None
    assert buffer.buffer_status == BufferSufficiencyStatus.NOT_COMPUTABLE


# ---------------------------------------------------------------------------
# Step 173A: route_geometry is exposed on a buffer only when the
# underlying RouteResult itself carried real, provider-backed geometry --
# copied verbatim, never re-derived from experience coordinates.
# ---------------------------------------------------------------------------


def test_buffer_exposes_route_geometry_when_provider_result_has_it() -> None:
    points = [RoutePathPoint(lat=38.7223, lon=-9.1393), RoutePathPoint(lat=38.7169, lon=-9.1399)]
    fake_provider = _FakeRoutingProvider(
        RouteResult(
            provider="fake_routing_provider",
            status=ProviderStatus.SUCCESS,
            distance_meters=1500.0,
            duration_seconds=900.0,
            geometry=points,
            source="fake_routing_provider",
            confidence=0.9,
            message="Fake route with geometry for test purposes only.",
        )
    )
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=38.7169, lng=-9.1399),
        ]
    )

    report = service.build_report(planning_state)

    assert report.buffers[0].route_geometry == points


def test_buffer_route_geometry_stays_none_when_provider_result_has_none() -> None:
    fake_provider = _FakeRoutingProvider()
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=38.7169, lng=-9.1399),
        ]
    )

    report = service.build_report(planning_state)

    assert report.buffers[0].status == TravelTimeBufferStatus.SUCCESS
    assert report.buffers[0].route_geometry is None


def test_buffer_route_geometry_is_none_when_not_connected() -> None:
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=NotConnectedRoutingProvider()))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=38.7169, lng=-9.1399),
        ]
    )

    report = service.build_report(planning_state)

    assert report.buffers[0].status == TravelTimeBufferStatus.NOT_CONNECTED
    assert report.buffers[0].route_geometry is None


def test_buffer_route_geometry_is_none_when_missing_coordinates() -> None:
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=_AssertNeverCalledRoutingProvider()))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=None, lng=None),
        ]
    )

    report = service.build_report(planning_state)

    assert report.buffers[0].status == TravelTimeBufferStatus.NOT_COMPUTABLE
    assert report.buffers[0].route_geometry is None


def test_legs_built_for_every_consecutive_pair_within_a_day() -> None:
    fake_provider = _FakeRoutingProvider()
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
            _experience("C", lat=0.0, lng=2.0),
        ]
    )

    report = service.build_report(planning_state)

    assert len(report.buffers) == 2
    assert [buffer.from_experience_name for buffer in report.buffers] == ["A", "B"]
    assert [buffer.to_experience_name for buffer in report.buffers] == ["B", "C"]
    assert len(fake_provider.calls) == 2


# ---------------------------------------------------------------------------
# Failed/unavailable route results never become a fabricated duration.
# ---------------------------------------------------------------------------


def test_failed_route_result_never_becomes_a_buffer_duration() -> None:
    failed_result = RouteResult(
        provider="fake_routing_provider",
        status=ProviderStatus.FAILED,
        distance_meters=None,
        duration_seconds=None,
        source="fake_routing_provider",
        message="Simulated failure.",
    )
    fake_provider = _FakeRoutingProvider(result=failed_result)
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )

    report = service.build_report(planning_state)

    buffer = report.buffers[0]
    assert buffer.status == TravelTimeBufferStatus.FAILED
    assert buffer.buffer_status == BufferSufficiencyStatus.UNAVAILABLE
    assert buffer.route_duration_seconds is None
    assert buffer.route_distance_meters is None
    assert buffer.recommended_buffer_seconds is None
    assert report.status == ProviderStatus.FAILED


def test_unavailable_route_result_never_becomes_a_buffer_duration() -> None:
    unavailable_result = RouteResult(
        provider="fake_routing_provider",
        status=ProviderStatus.UNAVAILABLE,
        distance_meters=None,
        duration_seconds=None,
        source="fake_routing_provider",
        message="No usable route found.",
    )
    fake_provider = _FakeRoutingProvider(result=unavailable_result)
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )

    report = service.build_report(planning_state)

    buffer = report.buffers[0]
    assert buffer.status == TravelTimeBufferStatus.UNAVAILABLE
    assert buffer.buffer_status == BufferSufficiencyStatus.UNAVAILABLE
    assert buffer.route_duration_seconds is None
    assert buffer.recommended_buffer_seconds is None


# ---------------------------------------------------------------------------
# Real schedule timestamps -- sufficient/insufficient buffer verdicts,
# never guessed when timestamps don't exist.
# ---------------------------------------------------------------------------


def test_real_gap_shorter_than_duration_is_insufficient() -> None:
    fake_provider = _FakeRoutingProvider(
        result=RouteResult(
            provider="fake_routing_provider",
            status=ProviderStatus.SUCCESS,
            distance_meters=1500.0,
            duration_seconds=900.0,
            source="fake_routing_provider",
        )
    )
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0, end_time="10:00"),
            _experience("B", lat=0.0, lng=1.0, start_time="10:05"),  # 300s gap < 900s duration
        ]
    )

    report = service.build_report(planning_state)

    buffer = report.buffers[0]
    assert buffer.available_gap_seconds == 300.0
    assert buffer.route_duration_seconds == 900.0
    assert buffer.buffer_status == BufferSufficiencyStatus.INSUFFICIENT


def test_real_gap_at_least_duration_is_sufficient() -> None:
    fake_provider = _FakeRoutingProvider(
        result=RouteResult(
            provider="fake_routing_provider",
            status=ProviderStatus.SUCCESS,
            distance_meters=1500.0,
            duration_seconds=900.0,
            source="fake_routing_provider",
        )
    )
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0, end_time="10:00"),
            _experience("B", lat=0.0, lng=1.0, start_time="10:20"),  # 1200s gap >= 900s duration
        ]
    )

    report = service.build_report(planning_state)

    buffer = report.buffers[0]
    assert buffer.available_gap_seconds == 1200.0
    assert buffer.route_duration_seconds == 900.0
    assert buffer.buffer_status == BufferSufficiencyStatus.SUFFICIENT


# ---------------------------------------------------------------------------
# Never reorders/drops scheduled experiences -- reporting only.
# ---------------------------------------------------------------------------


def test_travel_time_buffer_never_reorders_or_drops_scheduled_experiences() -> None:
    fake_provider = _FakeRoutingProvider()
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
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


def test_build_report_does_not_mutate_input_plan() -> None:
    fake_provider = _FakeRoutingProvider()
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )
    before = planning_state.model_copy(deep=True)

    service.build_report(planning_state)

    assert planning_state.experience_plan == before.experience_plan
    assert planning_state.travel_time_buffer_report is None


# ---------------------------------------------------------------------------
# Aggregate status is honest, never optimistic.
# ---------------------------------------------------------------------------


def test_aggregate_status_is_partial_when_some_legs_succeed_and_others_do_not() -> None:
    fake_provider = _FakeRoutingProvider()
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
            _experience("C", lat=None, lng=None),
        ]
    )

    report = service.build_report(planning_state)

    assert len(report.buffers) == 2
    assert report.buffers[0].status == TravelTimeBufferStatus.SUCCESS
    assert report.buffers[1].status == TravelTimeBufferStatus.NOT_COMPUTABLE
    assert report.status == ProviderStatus.PARTIAL


# ---------------------------------------------------------------------------
# Step 166D hardening: an unexpected exception from the routing provider
# is contained per-leg (never crashes the report or generation).
# ---------------------------------------------------------------------------


class _RaisingRoutingProvider(RoutingProvider):
    """Deterministic test double that raises instead of returning a
    `RouteResult` -- proves `TravelTimeBufferService` contains this
    exception itself rather than letting it crash the whole report."""

    provider_name = "raising_routing_provider"

    def get_route(self, request: RouteRequest) -> RouteResult:
        raise RuntimeError("Simulated unexpected provider failure for test purposes only.")


def test_raising_routing_provider_is_contained_and_reported_as_failed() -> None:
    gateway = ProviderGateway(routing=_RaisingRoutingProvider())
    service = TravelTimeBufferService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )

    # Must not raise.
    report = service.build_report(planning_state)

    buffer = report.buffers[0]
    assert buffer.status == TravelTimeBufferStatus.FAILED
    assert buffer.buffer_status == BufferSufficiencyStatus.UNAVAILABLE
    assert buffer.route_duration_seconds is None
    assert buffer.route_distance_meters is None
    assert buffer.recommended_buffer_seconds is None
    assert report.status == ProviderStatus.FAILED
    # The raw exception text is never stored in a user-facing message.
    assert "RuntimeError" not in (buffer.message or "")
    assert "Simulated unexpected provider failure" not in (buffer.message or "")


# ---------------------------------------------------------------------------
# Step 166E: cross-cutting movement-data-provenance labeling.
# ---------------------------------------------------------------------------


def test_movement_data_provenance_is_provider_backed_on_success() -> None:
    fake_provider = _FakeRoutingProvider()
    service = TravelTimeBufferService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=38.7169, lng=-9.1399),
        ]
    )

    report = service.build_report(planning_state)

    assert report.buffers[0].movement_data_provenance == MovementDataProvenance.PROVIDER_BACKED
    assert report.movement_data_provenance == MovementDataProvenance.PROVIDER_BACKED


def test_movement_data_provenance_is_not_connected_without_network() -> None:
    gateway = ProviderGateway(routing=NotConnectedRoutingProvider())
    service = TravelTimeBufferService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=38.7169, lng=-9.1399),
        ]
    )

    report = service.build_report(planning_state)

    assert report.buffers[0].movement_data_provenance == MovementDataProvenance.NOT_CONNECTED
    assert report.movement_data_provenance == MovementDataProvenance.NOT_CONNECTED


def test_movement_data_provenance_is_not_computable_for_missing_coordinates() -> None:
    gateway = ProviderGateway(routing=_AssertNeverCalledRoutingProvider())
    service = TravelTimeBufferService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=38.7223, lng=-9.1393),
            _experience("B", lat=None, lng=None),
        ]
    )

    report = service.build_report(planning_state)

    buffer = report.buffers[0]
    assert buffer.movement_data_provenance == MovementDataProvenance.NOT_COMPUTABLE
    assert buffer.status == TravelTimeBufferStatus.NOT_COMPUTABLE
    assert buffer.recommended_buffer_seconds is None


def test_movement_data_provenance_is_failed_for_raising_provider() -> None:
    gateway = ProviderGateway(routing=_RaisingRoutingProvider())
    service = TravelTimeBufferService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )

    report = service.build_report(planning_state)

    assert report.buffers[0].movement_data_provenance == MovementDataProvenance.FAILED
    assert report.movement_data_provenance == MovementDataProvenance.FAILED


# ---------------------------------------------------------------------------
# No disallowed vendor import, no direct network client, no haversine.
# ---------------------------------------------------------------------------


def test_travel_time_buffer_service_module_has_no_disallowed_imports() -> None:
    import ast
    import inspect

    from app.services import travel_time_buffer_service as module

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

    assert "app.utils.geo" not in source
    assert "haversine_distance_km(" not in source


def test_travel_time_buffer_service_only_reaches_routing_through_the_gateway() -> None:
    """Must never import an adapter (OSRM or otherwise) directly -- only
    `ProviderGateway`, matching the "call providers through the gateway"
    rule (docs/12_provider_architecture.md, docs/14_backend_architecture.md
    section 18)."""
    import inspect

    from app.services import travel_time_buffer_service as module

    source = inspect.getsource(module)
    assert "osrm_adapter" not in source
    assert "OSRMRoutingAdapter" not in source
