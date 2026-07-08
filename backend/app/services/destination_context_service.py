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
    data only through ProviderGateway. `candidate_pois` is filled with real
    attraction POIs when PlacesProvider returns usable data; neighborhood
    candidates, attraction clusters, and transport feasibility stay empty
    and marked `not_connected` rather than filled with invented data since
    no adapter implements those yet.
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

        attractions_response = self.gateway.places.search_attractions(destination_name)
        self.coverage_service.record_provider_result(planning_state, attractions_response, "places")

        transit_response = self.gateway.routes.estimate_transit_feasibility(
            origin={"name": destination_name}, destination={"name": destination_name}
        )
        self.coverage_service.record_provider_result(planning_state, transit_response, "routes")

        candidate_pois = (
            [poi.model_dump(mode="json") for poi in attractions_response.data]
            if attractions_response.data
            else []
        )

        assumptions = [
            "Neighborhood candidates and attraction clusters could not be generated "
            "because no provider for that data is connected yet."
        ]
        if not candidate_pois:
            assumptions.insert(
                0,
                "Candidate points of interest could not be generated because the places "
                "provider returned no usable attraction data for this destination.",
            )

        context = DestinationContext(
            destination_name=destination_name,
            candidate_pois=candidate_pois,
            provider_coverage=planning_state.provider_coverage.model_copy(),
            assumptions=assumptions,
            confidence=attractions_response.confidence if candidate_pois else 0.0,
        )

        planning_state.destination_context = context
        planning_state.touch()
        return planning_state
