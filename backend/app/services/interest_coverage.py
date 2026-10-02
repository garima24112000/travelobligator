"""Requested-interest coverage from the FINAL itinerary (Section 203C.2B).

An interest is covered by what the traveller is actually shown:

  * a final scheduled stop whose provider-derived taxonomy serves it (the
    existing `matched_interests` of each experience) -- for every interest;
  * for `food` only, also a final nearby food suggestion that is real and
    local: provider-backed (a provider source and coordinates), classified
    by the taxonomy as a food place, and within the nearby-food radius of
    one of that day's FINAL stops.

Food suggestions are rebuilt after every step that changes a day, so a
suggestion made for a superseded day is no longer in the final state; the
radius is nevertheless re-checked here against the final stops, so a
suggestion that is no longer near its day can never count. Nothing here
relaxes any scheduling rule: a food interest is never a reason to schedule
markets (see `services/schedule_diversity`).
"""

from __future__ import annotations

from app.models.planning_state import DailyPlan, PlanningState, RestaurantSuggestion
from app.services import place_taxonomy as taxonomy
# The same radius the planner uses to choose nearby food (defined there once).
from app.services.experience_planner_service import FOOD_NEARBY_RADIUS_KM
from app.utils.geo import haversine_distance_km

FOOD = "food"


def requested_interests(planning_state: PlanningState) -> list[str]:
    profile = planning_state.traveler_profile
    terms = profile.interests if profile else planning_state.trip_request.interests
    return taxonomy.canonical_interests(terms)


def _valid_food_suggestion(day: DailyPlan, suggestion: RestaurantSuggestion) -> bool:
    if not suggestion.source or suggestion.coordinates is None:
        return False
    classification = taxonomy.classify_place(None, suggestion.category)
    if FOOD not in taxonomy.matched_interests(classification, [FOOD]):
        return False
    stops = [stop.coordinates for stop in day.experiences if stop.coordinates is not None]
    return any(
        (distance := haversine_distance_km(stop, suggestion.coordinates)) is not None
        and distance <= FOOD_NEARBY_RADIUS_KM
        for stop in stops
    )


def final_food_evidence(planning_state: PlanningState) -> list[str]:
    """Names of the final food suggestions that count as food evidence."""
    plan = planning_state.experience_plan
    if plan is None:
        return []
    return [
        suggestion.name
        for day in plan.daily_plans
        for suggestion in day.restaurant_suggestions
        if _valid_food_suggestion(day, suggestion)
    ]


def interest_coverage(planning_state: PlanningState) -> dict[str, bool]:
    """`{canonical interest: covered}` for every requested interest."""
    plan = planning_state.experience_plan
    scheduled = [stop for day in (plan.daily_plans if plan else []) for stop in day.experiences]
    coverage: dict[str, bool] = {}
    for interest in requested_interests(planning_state):
        covered = any(interest in stop.matched_interests for stop in scheduled)
        if not covered and interest == FOOD:
            covered = bool(final_food_evidence(planning_state))
        coverage[interest] = covered
    return coverage
