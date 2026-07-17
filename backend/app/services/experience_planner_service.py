from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.models.common import ClaimSource, ClaimSourceType, DataQuality, DataStatus, GeoPoint
from app.models.planning_state import (
    AccommodationSuggestion,
    DailyPlan,
    DecisionSummary,
    ExperienceItem,
    ExperiencePlan,
    PlanningStage,
    PlanningState,
    RestaurantSuggestion,
    StayAreaGuidance,
    TripPace,
)
from app.services.base import PlanningStageService
from app.utils.geo import haversine_distance_km

_MAX_ATTRACTIONS_PER_DAY: dict[TripPace, int] = {
    TripPace.RELAXED: 2,
    TripPace.BALANCED: 3,
    TripPace.PACKED: 4,
}
_DEFAULT_CATEGORY = "attraction"
_FEASIBILITY_WARNING = (
    "Days are grouped and attractions are ordered within each day using "
    "straight-line (haversine) geographic proximity when coordinates are "
    "available; this is not route optimization. Route ordering, timing, "
    "opening hours, and feasibility checks are not implemented yet, so "
    "scheduled attractions have no start/end time, duration, walking "
    "distance, or cost estimate."
)

_MAX_RESTAURANT_SUGGESTIONS_PER_DAY = 2
_RESTAURANT_SUGGESTION_WHY = (
    "Suggested from provider-backed restaurant candidates in "
    "destination_context.candidate_restaurants, selected by straight-line "
    "(haversine) proximity to this day's scheduled attractions only. This "
    "is not a reservation, rating, price, or route recommendation."
)
_NO_RESTAURANT_CANDIDATES_WARNING = (
    "No restaurant candidates are available yet, so no nearby restaurant "
    "suggestions could be made for this day."
)
_NO_DAY_ANCHOR_WARNING = (
    "No coordinate-backed scheduled experiences are available for this day, "
    "so nearby restaurant suggestions could not be computed."
)
_NO_COORDINATE_BACKED_RESTAURANTS_WARNING = (
    "No coordinate-backed restaurant candidates are available, so nearby "
    "restaurant suggestions could not be computed for this day."
)

_MAX_ACCOMMODATION_SUGGESTIONS_PER_DAY = 2
_ACCOMMODATION_SUGGESTION_WHY = (
    "Suggested from provider-backed accommodation POI candidates in "
    "destination_context.candidate_accommodation_pois, selected by "
    "straight-line (haversine) proximity to this day's scheduled "
    "attractions only. These are open-data location candidates only, not "
    "bookable inventory, and this is not a price, availability, rating, "
    "booking, or route recommendation."
)
_NO_ACCOMMODATION_CANDIDATES_WARNING = (
    "No accommodation POI candidates are available yet, so no nearby "
    "accommodation suggestions could be made for this day."
)
_NO_DAY_ANCHOR_FOR_ACCOMMODATION_WARNING = (
    "No coordinate-backed scheduled experiences are available for this day, "
    "so nearby accommodation suggestions could not be computed."
)
_NO_COORDINATE_BACKED_ACCOMMODATIONS_WARNING = (
    "No coordinate-backed accommodation POI candidates are available, so "
    "nearby accommodation suggestions could not be computed for this day."
)

_MAX_STAY_GUIDANCE_ANCHORS = 3
_STAY_GUIDANCE_SUGGESTION_WHY = (
    "Selected from provider-backed accommodation POI candidates in "
    "destination_context.candidate_accommodation_pois, ranked by average "
    "straight-line (haversine) proximity to this plan's scheduled "
    "attractions. This is an open-data location candidate only, not "
    "bookable inventory, not a hotel recommendation, and not validated for "
    "price, availability, rating, or booking."
)
_STAY_GUIDANCE_ASSUMPTIONS = [
    "Suggested anchor accommodation POIs are ranked by average straight-line "
    "(haversine) distance to every coordinate-backed scheduled experience "
    "across the whole plan, not a single day, and not a walking or route "
    "distance."
]
_NO_STAY_GUIDANCE_ACCOMMODATION_CANDIDATES_WARNING = (
    "No accommodation POI candidates are available yet, so no stay-area "
    "guidance could be produced."
)
_NO_STAY_GUIDANCE_ANCHOR_WARNING = (
    "No coordinate-backed scheduled experiences are available across the "
    "plan, so stay-area guidance could not be computed."
)
_NO_COORDINATE_BACKED_STAY_GUIDANCE_ACCOMMODATIONS_WARNING = (
    "No coordinate-backed accommodation POI candidates are available, so "
    "stay-area guidance could not be computed."
)

_DECISION_SUMMARY_PROXIMITY_DECISIONS = [
    "Which candidates are grouped into the same day is decided by "
    "straight-line (haversine) distance to that day's anchor candidate, not "
    "route optimization.",
    "The order attractions appear within a day is decided by a "
    "nearest-neighbor walk over straight-line distance, not route "
    "optimization.",
    "Nearby restaurant and accommodation POI suggestions, both day-level and "
    "the plan-level stay-area guidance, are selected by straight-line "
    "proximity to scheduled attractions only.",
]
_DECISION_SUMMARY_UNVALIDATED_ITEMS = [
    "Route ordering between scheduled attractions is not validated.",
    "Opening hours are not checked against the schedule.",
    "Feasibility of the day-by-day schedule is not validated.",
    "Walking time between scheduled attractions is not calculated.",
    "Costs for the plan are not calculated.",
    "Hotel prices for suggested accommodation POIs are not checked.",
    "Availability for any suggested accommodation or restaurant is not checked.",
    "Ratings for suggested restaurants and accommodation POIs are not verified.",
    "Booking links are not generated or validated.",
]
_DECISION_SUMMARY_USER_REVIEW_REQUIRED = [
    "Confirm opening hours, travel time, and day-by-day feasibility before "
    "relying on this schedule.",
    "Treat restaurant and accommodation POI suggestions as location "
    "candidates only -- confirm details directly with the venue or a "
    "booking provider before visiting or booking.",
    "Do not treat any price, availability, or rating as final -- none is "
    "provided by this plan.",
]


class ExperiencePlannerService(PlanningStageService):
    """Owns `experience_plan`, `experience_cards`, and itinerary decision
    cards (docs/14_backend_architecture.md section 13).

    Consumes `traveler_profile`, `destination_context`, `trip_strategy`, and
    `stay_transport`. Day structure (day number/date) can be derived safely
    from the user's own dates. This stage makes no provider calls of its
    own.

    First conservative scheduling step: attractions are distributed across
    days directly from `destination_context.candidate_pois` (real
    provider-backed places only, never invented), respecting a deterministic
    per-day cap based on trip pace (relaxed=2, balanced=3, packed=4).
    Candidates are ordered into three priority tiers first, with the
    relative provider order preserved within each tier:

    1. must-visit matches (candidate `name` contains a `must_visit` term)
    2. interest matches (candidate `name`, `category`, or `address` contains
       an `interests` term)
    3. everything else, in existing provider order

    No new place is invented to satisfy a must-visit or interest match —
    only existing `candidate_pois` are reordered. Restaurants and
    accommodation POIs are used only for the day-level
    `restaurant_suggestions`/`accommodation_suggestions` described below;
    they are never scheduled into `experiences`.

    Days are grouped geographically: for each day, in order, the next
    highest-priority unscheduled candidate becomes that day's anchor, and
    the day's remaining slots (up to the pace cap) are filled with the
    nearest remaining coordinate-backed candidates to that anchor by
    straight-line (haversine) distance. This means a must-visit or interest
    candidate still anchors the earliest possible day, but which other
    candidates join it is driven by geography rather than tier. If the
    anchor has no coordinates, or too few coordinate-backed candidates
    remain, the day falls back to filling its remaining slots from the next
    candidates in stable priority/provider order — coordinates are never
    guessed at. Within each day, the resulting group is further ordered
    using a nearest-neighbor walk over straight-line distance, keeping the
    anchor first. This is geographic grouping/ordering only, not route
    optimization — candidates are never moved across days, and the total
    number of scheduled candidates never exceeds the previous per-day-cap
    behavior. No route ordering, timing, opening-hours, walking, or cost
    estimation is implemented yet, so every scheduled item keeps
    `start_time`/`end_time`/`estimated_duration_minutes` unset and every day
    carries an explicit feasibility warning.

    After scheduling, each day gets up to 2 `restaurant_suggestions` drawn
    only from `destination_context.candidate_restaurants`: the day's first
    coordinate-backed scheduled experience is the anchor, and the nearest
    coordinate-backed restaurant candidates to that anchor by straight-line
    (haversine) distance are suggested. No rating, price, review,
    opening-hours, reservation/booking link, availability, route time,
    walking distance, or cost is ever attached. If no restaurant candidates
    exist, no scheduled experience has coordinates, or no restaurant
    candidate has coordinates, suggestions stay empty and an honest warning
    is added instead of guessing.

    Each day also gets up to 2 `accommodation_suggestions` drawn only from
    `destination_context.candidate_accommodation_pois`, using the exact same
    anchor and straight-line-nearest-2 rule as restaurant suggestions. These
    are open-data location candidates only, never bookable inventory, and
    never carry a price, availability, rating, booking, or route claim. The
    same empty-list-plus-honest-warning fallback applies when no candidates,
    no day anchor, or no coordinate-backed candidates are available.

    Finally, the plan gets a single plan-level `stay_area_guidance`: up to 3
    accommodation POI candidates (again only from `candidate_accommodation_
    pois`) ranked by average straight-line (haversine) distance to every
    coordinate-backed scheduled experience across the whole plan, not just
    one day. This is a summary of where candidates cluster relative to the
    whole itinerary, not a recommendation of a hotel, a booking, or a
    price/availability/rating claim, and it never affects validation
    readiness by itself. The same empty-list-plus-honest-warning fallback
    applies when no accommodation candidates, no coordinate-backed scheduled
    experiences, or no coordinate-backed accommodation candidates exist.

    The plan also gets a single plan-level `decision_summary` explaining, in
    plain terms and built purely from data already computed above, why the
    plan looks the way it does: which sections are provider-backed, which
    decisions are straight-line-proximity-based only, which aspects are
    still unvalidated (route ordering, opening hours, feasibility, walking
    time, costs, hotel prices, availability, ratings, booking links), and
    what the user should review before trusting the plan. It honestly
    states when restaurant or accommodation POI candidates are unavailable
    rather than omitting them silently. No provider call, no AI/LLM, no
    invented fact, and it never affects validation readiness by itself.
    """

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.EXPERIENCE_PLAN)

        destination_context = planning_state.destination_context
        candidate_pois = list(destination_context.candidate_pois) if destination_context else []
        candidate_restaurants = (
            list(destination_context.candidate_restaurants) if destination_context else []
        )
        candidate_accommodation_pois = (
            list(destination_context.candidate_accommodation_pois)
            if destination_context
            else []
        )

        traveler_profile = planning_state.traveler_profile
        trip_request = planning_state.trip_request
        pace = traveler_profile.pace if traveler_profile else trip_request.pace
        must_visit_terms = (
            traveler_profile.must_visit if traveler_profile else trip_request.must_visit
        )
        interest_terms = (
            traveler_profile.interests if traveler_profile else trip_request.interests
        )
        max_per_day = _MAX_ATTRACTIONS_PER_DAY[pace]

        num_days = (trip_request.end_date - trip_request.start_date).days + 1

        ordered_pois, must_visit_ids, interest_ids = _order_candidates(
            candidate_pois, must_visit_terms, interest_terms
        )

        day_groups = _group_candidates_into_days(ordered_pois, num_days, max_per_day)

        daily_plans: list[DailyPlan] = []
        for day_number in range(1, num_days + 1):
            day_date = trip_request.start_date + timedelta(days=day_number - 1)
            day_pois = _order_day_by_distance(day_groups[day_number - 1])

            warnings: list[str] = []
            if not candidate_pois:
                warnings.append(
                    "No attraction candidates are available yet, so this day is empty."
                )
            else:
                warnings.append(_FEASIBILITY_WARNING)
                if not day_pois:
                    warnings.append(
                        "No remaining candidate attractions were available for this day."
                    )

            experiences = [
                _build_experience_item(poi, must_visit_ids, interest_ids) for poi in day_pois
            ]

            restaurant_suggestions = _suggest_nearby_restaurants(
                experiences, candidate_restaurants, warnings
            )
            accommodation_suggestions = _suggest_nearby_accommodations(
                experiences, candidate_accommodation_pois, warnings
            )

            daily_plans.append(
                DailyPlan(
                    day_number=day_number,
                    date=day_date,
                    experiences=experiences,
                    restaurant_suggestions=restaurant_suggestions,
                    accommodation_suggestions=accommodation_suggestions,
                    warnings=warnings,
                )
            )

        assumptions = [
            "Attractions are scheduled directly from provider-backed candidates: each "
            "day's highest-priority unscheduled candidate anchors that day, and "
            "remaining slots (up to the pace-based per-day cap) are filled and ordered "
            "using straight-line (haversine) geographic proximity when coordinates are "
            "available. Days are grouped using geographic proximity, not route "
            "optimization, and route ordering, timing, and opening-hours feasibility "
            "are not implemented yet."
        ]
        if not candidate_pois:
            assumptions.insert(
                0,
                "No experiences could be scheduled because no provider-backed attraction "
                "candidates are available.",
            )

        stay_area_guidance = _build_stay_area_guidance(
            daily_plans, candidate_accommodation_pois
        )
        decision_summary = _build_decision_summary(
            candidate_pois, candidate_restaurants, candidate_accommodation_pois, daily_plans
        )

        experience_plan = ExperiencePlan(
            daily_plans=daily_plans,
            stay_area_guidance=stay_area_guidance,
            decision_summary=decision_summary,
            provider_coverage=planning_state.provider_coverage.model_copy(),
            assumptions=assumptions,
            confidence=0.35 if candidate_pois else 0.0,
        )

        planning_state.experience_plan = experience_plan
        planning_state.touch()
        return planning_state


def _matches_must_visit(poi: dict[str, Any], terms_lower: list[str]) -> bool:
    name = str(poi.get("name") or "").lower()
    return any(term in name for term in terms_lower)


def _matches_interests(poi: dict[str, Any], terms_lower: list[str]) -> bool:
    """Match interest terms against existing provider-backed candidate fields only."""
    haystack = " ".join(
        [
            str(poi.get("name") or ""),
            str(poi.get("category") or ""),
            str(poi.get("address") or ""),
        ]
    ).lower()
    return any(term in haystack for term in terms_lower)


def _order_candidates(
    candidate_pois: list[dict[str, Any]],
    must_visit_terms: list[str],
    interest_terms: list[str],
) -> tuple[list[dict[str, Any]], set[int], set[int]]:
    """Order candidates into must-visit / interest / remaining tiers.

    Only reorders `candidate_pois` as returned by the places provider; never
    invents new candidates. Relative provider order is preserved within each
    tier. Returns the reordered list plus the `id()` of every poi placed in
    the must-visit and interest tiers, so callers can attribute the right
    `why_included` reason without relying on possibly-non-unique names.
    """
    must_visit_terms_lower = [term.lower() for term in must_visit_terms if term]
    interest_terms_lower = [term.lower() for term in interest_terms if term]

    must_visit_matched: list[dict[str, Any]] = []
    interest_matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for poi in candidate_pois:
        if must_visit_terms_lower and _matches_must_visit(poi, must_visit_terms_lower):
            must_visit_matched.append(poi)
        elif interest_terms_lower and _matches_interests(poi, interest_terms_lower):
            interest_matched.append(poi)
        else:
            unmatched.append(poi)

    must_visit_ids = {id(poi) for poi in must_visit_matched}
    interest_ids = {id(poi) for poi in interest_matched}
    ordered = must_visit_matched + interest_matched + unmatched
    return ordered, must_visit_ids, interest_ids


def _group_candidates_into_days(
    ordered_pois: list[dict[str, Any]], num_days: int, max_per_day: int
) -> list[list[dict[str, Any]]]:
    """Group priority-ordered candidates into `num_days` day-groups.

    For each day, in order, the next highest-priority unscheduled candidate
    becomes that day's anchor. The day's remaining slots (up to
    `max_per_day`) are filled with the nearest remaining coordinate-backed
    candidates to that anchor, by straight-line (haversine) distance — this
    is geographic grouping only, never a claim of route optimization.
    Candidates are never invented and never scheduled onto more than one
    day, so the total number of candidates scheduled across all days never
    exceeds `num_days * max_per_day`, exactly as before.

    If the anchor has no coordinates, or too few coordinate-backed
    candidates remain nearby, the day's remaining slots fall back to the
    next candidates in stable priority/provider order — coordinates are
    never guessed at and this never raises.
    """
    remaining = list(ordered_pois)
    day_groups: list[list[dict[str, Any]]] = []

    for _ in range(num_days):
        if not remaining or max_per_day <= 0:
            day_groups.append([])
            continue

        anchor = remaining.pop(0)
        day_group = [anchor]
        slots_left = max_per_day - 1

        anchor_point = _poi_coordinates(anchor) if slots_left > 0 else None
        if anchor_point is None:
            # No anchor coordinates to measure from; keep stable
            # priority/provider order for this day's remaining slots.
            day_group.extend(remaining[:slots_left])
            remaining = remaining[slots_left:]
            day_groups.append(day_group)
            continue

        with_coords: list[tuple[int, dict[str, Any], GeoPoint]] = []
        for index, poi in enumerate(remaining):
            point = _poi_coordinates(poi)
            if point is not None:
                with_coords.append((index, poi, point))

        with_coords.sort(key=lambda item: haversine_distance_km(anchor_point, item[2]))
        nearest = with_coords[:slots_left]
        selected_indices = {index for index, _, _ in nearest}
        day_group.extend(poi for _, poi, _ in nearest)
        slots_left -= len(nearest)

        if slots_left > 0:
            # Not enough coordinate-backed candidates nearby; fall back to
            # stable priority/provider order for the rest, never guessing.
            for index, poi in enumerate(remaining):
                if slots_left <= 0:
                    break
                if index in selected_indices:
                    continue
                day_group.append(poi)
                selected_indices.add(index)
                slots_left -= 1

        remaining = [poi for index, poi in enumerate(remaining) if index not in selected_indices]
        day_groups.append(day_group)

    return day_groups


def _poi_coordinates(poi: dict[str, Any]) -> GeoPoint | None:
    """Extract a candidate's coordinates, if present and well-formed.

    Never invents coordinates; missing or malformed values are treated as
    unavailable rather than raising, so a bad provider record can't crash
    planning.
    """
    coordinates = poi.get("coordinates")
    if not coordinates:
        return None
    try:
        return GeoPoint(lat=coordinates["lat"], lng=coordinates["lng"])
    except (KeyError, TypeError, ValueError):
        return None


def _order_day_by_distance(day_pois: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reorder one day's already-tiered candidates by straight-line distance.

    This only reorders candidates within the day already assigned to it by
    `_order_candidates` and the per-day cap — it never moves a candidate to a
    different day. The day's first (highest-tier) candidate stays first;
    remaining candidates are visited nearest-first using
    `haversine_distance_km`. Candidates with missing or invalid coordinates
    cannot be geographically placed, so they are kept in their existing
    stable/provider order and appended at the end rather than guessed at.
    """
    if len(day_pois) <= 1:
        return list(day_pois)

    anchor = day_pois[0]
    anchor_point = _poi_coordinates(anchor)

    remaining_with_coords: list[tuple[dict[str, Any], GeoPoint]] = []
    remaining_without_coords: list[dict[str, Any]] = []
    for poi in day_pois[1:]:
        point = _poi_coordinates(poi)
        if point is not None:
            remaining_with_coords.append((poi, point))
        else:
            remaining_without_coords.append(poi)

    if anchor_point is not None:
        geo_chain: list[dict[str, Any]] = []
        current_point = anchor_point
    elif remaining_with_coords:
        # No coordinates to measure from the anchor; start the geographic
        # chain from the first coordinate-backed candidate instead.
        start_poi, current_point = remaining_with_coords.pop(0)
        geo_chain = [start_poi]
    else:
        return [anchor, *remaining_without_coords]

    while remaining_with_coords:
        nearest_index = min(
            range(len(remaining_with_coords)),
            key=lambda index: haversine_distance_km(
                current_point, remaining_with_coords[index][1]
            ),
        )
        next_poi, current_point = remaining_with_coords.pop(nearest_index)
        geo_chain.append(next_poi)

    return [anchor, *geo_chain, *remaining_without_coords]


def _suggest_nearby_restaurants(
    experiences: list[ExperienceItem],
    candidate_restaurants: list[dict[str, Any]],
    warnings: list[str],
) -> list[RestaurantSuggestion]:
    """Suggest up to `_MAX_RESTAURANT_SUGGESTIONS_PER_DAY` restaurants near
    this day's scheduled experiences.

    Only ever draws from `candidate_restaurants` (real provider-backed
    candidates); never invents a restaurant. The day anchor is the first
    coordinate-backed scheduled experience, and suggestions are the nearest
    coordinate-backed restaurant candidates to that anchor by straight-line
    (haversine) distance only -- never a reservation, rating, price, or
    route claim. If no restaurant candidates exist, no scheduled experience
    has coordinates, or no restaurant candidate has coordinates, suggestions
    stay empty and an honest warning is appended instead of guessing.
    """
    if not candidate_restaurants:
        warnings.append(_NO_RESTAURANT_CANDIDATES_WARNING)
        return []

    anchor_point = next(
        (experience.coordinates for experience in experiences if experience.coordinates), None
    )
    if anchor_point is None:
        warnings.append(_NO_DAY_ANCHOR_WARNING)
        return []

    with_coords: list[tuple[dict[str, Any], GeoPoint]] = []
    for restaurant in candidate_restaurants:
        point = _poi_coordinates(restaurant)
        if point is not None:
            with_coords.append((restaurant, point))

    if not with_coords:
        warnings.append(_NO_COORDINATE_BACKED_RESTAURANTS_WARNING)
        return []

    with_coords.sort(key=lambda item: haversine_distance_km(anchor_point, item[1]))
    nearest = with_coords[:_MAX_RESTAURANT_SUGGESTIONS_PER_DAY]
    return [_build_restaurant_suggestion(restaurant) for restaurant, _ in nearest]


def _build_restaurant_suggestion(restaurant: dict[str, Any]) -> RestaurantSuggestion:
    data_status_value = restaurant.get("data_status") or DataStatus.LIVE.value
    return RestaurantSuggestion(
        name=restaurant.get("name") or "",
        category=restaurant.get("category"),
        coordinates=_poi_coordinates(restaurant),
        address=restaurant.get("address"),
        source=restaurant.get("source"),
        data_status=DataStatus(data_status_value),
        confidence=float(restaurant.get("confidence") or 0.0),
        why_suggested=_RESTAURANT_SUGGESTION_WHY,
    )


def _suggest_nearby_accommodations(
    experiences: list[ExperienceItem],
    candidate_accommodation_pois: list[dict[str, Any]],
    warnings: list[str],
) -> list[AccommodationSuggestion]:
    """Suggest up to `_MAX_ACCOMMODATION_SUGGESTIONS_PER_DAY` accommodation
    POIs near this day's scheduled experiences.

    Only ever draws from `candidate_accommodation_pois` (real open-data
    location candidates); never invents an accommodation. The day anchor is
    the first coordinate-backed scheduled experience, and suggestions are
    the nearest coordinate-backed accommodation POI candidates to that
    anchor by straight-line (haversine) distance only -- never a price,
    availability, rating, booking, or route claim. If no accommodation POI
    candidates exist, no scheduled experience has coordinates, or no
    accommodation candidate has coordinates, suggestions stay empty and an
    honest warning is appended instead of guessing.
    """
    if not candidate_accommodation_pois:
        warnings.append(_NO_ACCOMMODATION_CANDIDATES_WARNING)
        return []

    anchor_point = next(
        (experience.coordinates for experience in experiences if experience.coordinates), None
    )
    if anchor_point is None:
        warnings.append(_NO_DAY_ANCHOR_FOR_ACCOMMODATION_WARNING)
        return []

    with_coords: list[tuple[dict[str, Any], GeoPoint]] = []
    for accommodation in candidate_accommodation_pois:
        point = _poi_coordinates(accommodation)
        if point is not None:
            with_coords.append((accommodation, point))

    if not with_coords:
        warnings.append(_NO_COORDINATE_BACKED_ACCOMMODATIONS_WARNING)
        return []

    with_coords.sort(key=lambda item: haversine_distance_km(anchor_point, item[1]))
    nearest = with_coords[:_MAX_ACCOMMODATION_SUGGESTIONS_PER_DAY]
    return [_build_accommodation_suggestion(accommodation) for accommodation, _ in nearest]


def _build_accommodation_suggestion(
    accommodation: dict[str, Any], why: str = _ACCOMMODATION_SUGGESTION_WHY
) -> AccommodationSuggestion:
    data_status_value = accommodation.get("data_status") or DataStatus.LIVE.value
    return AccommodationSuggestion(
        name=accommodation.get("name") or "",
        category=accommodation.get("category"),
        coordinates=_poi_coordinates(accommodation),
        address=accommodation.get("address"),
        source=accommodation.get("source"),
        data_status=DataStatus(data_status_value),
        confidence=float(accommodation.get("confidence") or 0.0),
        why_suggested=why,
    )


def _build_stay_area_guidance(
    daily_plans: list[DailyPlan],
    candidate_accommodation_pois: list[dict[str, Any]],
) -> StayAreaGuidance:
    """Plan-level (not day-level) stay-area guidance: up to
    `_MAX_STAY_GUIDANCE_ANCHORS` accommodation POI candidates ranked by
    average straight-line (haversine) distance to every coordinate-backed
    scheduled experience across every day.

    Only ever draws from `candidate_accommodation_pois` (the same open-data
    candidates day-level `accommodation_suggestions` use) and experiences
    already scheduled onto `daily_plans`; never invents a place, calls a
    provider, or uses AI/LLM. If no accommodation POI candidates exist, no
    scheduled experience has coordinates, or no accommodation candidate has
    coordinates, suggestions stay empty and an honest warning is returned
    instead of guessing.
    """
    if not candidate_accommodation_pois:
        return StayAreaGuidance(
            summary=(
                "No accommodation POI candidates are available yet, so no "
                "stay-area guidance could be produced."
            ),
            suggested_anchor_accommodation_pois=[],
            assumptions=list(_STAY_GUIDANCE_ASSUMPTIONS),
            warnings=[_NO_STAY_GUIDANCE_ACCOMMODATION_CANDIDATES_WARNING],
        )

    experience_points = [
        experience.coordinates
        for day_plan in daily_plans
        for experience in day_plan.experiences
        if experience.coordinates is not None
    ]
    if not experience_points:
        return StayAreaGuidance(
            summary=(
                "No coordinate-backed scheduled experiences are available "
                "yet, so no stay-area guidance could be produced."
            ),
            suggested_anchor_accommodation_pois=[],
            assumptions=list(_STAY_GUIDANCE_ASSUMPTIONS),
            warnings=[_NO_STAY_GUIDANCE_ANCHOR_WARNING],
        )

    scored: list[tuple[float, dict[str, Any]]] = []
    for accommodation in candidate_accommodation_pois:
        point = _poi_coordinates(accommodation)
        if point is None:
            continue
        distances = [haversine_distance_km(point, exp_point) for exp_point in experience_points]
        scored.append((sum(distances) / len(distances), accommodation))

    if not scored:
        return StayAreaGuidance(
            summary=(
                "No coordinate-backed accommodation POI candidates are "
                "available, so no stay-area guidance could be produced."
            ),
            suggested_anchor_accommodation_pois=[],
            assumptions=list(_STAY_GUIDANCE_ASSUMPTIONS),
            warnings=[_NO_COORDINATE_BACKED_STAY_GUIDANCE_ACCOMMODATIONS_WARNING],
        )

    scored.sort(key=lambda item: item[0])
    nearest = scored[:_MAX_STAY_GUIDANCE_ANCHORS]
    suggestions = [
        _build_accommodation_suggestion(accommodation, why=_STAY_GUIDANCE_SUGGESTION_WHY)
        for _, accommodation in nearest
    ]

    summary = (
        f"{len(suggestions)} accommodation POI candidate"
        f"{'s' if len(suggestions) != 1 else ''} identified as closest, on "
        "average, to this plan's scheduled attractions by straight-line "
        "distance."
    )

    return StayAreaGuidance(
        summary=summary,
        suggested_anchor_accommodation_pois=suggestions,
        assumptions=list(_STAY_GUIDANCE_ASSUMPTIONS),
        warnings=[],
    )


def _build_decision_summary(
    candidate_pois: list[dict[str, Any]],
    candidate_restaurants: list[dict[str, Any]],
    candidate_accommodation_pois: list[dict[str, Any]],
    daily_plans: list[DailyPlan],
) -> DecisionSummary:
    """Plan-level decision summary explaining why the plan looks the way it
    does, built purely from data already on `PlanningState`/`ExperiencePlan`/
    `DestinationContext` -- no provider call, no AI/LLM, no invented fact.
    Restaurant/accommodation unavailability is stated honestly rather than
    silently omitted. Never affects validation readiness by itself.
    """
    scheduled_experiences_count = sum(len(day_plan.experiences) for day_plan in daily_plans)

    provider_backed_facts: list[str] = []
    summary_parts: list[str] = []

    if candidate_pois:
        provider_backed_facts.append(
            f"{scheduled_experiences_count} scheduled attraction"
            f"{'s' if scheduled_experiences_count != 1 else ''} came from "
            "provider-backed destination_context.candidate_pois."
        )
        summary_parts.append(
            "Attractions were scheduled from provider-backed candidate_pois."
        )
    else:
        summary_parts.append(
            "No attraction candidates were available, so no attractions were scheduled."
        )

    if candidate_restaurants:
        provider_backed_facts.append(
            f"{len(candidate_restaurants)} restaurant candidate"
            f"{'s' if len(candidate_restaurants) != 1 else ''} came from "
            "provider-backed destination_context.candidate_restaurants."
        )
        summary_parts.append(
            "Nearby restaurant suggestions used provider-backed candidate_restaurants."
        )
    else:
        summary_parts.append(
            "Restaurant candidates are unavailable, so no restaurant suggestions could be made."
        )

    if candidate_accommodation_pois:
        provider_backed_facts.append(
            f"{len(candidate_accommodation_pois)} accommodation POI candidate"
            f"{'s' if len(candidate_accommodation_pois) != 1 else ''} came from "
            "provider-backed destination_context.candidate_accommodation_pois."
        )
        summary_parts.append(
            "Nearby accommodation POI suggestions and stay-area guidance used "
            "provider-backed candidate_accommodation_pois."
        )
    else:
        summary_parts.append(
            "Accommodation POI candidates are unavailable, so no accommodation "
            "suggestions or stay-area guidance could be made."
        )

    summary_parts.append(
        "Day grouping, within-day ordering, and nearby restaurant/accommodation "
        "suggestions use straight-line (haversine) geographic proximity only, "
        "not route optimization."
    )
    summary_parts.append(
        "Route ordering, opening hours, feasibility, walking time, costs, "
        "hotel prices, availability, ratings, and booking links are not "
        "validated yet."
    )

    return DecisionSummary(
        summary=" ".join(summary_parts),
        provider_backed_facts=provider_backed_facts,
        proximity_based_decisions=list(_DECISION_SUMMARY_PROXIMITY_DECISIONS),
        unvalidated_items=list(_DECISION_SUMMARY_UNVALIDATED_ITEMS),
        user_review_required=list(_DECISION_SUMMARY_USER_REVIEW_REQUIRED),
    )


def _build_experience_item(
    poi: dict[str, Any], must_visit_ids: set[int], interest_ids: set[int]
) -> ExperienceItem:
    name = poi.get("name") or ""
    coordinates = _poi_coordinates(poi)
    confidence = float(poi.get("confidence") or 0.0)
    data_status_value = poi.get("data_status") or DataStatus.LIVE.value

    if id(poi) in must_visit_ids:
        why_included = "Matches your must-visit request."
    elif id(poi) in interest_ids:
        why_included = (
            "Matches your interests based on this candidate's provider-backed "
            "name, category, and address."
        )
    else:
        why_included = "Selected from provider-backed attraction candidates."

    source = poi.get("source")
    source_type = (
        ClaimSourceType.OPEN_DATA_FACT
        if source and "openstreetmap" in source
        else ClaimSourceType.PROVIDER_FACT
    )

    return ExperienceItem(
        name=name,
        category=poi.get("category") or _DEFAULT_CATEGORY,
        coordinates=coordinates,
        why_included=why_included,
        confidence=confidence,
        data_quality=DataQuality(
            data_status=DataStatus(data_status_value), confidence=confidence
        ),
        claim_sources=[
            ClaimSource(
                claim=f"{name} is a real place from destination_context.candidate_pois.",
                source_type=source_type,
                source=source,
                based_on=["destination_context.candidate_pois"],
            )
        ],
    )
