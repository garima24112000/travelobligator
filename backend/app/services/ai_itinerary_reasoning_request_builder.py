from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.models.ai_itinerary_reasoning import (
    AREA_INSTRUCTION,
    DEFAULT_ITINERARY_REASONING_INSTRUCTIONS,
    AIItineraryReasoningRequest,
    CandidateOrigin,
    FactualContextSummary,
    ItineraryAreaSummary,
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
from app.services import candidate_universe as universe
from app.services import candidate_usefulness as usefulness
from app.services import day_composition as composition
from app.services.ai_candidate_promotion_eligibility_service import find_quality_score
from app.services.grounded_anchors import grounded_anchor_place_ids
from app.services.must_visit_matching import must_visit_place_ids
from app.services.pace_targets import PACE_TARGET_PER_DAY, pace_of

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
# Q2: every reference then gets its planning signals by provider place id
# (`must_visit`, `semantic_anchor`, `provider_evidence`) and the candidate
# cap is filled in the canonical usefulness order -- see `_bounded`.
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

# Tier order of the RESTAURANT references in the bound (tier, score, then a
# neutral name/id tie-break). Attractions are ordered by the canonical
# usefulness key (`app.services.candidate_usefulness`, Q2) -- never by AI
# proposal confidence, never by arbitrary truncation order.
_FOOD_INTEREST = "food"
# How many attractions per requested interest the bound makes room for
# (one before the anchors, the second only while space remains).
_COVERAGE_CANDIDATES_PER_INTEREST = 2
# The planner shows at most this many food suggestions per day.
_MAX_RESTAURANTS_PER_DAY = 2
# Q3: how many of an area's near areas a request names (nearest first).
_MAX_NEAR_AREAS_LISTED = 3
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
        allowed_candidates, areas = self._allowed_candidates_and_areas(planning_state)

        return AIItineraryReasoningRequest(
            trip_id=planning_state.trip_id,
            destination_name=trip_request.primary_destination,
            start_date=trip_request.start_date.isoformat(),
            end_date=trip_request.end_date.isoformat(),
            trip_duration_days=self._trip_duration_days(planning_state),
            traveler_context=self._traveler_context(planning_state),
            trip_strategy_summary=self._trip_strategy_summary(planning_state),
            factual_context=self._factual_context(planning_state),
            allowed_candidates=allowed_candidates,
            areas=areas,
            # Q3: the area instruction travels only with a request that has areas.
            reasoning_instructions=[
                *DEFAULT_ITINERARY_REASONING_INSTRUCTIONS,
                *([AREA_INSTRUCTION] if areas else []),
            ],
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
        return self._allowed_candidates_and_areas(planning_state)[0]

    def _allowed_candidates_and_areas(
        self, planning_state: PlanningState
    ) -> tuple[list[ItineraryCandidateReference], list[ItineraryAreaSummary]]:
        """The bounded candidates and, with day composition on, the
        geographic areas they fall in (Q3). Areas come only from the
        candidates' verified coordinates (`day_composition.build_areas`):
        the eligible attractions are grouped first, so the bound can keep
        both companions and geographic alternatives, and the RETAINED
        attractions are then grouped again so the ids and nearness the model
        sees describe exactly the candidates it was given. Restaurants carry
        no area: a model-chosen restaurant is only a preference among a
        day's nearby food."""
        attractions, restaurants = self._candidate_universe(planning_state)
        interests = usefulness.requested_canonical_interests(planning_state)
        trip_days = self._trip_duration_days(planning_state)
        if not get_settings().day_composition_enabled:
            return self._bounded(attractions, restaurants, interests, trip_days), []

        eligible_areas = composition.build_areas(
            [
                composition.Located(candidate.candidate_id, candidate.coordinates, assessed.sort_key)
                for candidate, assessed in attractions
            ]
        )
        bounded = self._bounded(
            attractions,
            restaurants,
            interests,
            trip_days,
            area_of=eligible_areas.area_of,
            per_day=PACE_TARGET_PER_DAY[pace_of(planning_state)],
        )
        # `bounded` lists the attractions in canonical usefulness order.
        shown = composition.build_areas(
            [
                composition.Located(candidate.candidate_id, candidate.coordinates, (index,))
                for index, candidate in enumerate(bounded)
                if candidate.category == ItineraryReasoningCategory.ATTRACTION
            ]
        )
        summaries = [
            ItineraryAreaSummary(
                area_id=area_id,
                candidate_count=len(members),
                near_area_ids=list(shown.near.get(area_id, ()))[:_MAX_NEAR_AREAS_LISTED],
            )
            for area_id, members in shown.members.items()
        ]
        return (
            [
                candidate.model_copy(update={"area": shown.area_of.get(candidate.candidate_id)})
                for candidate in bounded
            ],
            summaries,
        )

    def must_visit_overflow(self, planning_state: PlanningState) -> int:
        """How many eligible grounded must-visits do NOT fit the candidate
        cap (0 = they all fit). A bounded request can only be presented as
        complete when this is 0: the caller must not send one otherwise."""
        attractions, _ = self._candidate_universe(planning_state)
        must_visits = sum(1 for _, assessed in attractions if assessed.must_visit)
        return max(0, must_visits - get_settings().ai_itinerary_reasoning_max_candidates)

    def _candidate_universe(
        self, planning_state: PlanningState
    ) -> tuple[list[tuple[ItineraryCandidateReference, usefulness.CandidateUsefulness]], list[ItineraryCandidateReference]]:
        """`(attractions with their usefulness, restaurants)`: every eligible
        reference once, enriched with its Q2 planning signals."""
        scored: list[tuple[ItineraryCandidateReference, CandidateQualityScore]] = []
        seen_ids: set[str] = set()
        # Task 10: a promoted AI-directed candidate that is the exact same
        # real place (same provider + provider id) as one already in the
        # broad pool -- the broad-pool entry (first, below) wins; never a
        # conflicting duplicate reference.
        for candidate, score in (*self._broad_pool_candidates(planning_state), *self._promoted_candidates(planning_state)):
            if candidate.candidate_id in seen_ids:
                continue
            seen_ids.add(candidate.candidate_id)
            scored.append((candidate, score))

        # Q2: the signals are attached AFTER duplicate suppression and by
        # provider place id alone, so the reference that survived carries
        # them wherever it came from -- a broad-pool reference that is also
        # a grounded anchor is marked exactly like a targeted-lookup one.
        must_visit_ids = must_visit_place_ids(planning_state)
        anchor_ids = grounded_anchor_place_ids(planning_state)
        interests = usefulness.requested_canonical_interests(planning_state)
        attractions: list[tuple[ItineraryCandidateReference, usefulness.CandidateUsefulness]] = []
        restaurants: list[ItineraryCandidateReference] = []
        for candidate, score in scored:
            if candidate.category == ItineraryReasoningCategory.RESTAURANT:
                restaurants.append(candidate)
                continue
            assessed = usefulness.assess(
                usefulness.evidence_from_score(score),
                must_visit=candidate.provider_place_id in must_visit_ids,
                grounded_anchor=candidate.provider_place_id in anchor_ids,
                canonical_interests=interests,
                name=candidate.name,
                place_id=candidate.provider_place_id,
            )
            attractions.append(
                (
                    candidate.model_copy(
                        update={
                            "normalized_category": score.normalized_category,
                            "matched_interests": list(score.matched_interests),
                            "must_visit": assessed.must_visit,
                            "semantic_anchor": assessed.semantic_anchor,
                            "provider_evidence": list(assessed.provider_evidence),
                        }
                    ),
                    assessed,
                )
            )
        return attractions, restaurants

    @staticmethod
    def _broad_pool_candidates(
        planning_state: PlanningState,
    ) -> list[tuple[ItineraryCandidateReference, CandidateQualityScore]]:
        destination_context = planning_state.destination_context
        quality_report = planning_state.candidate_quality_report
        if destination_context is None or quality_report is None:
            return []

        pois_by_id = _index_by_place_id(destination_context.candidate_pois)
        restaurants_by_id = _index_by_place_id(destination_context.candidate_restaurants)

        candidates: list[tuple[ItineraryCandidateReference, CandidateQualityScore]] = []
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
    def _promoted_candidates(
        planning_state: PlanningState,
    ) -> list[tuple[ItineraryCandidateReference, CandidateQualityScore]]:
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

        candidates: list[tuple[ItineraryCandidateReference, CandidateQualityScore]] = []
        # Only the promoted candidates the planner itself will schedule
        # (`candidate_universe`, the one decision both read): the model is
        # never offered an id the planner would not resolve.
        for promoted in universe.resolve_promoted_candidates(planning_state).accepted:
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

            # The same source the planner's own candidate dict carries, so
            # both build the identical candidate_id.
            provider_name = promoted.provider_source or promoted.source or "unknown_provider"
            candidate_id = build_candidate_id(provider_name, promoted.provider_place_id)
            category_hint = (promoted.category or "").strip().lower()
            category = (
                ItineraryReasoningCategory.RESTAURANT
                if category_hint in _RESTAURANT_CANDIDATE_TYPE_VALUES
                else ItineraryReasoningCategory.ATTRACTION
            )

            candidates.append(
                (
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
                    ),
                    score,
                )
            )
        return candidates

    @staticmethod
    def _bounded(
        attractions: list[tuple[ItineraryCandidateReference, usefulness.CandidateUsefulness]],
        restaurants: list[ItineraryCandidateReference],
        canonical_interests: list[str],
        trip_days: int,
        *,
        area_of: dict[str, str] | None = None,
        per_day: int = 0,
    ) -> list[ItineraryCandidateReference]:
        """Q2: the usefulness-aware bound. The cap is hard; every step below
        appends in canonical usefulness order, skips what is already
        retained, and stops at its limit -- so the result is total and
        deterministic whatever the sizes of the reserved sets:

          A. every eligible grounded must-visit;
          B. first coverage pass -- each requested interest with verified
             supply is served by at least ONE retained attraction;
          C. eligible grounded semantic anchors (those the usefulness band
             gate lets count);
          --  the restaurant allowance is fixed here, from the space A-C left
          D. second coverage pass -- a second attraction per interest;
          E. the remaining attractions;
          F. restaurants.

        Q3 (`area_of` given): step E is geography-aware, A-D are untouched.
        The space E has is filled in three parts, each in usefulness order:

          E1. companions -- at most HALF of the space: the areas that
              already hold a retained candidate get further members, one
              area after another, until each holds a day's worth
              (`per_day`), so a protected place is offered with places it
              can share a day with;
          E2. alternatives -- at most half of what is then left: one
              candidate for every area that does not hold a day's worth yet,
              best area first, so the model also sees other parts of the
              destination;
          E3. the rest by usefulness, exactly as before.

        No area can take the whole discretionary bound, and a compact
        destination (one area) is filled exactly as without areas.

        A-C are never displaced by a restaurant. When the must-visits alone
        exceed the cap this still returns `cap` references, but
        `must_visit_overflow` is then non-zero and the reasoning service
        does not send the request.
        """
        cap = get_settings().ai_itinerary_reasoning_max_candidates
        ordered = sorted(attractions, key=lambda item: item[1].sort_key)
        retained: list[tuple[ItineraryCandidateReference, usefulness.CandidateUsefulness]] = []
        retained_ids: set[str] = set()

        def keep(item: tuple[ItineraryCandidateReference, usefulness.CandidateUsefulness], limit: int) -> None:
            if len(retained) < limit and item[0].candidate_id not in retained_ids:
                retained.append(item)
                retained_ids.add(item[0].candidate_id)

        def cover(per_interest: int, limit: int) -> None:
            # Food is served by real nearby food, never by scheduling a
            # market for it (the planner's own coverage rule).
            for interest in canonical_interests:
                if interest == _FOOD_INTEREST:
                    continue
                serving = [item for item in ordered if interest in item[0].matched_interests]
                missing = per_interest - sum(1 for item in serving if item[0].candidate_id in retained_ids)
                for item in [item for item in serving if item[0].candidate_id not in retained_ids][: max(0, missing)]:
                    keep(item, limit)

        for item in ordered:
            if item[1].must_visit:
                keep(item, cap)
        cover(1, cap)
        for item in ordered:
            if item[1].semantic_anchor:
                keep(item, cap)

        # A model-chosen restaurant is only a PREFERENCE among a day's
        # nearby food (the planner still applies its radius, no-repeat and
        # per-day rules), so one per trip day is all the model can use --
        # taken only from the space the protected attractions left.
        reserved_for_restaurants = min(trip_days, len(restaurants), cap - len(retained))
        attraction_limit = cap - reserved_for_restaurants
        cover(_COVERAGE_CANDIDATES_PER_INTEREST, attraction_limit)
        if area_of and per_day > 0:

            def area(item: tuple[ItineraryCandidateReference, usefulness.CandidateUsefulness]) -> str | None:
                return area_of.get(item[0].candidate_id)

            def held(area_id: str) -> int:
                return sum(1 for item in retained if area(item) == area_id)

            def next_of(area_id: str) -> Any:
                return next(
                    (item for item in ordered if area(item) == area_id and item[0].candidate_id not in retained_ids),
                    None,
                )

            companion_limit = len(retained) + (attraction_limit - len(retained)) // 2
            retained_areas = list(dict.fromkeys(a for a in (area(item) for item in retained) if a is not None))
            progressed = True
            while progressed and len(retained) < companion_limit:
                progressed = False
                for area_id in retained_areas:
                    item = next_of(area_id) if held(area_id) < per_day else None
                    if item is not None and len(retained) < companion_limit:
                        keep(item, companion_limit)
                        progressed = True

            alternative_limit = len(retained) + (attraction_limit - len(retained)) // 2
            visited: set[str] = set()
            for item in ordered:
                area_id = area(item)
                if area_id is None or area_id in visited or item[0].candidate_id in retained_ids:
                    continue
                visited.add(area_id)
                if held(area_id) < per_day:
                    keep(item, alternative_limit)
        for item in ordered:
            keep(item, attraction_limit)

        # Space the attractions did not need (a thin pool) may also go to
        # restaurants, never beyond the planner's own per-day suggestion count.
        restaurant_count = min(len(restaurants), cap - len(retained), _MAX_RESTAURANTS_PER_DAY * trip_days)
        ordered_restaurants = sorted(
            restaurants,
            key=lambda candidate: (
                _TIER_SORT_ORDER.get(CandidateQualityTier(candidate.quality_tier), len(_TIER_SORT_ORDER)),
                -candidate.quality_score,
                " ".join(candidate.name.casefold().split()),
                candidate.candidate_id,
            ),
        )
        return [
            *(item[0] for item in sorted(retained, key=lambda item: item[1].sort_key)),
            *ordered_restaurants[:restaurant_count],
        ]


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
) -> list[tuple[ItineraryCandidateReference, CandidateQualityScore]]:
    references: list[tuple[ItineraryCandidateReference, CandidateQualityScore]] = []
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
            (
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
                    normalized_category=score.normalized_category,
                    matched_interests=list(score.matched_interests),
                ),
                score,
            )
        )
    return references
