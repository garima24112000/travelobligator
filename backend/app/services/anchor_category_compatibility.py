"""Grounded-anchor category hygiene (Section 203C.2B, final correction).

An AI anchor proposal is a hypothesis of a KIND ("a museum", "a market
area", "a landmark"). Grounding it geographically is not enough: a named
search can return a real place of the wrong kind -- an apartment or a
hotel whose name happens to contain a neighbourhood. A grounded anchor is
therefore eligible only when the PROVIDER'S OWN classification of the
place is compatible with the proposed anchor type.

Rules:
  * the decision uses provider categories/tags only -- never the place's
    name;
  * accommodation is never an attraction anchor;
  * an individual restaurant, cafe or bar is never an attraction anchor
    (a market or food hall is);
  * a place the provider gives no usable classification for cannot be
    verified and is not eligible;
  * user must-visits are NOT subject to this check: they are user
    assertions with their own grounding contract.
"""

from __future__ import annotations

from typing import Any

from app.models.ai_candidate_proposal import AICandidateType
from app.services import place_taxonomy as taxonomy

CATEGORY_MISMATCH_REASON = "grounding_category_mismatch"

_ACCOMMODATION_VALUES = frozenset(
    {"hotel", "hostel", "guest_house", "guesthouse", "motel", "apartment", "chalet", "resort", "hut", "accommodation"}
)
_NEIGHBOURHOOD_VALUES = frozenset({"suburb", "district", "neighbourhood", "neighborhood", "quarter", "city_block"})

_SIGHTS = frozenset(
    {
        taxonomy.LANDMARK, taxonomy.HISTORIC, taxonomy.ARCHITECTURE, taxonomy.MUSEUM, taxonomy.ART_CULTURE,
        taxonomy.RELIGIOUS, taxonomy.VIEWPOINT, taxonomy.PARK_NATURE, taxonomy.WATERFRONT, taxonomy.ENTERTAINMENT,
        taxonomy.FOOD_MARKET, taxonomy.GENERAL_ATTRACTION,
    }
)
_AREA = frozenset({taxonomy.NEIGHBORHOOD_AREA})

# Which provider-derived categories each proposed anchor type accepts.
_COMPATIBLE: dict[AICandidateType, frozenset[str]] = {
    AICandidateType.ATTRACTION: _SIGHTS,
    AICandidateType.LANDMARK: _SIGHTS - {taxonomy.FOOD_MARKET},
    AICandidateType.HISTORIC_SITE: frozenset(
        {taxonomy.LANDMARK, taxonomy.HISTORIC, taxonomy.ARCHITECTURE, taxonomy.RELIGIOUS, taxonomy.MUSEUM,
         taxonomy.GENERAL_ATTRACTION}
    ),
    AICandidateType.ARCHITECTURE: frozenset(
        {taxonomy.ARCHITECTURE, taxonomy.LANDMARK, taxonomy.HISTORIC, taxonomy.RELIGIOUS, taxonomy.MUSEUM,
         taxonomy.GENERAL_ATTRACTION}
    ),
    AICandidateType.MUSEUM: frozenset({taxonomy.MUSEUM, taxonomy.ART_CULTURE, taxonomy.ENTERTAINMENT}),
    AICandidateType.PARK: frozenset({taxonomy.PARK_NATURE, taxonomy.VIEWPOINT, taxonomy.WATERFRONT}),
    AICandidateType.VIEWPOINT: frozenset(
        {taxonomy.VIEWPOINT, taxonomy.PARK_NATURE, taxonomy.LANDMARK, taxonomy.GENERAL_ATTRACTION}
    ),
    AICandidateType.FERRY_OR_WATERFRONT: frozenset(
        {taxonomy.WATERFRONT, taxonomy.PARK_NATURE, taxonomy.VIEWPOINT, taxonomy.GENERAL_ATTRACTION}
    ),
    AICandidateType.MARKET: frozenset({taxonomy.FOOD_MARKET}) | _AREA,
    AICandidateType.FOOD_AREA: frozenset({taxonomy.FOOD_MARKET}) | _AREA,
    AICandidateType.SHOPPING_AREA: frozenset({taxonomy.FOOD_MARKET, taxonomy.SHOPPING}) | _AREA,
    AICandidateType.NEIGHBORHOOD: _AREA | frozenset({taxonomy.FOOD_MARKET, taxonomy.HISTORIC, taxonomy.LANDMARK}),
    AICandidateType.CULTURAL_AREA: _AREA
    | frozenset({taxonomy.ART_CULTURE, taxonomy.MUSEUM, taxonomy.HISTORIC, taxonomy.LANDMARK, taxonomy.ENTERTAINMENT}),
    AICandidateType.DAY_CLUSTER_IDEA: _SIGHTS | _AREA,
}


def provider_categories(provider_tags: dict[str, Any] | None, category: str | None) -> frozenset[str] | None:
    """The provider-derived categories of a grounded place, `{"accommodation"}`
    for lodging, or None when the provider gave nothing usable to classify it."""
    tags = {key: str(value).lower() for key, value in (provider_tags or {}).items() if value not in (None, "")}
    value = (category or "").strip().lower()
    category_path = tags.get("category_path", "")

    if (
        tags.get("tourism") in _ACCOMMODATION_VALUES
        or value in _ACCOMMODATION_VALUES
        or category_path.startswith("accommodation")
    ):
        return frozenset({"accommodation"})
    if value in _NEIGHBOURHOOD_VALUES or tags.get("place") in _NEIGHBOURHOOD_VALUES:
        return _AREA

    classifying_tags = {key: tag for key, tag in tags.items() if key not in ("category_path", "access", "wikidata", "wikipedia")}
    if not classifying_tags and not taxonomy._tags_from_category(value):
        return None  # no provider classification at all: cannot be verified
    classification = taxonomy.classify_place(classifying_tags or None, value or None)
    if classification.is_unsuitable:
        return frozenset({taxonomy.UNSUITABLE})
    return frozenset(classification.categories)


def anchor_category_compatible(
    candidate_type: AICandidateType, provider_tags: dict[str, Any] | None, category: str | None
) -> bool:
    """True only when the provider's own classification of the grounded
    place fits the proposed anchor type."""
    categories = provider_categories(provider_tags, category)
    if categories is None:
        return False
    return bool(categories & _COMPATIBLE.get(candidate_type, _SIGHTS))
