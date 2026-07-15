from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.models.common import ClaimSource, ClaimSourceType, DataQuality, DataStatus, GeoPoint
from app.models.planning_state import (
    DailyPlan,
    ExperienceItem,
    ExperiencePlan,
    PlanningStage,
    PlanningState,
    TripPace,
)
from app.services.base import PlanningStageService

_MAX_ATTRACTIONS_PER_DAY: dict[TripPace, int] = {
    TripPace.RELAXED: 2,
    TripPace.BALANCED: 3,
    TripPace.PACKED: 4,
}
_DEFAULT_CATEGORY = "attraction"
_FEASIBILITY_WARNING = (
    "Route ordering, timing, opening hours, and feasibility checks are not "
    "implemented yet, so scheduled attractions have no start/end time, "
    "duration, walking distance, or cost estimate."
)


class ExperiencePlannerService(PlanningStageService):
    """Owns `experience_plan`, `experience_cards`, and itinerary decision
    cards (docs/14_backend_architecture.md section 13).

    Consumes `traveler_profile`, `destination_context`, `trip_strategy`, and
    `stay_transport`. Day structure (day number/date) can be derived safely
    from the user's own dates. This stage makes no provider calls of its
    own.

    First conservative scheduling step: attractions are distributed across
    days directly from `destination_context.candidate_pois` (real
    provider-backed places only, never invented), using a simple
    deterministic per-day cap based on trip pace (relaxed=2, balanced=3,
    packed=4). Candidates are ordered into three tiers before scheduling,
    with the relative provider order preserved within each tier:

    1. must-visit matches (candidate `name` contains a `must_visit` term)
    2. interest matches (candidate `name`, `category`, or `address` contains
       an `interests` term)
    3. everything else, in existing provider order

    No new place is invented to satisfy a must-visit or interest match —
    only existing `candidate_pois` are reordered. Restaurants and
    accommodation POIs are intentionally not used yet. No route ordering,
    timing, opening-hours, walking, or cost estimation is implemented yet,
    so every scheduled item keeps `start_time`/`end_time`/
    `estimated_duration_minutes` unset and every day carries an explicit
    feasibility warning.
    """

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.EXPERIENCE_PLAN)

        destination_context = planning_state.destination_context
        candidate_pois = list(destination_context.candidate_pois) if destination_context else []

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

        daily_plans: list[DailyPlan] = []
        cursor = 0
        for day_number in range(1, num_days + 1):
            day_date = trip_request.start_date + timedelta(days=day_number - 1)
            day_pois = ordered_pois[cursor : cursor + max_per_day]
            cursor += max_per_day

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

            daily_plans.append(
                DailyPlan(
                    day_number=day_number,
                    date=day_date,
                    experiences=experiences,
                    warnings=warnings,
                )
            )

        assumptions = [
            "Attractions are scheduled directly from provider-backed candidates using a "
            "simple per-day cap based on pace; geographic grouping, route ordering, "
            "timing, and opening-hours feasibility are not implemented yet."
        ]
        if not candidate_pois:
            assumptions.insert(
                0,
                "No experiences could be scheduled because no provider-backed attraction "
                "candidates are available.",
            )

        experience_plan = ExperiencePlan(
            daily_plans=daily_plans,
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


def _build_experience_item(
    poi: dict[str, Any], must_visit_ids: set[int], interest_ids: set[int]
) -> ExperienceItem:
    name = poi.get("name") or ""

    coordinates: GeoPoint | None = None
    poi_coordinates = poi.get("coordinates")
    if poi_coordinates:
        coordinates = GeoPoint(lat=poi_coordinates["lat"], lng=poi_coordinates["lng"])

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
