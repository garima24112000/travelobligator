from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningRequest,
    CandidateOrigin,
    FactualContextSummary,
    ItineraryCandidateReference,
    ItineraryReasoningCategory,
    TravelerContextSummary,
    TripStrategySummary,
    build_candidate_id,
)
from app.models.candidate_grounding import GroundedCandidate
from app.models.candidate_quality import CandidateQualityReport, CandidateQualityScore, CandidateQualityTier
from app.models.common import DataStatus, GeoPoint
from app.models.planning_state import PlanningState
from app.services.ai_candidate_promotion_eligibility_service import find_quality_score

# Section 193A (docs/14_backend_architecture.md section 141): builds the
# bounded `AIItineraryReasoningRequest` contract from existing
# `PlanningState` data -- pure, read-only, never calls a provider/LLM/
# LangGraph, never mutates `planning_state`. Nothing in the runtime
# pipeline calls this yet (Section 193B wires a real provider call;
# Section 193C consumes an accepted result) -- this module exists purely
# so the request-building logic itself is inspectable and testable ahead
# of that.
#
# `allowed_candidates` is assembled from exactly two already-verified,
# already-provider-backed sources (Task 1):
#
# 1. the broad `destination_context` pool, filtered to the same accepted
#    quality tiers `ExperiencePlannerService`/
#    `AICandidatePromotionEligibilityService` already use for scheduling
#    eligibility -- never every candidate `CandidateQualityService`
#    scored, only the ones already judged good enough to schedule.
# 2. `ai_candidate_promotion_report.promoted_candidates` -- Section
#    192/192A candidates that already cleared real provider grounding,
#    deterministic quality scoring, and promotion eligibility. A
#    promoted candidate's real `CandidateQualityScore` is looked up via
#    the exact same `find_quality_score` join
#    `AICandidatePromotionEligibilityService` itself already uses --
#    never re-derived or guessed here.
#
# Never included: an ungrounded proposal, a `discovery_query` with no
# provider match, a `NOT_SEARCHED`/provider-failure attempt, an ambiguous/
# rejected grounding result, or accommodation/flight inventory (those stay
# a coarse factual summary, never a schedulable "day candidate" --
# accommodation POIs are a stay-area concept, not an itinerary activity,
# matching how `ExperiencePlannerService` itself treats them).

_ACCEPTED_QUALITY_TIERS = {
    CandidateQualityTier.PRIMARY_ANCHOR,
    CandidateQualityTier.GOOD_CANDIDATE,
    CandidateQualityTier.SECONDARY_CANDIDATE,
}

# Task 3's tier-first, score-second, id-third deterministic sort key for
# Task 9's bounding -- never AI proposal confidence, never arbitrary
# truncation order.
_TIER_SORT_ORDER: dict[CandidateQualityTier, int] = {
    CandidateQualityTier.PRIMARY_ANCHOR: 0,
    CandidateQualityTier.GOOD_CANDIDATE: 1,
    CandidateQualityTier.SECONDARY_CANDIDATE: 2,
}

# Matches Section 192A's own classification fallback
# (`CandidateQualityService.score_provider_backed_candidate`): the only
# `AICandidateType` value that maps to the restaurant domain. Every other
# value (attraction/neighborhood/viewpoint/museum/park/
# ferry_or_waterfront/cultural_area/shopping_area/day_cluster_idea) is
# treated as the attraction domain -- the same safe generic default used
# throughout this codebase's candidate-quality integration.
_RESTAURANT_CANDIDATE_TYPE_VALUES = {"food_area"}


def _coerce_data_status(value: Any) -> DataStatus:
    if isinstance(value, DataStatus):
        return value
    if isinstance(value, str):
        try:
            return DataStatus(value)
        except ValueError:
            pass
    return DataStatus.UNAVAILABLE


def _coerce_coordinates(value: Any) -> GeoPoint | None:
    if isinstance(value, GeoPoint):
        return value
    if isinstance(value, dict):
        lat = value.get("lat")
        lng = value.get("lng")
        if lat is None or lng is None:
            return None
        try:
            return GeoPoint(lat=lat, lng=lng)
        except (TypeError, ValueError):
            return None
    return None


class AIItineraryReasoningRequestBuilder:
    """Builds a future `AIItineraryReasoningRequest` from existing
    `PlanningState` data. Read-only: never mutates `planning_state`, never
    calls a provider/LLM, and never invents a candidate, coordinate,
    price, rating, or route fact.
    """

    def build_request(self, planning_state: PlanningState) -> AIItineraryReasoningRequest:
        trip_request = planning_state.trip_request

        return AIItineraryReasoningRequest(
            trip_id=planning_state.trip_id,
            destination_name=trip_request.primary_destination,
            start_date=trip_request.start_date.isoformat(),
            end_date=trip_request.end_date.isoformat(),
            trip_duration_days=self._trip_duration_days(planning_state),
            traveler_context=self._traveler_context(planning_state),
            trip_strategy_summary=self._trip_strategy_summary(planning_state),
            factual_context=self._factual_context(planning_state),
            allowed_candidates=self._allowed_candidates(planning_state),
        )

    @staticmethod
    def _trip_duration_days(planning_state: PlanningState) -> int:
        trip_request = planning_state.trip_request
        return max(1, (trip_request.end_date - trip_request.start_date).days + 1)

    @staticmethod
    def _traveler_context(planning_state: PlanningState) -> TravelerContextSummary:
        trip_request = planning_state.trip_request
        traveler_profile = planning_state.traveler_profile

        if traveler_profile is not None:
            return TravelerContextSummary(
                travelers_count=traveler_profile.travelers_count,
                travel_group_type=traveler_profile.travel_group_type.value,
                pace=traveler_profile.pace.value,
                interests=list(traveler_profile.interests),
                must_visit=list(traveler_profile.must_visit),
                must_avoid=list(traveler_profile.must_avoid),
                constraints=list(traveler_profile.constraints),
                budget_min=trip_request.budget_min,
                budget_max=trip_request.budget_max,
                budget_currency=trip_request.budget_currency,
            )
        return TravelerContextSummary(
            travelers_count=trip_request.travelers_count,
            travel_group_type=trip_request.travel_group_type.value,
            pace=trip_request.pace.value,
            interests=list(trip_request.interests),
            must_visit=list(trip_request.must_visit),
            must_avoid=list(trip_request.must_avoid),
            constraints=list(trip_request.constraints),
            budget_min=trip_request.budget_min,
            budget_max=trip_request.budget_max,
            budget_currency=trip_request.budget_currency,
        )

    @staticmethod
    def _trip_strategy_summary(planning_state: PlanningState) -> TripStrategySummary | None:
        trip_strategy = planning_state.trip_strategy
        if trip_strategy is None:
            return None
        return TripStrategySummary(
            recommended_trip_style=trip_strategy.recommended_trip_style,
            planning_strategy=list(trip_strategy.planning_strategy),
            tradeoffs=list(trip_strategy.tradeoffs),
        )

    @staticmethod
    def _factual_context(planning_state: PlanningState) -> FactualContextSummary:
        weather_context = planning_state.weather_context
        holiday_context = planning_state.holiday_context
        currency_context = planning_state.currency_context
        accommodation_report = planning_state.accommodation_inventory_report
        flight_report = planning_state.flight_inventory_report

        return FactualContextSummary(
            weather_status=weather_context.data_status.value if weather_context else None,
            holiday_count_in_range=len(holiday_context.holidays) if holiday_context else None,
            holiday_status=holiday_context.data_status.value if holiday_context else None,
            currency_status=currency_context.data_status.value if currency_context else None,
            accommodation_inventory_status=(
                accommodation_report.status.value if accommodation_report else None
            ),
            accommodation_offer_count=(
                len(accommodation_report.offers) if accommodation_report else 0
            ),
            flight_inventory_status=flight_report.status.value if flight_report else None,
            flight_offer_count=len(flight_report.offers) if flight_report else 0,
        )

    def _allowed_candidates(self, planning_state: PlanningState) -> list[ItineraryCandidateReference]:
        candidates: list[ItineraryCandidateReference] = []
        seen_ids: set[str] = set()

        for candidate in self._broad_pool_candidates(planning_state):
            if candidate.candidate_id in seen_ids:
                continue
            seen_ids.add(candidate.candidate_id)
            candidates.append(candidate)

        for candidate in self._promoted_candidates(planning_state):
            if candidate.candidate_id in seen_ids:
                # Task 10: a promoted AI-directed candidate that turns out
                # to be the exact same real place (same provider +
                # provider id) as one already in the broad pool -- the
                # broad-pool entry (already added first, above) wins;
                # never a conflicting duplicate reference.
                continue
            seen_ids.add(candidate.candidate_id)
            candidates.append(candidate)

        return self._bounded(candidates)

    @staticmethod
    def _broad_pool_candidates(planning_state: PlanningState) -> list[ItineraryCandidateReference]:
        destination_context = planning_state.destination_context
        quality_report = planning_state.candidate_quality_report
        if destination_context is None or quality_report is None:
            return []

        pois_by_id = _index_by_place_id(destination_context.candidate_pois)
        restaurants_by_id = _index_by_place_id(destination_context.candidate_restaurants)

        candidates: list[ItineraryCandidateReference] = []
        candidates.extend(
            _references_from_scores(
                quality_report.attraction_scores,
                pois_by_id,
                ItineraryReasoningCategory.ATTRACTION,
                CandidateOrigin.BROAD_PROVIDER_DISCOVERY,
            )
        )
        candidates.extend(
            _references_from_scores(
                quality_report.restaurant_scores,
                restaurants_by_id,
                ItineraryReasoningCategory.RESTAURANT,
                CandidateOrigin.BROAD_PROVIDER_DISCOVERY,
            )
        )
        return candidates

    @staticmethod
    def _promoted_candidates(planning_state: PlanningState) -> list[ItineraryCandidateReference]:
        promotion_report = planning_state.ai_candidate_promotion_report
        quality_report = planning_state.candidate_quality_report
        grounding_batch = planning_state.candidate_grounding_batch
        if promotion_report is None or not promotion_report.promoted_candidates:
            return []

        grounded_by_proposal_id: dict[str, GroundedCandidate] = {}
        if grounding_batch is not None and grounding_batch.result is not None:
            grounded_by_proposal_id = {
                candidate.proposal_id: candidate
                for candidate in grounding_batch.result.grounded_candidates
            }

        candidates: list[ItineraryCandidateReference] = []
        for promoted in promotion_report.promoted_candidates:
            if promoted.coordinates is None or promoted.provider_place_id is None:
                # No usable coordinates/provider id -- not schedulable,
                # never a guessed location (mirrors ExperiencePlannerService's
                # own defensive skip for the exact same reason).
                continue

            score = None
            if promoted.original_ai_candidate_id is not None:
                grounded = grounded_by_proposal_id.get(promoted.original_ai_candidate_id)
                if grounded is not None:
                    score = find_quality_score(grounded, quality_report)
            if score is None:
                # Promotion itself already required a real quality score
                # to exist (Rule 4) -- this should be unreachable in
                # practice; skip defensively rather than invent one.
                continue

            provider_name = promoted.provider_source or "unknown_provider"
            candidate_id = build_candidate_id(provider_name, promoted.provider_place_id)
            category_hint = (promoted.category or "").strip().lower()
            category = (
                ItineraryReasoningCategory.RESTAURANT
                if category_hint in _RESTAURANT_CANDIDATE_TYPE_VALUES
                else ItineraryReasoningCategory.ATTRACTION
            )

            candidates.append(
                ItineraryCandidateReference(
                    candidate_id=candidate_id,
                    name=promoted.name,
                    category=category,
                    provider_name=provider_name,
                    provider_place_id=promoted.provider_place_id,
                    coordinates=promoted.coordinates,
                    data_status=_coerce_data_status(promoted.data_status),
                    quality_score=score.total_score,
                    quality_tier=score.quality_tier.value,
                    origin=CandidateOrigin.AI_DIRECTED_PROVIDER_DISCOVERY,
                )
            )
        return candidates

    def _bounded(
        self, candidates: list[ItineraryCandidateReference]
    ) -> list[ItineraryCandidateReference]:
        max_candidates = get_settings().ai_itinerary_reasoning_max_candidates
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                _TIER_SORT_ORDER.get(
                    CandidateQualityTier(candidate.quality_tier), len(_TIER_SORT_ORDER)
                ),
                -candidate.quality_score,
                candidate.candidate_id,
            ),
        )
        return ordered[:max_candidates]


def _index_by_place_id(raw_candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for raw in raw_candidates:
        place_id = raw.get("place_id")
        if isinstance(place_id, str) and place_id:
            index[place_id] = raw
    return index


def _references_from_scores(
    scores: list[CandidateQualityScore],
    raw_by_id: dict[str, dict[str, Any]],
    category: ItineraryReasoningCategory,
    origin: CandidateOrigin,
) -> list[ItineraryCandidateReference]:
    references: list[ItineraryCandidateReference] = []
    for score in scores:
        if score.quality_tier not in _ACCEPTED_QUALITY_TIERS:
            continue
        raw = raw_by_id.get(score.candidate_id)
        if raw is None:
            # A quality score with no matching raw candidate is
            # structurally unexpected (every score is built from a real
            # destination_context entry) -- skip defensively rather than
            # guess coordinates/provider identity.
            continue
        coordinates = _coerce_coordinates(raw.get("coordinates"))
        if coordinates is None:
            continue

        provider_name = str(raw.get("source") or "unknown_provider")
        provider_place_id = str(raw.get("place_id") or score.candidate_id)
        references.append(
            ItineraryCandidateReference(
                candidate_id=build_candidate_id(provider_name, provider_place_id),
                name=score.candidate_name,
                category=category,
                provider_name=provider_name,
                provider_place_id=provider_place_id,
                coordinates=coordinates,
                data_status=_coerce_data_status(raw.get("data_status")),
                quality_score=score.total_score,
                quality_tier=score.quality_tier.value,
                origin=origin,
            )
        )
    return references
