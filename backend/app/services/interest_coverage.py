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

from app.models.planning_state import DailyPlan, ExperienceItem, PlanningState, RestaurantSuggestion
from app.services import place_taxonomy as taxonomy
# The same radius the planner uses to choose nearby food (defined there once).
from app.services.experience_planner_service import FOOD_NEARBY_RADIUS_KM
from app.utils.geo import haversine_distance_km

FOOD = "food"


def requested_interests(planning_state: PlanningState) -> list[str]:
    profile = planning_state.traveler_profile
    terms = profile.interests if profile else planning_state.trip_request.interests
    return taxonomy.canonical_interests(terms)


def valid_food_suggestion(day: DailyPlan, suggestion: RestaurantSuggestion) -> bool:
    """A nearby food suggestion that counts as food evidence for `day`."""
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
        if valid_food_suggestion(day, suggestion)
    ]


def scheduled_food_experiences(planning_state: PlanningState) -> list[str]:
    """Names of the scheduled stops that serve the food interest on the
    provider's own evidence (a verified culinary place -- see the `food`
    entry of `place_taxonomy.INTEREST_CATEGORIES`). A marketplace without
    food evidence is not one."""
    plan = planning_state.experience_plan
    return [
        stop.name
        for day in (plan.daily_plans if plan else [])
        for stop in day.experiences
        if FOOD in stop.matched_interests
    ]


def food_coverage_evidence(planning_state: PlanningState) -> dict[str, list[str]]:
    """What covers the food interest in the FINAL plan, by kind of evidence:
    scheduled culinary stops, and nearby restaurant suggestions that are
    actually on the itinerary. Either kind is enough; both empty means food
    is not covered. Nothing here is inferred or invented."""
    return {
        "scheduled_culinary_stops": scheduled_food_experiences(planning_state),
        "nearby_restaurant_suggestions": final_food_evidence(planning_state),
    }


# -- what a covered interest rests on (Phase 2, presentation only) ---------------------
#
# Coverage is binary, but two kinds of "covered" are weaker than the word
# suggests, and the stored state can tell them apart:
#
#   * food covered ONLY by nearby restaurant suggestions -- nothing food-led
#     is scheduled;
#   * history or art covered ONLY by museums whose provider data records no
#     subject: the taxonomy lets every museum serve both (the generic MUSEUM
#     category), but the live places response carries no museum subtype, so
#     a museum with no historic / art evidence of its own may be about
#     neither.
#
# This only describes the coverage the validator already reports. It never
# changes coverage, a finding, readiness or what is scheduled.
#
# `scheduled_place` names the SOURCE of the coverage decision -- a scheduled
# stop the taxonomy matched to the interest on categories other than the
# generic museum one. It is not an independent check that the place really
# suits the interest.
BASIS_SCHEDULED_PLACE = "scheduled_place"
BASIS_NEARBY_SUGGESTIONS_ONLY = "nearby_suggestions_only"
BASIS_MUSEUM_SUBJECT_UNRECORDED = "museum_subject_unrecorded"
# Interests a museum serves only through the generic MUSEUM category.
_MUSEUM_SUBJECT_INTERESTS = frozenset({"history", "art"})


def _classifications_by_place_id(planning_state: PlanningState) -> dict[str, taxonomy.PlaceClassification]:
    """The provider-derived classification of every candidate the plan can
    hold, by provider place id (the broad pool, then promoted candidates)."""
    found: dict[str, taxonomy.PlaceClassification] = {}
    context = planning_state.destination_context
    for poi in context.candidate_pois if context is not None else []:
        if poi.get("place_id"):
            found.setdefault(str(poi["place_id"]), taxonomy.classify_candidate(poi))
    report = planning_state.ai_candidate_promotion_report
    for promoted in report.promoted_candidates if report is not None else []:
        if promoted.provider_place_id:
            found.setdefault(
                str(promoted.provider_place_id), taxonomy.classify_place(promoted.provider_tags, promoted.category)
            )
    return found


def _rests_on_generic_museum(
    stop: ExperienceItem, interest: str, classifications: dict[str, taxonomy.PlaceClassification]
) -> bool:
    """True when `stop` serves `interest` only because it is a museum: none
    of its own categories other than MUSEUM serves the interest. A stop whose
    classification is not on record is judged by its stored category."""
    if interest not in _MUSEUM_SUBJECT_INTERESTS:
        return False
    classification = classifications.get(str(stop.provider_place_id or ""))
    if classification is None:
        return stop.normalized_category == taxonomy.MUSEUM
    categories = set(classification.categories)
    specific = taxonomy.INTEREST_CATEGORIES.get(interest, frozenset()) - {taxonomy.MUSEUM}
    return taxonomy.MUSEUM in categories and not (categories & specific)


def coverage_basis(planning_state: PlanningState) -> dict[str, str]:
    """`{canonical interest: basis}` for every COVERED requested interest
    (see the `BASIS_*` values); uncovered interests are absent."""
    plan = planning_state.experience_plan
    scheduled = [stop for day in (plan.daily_plans if plan else []) for stop in day.experiences]
    classifications = _classifications_by_place_id(planning_state)
    basis: dict[str, str] = {}
    for interest, covered in interest_coverage(planning_state).items():
        if not covered:
            continue
        serving = [stop for stop in scheduled if interest in stop.matched_interests]
        if not serving:
            # Only food can be covered with no scheduled stop serving it (by
            # its nearby suggestions alone); anything else gets no basis.
            if interest == FOOD:
                basis[interest] = BASIS_NEARBY_SUGGESTIONS_ONLY
        elif all(_rests_on_generic_museum(stop, interest, classifications) for stop in serving):
            basis[interest] = BASIS_MUSEUM_SUBJECT_UNRECORDED
        else:
            basis[interest] = BASIS_SCHEDULED_PLACE
    return basis


def interest_coverage(planning_state: PlanningState) -> dict[str, bool]:
    """`{canonical interest: covered}` for every requested interest. Food is
    covered by either kind of `food_coverage_evidence`."""
    plan = planning_state.experience_plan
    scheduled = [stop for day in (plan.daily_plans if plan else []) for stop in day.experiences]
    coverage: dict[str, bool] = {}
    for interest in requested_interests(planning_state):
        covered = any(interest in stop.matched_interests for stop in scheduled)
        if not covered and interest == FOOD:
            covered = bool(final_food_evidence(planning_state))
        coverage[interest] = covered
    return coverage
