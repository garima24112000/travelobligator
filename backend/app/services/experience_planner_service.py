from __future__ import annotations

from datetime import timedelta

from app.models.planning_state import DailyPlan, ExperiencePlan, PlanningStage, PlanningState
from app.services.base import PlanningStageService


class ExperiencePlannerService(PlanningStageService):
    """Owns `experience_plan`, `experience_cards`, and itinerary decision
    cards (docs/14_backend_architecture.md section 13).

    Consumes `traveler_profile`, `destination_context`, `trip_strategy`, and
    `stay_transport`. Day structure (day number/date) can be derived safely
    from the user's own dates. This stage makes no provider calls of its
    own: attraction/restaurant selection, geographic grouping, and
    day-level scheduling are not implemented yet, so it only reads the
    candidate count already fetched by DestinationContextService
    (`destination_context.candidate_pois`) to describe what is available,
    and leaves every day empty with an explicit warning. Calling OSM again
    here would be redundant and could overwrite the honest
    `provider_coverage` that DestinationContextService already recorded.
    """

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.EXPERIENCE_PLAN)

        destination_context = planning_state.destination_context
        candidate_poi_count = len(destination_context.candidate_pois) if destination_context else 0

        if candidate_poi_count:
            day_warning = (
                f"{candidate_poi_count} candidate attraction(s) are available from "
                "destination context, but day-level scheduling is not implemented yet, "
                "so this day is empty."
            )
        else:
            day_warning = (
                "No attraction candidates are available yet, and day-level scheduling "
                "is not implemented, so this day is empty."
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
                    warnings=[day_warning],
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
