from __future__ import annotations

from app.models.planning_state import (
    PlanningStage,
    PlanningState,
    StayTransportDecision,
    TransportStrategy,
)
from app.providers.gateway import ProviderGateway, provider_gateway
from app.services.base import PlanningStageService
from app.services.provider_coverage_service import ProviderCoverageService, provider_coverage_service


class StayTransportService(PlanningStageService):
    """Owns `stay_transport` and related decision cards/provider bookkeeping
    (docs/14_backend_architecture.md section 12).

    Consumes `traveler_profile`, `destination_context`, and `trip_strategy`.
    A stay area cannot be recommended without real neighborhood/accommodation
    data, so it stays unset. The transport strategy's primary mode may
    restate the user's own stated preference (user_input), which is not an
    invented fact.
    """

    def __init__(
        self,
        gateway: ProviderGateway | None = None,
        coverage_service: ProviderCoverageService | None = None,
    ) -> None:
        self.gateway = gateway or provider_gateway
        self.coverage_service = coverage_service or provider_coverage_service

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.STAY_TRANSPORT)

        destination_name = planning_state.trip_request.primary_destination

        accommodation_response = self.gateway.accommodation.search_accommodation_options(
            destination_name
        )
        self.coverage_service.record_provider_result(
            planning_state, accommodation_response, "accommodations"
        )

        transport_strategy = None
        transport_preferences = planning_state.trip_request.transport_preferences
        if transport_preferences:
            transport_strategy = TransportStrategy(
                primary_mode=transport_preferences[0],
                secondary_modes=transport_preferences[1:],
                rationale=["Restates the transport preference stated in the trip request."],
                confidence=0.3,
            )

        assumptions = [
            "No stay area or accommodation options could be recommended because "
            "the accommodation provider is not connected."
        ]

        stay_transport = StayTransportDecision(
            transport_strategy=transport_strategy,
            assumptions=assumptions,
            confidence=0.0,
        )

        planning_state.stay_transport = stay_transport
        planning_state.touch()
        return planning_state
