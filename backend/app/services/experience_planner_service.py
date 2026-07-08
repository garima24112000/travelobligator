from __future__ import annotations

from datetime import timedelta

from app.models.planning_state import DailyPlan, ExperiencePlan, PlanningStage, PlanningState
from app.providers.gateway import ProviderGateway, provider_gateway
from app.services.base import PlanningStageService
from app.services.provider_coverage_service import ProviderCoverageService, provider_coverage_service


class ExperiencePlannerService(PlanningStageService):
    """Owns `experience_plan`, `experience_cards`, and itinerary decision
    cards (docs/14_backend_architecture.md section 13).

    Consumes `traveler_profile`, `destination_context`, `trip_strategy`, and
    `stay_transport`. Day structure (day number/date) can be derived safely
    from the user's own dates. Attraction/restaurant selection, geographic
    grouping, and day-level scheduling are not implemented yet, so each day
    is created empty with an explicit warning instead of invented activities,
    even when provider-backed candidate data exists.
    """

    def __init__(
        self,
        gateway: ProviderGateway | None = None,
        coverage_service: ProviderCoverageService | None = None,
    ) -> None:
        self.gateway = gateway or provider_gateway
        self.coverage_service = coverage_service or provider_coverage_service

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.EXPERIENCE_PLAN)

        destination_name = planning_state.trip_request.primary_destination

        attractions_response = self.gateway.places.search_attractions(destination_name)
        self.coverage_service.record_provider_result(
            planning_state, attractions_response, "places"
        )

        restaurants_response = self.gateway.places.search_restaurants(destination_name)
        self.coverage_service.record_provider_result(
            planning_state, restaurants_response, "restaurants"
        )

        trip_request = planning_state.trip_request
        num_days = (trip_request.end_date - trip_request.start_date).days + 1

        daily_plans: list[DailyPlan] = []
        for day_number in range(1, num_days + 1):
            day_date = trip_request.start_date + timedelta(days=day_number - 1)
            daily_plans.append(
                DailyPlan(
                    day_number=day_number,
                    date=day_date,
                    warnings=[
                        "Attractions and restaurants have not been scheduled into this "
                        "day yet; day-level itinerary scheduling is not implemented."
                    ],
                )
            )

        experience_plan = ExperiencePlan(
            daily_plans=daily_plans,
            provider_coverage=planning_state.provider_coverage.model_copy(),
            assumptions=[
                "The daily plan is a date skeleton only; no experiences have "
                "been selected because day-level scheduling is not implemented yet."
            ],
            confidence=0.0,
        )

        planning_state.experience_plan = experience_plan
        planning_state.touch()
        return planning_state
