from __future__ import annotations

from app.models.planning_state import DestinationContext, PlanningStage, PlanningState
from app.providers.gateway import ProviderGateway, provider_gateway
from app.services.base import PlanningStageService
from app.services.provider_coverage_service import ProviderCoverageService, provider_coverage_service


class DestinationContextService(PlanningStageService):
    """Owns `destination_context`, `provider_status`, `provider_coverage`,
    `unavailable_data`, and `data_sources_used` (docs/14_backend_architecture.md
    section 10).

    Consumes `trip_request` and `traveler_profile`. Reaches provider-backed
    data only through ProviderGateway. Until a real PlacesProvider/
    RoutesProvider adapter is connected, every candidate list stays empty and
    is marked `not_connected` rather than filled with invented POIs.
    """

    def __init__(
        self,
        gateway: ProviderGateway | None = None,
        coverage_service: ProviderCoverageService | None = None,
    ) -> None:
        self.gateway = gateway or provider_gateway
        self.coverage_service = coverage_service or provider_coverage_service

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.DESTINATION_CONTEXT)

        destination_name = planning_state.trip_request.primary_destination

        places_response = self.gateway.places.search_places(destination_name)
        self.coverage_service.record_provider_result(planning_state, places_response, "places")

        attractions_response = self.gateway.places.search_attractions(destination_name)
        self.coverage_service.record_provider_result(planning_state, attractions_response, "places")

        transit_response = self.gateway.routes.estimate_transit_feasibility(
            origin={"name": destination_name}, destination={"name": destination_name}
        )
        self.coverage_service.record_provider_result(planning_state, transit_response, "routes")

        assumptions = [
            "Candidate points of interest, neighborhoods, and transport feasibility "
            "could not be generated because the places and routes providers are not connected."
        ]

        context = DestinationContext(
            destination_name=destination_name,
            provider_coverage=planning_state.provider_coverage.model_copy(),
            assumptions=assumptions,
            confidence=0.0,
        )

        planning_state.destination_context = context
        planning_state.touch()
        return planning_state
