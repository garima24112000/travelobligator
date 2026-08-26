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
from app.models.routing import BufferSufficiencyStatus, RouteRequest, RouteResult, TravelTimeBufferStatus
from app.providers.gateway import ProviderGateway
from app.providers.routing import NotConnectedRoutingProvider, RoutingProvider
from app.services.route_aware_sequencing_service import RouteAwareSequencingService
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.travel_time_buffer_service import TravelTimeBufferService

# Step 166D: cross-cutting hardening tests proving route feasibility,
# route-aware sequencing, and travel-time buffer reporting behave
# *consistently* for the same underlying condition (missing coordinates,
# partial route data) -- never diverging into a contradictory or
# fabricated state across the three reports, and that route-aware
# scheduling application still refuses partial/unavailable data end to
# end across services.


class _SelectiveFailureRoutingProvider(RoutingProvider):
    """Deterministic test double: succeeds with a duration proportional to
    longitude difference, except for a request whose (unordered)
    origin/destination longitude pair exactly matches `fail_on_lng_pair`,
    which always fails."""

    provider_name = "fake_routing_provider"

    def __init__(self, fail_on_lng_pair: frozenset[float]) -> None:
        self._fail_on_lng_pair = fail_on_lng_pair

    def get_route(self, request: RouteRequest) -> RouteResult:
        if frozenset({request.origin_lon, request.destination_lon}) == self._fail_on_lng_pair:
            return RouteResult(
                provider=self.provider_name,
                status=ProviderStatus.FAILED,
                distance_meters=None,
                duration_seconds=None,
                source=self.provider_name,
                message="Simulated failure for test purposes only.",
            )
        lng_diff = abs(request.destination_lon - request.origin_lon)
        return RouteResult(
            provider=self.provider_name,
            status=ProviderStatus.SUCCESS,
            distance_meters=lng_diff * 100_000.0,
            duration_seconds=lng_diff * 1_000.0,
            source=self.provider_name,
            confidence=0.9,
        )


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
# Required test #4: missing coordinates are consistent across all three
# reports for the same leg -- never called the provider, never fabricated.
# ---------------------------------------------------------------------------


def test_missing_coordinates_are_consistent_across_all_three_reports() -> None:
    gateway = ProviderGateway(routing=NotConnectedRoutingProvider())
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=None, lng=None),
        ]
    )

    feasibility_report = RouteFeasibilityService(gateway=gateway).build_report(planning_state)
    sequencing_report = RouteAwareSequencingService(gateway=gateway).build_report(planning_state)
    buffer_report = TravelTimeBufferService(gateway=gateway).build_report(planning_state)

    # Route feasibility: this leg is unavailable, and the routing provider
    # was never even called for it (missing coordinates).
    leg = feasibility_report.legs[0]
    assert leg.status == ProviderStatus.UNAVAILABLE
    assert leg.distance_meters is None
    assert leg.duration_seconds is None

    # Route-aware sequencing: this day's suggestion is unavailable (fewer
    # than two coordinate-backed experiences), never called the provider.
    suggestion = sequencing_report.suggestions[0]
    assert suggestion.status == ProviderStatus.UNAVAILABLE
    assert suggestion.route_duration_seconds is None
    assert suggestion.route_distance_meters is None

    # Travel-time buffer: this leg is not_computable, and its own
    # sufficiency verdict is honestly unavailable (no duration to compare
    # against any gap) -- never guessed either way.
    buffer = buffer_report.buffers[0]
    assert buffer.status == TravelTimeBufferStatus.NOT_COMPUTABLE
    assert buffer.buffer_status == BufferSufficiencyStatus.UNAVAILABLE
    assert buffer.route_duration_seconds is None
    assert buffer.recommended_buffer_seconds is None

    # None of the three reports ever fabricates a duration/distance for
    # this leg, and none of them ever contacted a routing provider.
    assert feasibility_report.status in {ProviderStatus.UNAVAILABLE, ProviderStatus.NOT_CONNECTED}
    assert sequencing_report.status in {ProviderStatus.UNAVAILABLE, ProviderStatus.NOT_CONNECTED}
    assert buffer_report.status in {ProviderStatus.UNAVAILABLE, ProviderStatus.NOT_CONNECTED}


# ---------------------------------------------------------------------------
# Required test #5/#7: partial route data stays partial and is never
# applied to itinerary order, end to end across services.
# ---------------------------------------------------------------------------


def test_partial_route_data_is_consistent_and_never_applied_to_itinerary() -> None:
    # A<->B always fails; B<->C and A<->C both succeed -- so the day's
    # *consecutive* legs (A->B, B->C) are a genuine mix of failure/success.
    fake_provider = _SelectiveFailureRoutingProvider(fail_on_lng_pair=frozenset({0.0, 1.0}))
    gateway = ProviderGateway(routing=fake_provider)
    planning_state = _planning_state_with_experiences(
        [
            _experience("A", lat=0.0, lng=0.0),
            _experience("B", lat=0.0, lng=1.0),
            _experience("C", lat=0.0, lng=2.0),
        ]
    )
    original_order = [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ]

    feasibility_report = RouteFeasibilityService(gateway=gateway).build_report(planning_state)
    assert feasibility_report.status == ProviderStatus.PARTIAL
    # The A->C leg (not touching B) succeeds; A->B/B->C fail.
    leg_statuses = {
        (leg.from_experience_name, leg.to_experience_name): leg.status
        for leg in feasibility_report.legs
    }
    assert leg_statuses[("A", "B")] == ProviderStatus.FAILED

    sequencing_service = RouteAwareSequencingService(gateway=gateway)
    sequencing_report = sequencing_service.build_report(planning_state)
    # Never SUCCESS -- at least one required leg (touching B) failed.
    assert sequencing_report.suggestions[0].status != ProviderStatus.SUCCESS

    applied = sequencing_service.apply_report(
        planning_state, sequencing_report, min_improvement_seconds=0.0
    )

    assert applied is False
    assert [
        experience.name for experience in planning_state.experience_plan.daily_plans[0].experiences
    ] == original_order

    buffer_report = TravelTimeBufferService(gateway=gateway).build_report(planning_state)
    assert buffer_report.status == ProviderStatus.PARTIAL
    buffer_statuses = {
        (buffer.from_experience_name, buffer.to_experience_name): buffer.status
        for buffer in buffer_report.buffers
    }
    assert buffer_statuses[("A", "B")] == TravelTimeBufferStatus.FAILED
    # No recommended buffer is ever produced for a failed leg.
    failed_buffer = next(
        buffer for buffer in buffer_report.buffers if buffer.status == TravelTimeBufferStatus.FAILED
    )
    assert failed_buffer.recommended_buffer_seconds is None
