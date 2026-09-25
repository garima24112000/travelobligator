from __future__ import annotations

import logging
from dataclasses import dataclass
import statistics
from datetime import timedelta
from typing import Any

from app.services.experience_identity import deterministic_experience_id
from app.core.config import get_settings
from app.models.ai_candidate_promotion import PromotedAICandidate
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningCategory,
    build_candidate_id,
)
from app.models.candidate_quality import CandidateQualityScore, CandidateQualityTier
from app.models.common import (
    ChecklistItemStatus,
    ClaimSource,
    ClaimSourceType,
    DataQuality,
    DataStatus,
    GeoPoint,
)
from app.models.planning_state import (
    AccommodationSuggestion,
    DailyPlan,
    DecisionSummary,
    ExperienceItem,
    ExperiencePlan,
    ImplementationGaps,
    PlanningStage,
    PlanningState,
    ReadinessChecklist,
    ReadinessChecklistItem,
    RestaurantSuggestion,
    RouteFeasibilityContext,
    StayAreaGuidance,
    TripPace,
)
from app.services import place_taxonomy as taxonomy
from app.services.base import PlanningStageService
from app.utils.geo import haversine_distance_km

logger = logging.getLogger(__name__)

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

_MAX_STAY_GUIDANCE_ANCHORS = 5
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

# Coverage values that mean a real provider actually returned usable data
# for that field, as opposed to "not_connected"/"failed"/"unavailable".
_CONNECTED_COVERAGE_STATUSES = {"success", "partial", "fallback_used", "open_poi_available"}

_IMPLEMENTATION_GAPS_WHY_NEEDS_REVIEW = [
    "Route ordering between scheduled attractions is not validated.",
    "Opening hours are not validated against the schedule.",
    "Walking time between scheduled attractions is not calculated.",
    "Costs and budget fit are not validated.",
    "Suggested accommodation POIs are open-data location candidates only, "
    "not bookable inventory.",
    "Public holiday data, when available, is not yet used to check venue "
    "closures or crowd risk against the schedule.",
]


# Step 156C/156E (docs/18_candidate_quality.md): deterministic pre-ranking
# support for consuming `PlanningState.candidate_quality_report` when
# scheduling attractions and suggesting nearby restaurants/accommodation
# POIs. This only ever reorders or drops candidates using their own
# already-computed `quality_tier`/`total_score` -- never invents a place,
# never mutates `candidate_quality_report`, and falls back to pre-156C
# behavior whenever no report (or no matching score) is available.
_QUALITY_TIER_RANK: dict[CandidateQualityTier, int] = {
    CandidateQualityTier.PRIMARY_ANCHOR: 4,
    CandidateQualityTier.GOOD_CANDIDATE: 3,
    CandidateQualityTier.SECONDARY_CANDIDATE: 2,
    CandidateQualityTier.LOW_PRIORITY: 1,
    CandidateQualityTier.REJECTED: 0,
}
# Trust-over-fullness (Step 156E, itinerary-generator-build-spec.md Stage
# 8): only these tiers are eligible for attraction scheduling. `rejected`
# and `low_priority` candidates are both excluded entirely -- never used
# as filler to pad out a lighter-than-usual day.
_ELIGIBLE_SCHEDULING_TIERS = {
    CandidateQualityTier.PRIMARY_ANCHOR,
    CandidateQualityTier.GOOD_CANDIDATE,
    CandidateQualityTier.SECONDARY_CANDIDATE,
}
_LOW_PRIORITY_OR_REJECTED_EXCLUDED_WARNING = (
    "Some low-priority or rejected candidate attractions were not scheduled. "
    "This day may be lighter because not enough stronger provider-backed "
    "candidates were available."
)
# Straight-line distances within this tolerance are treated as "similar"
# for restaurant/accommodation suggestion ordering, letting candidate
# quality break the tie -- never a walking/route feasibility claim, just a
# conservative widening of what counts as "the same distance".
_PROXIMITY_SIMILARITY_KM = 0.1


def _quality_rank_and_score(score: CandidateQualityScore | None) -> tuple[int, float]:
    """Neutral defaults (mid-rank, zero score) when no quality score is
    known for a candidate -- e.g. `candidate_quality_report` is `None`, or a
    length mismatch between the report and the current candidate list --
    so quality-aware ordering degrades to a harmless no-op instead of
    crashing or silently excluding the candidate.
    """
    if score is None:
        return (2, 0.0)
    return (_QUALITY_TIER_RANK.get(score.quality_tier, 2), score.total_score)


def _build_quality_lookup(
    candidates: list[dict[str, Any]], scores: list[CandidateQualityScore] | None
) -> dict[int, CandidateQualityScore]:
    """Maps each candidate dict's object identity to its
    `CandidateQualityScore`, relying on `CandidateQualityService.build_report`
    scoring every candidate in a `destination_context` candidate list
    exactly once, in the same order (Step 156A/156B). Returns an empty
    lookup (never raises, never guesses) if `scores` is missing or its
    length doesn't match `candidates` -- every call site below treats a
    missing lookup entry as "unknown quality" and falls back to legacy
    behavior for that candidate.
    """
    if not scores or len(candidates) != len(scores):
        return {}
    return {id(candidate): score for candidate, score in zip(candidates, scores)}


def _select_candidates_by_quality(
    candidate_pois: list[dict[str, Any]],
    quality_lookup: dict[int, CandidateQualityScore],
) -> list[dict[str, Any]]:
    """Deterministic pre-ranking filter/reorder over `candidate_pois` using
    `CandidateQualityService`'s existing scores (Step 156C/156E). Never
    invents, drops, or reorders a candidate based on anything but its own
    already-computed `quality_tier`/`total_score`.

    If `quality_lookup` is empty (no `candidate_quality_report`, or it
    doesn't correspond to this candidate list), returns `candidate_pois`
    unchanged so scheduling falls back to pre-156C behavior exactly.

    Trust-over-fullness (Step 156E, itinerary-generator-build-spec.md
    Stage 8): `rejected` candidates (e.g. missing coordinates, insufficient
    provider confidence) and `low_priority` candidates (e.g. generic
    historic districts, administrative/infrastructure objects) are both
    excluded from scheduling entirely -- neither is ever used, even as
    filler, to fill out a day. Only `primary_anchor`/`good_candidate`/
    `secondary_candidate` candidates are eligible. If there are not enough
    eligible candidates to fill every requested day/pace slot, the
    resulting schedule is deliberately left lighter rather than padded with
    weak candidates -- a grounded must-visit (which `CandidateQualityService`
    never demotes to `low_priority`/`rejected` outside a severe issue) is
    unaffected by this exclusion. The remaining eligible candidates are
    stable-sorted by quality tier/score, highest first, preserving relative
    order for ties.
    """
    if not quality_lookup:
        return candidate_pois

    eligible = [
        poi
        for poi in candidate_pois
        if quality_lookup.get(id(poi)) is None
        or quality_lookup[id(poi)].quality_tier in _ELIGIBLE_SCHEDULING_TIERS
    ]
    return sorted(
        eligible,
        key=lambda poi: _quality_rank_and_score(quality_lookup.get(id(poi))),
        reverse=True,
    )


def _distance_bucket(distance_km: float) -> float:
    """Rounds a straight-line distance to the nearest
    `_PROXIMITY_SIMILARITY_KM` so "similar" distances sort as tied,
    letting candidate quality break the tie (Step 156C).
    """
    return round(distance_km / _PROXIMITY_SIMILARITY_KM) * _PROXIMITY_SIMILARITY_KM


def _distance_and_quality_sort_key(
    distance_km: float, score: CandidateQualityScore | None
) -> tuple[float, int, float]:
    rank, total_score = _quality_rank_and_score(score)
    return (_distance_bucket(distance_km), -rank, -total_score)


# Step 170D (docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md):
# safe scheduling integration for AI candidates that already cleared Step
# 170B's deterministic eligibility rules and were materialized by Step
# 170C's `AICandidatePromotionService` into
# `PlanningState.ai_candidate_promotion_report`. A `PromotedAICandidate` is
# never a special case here -- it is converted into the exact same
# dict-shaped candidate representation `destination_context.candidate_pois`
# already uses, then merged into the scheduling pool so it goes through
# identical must-visit/interest tiering, geographic day-grouping, and
# nearest-neighbor ordering as every real provider candidate. It never
# re-enters `CandidateQualityService` (its quality tier was already
# verified once, during promotion) and never bypasses the pace-based
# per-day cap. If `PlanningState.ai_candidate_promotion_report` is absent
# or has zero promoted candidates, this is a complete no-op and scheduling
# behaves exactly as it did before Step 170D.
_PROMOTED_CANDIDATE_MISSING_COORDINATES_WARNING_TEMPLATE = (
    "Promoted AI candidate '{name}' is missing provider-backed coordinates, "
    "so it was skipped rather than scheduled with a guessed location."
)
_PROMOTED_CANDIDATE_DUPLICATE_WARNING_TEMPLATE = (
    "Promoted AI candidate '{name}' duplicates an existing provider "
    "candidate and was not scheduled a second time."
)


def _normalize_candidate_name(name: str) -> str:
    return name.strip().lower()


def _candidate_identity_keys(poi: dict[str, Any]) -> set[tuple[str, str]]:
    """Deterministic dedup keys for one candidate dict -- both a real
    provider place id (`place_id`/`provider_place_id`) *and* a normalized
    name, whichever are present, so two candidates count as duplicates if
    they share either signal (e.g. a real OSM candidate always carries a
    `place_id`, while a promoted candidate missing `provider_place_id`
    would otherwise only be comparable by name). Never invents an id;
    returns an empty set only when a candidate has neither, in which case
    it can't be deduplicated against.
    """
    keys: set[tuple[str, str]] = set()
    place_id = poi.get("place_id") or poi.get("provider_place_id")
    if place_id:
        keys.add(("place_id", str(place_id)))
    name = poi.get("name")
    if name:
        keys.add(("name", _normalize_candidate_name(str(name))))
    return keys


def _promoted_candidate_to_poi_dict(promoted: PromotedAICandidate) -> dict[str, Any] | None:
    """Converts one `PromotedAICandidate` into the same dict shape
    `_build_experience_item`/`_poi_coordinates`/`_order_candidates` already
    expect from a `destination_context` candidate. Every value here is
    copied verbatim from already-computed, provider-backed fields -- no
    coordinate, rating, opening hour, price, route, or description is ever
    invented. Returns `None` (skip, never fabricate) when the candidate is
    missing the one field required to schedule it geographically:
    coordinates.
    """
    if promoted.coordinates is None:
        return None
    return {
        # Deliberately NOT defaulted to `promoted.candidate_id` (a
        # promotion-internal id, not a real provider place id) -- when
        # `provider_place_id` is unavailable, `_candidate_identity_key`
        # must fall back to a normalized-name dedup match instead of a
        # meaningless internal id that could never collide with anything.
        "place_id": promoted.provider_place_id,
        "provider_place_id": promoted.provider_place_id,
        "name": promoted.name,
        "category": promoted.category or _DEFAULT_CATEGORY,
        "coordinates": {"lat": promoted.coordinates.lat, "lng": promoted.coordinates.lng},
        "source": promoted.provider_source or promoted.source,
        "data_status": promoted.data_status or DataStatus.LIVE.value,
        "confidence": promoted.confidence if promoted.confidence is not None else 0.0,
        "promoted_from_ai": True,
        # Its quality was verified once during promotion (Step 170D); carried
        # so the shared selection rules rank it like any other candidate.
        "quality_tier": promoted.quality_bucket,
        "original_ai_candidate_id": promoted.original_ai_candidate_id,
        "provider_source": promoted.provider_source,
    }


def _build_promoted_candidate_pois(
    planning_state: PlanningState,
    candidate_pois: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Builds the list of promoted-AI-candidate dicts eligible to join the
    attraction scheduling pool (Step 170D), plus any honest skip warnings.

    Only ever reads `planning_state.ai_candidate_promotion_report` (already
    computed by `AICandidatePromotionService` -- this never calls it, never
    calls a provider/LLM, and never mutates `planning_state`). Returns
    `([], [])` whenever the report is absent or has zero promoted
    candidates, so default behavior (no promotion report, or an empty one)
    is byte-for-byte unchanged from before Step 170D.

    Every promoted candidate missing required coordinates is skipped with
    an explicit warning instead of being scheduled with a guessed
    location. Every promoted candidate whose `provider_place_id`/name
    already matches a real candidate already in `candidate_pois` is
    skipped as a duplicate -- promotion never causes the same real place to
    be scheduled twice.
    """
    promotion_report = planning_state.ai_candidate_promotion_report
    if promotion_report is None or not promotion_report.promoted_candidates:
        return [], []

    existing_keys: set[tuple[str, str]] = set()
    for poi in candidate_pois:
        existing_keys |= _candidate_identity_keys(poi)

    promoted_pois: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_keys: set[tuple[str, str]] = set()

    for promoted in promotion_report.promoted_candidates:
        candidate_dict = _promoted_candidate_to_poi_dict(promoted)
        if candidate_dict is None:
            warnings.append(
                _PROMOTED_CANDIDATE_MISSING_COORDINATES_WARNING_TEMPLATE.format(name=promoted.name)
            )
            continue

        candidate_keys = _candidate_identity_keys(candidate_dict)
        if candidate_keys & (existing_keys | seen_keys):
            warnings.append(
                _PROMOTED_CANDIDATE_DUPLICATE_WARNING_TEMPLATE.format(name=promoted.name)
            )
            continue
        seen_keys |= candidate_keys

        promoted_pois.append(candidate_dict)

    return promoted_pois, warnings


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

    Finally, the plan gets a plan-level `implementation_gaps` section, built
    purely from `planning_state.provider_coverage` and
    `planning_state.provider_status`: which data is connected now (e.g.
    attractions/restaurants/accommodation POIs from the open-data places
    provider, weather when Open-Meteo returns usable daily forecast data,
    holidays when Nager.Date returns usable public holiday data, and
    currency when Frankfurter returns a usable exchange rate), which data
    is missing (routes/transit, a booking-capable accommodation provider,
    hotel prices, vacation rentals/Airbnb, weather when unavailable,
    holidays when unavailable, currency when unavailable), what provider
    would be needed next for each gap, and why the plan stays Needs Review
    (route ordering, opening hours, walking time, and costs/budget fit are
    unvalidated, accommodation POIs are open-data location candidates only,
    not bookable inventory, and public holiday data, when available, is not
    yet used to check venue closures or crowd risk). A connected currency
    provider only ever supplies a single-unit exchange rate; it never marks
    costs/budget fit as validated -- that stays Needs Review regardless of
    currency coverage. No provider call, no AI/LLM, no invented fact, and it
    never affects validation readiness by itself -- it explains existing
    gaps rather than resolving them.

    Finally, the plan gets a plan-level `readiness_checklist`: a fixed list
    of checklist items (attractions/restaurants/accommodation POI
    availability, route times, opening hours, walking feasibility,
    budget/cost fit, accommodation price/availability, weather impact,
    holiday/closure context, booking links), each labeled `checked`,
    `needs_review`, `missing_data`, or `not_implemented` from
    `provider_coverage`/`provider_status`/the candidate lists already
    computed above. The three candidate-availability items (attractions/
    restaurants/accommodation POIs) are plain data-availability checks, not
    travel-readiness checks: `checked` whenever provider-backed candidates
    exist at all, regardless of coverage tier, and `missing_data` only when
    none exist. Every other item is a travel-readiness check and never
    becomes `checked` in this deployment. "Weather impact checked" is
    `needs_review` (never `checked`) whenever `weather_context` has usable
    provider-backed daily data, since weather-aware itinerary adjustment is
    not implemented yet; it stays `missing_data` when weather is
    unavailable/failed/not connected. "Holiday/closure context checked"
    follows the same pattern: `needs_review` (never `checked`) whenever
    `holiday_context` has usable provider-backed holiday data (even an
    empty in-range list, as long as the provider call itself succeeded),
    since checking it against venue closures/crowd risk is not implemented
    yet; it stays `missing_data` when holidays are
    unavailable/failed/not connected. No provider call, no AI/LLM, no
    invented fact, and it never marks the plan ready by itself --
    `ValidationReport.readiness_status` remains the single source of truth
    for overall readiness.

    Finally, the plan gets a plan-level `route_feasibility_context`: a
    data-model foundation only, so the app is ready for a real
    RoutesProvider later. No routes provider is connected in this
    deployment, so `daily_route_feasibility` always stays empty --
    `data_status` stays `not_connected`, `confidence` stays `0.0`, and no
    straight-line distance, walking time, transit time, driving time, or
    feasibility score is ever calculated or presented as real route data.
    This does not change "Route times checked"/"Walking feasibility
    checked" in `readiness_checklist`, which already stay `missing_data`
    from `provider_coverage.routes`, and it never affects validation
    readiness by itself.

    Step 156C/156E (docs/18_candidate_quality.md): before the must-visit/
    interest/provider-order tiering above runs, candidates are additionally
    filtered/reordered using `PlanningState.candidate_quality_report` when
    it is present. Trust-over-fullness (Step 156E,
    itinerary-generator-build-spec.md Stage 8): `rejected` candidates
    (missing coordinates, insufficient provider confidence, etc.) and
    `low_priority` candidates (generic historic districts, administrative/
    infrastructure objects, etc.) are both excluded from scheduling
    entirely -- neither is ever used as filler. If there are not enough
    `primary_anchor`/`good_candidate`/`secondary_candidate` candidates to
    fill every day/pace slot, the day is deliberately left lighter instead,
    with an honest warning explaining why. The remaining eligible
    candidates are stable-sorted by quality tier/score. This never invents
    a place and never mutates `candidate_quality_report`; if no report is
    present, scheduling falls back to the exact pre-156C behavior. The same
    quality-aware tie-break (distance first, quality when distance is
    similar) is applied to nearby restaurant/accommodation-POI suggestions
    and to plan-level stay-area guidance below -- those keep excluding only
    `rejected` candidates, never `low_priority` ones, since low-priority
    location candidates are still safe to show as nearby-only suggestions.

    Step 170D (docs/13_llm_reasoning_pipeline.md, docs/14_backend_
    architecture.md): after quality-based selection, any already-promoted
    AI candidates on `PlanningState.ai_candidate_promotion_report` (Step
    170C -- themselves only ever produced from a provider-grounded,
    quality-approved candidate that already cleared Step 170B's
    deterministic promotion rules) are merged into the same scheduling pool as
    real `candidate_pois`, going through identical must-visit/interest
    tiering, geographic day-grouping, and per-day pace caps -- never a
    special case, never re-scored by `CandidateQualityService`, never
    allowed to bump an already-scheduled real candidate out of a slot it
    would otherwise win. A promoted candidate missing coordinates, or
    duplicating a real candidate already in `candidate_pois`, is skipped
    with an honest assumption/warning instead of being scheduled with a
    guessed location or scheduled twice. Every resulting `ExperienceItem`
    scheduled this way carries `promoted_from_ai=True` plus
    `original_ai_candidate_id`/`provider_place_id`/`provider_source` so its
    provenance is never lost. If `ai_candidate_promotion_report` is absent
    or has zero promoted candidates, scheduling is a byte-for-byte no-op
    versus pre-170D behavior.
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

        quality_report = planning_state.candidate_quality_report
        attraction_quality_lookup = _build_quality_lookup(
            candidate_pois, quality_report.attraction_scores if quality_report else None
        )
        restaurant_quality_lookup = _build_quality_lookup(
            candidate_restaurants, quality_report.restaurant_scores if quality_report else None
        )
        accommodation_quality_lookup = _build_quality_lookup(
            candidate_accommodation_pois,
            quality_report.accommodation_poi_scores if quality_report else None,
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

        scheduling_candidate_pois = _select_candidates_by_quality(
            candidate_pois, attraction_quality_lookup
        )
        # Trust-over-fullness (Step 156E): candidates excluded here are
        # low_priority/rejected by candidate quality, never a missing
        # provider result -- used below to explain a lighter-than-usual day
        # honestly instead of silently under-filling it.
        quality_excluded_count = len(candidate_pois) - len(scheduling_candidate_pois)

        # Step 170D: merge in any already-promoted AI candidates. This is a
        # complete no-op when `ai_candidate_promotion_report` is absent or
        # has zero promoted candidates -- `promoted_pois` stays `[]` and
        # scheduling proceeds exactly as before Step 170D. Appended after
        # quality selection (never re-filtered by CandidateQualityService --
        # their quality tier was already verified once during promotion)
        # and before must-visit/interest tiering, so a promoted candidate
        # is treated identically to any other candidate from this point on:
        # it can anchor a day if it matches a must-visit/interest term, or
        # simply fill a remaining slot if the planner's existing geographic
        # nearest-neighbor fill naturally picks it. It never bumps an
        # already-scheduled real candidate out of a slot it would otherwise
        # have won.
        promoted_pois, promoted_candidate_warnings = _build_promoted_candidate_pois(
            planning_state, candidate_pois
        )
        scheduling_candidate_pois = scheduling_candidate_pois + promoted_pois
        has_any_attraction_candidates = bool(candidate_pois) or bool(promoted_pois)

        ordered_pois, must_visit_ids, interest_ids = _order_candidates(
            scheduling_candidate_pois, must_visit_terms, interest_terms
        )

        # Section 193C (docs/14_backend_architecture.md section 143):
        # attempt an AI-guided (LLM #2) day grouping first, falling back
        # to the existing deterministic geographic grouping whenever no
        # completed/valid reasoning result is available -- this is the
        # only branch point this step adds; every other line of `run`
        # below is unchanged regardless of which path produced
        # `day_groups`.
        scheduling_candidate_restaurants = _select_candidates_by_quality(
            candidate_restaurants, restaurant_quality_lookup
        )
        candidate_reverse_index = _build_ai_candidate_reverse_index(
            scheduling_candidate_pois, scheduling_candidate_restaurants
        )
        ai_day_groups, ai_restaurants_by_day = _resolve_ai_guided_day_groups(
            planning_state.ai_itinerary_reasoning_result,
            candidate_reverse_index,
            num_days,
            max_per_day,
        )
        used_ai_reasoning = ai_day_groups is not None
        canonical_interests = taxonomy.canonical_interests(interest_terms)
        profiles = {
            id(poi): _candidate_profile(poi, attraction_quality_lookup.get(id(poi)), canonical_interests)
            for poi in scheduling_candidate_pois
        }
        if ai_day_groups is not None:
            # Section 202B.2 (Task 22): a valid AI grouping may still leave
            # a day empty; fill only from unused quality-eligible candidates.
            day_groups = _fill_empty_days(ai_day_groups, scheduling_candidate_pois, profiles)
        else:
            # Section 202B.2: shared candidate-quality evidence drives the
            # deterministic fallback too -- interest coverage, diversity
            # pressure, outlier control, then balanced geographic days.
            selected = _select_diverse_scheduling_set(
                ordered_pois, profiles, num_days * max_per_day, canonical_interests, must_visit_ids
            )
            day_groups = _group_candidates_into_balanced_days(selected, num_days, max_per_day)

        reasoning_result = planning_state.ai_itinerary_reasoning_result
        logger.info(
            "ExperiencePlannerService resolved day grouping.",
            extra={
                "stage": "ai_itinerary_reasoning",
                "enabled": get_settings().ai_itinerary_reasoning_enabled,
                "provider": reasoning_result.provider_name if reasoning_result is not None else None,
                "status": reasoning_result.status.value if reasoning_result is not None else None,
                "candidate_count": len(candidate_reverse_index),
                "selected_candidate_count": (
                    sum(len(day) for day in ai_day_groups) if used_ai_reasoning and ai_day_groups else 0
                ),
                "day_count": num_days,
                "fallback_used": not used_ai_reasoning,
            },
        )

        daily_plans: list[DailyPlan] = []
        used_experience_ids: set[str] = set()
        for day_number in range(1, num_days + 1):
            day_date = trip_request.start_date + timedelta(days=day_number - 1)
            # Section 193C: an AI-guided day keeps LLM #2's own chosen
            # order verbatim -- re-sorting it by geographic distance here
            # would silently discard the one thing LLM #2 was asked to
            # decide (ordering). The deterministic path is unchanged.
            day_pois = (
                day_groups[day_number - 1]
                if used_ai_reasoning
                else _order_day_by_distance(day_groups[day_number - 1])
            )

            warnings: list[str] = []
            if not has_any_attraction_candidates:
                warnings.append(
                    "No attraction candidates are available yet, so this day is empty."
                )
            else:
                warnings.append(_FEASIBILITY_WARNING)
                if not day_pois:
                    warnings.append(
                        "No remaining candidate attractions were available for this day."
                    )
                if (
                    not used_ai_reasoning
                    and quality_excluded_count > 0
                    and len(day_pois) < max_per_day
                ):
                    # Section 193C: for an AI-guided day, a count below
                    # max_per_day is explained by LLM #2's own selection
                    # (a restaurant-category pick routed to
                    # restaurant_suggestions instead, or simply choosing
                    # fewer attractions) -- attributing it to quality-tier
                    # exclusion here would be a real fact about the wrong
                    # cause, not a fabrication but still misleading.
                    warnings.append(_LOW_PRIORITY_OR_REJECTED_EXCLUDED_WARNING)
            if used_ai_reasoning:
                reasoning_day = next(
                    (
                        day
                        for day in planning_state.ai_itinerary_reasoning_result.days
                        if day.day_index == day_number
                    ),
                    None,
                )
                if reasoning_day is not None:
                    warnings.append(
                        f"AI itinerary reasoning rationale for this day: {reasoning_day.rationale}"
                    )

            experiences = [
                _build_experience_item(
                    poi,
                    must_visit_ids,
                    interest_ids,
                    trip_id=planning_state.trip_id,
                    used_experience_ids=used_experience_ids,
                    profile=profiles.get(id(poi)),
                )
                for poi in day_pois
            ]
            # Step 172A: stable ordering metadata, restating this day's own
            # already-decided schedule position -- see ExperienceItem's own
            # docstring/comment. Re-stamped later by
            # RouteAwareSequencingService.apply_report if a provider-backed
            # reorder changes this day's order.
            for stop_index, experience in enumerate(experiences, start=1):
                experience.day_number = day_number
                experience.stop_order = stop_index

            # Section 193C: if LLM #2 explicitly selected one or more
            # restaurant-category candidates for this day, honor that
            # choice instead of the geographic nearest-neighbor
            # suggestion -- the same "use only what the LLM actually
            # selected" principle as attractions above. A day the AI
            # reasoning didn't mention any restaurant for still falls
            # back to the existing geographic suggestion, unchanged.
            ai_restaurants_for_day = ai_restaurants_by_day.get(day_number) if used_ai_reasoning else None
            if ai_restaurants_for_day:
                restaurant_suggestions = [
                    _build_restaurant_suggestion(restaurant) for restaurant in ai_restaurants_for_day
                ]
            else:
                restaurant_suggestions = _suggest_nearby_restaurants(
                    experiences, candidate_restaurants, warnings, restaurant_quality_lookup
                )
            accommodation_suggestions = _suggest_nearby_accommodations(
                experiences, candidate_accommodation_pois, warnings, accommodation_quality_lookup
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

        if used_ai_reasoning:
            assumptions = [
                "Attraction (and, where selected, restaurant) grouping and order for this "
                "plan were chosen by AI itinerary reasoning (LLM #2, Section 193B) from "
                "the same provider-backed, quality-approved candidate pool the "
                "deterministic path uses -- every scheduled place's name, coordinates, and "
                "provider identity still come entirely from the real provider record, "
                "never from the AI. A day this reasoning did not select any candidate for "
                "stays empty rather than being auto-filled. Route ordering, timing, and "
                "opening-hours feasibility are not implemented yet."
            ]
        else:
            assumptions = [
                "Attractions are scheduled directly from provider-backed candidates: each "
                "day's highest-priority unscheduled candidate anchors that day, and "
                "remaining slots (up to the pace-based per-day cap) are filled and ordered "
                "using straight-line (haversine) geographic proximity when coordinates are "
                "available. Days are grouped using geographic proximity, not route "
                "optimization, and route ordering, timing, and opening-hours feasibility "
                "are not implemented yet."
            ]
        if not has_any_attraction_candidates:
            assumptions.insert(
                0,
                "No experiences could be scheduled because no provider-backed attraction "
                "candidates are available.",
            )
        if promoted_pois:
            assumptions.append(
                f"{len(promoted_pois)} additional candidate"
                f"{'s' if len(promoted_pois) != 1 else ''} came from "
                "ai_candidate_promotion_report.promoted_candidates -- each one is a "
                "provider-grounded, quality-approved AI-proposed candidate, scheduled "
                "using the exact same rules as every other candidate."
            )
        assumptions.extend(promoted_candidate_warnings)

        stay_area_guidance = _build_stay_area_guidance(
            daily_plans, candidate_accommodation_pois, accommodation_quality_lookup
        )
        decision_summary = _build_decision_summary(
            candidate_pois, candidate_restaurants, candidate_accommodation_pois, daily_plans
        )
        implementation_gaps = _build_implementation_gaps(planning_state)
        readiness_checklist = _build_readiness_checklist(
            planning_state, candidate_pois, candidate_restaurants, candidate_accommodation_pois
        )
        route_feasibility_context = _build_route_feasibility_context()

        experience_plan = ExperiencePlan(
            daily_plans=daily_plans,
            stay_area_guidance=stay_area_guidance,
            decision_summary=decision_summary,
            implementation_gaps=implementation_gaps,
            readiness_checklist=readiness_checklist,
            route_feasibility_context=route_feasibility_context,
            provider_coverage=planning_state.provider_coverage.model_copy(),
            assumptions=assumptions,
            confidence=0.35 if has_any_attraction_candidates else 0.0,
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


# Section 193C (docs/14_backend_architecture.md section 143): AI-guided
# day grouping. LLM #2 (Section 193B) may only ever cite a `candidate_id`
# already present in the exact same provider-backed, quality-approved
# candidate pool this service already computes for its own deterministic
# path (`scheduling_candidate_pois`/quality-filtered restaurants) --
# `_build_ai_candidate_reverse_index` maps `candidate_id ->
# (category, poi dict)` using `build_candidate_id`, the single formula
# also used by `AIItineraryReasoningRequestBuilder` (Section 193A), so a
# candidate_id always resolves to the same real place both places agree
# on. Nothing here ever constructs a place from AI text -- resolution
# either finds a real poi dict already in this pool, or the whole
# AI-guided attempt is abandoned (never a partially-trusted result, never
# a placeholder stop).


def _build_ai_candidate_reverse_index(
    scheduling_candidate_pois: list[dict[str, Any]],
    scheduling_candidate_restaurants: list[dict[str, Any]],
) -> dict[str, tuple[ItineraryReasoningCategory, dict[str, Any]]]:
    index: dict[str, tuple[ItineraryReasoningCategory, dict[str, Any]]] = {}
    for poi in scheduling_candidate_pois:
        candidate_id = build_candidate_id(
            str(poi.get("source") or "unknown_provider"), str(poi.get("place_id") or "")
        )
        index.setdefault(candidate_id, (ItineraryReasoningCategory.ATTRACTION, poi))
    for poi in scheduling_candidate_restaurants:
        candidate_id = build_candidate_id(
            str(poi.get("source") or "unknown_provider"), str(poi.get("place_id") or "")
        )
        index.setdefault(candidate_id, (ItineraryReasoningCategory.RESTAURANT, poi))
    return index


def _resolve_ai_guided_day_groups(
    reasoning_result: AIItineraryReasoningResult | None,
    candidate_reverse_index: dict[str, tuple[ItineraryReasoningCategory, dict[str, Any]]],
    num_days: int,
    max_per_day: int,
) -> tuple[list[list[dict[str, Any]]] | None, dict[int, list[dict[str, Any]]]]:
    """Resolves a Section 193B `AIItineraryReasoningResult` into the exact
    same `(day_groups, restaurant-suggestions-by-day)` shape the
    deterministic path already produces.

    Returns `(None, {})` -- signaling the caller to fall back to the
    existing deterministic `_group_candidates_into_days` path entirely,
    never a partially-trusted AI result -- whenever:

    - `reasoning_result` is absent or not `completed` (feature disabled,
      provider not connected/rejected/failed; Task 3).
    - any `day_index` is outside `1..num_days`, or two days share one
      (Task 22/25 generation-layer defense-in-depth: Section 193B's own
      adapters already enforce this via `validate_result_against_request`
      before ever returning `completed`, but a `PlanningState` populated
      by any other path -- a future caller, a test, a bug -- must never
      be trusted blindly here either).
    - any `candidate_id` is not in `candidate_reverse_index` (Task 22:
      "never create a placeholder stop").
    - any `candidate_id` is referenced on more than one day (Task 25).
    - after all of the above, zero attractions end up scheduled across
      the whole trip (Task 10: an entirely empty AI-guided plan is never
      preferred over attempting the deterministic algorithm).

    A day's attraction list is truncated to `max_per_day` (Task 9: the
    pace-based per-day cap is a hard deterministic constraint LLM #2's
    own selection does not override), keeping the LLM's own chosen order
    for both which candidates are kept and their `stop_order` -- this
    function never re-sorts by distance or priority.
    """
    if reasoning_result is None or reasoning_result.status != AIItineraryReasoningStatus.COMPLETED:
        return None, {}

    seen_candidate_ids: set[str] = set()
    seen_day_indexes: set[int] = set()
    day_attractions: dict[int, list[dict[str, Any]]] = {}
    day_restaurants: dict[int, list[dict[str, Any]]] = {}

    for day in reasoning_result.days:
        if not (1 <= day.day_index <= num_days) or day.day_index in seen_day_indexes:
            return None, {}
        seen_day_indexes.add(day.day_index)

        attractions: list[dict[str, Any]] = []
        restaurants: list[dict[str, Any]] = []
        for candidate_id in day.candidate_ids:
            if candidate_id in seen_candidate_ids:
                return None, {}
            seen_candidate_ids.add(candidate_id)

            entry = candidate_reverse_index.get(candidate_id)
            if entry is None:
                return None, {}
            category, poi = entry
            if category == ItineraryReasoningCategory.RESTAURANT:
                restaurants.append(poi)
            else:
                attractions.append(poi)

        if attractions:
            day_attractions[day.day_index] = attractions[:max_per_day]
        if restaurants:
            day_restaurants[day.day_index] = restaurants

    if not any(day_attractions.values()):
        return None, {}

    day_groups = [day_attractions.get(day_number, []) for day_number in range(1, num_days + 1)]
    return day_groups, day_restaurants


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


# -- Section 202B.2: diversity-aware selection, balanced allocation -------------------
#
# Audit (docs/14 section 157): the old scheduler took the top-N candidates
# by tier, then filled each day with the geographically NEAREST remaining
# candidate regardless of quality, front-loaded whole days (so a short
# candidate supply left the last day empty), and let requested interests
# participate only through a name/category substring match. This section
# keeps the same inputs (quality-approved candidates, pace caps, geography)
# and adds deterministic, general rules -- no city or place names.

_LOW_VALUE_SHARE_OF_CAPACITY = 0.25  # soft cap for "diluted" categories in a general itinerary
_ART_FOCUSED_LOW_VALUE_SHARE = 0.6
_SAME_CATEGORY_REPEAT_PENALTY = 0.03
_OUTLIER_MIN_KM = 8.0
_OUTLIER_MEDIAN_FACTOR = 2.5
_CLUSTER_CELL_DEGREES = 0.02  # ~2 km grid for structurally tagged sub-feature complexes


@dataclass
class _CandidateProfile:
    score: float
    tier_rank: int
    primary: str
    categories: frozenset[str]
    low_value: bool
    commercial_gallery: bool
    sub_feature_cluster: str | None
    matched_interests: list[str]
    tier: str | None
    # Section 202C.1A
    object_kind: str | None = None
    notable_object: bool = False


def _candidate_profile(
    poi: dict[str, Any],
    score: CandidateQualityScore | None,
    canonical_interests: list[str],
) -> _CandidateProfile:
    classification = taxonomy.classify_candidate(poi)
    matched = taxonomy.matched_interests(classification, canonical_interests)
    point = _poi_coordinates(poi)
    cluster = None
    if classification.sub_feature_kind and point is not None:
        cluster = (
            f"{classification.sub_feature_kind}:{round(point.lat / _CLUSTER_CELL_DEGREES)}:"
            f"{round(point.lng / _CLUSTER_CELL_DEGREES)}"
        )
    rank, total = _quality_rank_and_score(score)
    if score is None and poi.get("quality_tier"):
        rank = {"primary_anchor": 4, "good_candidate": 3, "secondary_candidate": 2}.get(
            str(poi["quality_tier"]), rank
        )
        total = 0.6
    return _CandidateProfile(
        score=total,
        tier_rank=rank,
        primary=classification.primary_category,
        categories=frozenset(classification.categories),
        low_value=classification.low_value,
        commercial_gallery=classification.commercial_gallery,
        sub_feature_cluster=cluster,
        matched_interests=matched,
        object_kind=classification.object_kind,
        notable_object=classification.notable_object,
        tier=(
            score.quality_tier.value
            if score is not None
            else (str(poi["quality_tier"]) if poi.get("quality_tier") else None)
        ),
    )


def _select_diverse_scheduling_set(
    pool: list[dict[str, Any]],
    profiles: dict[int, _CandidateProfile],
    capacity: int,
    canonical_interests: list[str],
    must_visit_ids: set[int],
) -> list[dict[str, Any]]:
    """Deterministically chooses up to `capacity` candidates from the
    quality-eligible `pool`:

    1. grounded must-visit candidates;
    2. requested-interest coverage: for each requested interest that has at
       least one eligible matching candidate, the best-ranked match (a
       requested interest with no eligible supply is simply skipped -- no
       place is ever fabricated for it);
    3. remaining slots by quality, with diversity pressure: "diluted"
       candidates (small memorial/sculpture objects, commercial galleries
       for a non-art request, and repeated sub-features of one tagged
       complex) may fill slots only up to a soft share of the capacity
       while non-diluted alternatives remain, and a repeated primary
       category pays a small penalty;
    4. an isolated geographic outlier is replaced by the best remaining
       non-diluted candidate when one exists.
    """
    if capacity <= 0 or not pool:
        return []

    def base_key(poi: dict[str, Any]) -> tuple[int, float]:
        profile = profiles[id(poi)]
        return (profile.tier_rank, profile.score)

    ranked = sorted(pool, key=base_key, reverse=True)  # stable: provider order breaks ties
    art_focused = "art" in canonical_interests
    diluted_cap = max(
        1,
        int(capacity * (_ART_FOCUSED_LOW_VALUE_SHARE if art_focused else _LOW_VALUE_SHARE_OF_CAPACITY)),
    )

    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    selected_clusters: set[str] = set()

    def is_diluted(poi: dict[str, Any]) -> bool:
        profile = profiles[id(poi)]
        if profile.sub_feature_cluster is not None and profile.sub_feature_cluster in selected_clusters:
            return True
        if profile.low_value:
            # Section 202C.1A: art-focused trips may keep artworks.
            return not (art_focused and profile.object_kind == "artwork")
        # Documented (notable) objects are NOT diluted: their score cap already
        # ranks them below real attractions, so they fill slots only after
        # stronger candidates run out (a hard share cap would push far-away
        # areas into a compact-city plan).
        return profile.commercial_gallery and not art_focused

    def take(poi: dict[str, Any]) -> None:
        selected.append(poi)
        selected_ids.add(id(poi))
        cluster = profiles[id(poi)].sub_feature_cluster
        if cluster is not None:
            selected_clusters.add(cluster)

    for poi in ranked:
        if len(selected) >= capacity:
            break
        if id(poi) in must_visit_ids:
            take(poi)

    for interest in canonical_interests:
        if len(selected) >= capacity:
            break
        if any(interest in profiles[id(p)].matched_interests for p in selected):
            continue
        matches = [
            p for p in ranked if id(p) not in selected_ids and interest in profiles[id(p)].matched_interests
        ]
        # Prefer a non-diluted match; a diluted one only if nothing else serves the interest.
        chosen = next((p for p in matches if not is_diluted(p)), matches[0] if matches else None)
        if chosen is not None:
            take(chosen)

    def is_junk(poi: dict[str, Any]) -> bool:
        profile = profiles[id(poi)]
        return profile.low_value and not (art_focused and profile.object_kind == "artwork")

    while len(selected) < capacity:
        diluted_count = sum(1 for p in selected if profiles[id(p)].low_value or (
            profiles[id(p)].commercial_gallery and not art_focused))
        remaining = [p for p in ranked if id(p) not in selected_ids]
        # Section 202C.1A (sparse supply): low-value single objects
        # (plaques, plain statues, ...) may never exceed the diluted share
        # of the itinerary, even when nothing better remains -- the day is
        # left lighter and the validator reports the real supply limit
        # instead of padding with junk. Must-visits were taken above.
        junk_count = sum(1 for p in selected if is_junk(p))
        if junk_count >= diluted_cap:
            remaining = [p for p in remaining if not is_junk(p)]
        if not remaining:
            break
        # Parent/child collapse (Task 5): a second sub-feature of an
        # already-selected structurally tagged complex is never scheduled --
        # the day is left lighter rather than repeating one attraction.
        no_repeat = [
            p for p in remaining
            if not (profiles[id(p)].sub_feature_cluster is not None
                    and profiles[id(p)].sub_feature_cluster in selected_clusters)
        ]
        remaining = no_repeat  # siblings are never scheduled: same attraction, not a new place
        if not remaining:
            break
        non_diluted = [p for p in remaining if not is_diluted(p)]
        if diluted_count >= diluted_cap and non_diluted:
            pool_for_pick = non_diluted
        else:
            pool_for_pick = remaining

        def adjusted(p: dict[str, Any]) -> tuple[float, int]:
            profile = profiles[id(p)]
            repeats = sum(1 for q in selected if profiles[id(q)].primary == profile.primary)
            penalty = _SAME_CATEGORY_REPEAT_PENALTY * repeats
            if is_diluted(p):
                penalty += 0.05
            return (profile.tier_rank * 1.0 + profile.score - penalty, -ranked.index(p))

        take(max(pool_for_pick, key=adjusted))

    return _replace_isolated_outliers(selected, ranked, selected_ids, profiles, is_diluted, canonical_interests)


def _replace_isolated_outliers(
    selected: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    selected_ids: set[int],
    profiles: dict[int, _CandidateProfile],
    is_diluted: Any,
    canonical_interests: list[str],
) -> list[dict[str, Any]]:
    points = [(poi, _poi_coordinates(poi)) for poi in selected]
    coords = [pt for _, pt in points if pt is not None]
    if len(coords) < 4:
        return selected
    center = GeoPoint(
        lat=statistics.median(pt.lat for pt in coords), lng=statistics.median(pt.lng for pt in coords)
    )
    distances = {id(poi): haversine_distance_km(center, pt) for poi, pt in points if pt is not None}
    median_distance = statistics.median(distances.values())
    threshold = max(_OUTLIER_MIN_KM, _OUTLIER_MEDIAN_FACTOR * median_distance)

    result = list(selected)
    used = set(selected_ids)
    for poi in sorted(selected, key=lambda p: -distances.get(id(p), 0.0)):
        if distances.get(id(poi), 0.0) <= threshold:
            break
        # Never drop the only selected place that serves a requested interest.
        sole_cover = any(
            interest in profiles[id(poi)].matched_interests
            and not any(
                interest in profiles[id(other)].matched_interests for other in result if other is not poi
            )
            for interest in canonical_interests
        )
        if sole_cover:
            continue
        replacement = next(
            (
                p
                for p in ranked
                if id(p) not in used
                and not is_diluted(p)
                and (pt := _poi_coordinates(p)) is not None
                and haversine_distance_km(center, pt) <= threshold
            ),
            None,
        )
        if replacement is None:
            continue
        result[result.index(poi)] = replacement
        used.add(id(replacement))
    return result


def _group_candidates_into_balanced_days(
    selected: list[dict[str, Any]], num_days: int, max_per_day: int
) -> list[list[dict[str, Any]]]:
    """Balanced, geographically grouped allocation (Task 21). The selected
    set is spread as evenly as the pace cap allows (no whole-day
    front-loading, so a short supply never leaves the FINAL day empty while
    earlier days are full); within that size plan, each day is an anchor
    (best-ranked remaining) plus its nearest remaining coordinate-backed
    candidates -- the same geographic rule as `_group_candidates_into_days`.
    Deterministic; never shuffles."""
    total = min(len(selected), num_days * max_per_day)
    if num_days <= 0:
        return []
    base, extra = divmod(total, num_days)
    sizes = [min(max_per_day, base + (1 if index < extra else 0)) for index in range(num_days)]

    remaining = list(selected[:total])
    day_groups: list[list[dict[str, Any]]] = []
    for size in sizes:
        if size <= 0 or not remaining:
            day_groups.append([])
            continue
        anchor = remaining.pop(0)
        group = [anchor]
        anchor_point = _poi_coordinates(anchor)
        slots = size - 1
        if slots > 0 and anchor_point is not None:
            with_coords = [(p, pt) for p in remaining if (pt := _poi_coordinates(p)) is not None]
            with_coords.sort(key=lambda item: haversine_distance_km(anchor_point, item[1]))
            chosen = [p for p, _ in with_coords[:slots]]
        else:
            chosen = remaining[:slots]
        chosen_ids = {id(p) for p in chosen}
        group.extend(chosen)
        remaining = [p for p in remaining if id(p) not in chosen_ids]
        # Not enough coordinate-backed neighbours: fall back to priority order.
        while len(group) < size and remaining:
            group.append(remaining.pop(0))
        day_groups.append(group)
    return day_groups


def _fill_empty_days(
    day_groups: list[list[dict[str, Any]]],
    pool: list[dict[str, Any]],
    profiles: dict[int, _CandidateProfile],
) -> list[list[dict[str, Any]]]:
    """Task 22: an EMPTY day is filled with the best-ranked unused
    quality-eligible candidate (never a rejected/low-priority one, never a
    duplicate). Used for AI-guided groupings, which may leave a day empty
    while eligible candidates remain. With no eligible candidate left the
    day stays empty (the validator then reports the real limitation)."""
    used = {id(p) for group in day_groups for p in group}
    unused = sorted(
        (p for p in pool if id(p) not in used),
        key=lambda p: (profiles[id(p)].tier_rank, profiles[id(p)].score),
        reverse=True,
    )
    filled = [list(group) for group in day_groups]
    for index, group in enumerate(filled):
        if group or not unused:
            continue
        filled[index] = [unused.pop(0)]
    return filled


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
    quality_lookup: dict[int, CandidateQualityScore] | None = None,
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

    Step 156C: `rejected`-quality candidates (per `quality_lookup`) are
    excluded from consideration, and when two candidates sit at a similar
    distance from the anchor, the higher-quality one is preferred -- this
    never treats a restaurant as a confirmed pick or adds a rating/price/
    reservation/opening-hour claim. If `quality_lookup` is empty
    (e.g. no `candidate_quality_report`), ordering falls back to plain
    nearest-distance exactly as before.
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

    quality_lookup = quality_lookup or {}
    with_coords: list[tuple[dict[str, Any], GeoPoint]] = []
    for restaurant in candidate_restaurants:
        score = quality_lookup.get(id(restaurant))
        if score is not None and score.quality_tier == CandidateQualityTier.REJECTED:
            continue
        point = _poi_coordinates(restaurant)
        if point is not None:
            with_coords.append((restaurant, point))

    if not with_coords:
        warnings.append(_NO_COORDINATE_BACKED_RESTAURANTS_WARNING)
        return []

    if quality_lookup:
        with_coords.sort(
            key=lambda item: _distance_and_quality_sort_key(
                haversine_distance_km(anchor_point, item[1]), quality_lookup.get(id(item[0]))
            )
        )
    else:
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
    quality_lookup: dict[int, CandidateQualityScore] | None = None,
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

    Step 156C: `rejected`-quality candidates (per `quality_lookup`) are
    excluded from consideration, and when two candidates sit at a similar
    distance from the anchor, the higher-quality one is preferred -- these
    remain open-data location candidates only, never bookable inventory. If
    `quality_lookup` is empty (e.g. no `candidate_quality_report`), ordering
    falls back to plain nearest-distance exactly as before.
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

    quality_lookup = quality_lookup or {}
    with_coords: list[tuple[dict[str, Any], GeoPoint]] = []
    for accommodation in candidate_accommodation_pois:
        score = quality_lookup.get(id(accommodation))
        if score is not None and score.quality_tier == CandidateQualityTier.REJECTED:
            continue
        point = _poi_coordinates(accommodation)
        if point is not None:
            with_coords.append((accommodation, point))

    if not with_coords:
        warnings.append(_NO_COORDINATE_BACKED_ACCOMMODATIONS_WARNING)
        return []

    if quality_lookup:
        with_coords.sort(
            key=lambda item: _distance_and_quality_sort_key(
                haversine_distance_km(anchor_point, item[1]), quality_lookup.get(id(item[0]))
            )
        )
    else:
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
    quality_lookup: dict[int, CandidateQualityScore] | None = None,
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

    Step 156C: `rejected`-quality candidates (per `quality_lookup`) are
    excluded from consideration, and when two candidates sit at a similar
    average distance, the higher-quality one is preferred. If
    `quality_lookup` is empty (e.g. no `candidate_quality_report`), ranking
    falls back to plain average-distance exactly as before.
    """
    quality_lookup = quality_lookup or {}
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
        score = quality_lookup.get(id(accommodation))
        if score is not None and score.quality_tier == CandidateQualityTier.REJECTED:
            continue
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

    if quality_lookup:
        scored.sort(
            key=lambda item: _distance_and_quality_sort_key(
                item[0], quality_lookup.get(id(item[1]))
            )
        )
    else:
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


def _provider_missing_explanation(
    provider_label: str, data_label: str, coverage_value: str | None
) -> str:
    """Plain-language explanation of why `provider_label` can't supply
    `data_label` yet, accurately distinguishing what `coverage_value`
    actually means instead of always saying "not connected":

    - `not_connected` (or unset): no provider is connected at all.
    - `failed`: a provider was reached, but the request failed or returned
      no usable data -- different from no provider being connected.
    - `unavailable`: a provider was reached and responded, but reported no
      usable data for this request.
    - `fallback_used`: a fallback result exists, but the check this
      explanation is attached to still isn't implemented, so that fallback
      data still needs review.

    Used only to explain why a check is `missing_data`/`not_implemented`;
    never invents a price, rating, review, opening hour, booking,
    availability, route, walking, or cost value.
    """
    if coverage_value == "failed":
        return f"The {provider_label} request failed or returned no usable {data_label}"
    if coverage_value == "unavailable":
        return f"The {provider_label} returned no usable {data_label}"
    if coverage_value == "fallback_used":
        return f"The {provider_label} returned fallback {data_label}, which still needs review"
    # not_connected, None, or any other not-yet-connected value.
    return f"A {provider_label} is not connected"


def _build_implementation_gaps(planning_state: PlanningState) -> ImplementationGaps:
    """Plan-level summary of what data is connected now, what is missing,
    what provider would be needed next, and why the plan is still Needs
    Review -- built purely from `planning_state.provider_coverage` and
    `planning_state.provider_status`. No provider call, no AI/LLM, no
    invented fact.
    """
    coverage = planning_state.provider_coverage

    connected_data: list[str] = []
    missing_data: list[str] = []
    next_data_needed: list[str] = []

    if coverage.places in _CONNECTED_COVERAGE_STATUSES:
        connected_data.append(
            "Attractions are connected: provider-backed candidate_pois came "
            f"from a real places provider (coverage: places={coverage.places})."
        )

    if coverage.restaurants in {"success", "fallback_used", "partial"}:
        connected_data.append(
            "Restaurants are connected: provider-backed candidate_restaurants "
            f"came from a real places provider (coverage: "
            f"restaurants={coverage.restaurants})."
        )

    if coverage.accommodations in _CONNECTED_COVERAGE_STATUSES:
        connected_data.append(
            "Accommodation POIs are connected: provider-backed "
            "candidate_accommodation_pois came from an open-data places "
            f"provider (coverage: accommodations={coverage.accommodations}), "
            "not a booking-capable accommodation provider."
        )

    if coverage.routes not in _CONNECTED_COVERAGE_STATUSES:
        missing_data.append(
            f"{_provider_missing_explanation('routes/transit provider', 'route/transit data', coverage.routes)} "
            f"(coverage: routes={coverage.routes})."
        )
        next_data_needed.append(
            "A routes provider is needed for route time and walking feasibility."
        )

    accommodation_provider_status = planning_state.provider_status.get(
        "accommodation_provider:accommodations"
    )
    accommodation_provider_coverage_value = (
        accommodation_provider_status.status.value
        if accommodation_provider_status is not None
        else "not_connected"
    )
    accommodation_provider_connected = (
        accommodation_provider_coverage_value in _CONNECTED_COVERAGE_STATUSES
    )
    if not accommodation_provider_connected:
        missing_data.append(
            f"{_provider_missing_explanation('booking-capable accommodation provider', 'accommodation data', accommodation_provider_coverage_value)}, "
            "so no hotel/accommodation price, availability, rating, or booking link "
            "is available."
        )
        next_data_needed.append(
            "An accommodation provider is needed for prices, availability, "
            "and booking links."
        )

    if coverage.hotel_prices not in _CONNECTED_COVERAGE_STATUSES:
        missing_data.append(
            f"{_provider_missing_explanation('hotel prices provider', 'hotel price data', coverage.hotel_prices)} "
            f"(coverage: hotel_prices={coverage.hotel_prices})."
        )

    if (
        coverage.vacation_rentals not in _CONNECTED_COVERAGE_STATUSES
        and coverage.airbnb not in _CONNECTED_COVERAGE_STATUSES
    ):
        missing_data.append(
            f"{_provider_missing_explanation('vacation rentals/Airbnb-style listings provider', 'vacation rental/Airbnb data', coverage.vacation_rentals)} "
            f"(coverage: vacation_rentals={coverage.vacation_rentals}, "
            f"airbnb={coverage.airbnb})."
        )

    if coverage.weather in _CONNECTED_COVERAGE_STATUSES:
        connected_data.append(
            "Weather is connected: provider-backed daily forecast data came from a "
            f"real weather provider (coverage: weather={coverage.weather})."
        )
    else:
        missing_data.append(
            f"{_provider_missing_explanation('weather provider', 'weather data', coverage.weather)} "
            f"(coverage: weather={coverage.weather})."
        )
        next_data_needed.append(
            "A weather provider is needed for weather-aware planning."
        )

    if coverage.holidays in _CONNECTED_COVERAGE_STATUSES:
        connected_data.append(
            "Holidays are connected: provider-backed public holiday data came from a "
            f"real holidays provider (coverage: holidays={coverage.holidays})."
        )
    else:
        missing_data.append(
            f"{_provider_missing_explanation('holidays provider', 'holiday data', coverage.holidays)} "
            f"(coverage: holidays={coverage.holidays})."
        )
        next_data_needed.append(
            "A holidays provider is needed for closure/crowd-risk context."
        )

    if coverage.currency in _CONNECTED_COVERAGE_STATUSES:
        connected_data.append(
            "Currency is connected: provider-backed exchange-rate data came from a "
            f"real currency provider (coverage: currency={coverage.currency}), but this "
            "is a single-unit exchange rate only, not a calculated total trip cost or "
            "budget fit."
        )
    else:
        missing_data.append(
            f"{_provider_missing_explanation('currency provider', 'currency conversion data', coverage.currency)} "
            f"(coverage: currency={coverage.currency})."
        )
        next_data_needed.append(
            "A currency provider is needed for budget conversion."
        )

    summary = (
        f"{len(connected_data)} data area"
        f"{'s' if len(connected_data) != 1 else ''} "
        f"{'are' if len(connected_data) != 1 else 'is'} connected to a real "
        f"provider; {len(missing_data)} data area"
        f"{'s' if len(missing_data) != 1 else ''} "
        f"{'are' if len(missing_data) != 1 else 'is'} still missing, which is "
        "why this plan stays Needs Review."
    )

    return ImplementationGaps(
        summary=summary,
        connected_data=connected_data,
        missing_data=missing_data,
        next_data_needed=next_data_needed,
        why_needs_review=list(_IMPLEMENTATION_GAPS_WHY_NEEDS_REVIEW),
    )


def _candidate_availability_status(candidates_exist: bool) -> ChecklistItemStatus:
    """Status for a "candidates available" checklist item.

    This is a data-availability check, not a travel-readiness check:
    `checked` whenever provider-backed candidates exist at all, regardless
    of coverage tier (full success, partial, or fallback -- all mean a real
    provider actually returned usable candidates). `missing_data` only when
    no candidates exist at all, since then nothing was actually returned to
    check.
    """
    return ChecklistItemStatus.CHECKED if candidates_exist else ChecklistItemStatus.MISSING_DATA


def _not_yet_checked_status(connected: bool) -> ChecklistItemStatus:
    """Status for a checklist item describing a check this app never runs.

    `missing_data` when the provider that would supply the underlying data
    is not connected; `not_implemented` when a provider is connected but the
    app still has no code path that performs this check.
    """
    return ChecklistItemStatus.MISSING_DATA if not connected else ChecklistItemStatus.NOT_IMPLEMENTED


def _build_readiness_checklist(
    planning_state: PlanningState,
    candidate_pois: list[dict[str, Any]],
    candidate_restaurants: list[dict[str, Any]],
    candidate_accommodation_pois: list[dict[str, Any]],
) -> ReadinessChecklist:
    """Plan-level checklist of what has and has not been validated yet,
    built purely from `planning_state.provider_coverage`,
    `planning_state.provider_status`, and the candidate lists already
    computed for this `ExperiencePlan` -- no provider call, no AI/LLM, no
    invented fact. This never marks the plan ready by itself;
    `ValidationReport.readiness_status` remains the single source of truth.
    """
    coverage = planning_state.provider_coverage

    accommodation_provider_status = planning_state.provider_status.get(
        "accommodation_provider:accommodations"
    )
    accommodation_provider_coverage_value = (
        accommodation_provider_status.status.value
        if accommodation_provider_status is not None
        else "not_connected"
    )
    accommodation_provider_connected = (
        accommodation_provider_coverage_value in _CONNECTED_COVERAGE_STATUSES
    )

    items: list[ReadinessChecklistItem] = []

    places_status = _candidate_availability_status(bool(candidate_pois))
    items.append(
        ReadinessChecklistItem(
            label="Provider-backed attractions available",
            status=places_status,
            explanation=(
                f"{len(candidate_pois)} attraction candidate(s) came from a real "
                f"places provider (coverage: places={coverage.places})."
                if places_status == ChecklistItemStatus.CHECKED
                else (
                    f"No provider-backed attraction candidates are available yet "
                    f"(coverage: places={coverage.places})."
                )
            ),
        )
    )

    restaurants_status = _candidate_availability_status(bool(candidate_restaurants))
    items.append(
        ReadinessChecklistItem(
            label="Restaurant candidates available",
            status=restaurants_status,
            explanation=(
                f"{len(candidate_restaurants)} restaurant candidate(s) came from a "
                f"real places provider (coverage: restaurants={coverage.restaurants})."
                if restaurants_status == ChecklistItemStatus.CHECKED
                else (
                    f"No provider-backed restaurant candidates are available yet "
                    f"(coverage: restaurants={coverage.restaurants})."
                )
            ),
        )
    )

    accommodations_status = _candidate_availability_status(bool(candidate_accommodation_pois))
    items.append(
        ReadinessChecklistItem(
            label="Accommodation POI candidates available",
            status=accommodations_status,
            explanation=(
                f"{len(candidate_accommodation_pois)} accommodation POI candidate(s) "
                "came from an open-data places provider (coverage: "
                f"accommodations={coverage.accommodations}); these are open-data "
                "location candidates only, not bookable inventory."
                if accommodations_status == ChecklistItemStatus.CHECKED
                else (
                    "No provider-backed accommodation POI candidates are available yet "
                    f"(coverage: accommodations={coverage.accommodations})."
                )
            ),
        )
    )

    routes_connected = coverage.routes in _CONNECTED_COVERAGE_STATUSES
    route_times_status = _not_yet_checked_status(routes_connected)
    items.append(
        ReadinessChecklistItem(
            label="Route times checked",
            status=route_times_status,
            explanation=(
                f"{_provider_missing_explanation('routes provider', 'route data', coverage.routes)}, "
                "so route time cannot be checked."
                if route_times_status == ChecklistItemStatus.MISSING_DATA
                else "Route ordering and timing are not implemented yet, so route time is not checked."
            ),
        )
    )

    items.append(
        ReadinessChecklistItem(
            label="Opening hours checked",
            status=ChecklistItemStatus.NOT_IMPLEMENTED,
            explanation=(
                "Checking scheduled attractions against opening hours is not "
                "implemented yet, so opening hours are not checked."
            ),
        )
    )

    walking_status = _not_yet_checked_status(routes_connected)
    items.append(
        ReadinessChecklistItem(
            label="Walking feasibility checked",
            status=walking_status,
            explanation=(
                f"{_provider_missing_explanation('routes provider', 'route data', coverage.routes)}, "
                "so walking feasibility cannot be checked."
                if walking_status == ChecklistItemStatus.MISSING_DATA
                else "Walking-distance feasibility is not implemented yet, so it is not checked."
            ),
        )
    )

    items.append(
        ReadinessChecklistItem(
            label="Budget/cost fit checked",
            status=ChecklistItemStatus.NOT_IMPLEMENTED,
            explanation=(
                "Calculating estimated costs and checking them against the traveler's "
                "budget is not implemented yet, so budget/cost fit is not checked."
            ),
        )
    )

    accommodation_price_status = _not_yet_checked_status(accommodation_provider_connected)
    items.append(
        ReadinessChecklistItem(
            label="Accommodation price/availability checked",
            status=accommodation_price_status,
            explanation=(
                f"{_provider_missing_explanation('booking-capable accommodation provider', 'accommodation data', accommodation_provider_coverage_value)}, "
                "so accommodation price and availability cannot be checked."
                if accommodation_price_status == ChecklistItemStatus.MISSING_DATA
                else (
                    "Accommodation price/availability checking is not implemented yet, "
                    "so it is not checked."
                )
            ),
        )
    )

    # Unlike the other "not yet checked" items, a connected weather provider
    # means real daily forecast data does exist -- so this is NEEDS_REVIEW
    # (data available, but weather-aware itinerary adjustment such as
    # rerouting or rescheduling around rain is not implemented yet), not
    # NOT_IMPLEMENTED. It still never becomes CHECKED: having weather data
    # is not the same as having used it to validate/adjust the plan.
    weather_connected = coverage.weather in _CONNECTED_COVERAGE_STATUSES
    if weather_connected:
        weather_status = ChecklistItemStatus.NEEDS_REVIEW
        weather_explanation = (
            "Provider-backed daily forecast data is available (coverage: "
            f"weather={coverage.weather}), but weather-aware itinerary adjustment "
            "(e.g. rerouting or rescheduling around rain) is not implemented yet, so "
            "weather impact is not checked."
        )
    else:
        weather_status = ChecklistItemStatus.MISSING_DATA
        weather_explanation = (
            f"{_provider_missing_explanation('weather provider', 'weather data', coverage.weather)}, "
            "so weather impact cannot be checked."
        )
    items.append(
        ReadinessChecklistItem(
            label="Weather impact checked",
            status=weather_status,
            explanation=weather_explanation,
        )
    )

    # Unlike a plain "not yet checked" item, a connected holidays provider
    # means real public holiday data does exist -- so this is NEEDS_REVIEW
    # (data available, but interpreting it against venue closures/crowd risk
    # is not implemented yet), not NOT_IMPLEMENTED. It still never becomes
    # CHECKED: having holiday data is not the same as having used it to
    # check closures or crowd risk.
    holidays_connected = coverage.holidays in _CONNECTED_COVERAGE_STATUSES
    if holidays_connected:
        holidays_status = ChecklistItemStatus.NEEDS_REVIEW
        holidays_explanation = (
            "Provider-backed public holiday data is available (coverage: "
            f"holidays={coverage.holidays}), but checking it against venue closures "
            "or crowd risk is not implemented yet, so holiday/closure context is not "
            "checked."
        )
    else:
        holidays_status = ChecklistItemStatus.MISSING_DATA
        holidays_explanation = (
            f"{_provider_missing_explanation('holidays provider', 'holiday data', coverage.holidays)}, "
            "so holiday/closure context cannot be checked."
        )
    items.append(
        ReadinessChecklistItem(
            label="Holiday/closure context checked",
            status=holidays_status,
            explanation=holidays_explanation,
        )
    )

    booking_links_status = _not_yet_checked_status(accommodation_provider_connected)
    items.append(
        ReadinessChecklistItem(
            label="Booking links available",
            status=booking_links_status,
            explanation=(
                f"{_provider_missing_explanation('booking-capable accommodation provider', 'accommodation data', accommodation_provider_coverage_value)}, "
                "so no booking links are available."
                if booking_links_status == ChecklistItemStatus.MISSING_DATA
                else "Booking link generation is not implemented yet, so none is available."
            ),
        )
    )

    checked_count = sum(1 for item in items if item.status == ChecklistItemStatus.CHECKED)
    summary = (
        f"{checked_count} of {len(items)} checklist items are checked. This checklist "
        "never marks the plan ready by itself -- see the validation report for overall "
        "readiness."
    )

    return ReadinessChecklist(summary=summary, items=items)


def _build_route_feasibility_context() -> RouteFeasibilityContext:
    """Plan-level route/walking feasibility context -- a data-model
    foundation only, so the app is ready for a real RoutesProvider later
    (docs/12_provider_architecture.md section 12). No routes provider is
    connected in this deployment, so `daily_route_feasibility` always
    stays empty: no straight-line distance is ever presented as route
    distance, no walking time is ever calculated, and no travel mode is
    ever inferred. This never marks route times or walking feasibility as
    checked in `readiness_checklist`, and never affects validation
    readiness by itself.
    """
    return RouteFeasibilityContext(
        source="routes_provider",
        data_status=DataStatus.NOT_CONNECTED,
        confidence=0.0,
        daily_route_feasibility=[],
        assumptions=[
            "Route feasibility is a data-model foundation only; no route provider is "
            "connected yet, so no real route time, walking distance, transit time, "
            "driving time, or feasibility score has been calculated."
        ],
        warnings=[
            "Route feasibility is not available because no route provider is connected."
        ],
    )


def _build_experience_item(
    poi: dict[str, Any],
    must_visit_ids: set[int],
    interest_ids: set[int],
    trip_id: str | None = None,
    used_experience_ids: set[str] | None = None,
    profile: "_CandidateProfile | None" = None,
) -> ExperienceItem:
    name = poi.get("name") or ""
    coordinates = _poi_coordinates(poi)
    confidence = float(poi.get("confidence") or 0.0)
    data_status_value = poi.get("data_status") or DataStatus.LIVE.value
    # Step 170D: set only on a dict built by `_promoted_candidate_to_poi_dict`
    # -- never on a real `destination_context.candidate_pois` entry.
    promoted_from_ai = bool(poi.get("promoted_from_ai"))

    if id(poi) in must_visit_ids:
        why_included = "Matches your must-visit request."
    elif id(poi) in interest_ids:
        why_included = (
            "Matches your interests based on this candidate's provider-backed "
            "name, category, and address."
        )
    elif promoted_from_ai:
        why_included = (
            "Promoted from an AI-proposed candidate that was independently "
            "provider-grounded and quality-approved (see "
            "ai_candidate_promotion_report)."
        )
    else:
        why_included = "Selected from provider-backed attraction candidates."

    source = poi.get("source")
    source_type = (
        ClaimSourceType.OPEN_DATA_FACT
        if source and "openstreetmap" in source
        else ClaimSourceType.PROVIDER_FACT
    )

    if promoted_from_ai:
        claim = (
            f"{name} is a real, provider-grounded place promoted from an AI-proposed "
            "candidate via ai_candidate_promotion_report."
        )
        based_on = ["ai_candidate_promotion_report.promoted_candidates"]
    else:
        claim = f"{name} is a real place from destination_context.candidate_pois."
        based_on = ["destination_context.candidate_pois"]

    # Section 202B.1 (Tasks 5/6): every provider-backed place carries its
    # stable provider identity (not only AI-promoted ones), and its
    # `experience_id` is derived from it -- see `experience_identity`.
    provider_place_id = poi.get("provider_place_id") or poi.get("place_id")
    provider_source = poi.get("provider_source") or poi.get("source")
    identity_kwargs: dict[str, Any] = {}
    if provider_place_id and provider_source:
        identity_kwargs["provider_place_id"] = str(provider_place_id)
        identity_kwargs["provider_source"] = str(provider_source)
        stable_id = (
            deterministic_experience_id(trip_id, provider_source, str(provider_place_id))
            if trip_id
            else None
        )
        # Never emit two items with the same id in one plan; a repeat
        # (which the planner's own de-duplication should already prevent)
        # falls back to a random id rather than colliding.
        if stable_id and (used_experience_ids is None or stable_id not in used_experience_ids):
            identity_kwargs["experience_id"] = stable_id
            if used_experience_ids is not None:
                used_experience_ids.add(stable_id)

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
                claim=claim,
                source_type=source_type,
                source=source,
                based_on=based_on,
            )
        ],
        promoted_from_ai=promoted_from_ai,
        original_ai_candidate_id=poi.get("original_ai_candidate_id") if promoted_from_ai else None,
        normalized_category=profile.primary if profile is not None else None,
        matched_interests=list(profile.matched_interests) if profile is not None else [],
        quality_tier=profile.tier if profile is not None else None,
        low_value_object=profile.low_value if profile is not None else False,
        notable_object=profile.notable_object if profile is not None else False,
        commercial_gallery=profile.commercial_gallery if profile is not None else False,
        **identity_kwargs,
    )
