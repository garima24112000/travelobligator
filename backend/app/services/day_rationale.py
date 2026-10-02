"""Day rationale consistency (Section 203C.2B, canary correction).

An AI itinerary-reasoning rationale describes the day the model PROPOSED.
The final day can differ: geographic regrouping, the deterministic
fallback/top-up, a route-aware reorder, a repair or the pace cap may all
change which places are scheduled or their order. A rationale is therefore
only ever attached to a day when that day's final scheduled attractions
are exactly the model's own, in the model's own order. Otherwise it is
dropped and the day is described by a deterministic, factual summary of
what is actually scheduled.
"""

from __future__ import annotations

from typing import Sequence

from app.models.ai_itinerary_reasoning import AIItineraryReasoningStatus, build_candidate_id
from app.models.planning_state import DailyPlan, PlanningState

RATIONALE_WARNING_PREFIX = "AI itinerary reasoning rationale for this day: "


def _restaurant_candidate_ids(planning_state: PlanningState) -> set[str]:
    """Candidate ids of restaurants: the model may cite one in a day, where
    it becomes a food suggestion and is never a scheduled attraction."""
    context = planning_state.destination_context
    return {
        build_candidate_id(str(poi.get("source") or "unknown_provider"), str(poi.get("place_id") or ""))
        for poi in (context.candidate_restaurants if context is not None else [])
    }


def current_day_rationale(planning_state: PlanningState, day_plan: DailyPlan) -> str | None:
    """The reasoning rationale for `day_plan`, or None when the final day no
    longer matches what the model proposed (or no completed reasoning
    result exists)."""
    reasoning = planning_state.ai_itinerary_reasoning_result
    if reasoning is None or reasoning.status != AIItineraryReasoningStatus.COMPLETED:
        return None
    reasoning_day = next((day for day in reasoning.days if day.day_index == day_plan.day_number), None)
    if reasoning_day is None:
        return None

    # A day whose stops carry no provider identity cannot be verified
    # against the proposal, so its rationale is not attached.
    if any(not (e.provider_source and e.provider_place_id) for e in day_plan.experiences):
        return None
    restaurant_ids = _restaurant_candidate_ids(planning_state)
    proposed = [candidate_id for candidate_id in reasoning_day.candidate_ids if candidate_id not in restaurant_ids]
    scheduled = [
        build_candidate_id(str(experience.provider_source), str(experience.provider_place_id))
        for experience in day_plan.experiences
    ]
    return reasoning_day.rationale if proposed == scheduled else None


def deterministic_day_summary(place_names: Sequence[str]) -> str | None:
    """A factual one-line description of a final day: only the names of the
    places actually scheduled, in order. No claim about why, how long, or
    how good."""
    names = [name for name in place_names if name]
    if not names:
        return None
    if len(names) == 1:
        return f"This day's stop: {names[0]}."
    return f"This day's stops, grouped by location: {', '.join(names[:-1])} and {names[-1]}."
