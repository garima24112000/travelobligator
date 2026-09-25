from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.models.candidate_quality import (
    CandidateQualityReport,
    CandidateQualityScore,
    CandidateQualityTier,
    CandidateRejectReason,
    CandidateUseCase,
)
from app.models.planning_state import PlanningState
from app.models.providers import NormalizedPlace
from app.services import place_taxonomy as taxonomy

# Deterministic candidate quality scoring (Step 156A,
# docs/12_provider_architecture.md, docs/14_backend_architecture.md section
# 25, docs/18_candidate_quality.md). This is a pre-ranking signal layer
# only, placed between provider-backed candidate collection
# (DestinationContextService) and future scheduling/ranking work. It:
#
# - never calls an LLM, LangGraph, or LangSmith
# - never calls a provider or external API
# - never invents an attraction, restaurant, or accommodation
# - never invents a price, rating, opening hour, route time, review count,
#   booking link, or safety score
# - never mutates PlanningState -- build_report only reads it
#
# Scoring is intentionally conservative: it classifies existing
# candidate_pois/candidate_restaurants/candidate_accommodation_pois dicts
# (or NormalizedPlace instances) by category/name/address keyword
# heuristics, provider confidence, and coordinate presence -- nothing else.
# A high `quality_tier` is a pre-ranking signal only, never a claim of
# final quality, availability, or bookability.

_MIN_ACCEPTABLE_CONFIDENCE = 0.15

# Section 202B.2 (Task 6): deterministic taxonomy-based category signal.
# Every value is a documented pre-ranking constant, never a rating,
# popularity or "top" claim. Applied to the BEST non-flag category a place
# carries; then capped/boosted by the signals below.
_TAXONOMY_CATEGORY_SCORE: dict[str, float] = {
    taxonomy.LANDMARK: 0.8,
    taxonomy.MUSEUM: 0.85,
    taxonomy.VIEWPOINT: 0.75,
    taxonomy.ARCHITECTURE: 0.75,
    taxonomy.FOOD_MARKET: 0.75,
    taxonomy.ENTERTAINMENT: 0.75,
    taxonomy.HISTORIC: 0.7,
    taxonomy.GENERAL_ATTRACTION: 0.7,
    taxonomy.PARK_NATURE: 0.6,
    taxonomy.WATERFRONT: 0.6,
    taxonomy.ART_CULTURE: 0.6,
    taxonomy.NIGHTLIFE: 0.6,
    taxonomy.RELIGIOUS: 0.6,
    taxonomy.NEIGHBORHOOD_AREA: 0.6,
    taxonomy.RESTAURANT: 0.5,
    taxonomy.SHOPPING: 0.4,
}
_LOW_VALUE_OBJECT_SCORE_CAP = 0.4
# Section 202C.1A: an isolated single tree is capped below the scheduling
# threshold (total < 0.35 at typical provider confidence) so it is never
# used as filler; only a grounded must-visit can still schedule it.
_ISOLATED_TREE_SCORE_CAP = 0.2
# A documented (wikipedia/heritage) small object stays a candidate but ranks
# below real attractions; an art-focused request lifts the cap for artworks.
_NOTABLE_OBJECT_SCORE_CAP = 0.5
_ART_FOCUSED_OBJECT_SCORE_CAP = 0.7
_COMMERCIAL_GALLERY_SCORE_CAP = 0.45
_SIGNIFICANCE_BOOST_PER_SIGNAL = 0.03
_SIGNIFICANCE_BOOST_MAX = 0.06
_INTEREST_MATCH_BOOST = 0.1
_STRONG_SIGNIFICANCE = frozenset({"wikipedia", "heritage", "major_historic_type"})

_SEVERE_REJECT_REASONS = {
    CandidateRejectReason.UNSUITABLE_PLACE_TYPE,
    CandidateRejectReason.MISSING_COORDINATES,
    CandidateRejectReason.INSUFFICIENT_PROVIDER_CONFIDENCE,
    CandidateRejectReason.UNSUPPORTED_ACCOMMODATION_INVENTORY,
}

_TIER_THRESHOLDS: tuple[tuple[float, CandidateQualityTier], ...] = (
    (0.75, CandidateQualityTier.PRIMARY_ANCHOR),
    (0.55, CandidateQualityTier.GOOD_CANDIDATE),
    (0.35, CandidateQualityTier.SECONDARY_CANDIDATE),
    (0.20, CandidateQualityTier.LOW_PRIORITY),
)

_STRONG_ATTRACTION_KEYWORDS = {
    "museum",
    "attraction",
    "viewpoint",
    "gallery",
    "park",
    "monument",
    "landmark",
    "observatory",
    "bridge",
    "tower",
    "square",
    "island",
    "ferry",
    "skydeck",
    "garden",
    "zoo",
    "theme_park",
}
_MODERATE_ATTRACTION_KEYWORDS = {"artwork"}
_HISTORIC_DISTRICT_KEYWORDS = {"historic district", "district"}
_SCHOOL_KEYWORDS = {"school"}
_ADMIN_INFRA_KEYWORDS = {
    "reservoir",
    "court",
    "administrative",
    "townhall",
    "town hall",
    "office",
    "government",
    "place_of_worship",
    "memorial",
}
_UNKNOWN_CATEGORY_VALUES = {"", "yes", "unknown"}

_SKYDECK_INTEREST_KEYWORDS = {"tower", "observatory", "skyline", "empire", "state", "building"}
_FERRY_INTEREST_KEYWORDS = {"ferry", "island", "statue", "harbor", "harbour"}
_MUSEUM_INTEREST_KEYWORDS = {"museum", "gallery"}

_STRONG_RESTAURANT_CATEGORIES = {"restaurant", "cafe"}
_CASUAL_RESTAURANT_CATEGORIES = {"fast_food", "bar", "pub"}
_CASUAL_INTEREST_KEYWORDS = {"casual", "nightlife"}

_ACCOMMODATION_KEYWORDS = {
    "hotel",
    "hostel",
    "guest_house",
    "guesthouse",
    "motel",
    "apartment",
    "chalet",
    "resort",
}
_FORBIDDEN_ACCOMMODATION_DICT_KEYS = {
    "price",
    "rating",
    "booking_url",
    "availability",
    "review_count",
    "opening_hours",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "unnamed"


def _field(place: NormalizedPlace | dict[str, Any], field_name: str) -> Any:
    if isinstance(place, dict):
        return place.get(field_name)
    return getattr(place, field_name, None)


def _has_coordinates(place: NormalizedPlace | dict[str, Any]) -> bool:
    coordinates = _field(place, "coordinates")
    if coordinates is None:
        return False
    if isinstance(coordinates, dict):
        return coordinates.get("lat") is not None and coordinates.get("lng") is not None
    return getattr(coordinates, "lat", None) is not None and getattr(coordinates, "lng", None) is not None


def _data_status_str(place: NormalizedPlace | dict[str, Any]) -> str | None:
    value = _field(place, "data_status")
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _candidate_identity(
    place: NormalizedPlace | dict[str, Any], use_case: CandidateUseCase
) -> tuple[str, str]:
    """Derives a non-blank `(candidate_id, candidate_name)` pair from an
    existing candidate's own fields only -- never invents a new place. Falls
    back to a deterministic slug of the name (or a generic placeholder) only
    when the provider candidate itself is missing a `place_id`/`name`,
    never a fabricated travel fact.
    """
    raw_name = _field(place, "name")
    candidate_name = str(raw_name).strip() if raw_name else "Unnamed candidate"

    raw_id = _field(place, "place_id")
    candidate_id = str(raw_id) if raw_id else f"unknown_{use_case.value}_{_slugify(candidate_name)}"

    return candidate_id, candidate_name


def _tier_from_score(score: float) -> CandidateQualityTier:
    for threshold, tier in _TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return CandidateQualityTier.REJECTED


def _finalize_tier(
    total_score: float, reject_reasons: list[CandidateRejectReason]
) -> CandidateQualityTier:
    """Resolves the final tier so it always stays consistent with
    `CandidateQualityScore`'s own validation rules: any severe reason (missing
    coordinates, insufficient provider confidence, unsupported accommodation
    inventory) forces `rejected` regardless of score; any other non-empty
    reject reason caps the tier at `low_priority` even if the raw score would
    otherwise clear a higher threshold.
    """
    if any(reason in _SEVERE_REJECT_REASONS for reason in reject_reasons):
        return CandidateQualityTier.REJECTED

    tier = _tier_from_score(total_score)
    if reject_reasons and tier not in (
        CandidateQualityTier.LOW_PRIORITY,
        CandidateQualityTier.REJECTED,
    ):
        return CandidateQualityTier.LOW_PRIORITY
    return tier


def _confidence_signal(
    confidence: float,
) -> tuple[list[str], list[CandidateRejectReason]]:
    if confidence < _MIN_ACCEPTABLE_CONFIDENCE:
        return (
            ["Provider confidence is very low."],
            [CandidateRejectReason.INSUFFICIENT_PROVIDER_CONFIDENCE],
        )
    return [], []


class CandidateQualityService:
    """Deterministic pre-ranking quality layer for existing candidate
    places (docs/14_backend_architecture.md section 25,
    docs/18_candidate_quality.md).

    Reads `NormalizedPlace` instances or their `dict` (JSON-mode) shape as
    already stored in `DestinationContext.candidate_pois`/
    `candidate_restaurants`/`candidate_accommodation_pois` -- never a
    provider adapter, LLM, LangGraph, or LangSmith call, and never mutates
    `PlanningState`.
    """

    def score_attraction(
        self,
        place: NormalizedPlace | dict[str, Any],
        user_interests: list[str] | None = None,
        must_visit_names: list[str] | None = None,
    ) -> CandidateQualityScore:
        candidate_id, candidate_name = _candidate_identity(place, CandidateUseCase.ATTRACTION)
        category = str(_field(place, "category") or "")
        address = str(_field(place, "address") or "")
        confidence = float(_field(place, "confidence") or 0.0)
        haystack = f"{candidate_name} {category} {address}".lower()

        positive_signals: list[str] = []
        negative_signals: list[str] = []
        reject_reasons: list[CandidateRejectReason] = []

        if not _has_coordinates(place):
            reject_reasons.append(CandidateRejectReason.MISSING_COORDINATES)
            negative_signals.append("Candidate has no usable coordinates.")

        # Weak/negative category signals are checked before strong positive
        # keywords so a candidate like "Central Park Reservoir" (name
        # contains the strong keyword "park" but category is "reservoir")
        # is correctly demoted rather than boosted -- the specific,
        # narrower signal wins over the broader one.
        category_score = 0.4
        if any(keyword in haystack for keyword in _HISTORIC_DISTRICT_KEYWORDS):
            category_score = 0.25
            negative_signals.append(
                "Name/category suggests a generic historic district rather than a "
                "specific itinerary anchor."
            )
            reject_reasons.append(CandidateRejectReason.GENERIC_HISTORIC_DISTRICT)
        elif any(keyword in haystack for keyword in _SCHOOL_KEYWORDS):
            category_score = 0.15
            negative_signals.append(
                "Name/category suggests a school, which is typically not a tourist "
                "attraction."
            )
            reject_reasons.append(CandidateRejectReason.SCHOOL_OR_NON_TOURIST_LOCAL_USE)
        elif any(keyword in haystack for keyword in _ADMIN_INFRA_KEYWORDS):
            category_score = 0.2
            negative_signals.append(
                "Name/category suggests an administrative or infrastructure object "
                "rather than a tourist attraction."
            )
            reject_reasons.append(CandidateRejectReason.ADMINISTRATIVE_OR_INFRASTRUCTURE)
        elif any(keyword in haystack for keyword in _STRONG_ATTRACTION_KEYWORDS):
            category_score = 0.85
            positive_signals.append("Name/category matches a strong attraction-type keyword.")
        elif any(keyword in haystack for keyword in _MODERATE_ATTRACTION_KEYWORDS):
            category_score = 0.6
            positive_signals.append("Name/category matches a moderate attraction-type keyword.")
        elif category.strip().lower() in _UNKNOWN_CATEGORY_VALUES:
            category_score = 0.25
            negative_signals.append("Category is missing, generic, or unknown.")
            reject_reasons.append(CandidateRejectReason.WEAK_CATEGORY)
        else:
            positive_signals.append("Category did not match any known weak-category pattern.")

        interests_lower = [term.lower() for term in (user_interests or []) if term]
        if "skydeck" in interests_lower and any(
            keyword in haystack for keyword in _SKYDECK_INTEREST_KEYWORDS
        ):
            category_score = max(category_score, 0.8)
            positive_signals.append("Matches user interest 'skydeck'.")
        if "ferry" in interests_lower and any(
            keyword in haystack for keyword in _FERRY_INTEREST_KEYWORDS
        ):
            category_score = max(category_score, 0.8)
            positive_signals.append("Matches user interest 'ferry'.")
        if "museum" in interests_lower and any(
            keyword in haystack for keyword in _MUSEUM_INTEREST_KEYWORDS
        ):
            category_score = max(category_score, 0.8)
            positive_signals.append("Matches user interest 'museum'.")

        # -- Section 202B.2: taxonomy (provider-tag) evidence ------------------------
        provider_tags = _field(place, "provider_tags")
        structured = bool(provider_tags)
        classification = taxonomy.classify_place(provider_tags, category)
        canonical = taxonomy.canonical_interests(user_interests or [])
        matched = taxonomy.matched_interests(classification, canonical)
        if structured:
            # Provider tags take precedence over name/category keyword
            # heuristics: only a missing-coordinates finding and a generic
            # "historic district" area survive from the keyword stage.
            keep = {CandidateRejectReason.MISSING_COORDINATES}
            if not classification.significance_signals:
                keep.add(CandidateRejectReason.GENERIC_HISTORIC_DISTRICT)
            reject_reasons = [reason for reason in reject_reasons if reason in keep]
            positive_signals = []
            negative_signals = [text for text in negative_signals if "no usable coordinates" in text or "historic district" in text]
            usable = [c for c in classification.categories if c in _TAXONOMY_CATEGORY_SCORE]
            category_score = max((_TAXONOMY_CATEGORY_SCORE[c] for c in usable), default=0.4)
            if CandidateRejectReason.GENERIC_HISTORIC_DISTRICT in reject_reasons:
                category_score = min(category_score, 0.25)
            positive_signals.append(
                f"Provider tags classify this place as '{classification.primary_category}'."
            )
        if classification.is_unsuitable:
            category_score = min(category_score, 0.1)
            negative_signals.append(
                f"Provider data marks this as a non-tourist place type ({classification.unsuitable_reason})."
            )
            if CandidateRejectReason.UNSUITABLE_PLACE_TYPE not in reject_reasons:
                reject_reasons.append(CandidateRejectReason.UNSUITABLE_PLACE_TYPE)
        else:
            art_focused_artwork = "art" in canonical and classification.object_kind == taxonomy.OBJECT_ARTWORK
            object_cap: float | None = None
            if classification.low_value:
                if classification.object_kind == taxonomy.OBJECT_TREE:
                    object_cap = _ISOLATED_TREE_SCORE_CAP
                    negative_signals.append(
                        "Isolated single tree: not scheduled as a general attraction "
                        "(a park, garden or reserve would be treated differently)."
                    )
                else:
                    object_cap = _ART_FOCUSED_OBJECT_SCORE_CAP if art_focused_artwork else _LOW_VALUE_OBJECT_SCORE_CAP
                    negative_signals.append(
                        "Small memorial/statue/sculpture-type object: kept as a candidate but ranked below major attractions."
                    )
                category_score = min(category_score, object_cap)
            elif classification.notable_object:
                object_cap = _ART_FOCUSED_OBJECT_SCORE_CAP if art_focused_artwork else _NOTABLE_OBJECT_SCORE_CAP
                category_score = min(category_score, object_cap)
                negative_signals.append(
                    "Documented small object (memorial/artwork): eligible, but ranked below major attractions."
                )
            if classification.commercial_gallery:
                cap = 0.7 if "art" in canonical else _COMMERCIAL_GALLERY_SCORE_CAP
                category_score = min(category_score, cap)
                negative_signals.append(
                    "Commercial gallery: relevant for art-focused requests, capped for general trips."
                )
            # Section 202C.1A: only STRONG evidence (wikipedia / heritage /
            # major historic type) lifts a score. A bare wikidata id is
            # carried by countless small local features, and single
            # objects never receive the floor (their caps stand).
            strong_signals = [
                sig for sig in classification.significance_signals if sig in _STRONG_SIGNIFICANCE
            ]
            if strong_signals and classification.object_kind is None:
                boost = min(
                    _SIGNIFICANCE_BOOST_MAX,
                    _SIGNIFICANCE_BOOST_PER_SIGNAL * len(strong_signals),
                )
                category_score = max(category_score, 0.6) + boost
                positive_signals.append(
                    "Provider carries structured landmark/heritage evidence "
                    f"({', '.join(classification.significance_signals)}): a significance signal, "
                    "not a claim that this is best or top rated."
                )
            if matched:
                category_score += _INTEREST_MATCH_BOOST
                positive_signals.append(
                    "Provider metadata supports requested interest(s): " + ", ".join(matched) + "."
                )
            if object_cap is not None:
                category_score = min(category_score, object_cap)

        must_visit_lower = [term.lower() for term in (must_visit_names or []) if term]
        if must_visit_lower and any(term in haystack for term in must_visit_lower):
            category_score = max(category_score, 0.9)
            positive_signals.append(
                "Matches a must-visit request, which overrides weak-category signals."
            )
            reject_reasons = [
                reason
                for reason in reject_reasons
                if reason
                not in (
                    CandidateRejectReason.GENERIC_HISTORIC_DISTRICT,
                    CandidateRejectReason.SCHOOL_OR_NON_TOURIST_LOCAL_USE,
                    CandidateRejectReason.ADMINISTRATIVE_OR_INFRASTRUCTURE,
                    CandidateRejectReason.WEAK_CATEGORY,
                    CandidateRejectReason.UNSUITABLE_PLACE_TYPE,
                )
            ]

        confidence_negative, confidence_reasons = _confidence_signal(confidence)
        negative_signals.extend(confidence_negative)
        reject_reasons.extend(confidence_reasons)

        category_score = max(0.0, min(1.0, category_score))
        total_score = max(0.0, min(1.0, 0.65 * category_score + 0.35 * confidence))
        quality_tier = _finalize_tier(total_score, reject_reasons)

        return CandidateQualityScore(
            normalized_category=classification.primary_category,
            categories=list(classification.categories),
            matched_interests=matched,
            significance_signals=list(classification.significance_signals),
            low_value_object=classification.low_value,
            notable_object=classification.notable_object,
            commercial_gallery=classification.commercial_gallery,
            sub_feature_kind=classification.sub_feature_kind,
            candidate_id=candidate_id,
            candidate_name=candidate_name,
            use_case=CandidateUseCase.ATTRACTION,
            quality_tier=quality_tier,
            total_score=total_score,
            score_components={
                "category_signal": category_score,
                "provider_confidence": confidence,
            },
            positive_signals=positive_signals,
            negative_signals=negative_signals,
            reject_reasons=reject_reasons,
            source=_field(place, "source"),
            data_status=_data_status_str(place),
            confidence=confidence,
        )

    def score_restaurant(
        self,
        place: NormalizedPlace | dict[str, Any],
        user_interests: list[str] | None = None,
    ) -> CandidateQualityScore:
        candidate_id, candidate_name = _candidate_identity(place, CandidateUseCase.RESTAURANT)
        category = str(_field(place, "category") or "").strip().lower()
        confidence = float(_field(place, "confidence") or 0.0)

        positive_signals: list[str] = []
        negative_signals: list[str] = []
        reject_reasons: list[CandidateRejectReason] = []

        if not _has_coordinates(place):
            reject_reasons.append(CandidateRejectReason.MISSING_COORDINATES)
            negative_signals.append("Candidate has no usable coordinates.")

        interests_lower = [term.lower() for term in (user_interests or []) if term]

        if category in _STRONG_RESTAURANT_CATEGORIES:
            category_score = 0.65
            positive_signals.append("Category is a restaurant/cafe, a good itinerary candidate.")
        elif category in _CASUAL_RESTAURANT_CATEGORIES:
            category_score = 0.35
            negative_signals.append(
                "Casual/fast-food/bar/pub category scores lower than restaurant/cafe "
                "by default."
            )
            if any(term in interests_lower for term in _CASUAL_INTEREST_KEYWORDS):
                category_score = 0.6
                positive_signals.append(
                    "User interest indicates casual dining/nightlife, so this category "
                    "is not downgraded."
                )
        elif category in _UNKNOWN_CATEGORY_VALUES:
            category_score = 0.25
            negative_signals.append("Category is missing, generic, or unknown.")
            reject_reasons.append(CandidateRejectReason.WEAK_CATEGORY)
        else:
            category_score = 0.4
            positive_signals.append("Category did not match any known weak-category pattern.")

        confidence_negative, confidence_reasons = _confidence_signal(confidence)
        negative_signals.extend(confidence_negative)
        reject_reasons.extend(confidence_reasons)

        total_score = max(0.0, min(1.0, 0.6 * category_score + 0.4 * confidence))
        quality_tier = _finalize_tier(total_score, reject_reasons)

        return CandidateQualityScore(
            candidate_id=candidate_id,
            candidate_name=candidate_name,
            use_case=CandidateUseCase.RESTAURANT,
            quality_tier=quality_tier,
            total_score=total_score,
            score_components={
                "category_signal": category_score,
                "provider_confidence": confidence,
            },
            positive_signals=positive_signals,
            negative_signals=negative_signals,
            reject_reasons=reject_reasons,
            source=_field(place, "source"),
            data_status=_data_status_str(place),
            confidence=confidence,
        )

    def score_accommodation_poi(
        self, place: NormalizedPlace | dict[str, Any]
    ) -> CandidateQualityScore:
        candidate_id, candidate_name = _candidate_identity(
            place, CandidateUseCase.ACCOMMODATION_POI
        )
        category = str(_field(place, "category") or "")
        address = str(_field(place, "address") or "")
        confidence = float(_field(place, "confidence") or 0.0)
        haystack = f"{candidate_name} {category} {address}".lower()

        positive_signals: list[str] = []
        negative_signals: list[str] = [
            "This is an open-data location candidate only, never bookable inventory -- "
            "no price, availability, rating, or booking link is implied by this score."
        ]
        reject_reasons: list[CandidateRejectReason] = []

        if not _has_coordinates(place):
            reject_reasons.append(CandidateRejectReason.MISSING_COORDINATES)
            negative_signals.append("Candidate has no usable coordinates.")

        if isinstance(place, dict) and any(
            key in place for key in _FORBIDDEN_ACCOMMODATION_DICT_KEYS
        ):
            reject_reasons.append(CandidateRejectReason.UNSUPPORTED_ACCOMMODATION_INVENTORY)
            negative_signals.append(
                "Input candidate carried booking-like fields (price/rating/availability/"
                "etc.), which accommodation POI candidates must never carry."
            )

        if any(keyword in haystack for keyword in _ACCOMMODATION_KEYWORDS):
            category_score = 0.55
            positive_signals.append(
                "Name/category matches a recognized accommodation-type POI term."
            )
        else:
            category_score = 0.3
            negative_signals.append("Category is missing or unrecognized for an accommodation POI.")
            reject_reasons.append(CandidateRejectReason.WEAK_CATEGORY)

        confidence_negative, confidence_reasons = _confidence_signal(confidence)
        negative_signals.extend(confidence_negative)
        reject_reasons.extend(confidence_reasons)

        total_score = max(0.0, min(1.0, 0.6 * category_score + 0.4 * confidence))
        quality_tier = _finalize_tier(total_score, reject_reasons)

        return CandidateQualityScore(
            candidate_id=candidate_id,
            candidate_name=candidate_name,
            use_case=CandidateUseCase.ACCOMMODATION_POI,
            quality_tier=quality_tier,
            total_score=total_score,
            score_components={
                "category_signal": category_score,
                "provider_confidence": confidence,
            },
            positive_signals=positive_signals,
            negative_signals=negative_signals,
            reject_reasons=reject_reasons,
            source=_field(place, "source"),
            data_status=_data_status_str(place),
            confidence=confidence,
        )

    def score_provider_backed_candidate(
        self,
        place: NormalizedPlace,
        candidate_type_hint: str = "",
        user_interests: list[str] | None = None,
        must_visit_names: list[str] | None = None,
    ) -> CandidateQualityScore:
        """Section 192A (docs/14_backend_architecture.md section 140):
        scores one already-grounded, provider-backed candidate that did
        not come from `DestinationContext`'s own broad candidate
        collections (today, a Section 192 AI-directed
        `match_type=targeted_lookup` grounding result) using the exact
        same deterministic rules as any broad-discovery candidate --
        there is one quality policy, not a second AI-specific one. This
        method adds only a classification dispatch on top of the existing
        `score_attraction`/`score_restaurant`/`score_accommodation_poi`;
        it invents no new scoring heuristic, weight, or threshold.

        `place`'s own fields (name/category/coordinates/source/
        data_status/confidence -- all real provider evidence) are what
        get scored, exactly as for any other candidate. `candidate_type_hint`
        (a plain string, e.g. an `AICandidateType.value`) is used only to
        pick *which* of the three existing scoring functions to call when
        `place.category` itself doesn't already answer that -- it is never
        passed into the scoring math itself, so an AI's own labeling can
        never inflate or substitute for provider-backed quality.

        Classification prefers real provider category over the hint
        (Task 3: "do not use LLM category alone as authoritative if
        provider category exists"): a category recognized by
        `score_restaurant`'s own restaurant/cafe/casual keyword sets, or
        by an accommodation keyword, routes there regardless of the hint.
        Only when the provider category gives no such signal does
        `candidate_type_hint == "food_area"` route to restaurant scoring
        as a fallback signal. Every other case (including an unrecognized
        or missing provider category with any other/no hint) uses
        `score_attraction` -- the safest existing generic POI path,
        matching this task's own instruction rather than inventing a new
        one. `AICandidateType` has no accommodation member today, so the
        accommodation branch here is only ever reached by a real provider
        category match, never by an AI hint alone -- documented, not
        worked around.
        """
        category = str(_field(place, "category") or "").strip().lower()

        if category in _STRONG_RESTAURANT_CATEGORIES or category in _CASUAL_RESTAURANT_CATEGORIES:
            return self.score_restaurant(place, user_interests=user_interests)
        if category and any(keyword in category for keyword in _ACCOMMODATION_KEYWORDS):
            return self.score_accommodation_poi(place)
        if candidate_type_hint.strip().lower() == "food_area":
            return self.score_restaurant(place, user_interests=user_interests)
        return self.score_attraction(
            place, user_interests=user_interests, must_visit_names=must_visit_names
        )

    def build_report(self, planning_state: PlanningState) -> CandidateQualityReport:
        """Builds a `CandidateQualityReport` purely from
        `planning_state.destination_context`'s existing candidate lists --
        never mutates `planning_state`, never calls a provider or AI/LLM,
        never creates a new place.
        """
        destination_context = planning_state.destination_context
        destination_name = (
            destination_context.destination_name
            if destination_context
            else planning_state.trip_request.primary_destination
        )

        traveler_profile = planning_state.traveler_profile
        user_interests = (
            traveler_profile.interests if traveler_profile else planning_state.trip_request.interests
        )
        must_visit_names = (
            traveler_profile.must_visit
            if traveler_profile
            else planning_state.trip_request.must_visit
        )

        candidate_pois = list(destination_context.candidate_pois) if destination_context else []
        candidate_restaurants = (
            list(destination_context.candidate_restaurants) if destination_context else []
        )
        candidate_accommodation_pois = (
            list(destination_context.candidate_accommodation_pois) if destination_context else []
        )

        attraction_scores = _dedupe_scores(
            [
                self.score_attraction(poi, user_interests=user_interests, must_visit_names=must_visit_names)
                for poi in candidate_pois
            ]
        )
        restaurant_scores = _dedupe_scores(
            [
                self.score_restaurant(restaurant, user_interests=user_interests)
                for restaurant in candidate_restaurants
            ]
        )
        accommodation_poi_scores = _dedupe_scores(
            [self.score_accommodation_poi(poi) for poi in candidate_accommodation_pois]
        )

        summary = _build_summary(attraction_scores, restaurant_scores, accommodation_poi_scores)

        return CandidateQualityReport(
            destination_name=destination_name,
            generated_at=_utc_now(),
            attraction_scores=attraction_scores,
            restaurant_scores=restaurant_scores,
            accommodation_poi_scores=accommodation_poi_scores,
            summary=summary,
        )


def _dedupe_scores(scores: list[CandidateQualityScore]) -> list[CandidateQualityScore]:
    """Demotes repeated candidates (by normalized `candidate_name`) to
    reflect `duplicate_or_near_duplicate`, preserving stable order. Only
    ever adjusts `reject_reasons`/`quality_tier`/`negative_signals` on
    already-computed scores -- never re-derives a score from a new source
    or invents a candidate.
    """
    seen_names: set[str] = set()
    deduped: list[CandidateQualityScore] = []

    for score in scores:
        normalized = _normalize_name(score.candidate_name)
        if normalized and normalized in seen_names:
            updated_reasons = list(score.reject_reasons)
            if CandidateRejectReason.DUPLICATE_OR_NEAR_DUPLICATE not in updated_reasons:
                updated_reasons.append(CandidateRejectReason.DUPLICATE_OR_NEAR_DUPLICATE)
            updated_tier = _finalize_tier(score.total_score, updated_reasons)
            score = score.model_copy(
                update={
                    "reject_reasons": updated_reasons,
                    "quality_tier": updated_tier,
                    "negative_signals": [
                        *score.negative_signals,
                        "Duplicate or near-duplicate candidate name already scored.",
                    ],
                }
            )
        if normalized:
            seen_names.add(normalized)
        deduped.append(score)

    return deduped


def _build_summary(
    attraction_scores: list[CandidateQualityScore],
    restaurant_scores: list[CandidateQualityScore],
    accommodation_poi_scores: list[CandidateQualityScore],
) -> dict[str, int]:
    """Deterministic tier-count rollup across all scored candidates, plus a
    per-section total. Built purely from already-computed scores.
    """
    tier_counter: Counter[str] = Counter()
    for score in (*attraction_scores, *restaurant_scores, *accommodation_poi_scores):
        tier_counter[score.quality_tier.value] += 1

    summary: dict[str, int] = {tier.value: tier_counter.get(tier.value, 0) for tier in CandidateQualityTier}
    summary["attraction_total"] = len(attraction_scores)
    summary["restaurant_total"] = len(restaurant_scores)
    summary["accommodation_poi_total"] = len(accommodation_poi_scores)
    return summary
