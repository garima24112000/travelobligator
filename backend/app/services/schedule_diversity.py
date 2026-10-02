"""Schedule-diversity contract (Section 203C.2B, generalization correction).

A day of three markets is not a useful day for a traveller who asked for
architecture, history and food, however healthy the candidate pool. This
module defines the COARSE attraction classes a schedule is balanced over
and the per-day concentration rules. It is pure: classes come only from
the existing taxonomy's provider-derived primary category -- never from a
place's name, and never from anything city-specific.

Rules (per day):
  * at most `SCHEDULE_DIVERSITY_MAX_PER_CLASS_PER_DAY` stops of one class;
  * at most `SCHEDULE_DIVERSITY_MAX_MARKETS_PER_DAY` marketplace stops;
  * a day of two or more stops is never entirely one class.

The marketplace class is exempt from all three only when the traveller
EXPLICITLY asked for markets/shopping (the canonical `shopping` interest).
A general "food" interest is not that: it is served by one market at most
and by the factual nearby-food suggestions.

Whether a rule can actually be satisfied depends on supply; the planner's
bounded repair (`experience_planner_service._enforce_day_diversity`) only
ever swaps in an unused, quality-eligible candidate of another class.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Sequence

from app.core.config import get_settings
from app.models.planning_state import PlanningState
from app.services import place_taxonomy as taxonomy

HISTORY_ARCHITECTURE = "history_architecture"
MUSEUM_CULTURE = "museum_culture"
NATURE_VIEW = "nature_view"
MARKETPLACE = "marketplace"
OTHER = "other"

_CLASS_OF_CATEGORY: dict[str, str] = {
    taxonomy.LANDMARK: HISTORY_ARCHITECTURE,
    taxonomy.HISTORIC: HISTORY_ARCHITECTURE,
    taxonomy.ARCHITECTURE: HISTORY_ARCHITECTURE,
    taxonomy.RELIGIOUS: HISTORY_ARCHITECTURE,
    taxonomy.MUSEUM: MUSEUM_CULTURE,
    taxonomy.ART_CULTURE: MUSEUM_CULTURE,
    taxonomy.ENTERTAINMENT: MUSEUM_CULTURE,
    taxonomy.PARK_NATURE: NATURE_VIEW,
    taxonomy.VIEWPOINT: NATURE_VIEW,
    taxonomy.WATERFRONT: NATURE_VIEW,
    taxonomy.FOOD_MARKET: MARKETPLACE,
    taxonomy.SHOPPING: MARKETPLACE,
}

# The canonical interest that explicitly asks for markets/shopping.
_MARKET_INTEREST = "shopping"


def coarse_class(primary_category: str | None) -> str:
    """The coarse scheduling class of a place's taxonomy primary category."""
    return _CLASS_OF_CATEGORY.get(primary_category or "", OTHER)


def markets_explicitly_requested(interest_terms: Iterable[str]) -> bool:
    return _MARKET_INTEREST in taxonomy.canonical_interests(interest_terms)


def markets_requested_for(planning_state: PlanningState) -> bool:
    profile = planning_state.traveler_profile
    terms = profile.interests if profile else planning_state.trip_request.interests
    return markets_explicitly_requested(terms)


def class_cap(coarse: str, markets_requested: bool) -> int | None:
    """Most stops of `coarse` one day may hold; None when uncapped."""
    settings = get_settings()
    if coarse == MARKETPLACE:
        return None if markets_requested else settings.schedule_diversity_max_markets_per_day
    return settings.schedule_diversity_max_per_class_per_day


def excess_by_class(classes: Sequence[str], markets_requested: bool) -> dict[str, int]:
    """How many stops of each class a day holds beyond what the rules
    allow -- empty when the day satisfies them."""
    counts = Counter(classes)
    excess: dict[str, int] = {}
    for coarse, count in counts.items():
        cap = class_cap(coarse, markets_requested)
        if cap is not None and count > cap:
            excess[coarse] = count - cap
    if not excess and len(classes) >= 2 and len(counts) == 1:
        only = next(iter(counts))
        if class_cap(only, markets_requested) is not None:
            excess[only] = 1  # a whole day of one class: one stop should differ
    return excess


def concentration_violation(classes: Sequence[str], markets_requested: bool) -> bool:
    return bool(excess_by_class(classes, markets_requested))
