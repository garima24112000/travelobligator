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
    data only through ProviderGateway, calling only the OSM PlacesProvider
    methods it actually implements: `search_attractions`,
    `search_restaurants`, and `search_accommodation_pois`.

    `candidate_pois` is filled with real attraction POIs when the places
    provider returns usable data. Restaurant and accommodation POI results
    are only used for honest `provider_status`/`provider_coverage`
    bookkeeping right now — see the TODO in `run()` below for why they are
    not stored as candidates yet. Accommodation POIs are open-data location
    candidates only, never bookable inventory. Neighborhood candidates and
    attraction clusters stay empty since no adapter implements those yet.
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

        restaurants_response = self.gateway.places.search_restaurants(destination_name)
        self.coverage_service.record_provider_result(
            planning_state, restaurants_response, "restaurants"
        )

        # TODO: DestinationContext has no candidate-list field for restaurants or
        # accommodation POIs yet (docs/10_data_model.md section 7 only defines
        # `candidate_pois`, `neighborhood_candidates`, `attraction_clusters`). Once a
        # suitable field is added, store restaurants_response.data /
        # accommodation_response.data there instead of only recording provider
        # bookkeeping. Accommodation POIs from OSM must stay labeled as open-data
        # location candidates only, not bookable inventory, when that happens.
        accommodation_response = self.gateway.places.search_accommodation_pois(destination_name)
        self.coverage_service.record_provider_result(
            planning_state, accommodation_response, "accommodations"
        )

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
            "because no provider for that data is connected yet.",
            "Restaurant and accommodation POI candidates from OpenStreetMap were "
            "checked for provider coverage only; they are not yet stored as candidates "
            "because no PlanningState field exists for them. Accommodation POIs are "
            "open-data location candidates only, not bookable inventory.",
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
