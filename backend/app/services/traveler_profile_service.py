from __future__ import annotations

from app.models.common import ClaimSource, ClaimSourceType
from app.models.planning_state import PlanningStage, PlanningState, TravelerProfile
from app.services.base import PlanningStageService


class TravelerProfileService(PlanningStageService):
    """Owns `traveler_profile` (docs/14_backend_architecture.md section 9).

    Consumes `trip_request` only. Never calls travel providers. Structured
    fields (group type, pace, interests, budget range, ...) are copied
    directly from the user's own trip request, which is a legitimate
    `user_input` claim, not an invented fact. Free-text interpretation and
    decision-weight inference require an AIReasoningProvider; until one is
    connected, those fields stay empty and the profile's confidence and
    assumptions say why.
    """

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.TRAVELER_PROFILE)

        trip_request = planning_state.trip_request

        claim_sources = [
            ClaimSource(
                claim="Traveler group type, pace, interests, and constraints reflect the user's trip request.",
                source_type=ClaimSourceType.USER_INPUT,
                source="trip_request",
                based_on=["travel_group_type", "pace", "interests", "constraints"],
            )
        ]

        assumptions: list[str] = []
        if trip_request.free_text_preferences:
            assumptions.append(
                "Free-text preferences were provided but could not be interpreted into "
                "structured signals because the AI reasoning provider is not connected."
            )

        budget_profile: dict[str, object] = {}
        if trip_request.budget_min is not None or trip_request.budget_max is not None:
            budget_profile = {
                "budget_min": trip_request.budget_min,
                "budget_max": trip_request.budget_max,
                "currency": trip_request.budget_currency,
            }
        else:
            assumptions.append("No budget range was provided in the trip request.")

        decision_weights: dict[str, float] = {}
        interests_lower = {interest.lower() for interest in trip_request.interests}
        if interests_lower:
            decision_weights = {
                interest: 1.0 for interest in interests_lower
            }

        confidence = 0.55 if not trip_request.free_text_preferences else 0.4

        profile = TravelerProfile(
            travel_group_type=trip_request.travel_group_type,
            travelers_count=trip_request.travelers_count,
            pace=trip_request.pace,
            interests=list(trip_request.interests),
            must_visit=list(trip_request.must_visit),
            must_avoid=list(trip_request.must_avoid),
            constraints=list(trip_request.constraints),
            decision_weights=decision_weights,
            budget_profile=budget_profile,
            assumptions=assumptions,
            confidence=confidence,
            claim_sources=claim_sources,
        )

        planning_state.traveler_profile = profile
        planning_state.touch()
        return planning_state
