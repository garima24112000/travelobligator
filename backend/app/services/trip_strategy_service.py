from __future__ import annotations

from app.models.planning_state import PlanningStage, PlanningState, TripStrategy
from app.services.base import PlanningStageService


class TripStrategyService(PlanningStageService):
    """Owns `trip_strategy` (docs/14_backend_architecture.md section 11).

    Consumes `traveler_profile` and `destination_context`. Duration is a
    deterministic calculation over the user's own dates, so it is safe to
    compute now. Destination suitability and budget assessment depend on
    provider-backed destination/cost data that is not connected yet, so they
    stay unset with an assumption explaining why instead of a guessed score.
    """

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.TRIP_STRATEGY)

        trip_request = planning_state.trip_request
        duration_days = (trip_request.end_date - trip_request.start_date).days + 1

        duration_assessment = {
            "duration_days": duration_days,
            "label": "computed_from_dates",
        }

        assumptions = [
            "Destination suitability and budget assessment require destination "
            "context and cost data that is not available because upstream "
            "providers are not connected."
        ]

        strategy = TripStrategy(
            duration_assessment=duration_assessment,
            assumptions=assumptions,
            confidence=0.0,
        )

        planning_state.trip_strategy = strategy
        planning_state.touch()
        return planning_state
