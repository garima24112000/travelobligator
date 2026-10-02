"""Schedule-diversity contract (Section 203C.2B).

This module defines the COARSE attraction classes a schedule is balanced
over and what counts as a concentrated day. It is pure: classes come only
from the existing taxonomy's provider-derived primary category -- never
from a place's name, and never from anything city-specific.

Two different kinds of rule:

HARD -- marketplaces. A day holds at most
`SCHEDULE_DIVERSITY_MAX_MARKETS_PER_DAY` marketplace stops, unless the
traveller EXPLICITLY asked for markets/shopping (the canonical `shopping`
interest: shopping, markets, bazaars, souks). A general "food" interest is
not that: it is served by one market at most and by the factual nearby-food
suggestions. A hard violation is a defect.

SOFT -- every other class. Class variety is a preference, not a
constraint. A day is "concentrated" when it holds more than
`SCHEDULE_DIVERSITY_MAX_PER_CLASS_PER_DAY` stops of one class, or is two
or more stops of a single class. That is:

  * JUSTIFIED when the class directly serves an interest the traveller
    asked for (three historic sights for a history trip is the plan they
    asked for) -- never repaired, never a finding;
  * otherwise a SOFT concentration, which the planner may relieve only with
    a candidate that is no worse and just as well placed. It is reported,
    never a failure.

Whether anything can be relieved depends on supply; the planner's bounded
pass (`experience_planner_service._enforce_day_diversity`) only ever uses
an unused, quality-eligible candidate of another class.
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

HARD = "hard"
SOFT = "soft"
JUSTIFIED = "justified"

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


def justified_classes(interest_terms: Iterable[str]) -> frozenset[str]:
    """The coarse classes that directly serve an interest the traveller
    asked for, through the taxonomy's own interest -> category table. The
    marketplace class is never justified this way: it has its own explicit
    exception (`markets_explicitly_requested`)."""
    classes: set[str] = set()
    for interest in taxonomy.canonical_interests(interest_terms):
        for category in taxonomy.INTEREST_CATEGORIES.get(interest, frozenset()):
            classes.add(coarse_class(category))
    return frozenset(classes - {MARKETPLACE, OTHER})


def _interest_terms(planning_state: PlanningState) -> list[str]:
    profile = planning_state.traveler_profile
    return list(profile.interests if profile else planning_state.trip_request.interests)


def markets_requested_for(planning_state: PlanningState) -> bool:
    return markets_explicitly_requested(_interest_terms(planning_state))


def justified_classes_for(planning_state: PlanningState) -> frozenset[str]:
    return justified_classes(_interest_terms(planning_state))


def hard_excess(classes: Sequence[str], markets_requested: bool) -> dict[str, int]:
    """Marketplace stops beyond the hard per-day cap -- empty when the day
    satisfies the hard rule."""
    if markets_requested:
        return {}
    over = list(classes).count(MARKETPLACE) - get_settings().schedule_diversity_max_markets_per_day
    return {MARKETPLACE: over} if over > 0 else {}


def _concentrated(classes: Sequence[str]) -> dict[str, int]:
    """Non-marketplace classes a day is concentrated in, with how many stops
    would have to differ for it not to be."""
    counts = Counter(coarse for coarse in classes if coarse != MARKETPLACE)
    cap = get_settings().schedule_diversity_max_per_class_per_day
    over = {coarse: count - cap for coarse, count in counts.items() if count > cap}
    if not over and len(classes) >= 2 and len(set(classes)) == 1 and classes[0] != MARKETPLACE:
        over[classes[0]] = 1  # a whole day of one class
    return over


def soft_excess(classes: Sequence[str], justified: frozenset[str] = frozenset()) -> dict[str, int]:
    """Concentration in classes the traveller did NOT ask for -- a
    preference the planner may relieve, never a defect."""
    return {coarse: over for coarse, over in _concentrated(classes).items() if coarse not in justified}


def relievable_excess(
    classes: Sequence[str], markets_requested: bool, justified: frozenset[str] = frozenset()
) -> dict[str, int]:
    """Everything the planner's pass may act on: the hard marketplace
    excess plus any soft (unjustified) concentration."""
    return {**soft_excess(classes, justified), **hard_excess(classes, markets_requested)}


def concentration_kind(
    classes: Sequence[str], markets_requested: bool, justified: frozenset[str] = frozenset()
) -> str | None:
    """`hard`, `soft`, `justified`, or None for an unconcentrated day."""
    if hard_excess(classes, markets_requested):
        return HARD
    concentrated = _concentrated(classes)
    if not concentrated:
        return None
    return SOFT if any(coarse not in justified for coarse in concentrated) else JUSTIFIED


def plan_class_cap(
    coarse: str, markets_requested: bool, justified: frozenset[str], num_days: int
) -> int | None:
    """Most stops of `coarse` candidate SELECTION should put in the whole
    plan while candidates of other classes remain; None when uncapped (a
    class the traveller asked for, or markets when explicitly requested)."""
    settings = get_settings()
    if coarse == MARKETPLACE:
        return None if markets_requested else settings.schedule_diversity_max_markets_per_day * num_days
    if coarse in justified:
        return None
    return settings.schedule_diversity_max_per_class_per_day * num_days
