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
    MovementDataProvenance,
    RouteAwareSequencingReport,
    RouteRequest,
    RouteResult,
)
from app.providers.gateway import ProviderGateway
from app.providers.routing import NotConnectedRoutingProvider, RoutingProvider
from app.services.route_aware_sequencing_service import RouteAwareSequencingService

# Step 166A: RouteAwareSequencingService tests. Every test here injects a
# ProviderGateway with either the default NotConnectedRoutingProvider or a
# deterministic in-memory fake RoutingProvider double -- never a real OSRM
# instance, matching test_route_feasibility_service.py's own no-network
# guarantee. This service is shadow/report-only: it must never reorder,
# add, or drop a scheduled experience, and it must never fabricate a
# route duration/distance or use a straight-line (haversine) estimate as
# one.


class _LngBasedFakeRoutingProvider(RoutingProvider):
    """Deterministic test double: duration/distance are a simple, fixed
    function of the longitude difference between origin/destination, so
    nearest-next sequencing results are fully predictable. Records every
    request it's called with so tests can prove the gateway is (or isn't)
    actually invoked.
    """

    provider_name = "fake_routing_provider"

    def __init__(self) -> None:
        self.calls: list[RouteRequest] = []

    def get_route(self, request: RouteRequest) -> RouteResult:
        self.calls.append(request)
        lng_diff = abs(request.destination_lon - request.origin_lon)
        return RouteResult(
            provider=self.provider_name,
            status=ProviderStatus.SUCCESS,
            distance_meters=lng_diff * 100_000.0,
            duration_seconds=lng_diff * 1_000.0,
            geometry=None,
            source=self.provider_name,
            confidence=0.9,
            message="Fake route for test purposes only.",
        )


class _SelectiveFailureRoutingProvider(RoutingProvider):
    """Deterministic test double that fails for one specific
    origin->destination longitude pair and otherwise succeeds like
    `_LngBasedFakeRoutingProvider` -- used to prove a single failed lookup
    caps the day at `partial` and suppresses the duration/distance total.
    """

    provider_name = "fake_routing_provider"

    def __init__(self, fail_destination_lng: float) -> None:
        self._fail_destination_lng = fail_destination_lng

    def get_route(self, request: RouteRequest) -> RouteResult:
        if request.destination_lon == self._fail_destination_lng:
            return RouteResult(
                provider=self.provider_name,
                status=ProviderStatus.FAILED,
                distance_meters=None,
                duration_seconds=None,
                geometry=None,
                source=self.provider_name,
                confidence=0.0,
                message="Fake failure for test purposes only.",
            )
        lng_diff = abs(request.destination_lon - request.origin_lon)
        return RouteResult(
            provider=self.provider_name,
            status=ProviderStatus.SUCCESS,
            distance_meters=lng_diff * 100_000.0,
            duration_seconds=lng_diff * 1_000.0,
            geometry=None,
            source=self.provider_name,
            confidence=0.9,
            message="Fake route for test purposes only.",
        )


class _AssertNeverCalledRoutingProvider(RoutingProvider):
    """Fails the test loudly if the routing provider is ever invoked --
    used to prove a day with fewer than two coordinate-backed experiences
    never reaches the provider at all."""

    provider_name = "assert_never_called"

    def get_route(self, request: RouteRequest) -> RouteResult:
        raise AssertionError("Routing provider must not be called for an unsequenceable day.")


def _experience(name: str, *, lat: float | None, lng: float | None) -> ExperienceItem:
    coordinates = GeoPoint(lat=lat, lng=lng) if lat is not None and lng is not None else None
    return ExperienceItem(name=name, category="attraction", coordinates=coordinates)


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


def test_not_connected_routing_provider_produces_not_connected_suggestion_without_network() -> None:
    gateway = ProviderGateway(routing=NotConnectedRoutingProvider())
    service = RouteAwareSequencingService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.NOT_CONNECTED
    assert len(report.suggestions) == 1
    suggestion = report.suggestions[0]
    assert suggestion.status == ProviderStatus.NOT_CONNECTED
    assert suggestion.route_duration_seconds is None
    assert suggestion.route_distance_meters is None
    assert suggestion.improvement_seconds is None
    # No reorder is ever suggested when no routing data exists.
    assert suggestion.suggested_order == suggestion.original_order


def test_empty_experience_plan_produces_not_connected_report_with_no_suggestions() -> None:
    gateway = ProviderGateway(routing=NotConnectedRoutingProvider())
    service = RouteAwareSequencingService(gateway=gateway)
    planning_state = _planning_state_with_experiences([])

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.NOT_CONNECTED
    assert report.suggestions == []


def test_no_experience_plan_at_all_produces_not_connected_report_with_no_suggestions() -> None:
    trip_request = TripRequest(
        primary_destination="Testville, Testland",
        start_date="2026-08-10",
        end_date="2026-08-10",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    planning_state = PlanningState(trip_request=trip_request)
    service = RouteAwareSequencingService(
        gateway=ProviderGateway(routing=NotConnectedRoutingProvider())
    )

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.NOT_CONNECTED
    assert report.suggestions == []


def test_day_with_fewer_than_two_experiences_is_skipped() -> None:
    gateway = ProviderGateway(routing=_AssertNeverCalledRoutingProvider())
    service = RouteAwareSequencingService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [_experience("A", lat=0.0, lng=0.0)],
        [],
    )

    report = service.build_report(planning_state)

    assert report.suggestions == []
    assert report.status == ProviderStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# Missing coordinates -- never guessed, never calls the provider when
# fewer than two experiences in the day have coordinates.
# ---------------------------------------------------------------------------


def test_fewer_than_two_coordinate_backed_experiences_is_unavailable_and_never_calls_provider() -> None:
    gateway = ProviderGateway(routing=_AssertNeverCalledRoutingProvider())
    service = RouteAwareSequencingService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=None, lng=None),
        ]
    )

    report = service.build_report(planning_state)

    suggestion = report.suggestions[0]
    assert suggestion.status == ProviderStatus.UNAVAILABLE
    assert suggestion.route_duration_seconds is None
    assert suggestion.route_distance_meters is None
    assert suggestion.suggested_order == suggestion.original_order


def test_one_missing_coordinate_experience_caps_status_at_partial_and_is_appended_last() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
            _experience("NoCoords", lat=None, lng=None),
        ]
    )

    report = service.build_report(planning_state)

    suggestion = report.suggestions[0]
    assert suggestion.status == ProviderStatus.PARTIAL
    assert suggestion.route_duration_seconds is None
    assert suggestion.route_distance_meters is None
    # The coordinate-less experience is never dropped, and is appended last
    # rather than inserted at a guessed position.
    no_coords_experience_id = suggestion.original_order[2]
    assert suggestion.suggested_order[-1] == no_coords_experience_id
    assert set(suggestion.suggested_order) == set(suggestion.original_order)


# ---------------------------------------------------------------------------
# Successful, provider-backed sequencing -- real duration/distance,
# nearest-next reordering, honest improvement.
# ---------------------------------------------------------------------------


def test_successful_routing_provider_produces_nearest_next_suggested_order_and_totals() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )

    report = service.build_report(planning_state)

    assert report.status == ProviderStatus.SUCCESS
    suggestion = report.suggestions[0]
    assert suggestion.status == ProviderStatus.SUCCESS

    original_names = ["Anchor", "Far", "Near"]
    experiences_by_id = {
        experience.experience_id: experience.name
        for experience in planning_state.experience_plan.daily_plans[0].experiences
    }
    assert [experiences_by_id[eid] for eid in suggestion.original_order] == original_names

    # Nearest-next from Anchor(lng=0) picks Near(lng=1, duration 1000s)
    # before Far(lng=5, duration 5000s) -- proving real route duration,
    # not provider order, drives the suggestion.
    suggested_names = [experiences_by_id[eid] for eid in suggestion.suggested_order]
    assert suggested_names == ["Anchor", "Near", "Far"]

    # Original order duration: Anchor->Far (5000s) + Far->Near (4000s) = 9000s.
    # Suggested order duration: Anchor->Near (1000s) + Near->Far (4000s) = 5000s.
    assert suggestion.route_duration_seconds == 5000.0
    assert suggestion.route_distance_meters == 500_000.0
    assert suggestion.improvement_seconds == 4000.0
    assert suggestion.provider == fake_provider.provider_name


def test_selective_route_failure_caps_status_at_partial_and_suppresses_totals() -> None:
    # Fails every lookup whose destination is Far(lng=5) -- e.g. the
    # provider found no usable route to that particular experience.
    fake_provider = _SelectiveFailureRoutingProvider(fail_destination_lng=5.0)
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )

    report = service.build_report(planning_state)

    suggestion = report.suggestions[0]
    assert suggestion.status == ProviderStatus.PARTIAL
    # A partial day never reports a duration/distance total -- no partial
    # sum is ever presented as a complete one.
    assert suggestion.route_duration_seconds is None
    assert suggestion.route_distance_meters is None
    assert suggestion.improvement_seconds is None


# ---------------------------------------------------------------------------
# Never reorders/drops scheduled experiences -- shadow/report-only.
# ---------------------------------------------------------------------------


def test_report_never_reorders_or_drops_scheduled_experiences() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )
    original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]

    report = service.build_report(planning_state)

    assert [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ] == original_order == ["Anchor", "Far", "Near"]
    # The report itself proposes a different order, proving the itinerary
    # genuinely was not updated to match it.
    experiences_by_id = {
        experience.experience_id: experience.name
        for experience in planning_state.experience_plan.daily_plans[0].experiences
    }
    suggested_names = [experiences_by_id[eid] for eid in report.suggestions[0].suggested_order]
    assert suggested_names != original_order


def test_service_does_not_mutate_input_plan() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
        ]
    )
    before = planning_state.model_copy(deep=True)

    service.build_report(planning_state)

    assert planning_state.experience_plan == before.experience_plan
    assert planning_state.route_aware_sequencing_report is None


def test_report_is_always_shadow_only_and_never_applied() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
        ]
    )

    report = service.build_report(planning_state)

    assert report.is_shadow_only is True
    assert report.applied_to_itinerary is False


# ---------------------------------------------------------------------------
# Step 166D hardening: an unexpected exception from the routing provider
# is contained per-leg (never crashes the report or generation).
# ---------------------------------------------------------------------------


class _RaisingRoutingProvider(RoutingProvider):
    """Deterministic test double that raises instead of returning a
    `RouteResult` -- proves `RouteAwareSequencingService` contains this
    exception itself rather than letting it crash the whole report."""

    provider_name = "raising_routing_provider"

    def get_route(self, request: RouteRequest) -> RouteResult:
        raise RuntimeError("Simulated unexpected provider failure for test purposes only.")


def test_raising_routing_provider_is_contained_and_reported_as_failed() -> None:
    gateway = ProviderGateway(routing=_RaisingRoutingProvider())
    service = RouteAwareSequencingService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )

    # Must not raise.
    report = service.build_report(planning_state)

    suggestion = report.suggestions[0]
    assert suggestion.status == ProviderStatus.FAILED
    assert suggestion.route_duration_seconds is None
    assert suggestion.route_distance_meters is None
    assert suggestion.suggested_order == suggestion.original_order
    # The raw exception text is never stored in a user-facing message.
    assert "RuntimeError" not in (suggestion.message or "")
    assert "Simulated unexpected provider failure" not in (suggestion.message or "")


def test_apply_report_never_applies_a_failed_suggestion_from_a_raising_provider() -> None:
    gateway = ProviderGateway(routing=_RaisingRoutingProvider())
    service = RouteAwareSequencingService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )
    original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    report = service.build_report(planning_state)

    applied = service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert applied is False
    assert [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ] == original_order


# ---------------------------------------------------------------------------
# Step 166D hardening: apply_report contains an unexpected error applying
# one day's suggestion without aborting other, still-safe days.
# ---------------------------------------------------------------------------


def test_apply_report_contains_unexpected_error_for_one_day_without_aborting_others(
    monkeypatch,
) -> None:
    from app.services import route_aware_sequencing_service as module

    fake_provider = _LngBasedFakeRoutingProvider()
    service = module.RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor1", lat=0.0, lng=0.0),
            _experience("Far1", lat=0.0, lng=5.0),
            _experience("Near1", lat=0.0, lng=1.0),
        ],
        [
            _experience("Anchor2", lat=0.0, lng=0.0),
            _experience("Far2", lat=0.0, lng=5.0),
            _experience("Near2", lat=0.0, lng=1.0),
        ],
    )
    day1_original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    report = service.build_report(planning_state)
    assert report.suggestions[0].status == ProviderStatus.SUCCESS
    assert report.suggestions[1].status == ProviderStatus.SUCCESS

    real_apply_day_order = module._apply_day_order

    def _raising_for_day_one(day_plan, suggested_order):
        if day_plan.day_number == 1:
            raise RuntimeError("Simulated bug applying day 1 for test purposes only.")
        return real_apply_day_order(day_plan, suggested_order)

    monkeypatch.setattr(module, "_apply_day_order", _raising_for_day_one)

    # Must not raise, and day 2 must still be applied even though day 1
    # blew up.
    applied = service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert applied is True
    day1_names = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    day2_names = [
        experience.name for experience in planning_state.experience_plan.daily_plans[1].experiences
    ]
    # Day 1 is left exactly as it was -- never partially reordered.
    assert day1_names == day1_original_order
    assert report.suggestions[0].applied is False
    # Day 2 was safely reordered.
    assert day2_names == ["Anchor2", "Near2", "Far2"]
    assert report.suggestions[1].applied is True


# ---------------------------------------------------------------------------
# Step 166D hardening: applying a suggestion never leaves a stale message
# that contradicts `applied=True`.
# ---------------------------------------------------------------------------


def test_applied_suggestion_message_no_longer_claims_it_was_not_applied() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )
    report = service.build_report(planning_state)
    assert "not applied" in report.suggestions[0].message

    service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert report.suggestions[0].applied is True
    assert "not applied" not in report.suggestions[0].message
    assert "applied" in report.suggestions[0].message


# ---------------------------------------------------------------------------
# Step 166E: cross-cutting movement-data-provenance labeling.
# ---------------------------------------------------------------------------


def test_movement_data_provenance_is_not_applied_for_a_successful_unapplied_suggestion() -> None:
    """A `success` suggestion is `not_applied` (never `provider_backed`)
    until `apply_report` actually reorders the day -- real data can exist
    and still not have been applied to the schedule."""
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )

    report = service.build_report(planning_state)

    suggestion = report.suggestions[0]
    assert suggestion.status == ProviderStatus.SUCCESS
    assert suggestion.applied is False
    assert suggestion.movement_data_provenance == MovementDataProvenance.NOT_APPLIED
    assert report.movement_data_provenance == MovementDataProvenance.PROVIDER_BACKED


def test_movement_data_provenance_flips_to_provider_backed_once_applied() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )
    report = service.build_report(planning_state)
    assert report.suggestions[0].movement_data_provenance == MovementDataProvenance.NOT_APPLIED

    service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert report.suggestions[0].applied is True
    assert report.suggestions[0].movement_data_provenance == MovementDataProvenance.PROVIDER_BACKED


def test_movement_data_provenance_is_not_connected_without_network() -> None:
    gateway = ProviderGateway(routing=NotConnectedRoutingProvider())
    service = RouteAwareSequencingService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )

    report = service.build_report(planning_state)

    assert report.suggestions[0].movement_data_provenance == MovementDataProvenance.NOT_CONNECTED
    assert report.movement_data_provenance == MovementDataProvenance.NOT_CONNECTED


def test_movement_data_provenance_is_not_computable_for_missing_coordinates() -> None:
    gateway = ProviderGateway(routing=_AssertNeverCalledRoutingProvider())
    service = RouteAwareSequencingService(gateway=gateway)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=None, lng=None),
        ]
    )

    report = service.build_report(planning_state)

    suggestion = report.suggestions[0]
    assert suggestion.movement_data_provenance == MovementDataProvenance.NOT_COMPUTABLE
    assert suggestion.status == ProviderStatus.UNAVAILABLE


def test_movement_data_provenance_is_failed_for_raising_provider() -> None:
    fake_provider = _RaisingRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )

    report = service.build_report(planning_state)

    assert report.suggestions[0].movement_data_provenance == MovementDataProvenance.FAILED
    assert report.movement_data_provenance == MovementDataProvenance.FAILED


# ---------------------------------------------------------------------------
# No disallowed vendor import, no direct network client, no haversine.
# ---------------------------------------------------------------------------


def test_route_aware_sequencing_service_module_has_no_disallowed_imports() -> None:
    import ast
    import inspect

    from app.services import route_aware_sequencing_service as module

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
        "haversine",
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

    # Docstrings legitimately mention "haversine" to explain that a
    # straight-line estimate is never substituted for a real route
    # duration -- what must never appear is an actual import/call of the
    # haversine helper itself.
    assert "app.utils.geo" not in source
    assert "haversine_distance_km(" not in source


# ---------------------------------------------------------------------------
# Step 166B: RouteAwareSequencingService.apply_report -- the config-gated
# application path. Whether it is ever called at all is
# PlanningOrchestrator's job (gated by Settings.route_aware_scheduling_
# enabled); these tests exercise apply_report itself, in isolation, to
# prove its own safety contract holds regardless of caller.
# ---------------------------------------------------------------------------


def test_apply_report_reorders_day_and_flips_report_flags_when_safe() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )
    report = service.build_report(planning_state)
    assert report.suggestions[0].status == ProviderStatus.SUCCESS

    applied = service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert applied is True
    assert report.is_shadow_only is False
    assert report.applied_to_itinerary is True
    assert report.suggestions[0].applied is True

    reordered_names = [
        experience.name
        for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    assert reordered_names == ["Anchor", "Near", "Far"]


def test_apply_report_never_adds_removes_or_changes_experience_content() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )
    original_experiences_by_id = {
        experience.experience_id: experience
        for experience in planning_state.experience_plan.daily_plans[0].experiences
    }
    report = service.build_report(planning_state)

    service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    reordered = planning_state.experience_plan.daily_plans[0].experiences
    assert len(reordered) == 3
    assert {experience.experience_id for experience in reordered} == set(
        original_experiences_by_id
    )
    # Every ExperienceItem object is reused as-is -- only order changed.
    for experience in reordered:
        assert experience is original_experiences_by_id[experience.experience_id]


def test_apply_report_does_not_apply_not_connected_suggestion() -> None:
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=NotConnectedRoutingProvider()))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
        ]
    )
    original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    report = service.build_report(planning_state)
    assert report.suggestions[0].status == ProviderStatus.NOT_CONNECTED

    applied = service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert applied is False
    assert report.is_shadow_only is True
    assert report.applied_to_itinerary is False
    assert [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ] == original_order


def test_apply_report_does_not_apply_partial_suggestion() -> None:
    fake_provider = _SelectiveFailureRoutingProvider(fail_destination_lng=5.0)
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )
    original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    report = service.build_report(planning_state)
    assert report.suggestions[0].status == ProviderStatus.PARTIAL

    applied = service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert applied is False
    assert [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ] == original_order


def test_apply_report_does_not_apply_unavailable_suggestion_missing_coordinates() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("NoCoords", lat=None, lng=None),
        ]
    )
    original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    report = service.build_report(planning_state)
    assert report.suggestions[0].status == ProviderStatus.UNAVAILABLE

    applied = service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert applied is False
    assert [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ] == original_order


def test_apply_report_does_not_apply_when_improvement_below_minimum() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )
    original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    report = service.build_report(planning_state)
    assert report.suggestions[0].improvement_seconds == 4000.0

    # A minimum higher than the real improvement must suppress application.
    applied = service.apply_report(planning_state, report, min_improvement_seconds=10_000.0)

    assert applied is False
    assert report.is_shadow_only is True
    assert report.applied_to_itinerary is False
    assert [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ] == original_order


def test_apply_report_rejects_malformed_suggested_order_with_duplicate_id() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )
    original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    original_ids = [
        experience.experience_id
        for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    report = service.build_report(planning_state)
    # Tamper with the suggestion to duplicate the first ID and drop the last.
    suggestion = report.suggestions[0]
    suggestion.suggested_order = [original_ids[0], original_ids[0], original_ids[1]]

    applied = service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert applied is False
    assert [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ] == original_order


def test_apply_report_rejects_malformed_suggested_order_with_extra_id() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
        ]
    )
    original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]
    report = service.build_report(planning_state)
    suggestion = report.suggestions[0]
    suggestion.suggested_order = [*suggestion.suggested_order, "unknown_experience_id"]

    applied = service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert applied is False
    assert [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ] == original_order


def test_apply_report_rejects_stale_suggestion_when_schedule_has_moved_on() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    planning_state = _planning_state_with_experiences(
        [
            _experience("Anchor", lat=0.0, lng=0.0),
            _experience("Far", lat=0.0, lng=5.0),
            _experience("Near", lat=0.0, lng=1.0),
        ]
    )
    report = service.build_report(planning_state)

    # Simulate the schedule having changed since the report was built.
    day_plan = planning_state.experience_plan.daily_plans[0]
    day_plan.experiences = list(reversed(day_plan.experiences))
    moved_on_order = [experience.name for experience in day_plan.experiences]

    applied = service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert applied is False
    assert [experience.name for experience in day_plan.experiences] == moved_on_order


def test_apply_report_returns_false_and_is_noop_with_no_experience_plan() -> None:
    fake_provider = _LngBasedFakeRoutingProvider()
    service = RouteAwareSequencingService(gateway=ProviderGateway(routing=fake_provider))
    trip_request = TripRequest(
        primary_destination="Testville, Testland",
        start_date="2026-08-10",
        end_date="2026-08-10",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    planning_state = PlanningState(trip_request=trip_request)
    report = RouteAwareSequencingReport(status=ProviderStatus.NOT_CONNECTED, suggestions=[])

    applied = service.apply_report(planning_state, report, min_improvement_seconds=0.0)

    assert applied is False
    assert planning_state.experience_plan is None


def test_route_aware_sequencing_service_only_reaches_routing_through_the_gateway() -> None:
    """Must never import an adapter (OSRM or otherwise) directly -- only
    `ProviderGateway`, matching the "call providers through the gateway"
    rule (docs/12_provider_architecture.md, docs/14_backend_architecture.md
    section 18)."""
    import inspect

    from app.services import route_aware_sequencing_service as module

    source = inspect.getsource(module)
    assert "osrm_adapter" not in source
    assert "OSRMRoutingAdapter" not in source
