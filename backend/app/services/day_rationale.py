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


def clear_day_rationale(day_plan: DailyPlan) -> None:
    """Removes the AI rationale shown for `day_plan`, if any. Identified by
    the day's own provenance field; for a day stored before that field
    existed, by the fixed prefix the rationale entry was always written with."""
    tracked = day_plan.ai_rationale_warning
    day_plan.warnings = [
        warning
        for warning in day_plan.warnings
        if warning != tracked and not (tracked is None and warning.startswith(RATIONALE_WARNING_PREFIX))
    ]
    day_plan.ai_rationale_warning = None


def finalize_day_explanations(planning_state: PlanningState) -> None:
    """The ONE authority for what explains each final day. Run after every
    step that can change a day's places or order. For each day:

      * any previously attached AI rationale is removed;
      * the factual deterministic summary of the FINAL stops is (re)built;
      * the model's rationale is attached again only when
        `current_day_rationale` says it still describes this exact day.

    So an obsolete rationale and the new summary of a changed day never
    coexist, whichever step changed the day."""
    plan = planning_state.experience_plan
    if plan is None:
        return
    for day_plan in plan.daily_plans:
        clear_day_rationale(day_plan)
        day_plan.goal = deterministic_day_summary([experience.name for experience in day_plan.experiences])
        rationale = current_day_rationale(planning_state, day_plan)
        if rationale:
            warning = f"{RATIONALE_WARNING_PREFIX}{rationale}"
            day_plan.warnings.append(warning)
            day_plan.ai_rationale_warning = warning


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
