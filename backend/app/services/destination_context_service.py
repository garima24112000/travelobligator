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

    `candidate_pois`, `candidate_restaurants`, and
    `candidate_accommodation_pois` are filled with real OSM POIs when the
    places provider returns usable data. `candidate_accommodation_pois` are
    open-data location candidates only — never bookable inventory, and never
    given a price, availability, rating, or booking link. Neighborhood
    candidates and attraction clusters stay empty since no adapter implements
    those yet.
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
        candidate_restaurants = (
            [poi.model_dump(mode="json") for poi in restaurants_response.data]
            if restaurants_response.data
            else []
        )
        # Open-data location candidates only. Do not attach price, availability,
        # rating, or booking link fields to these — OSM does not supply them.
        candidate_accommodation_pois = (
            [poi.model_dump(mode="json") for poi in accommodation_response.data]
            if accommodation_response.data
            else []
        )

        assumptions = [
            "Neighborhood candidates and attraction clusters could not be generated "
            "because no provider for that data is connected yet.",
            "Accommodation POI candidates are open-data location candidates from "
            "OpenStreetMap only, not bookable inventory. They have no price, "
            "availability, rating, or booking link.",
        ]
        if not candidate_pois:
            assumptions.insert(
                0,
                "Candidate points of interest could not be generated because the places "
                "provider returned no usable attraction data for this destination.",
            )
        if not candidate_restaurants:
            assumptions.append(
                "Candidate restaurants could not be generated because the places "
                "provider returned no usable restaurant data for this destination."
            )
        if not candidate_accommodation_pois:
            assumptions.append(
                "Candidate accommodation location POIs could not be generated because "
                "the places provider returned no usable accommodation data for this "
                "destination."
            )

        context = DestinationContext(
            destination_name=destination_name,
            candidate_pois=candidate_pois,
            candidate_restaurants=candidate_restaurants,
            candidate_accommodation_pois=candidate_accommodation_pois,
            provider_coverage=planning_state.provider_coverage.model_copy(),
            assumptions=assumptions,
            confidence=attractions_response.confidence if candidate_pois else 0.0,
        )

        planning_state.destination_context = context
        planning_state.touch()
        return planning_state
