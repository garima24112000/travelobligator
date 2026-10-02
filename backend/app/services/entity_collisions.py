"""Suspected duplicate candidates (Section 203C.2B, live cleanup).

Two candidate records can be the same real place without the provider
giving any evidence that says so: different place ids, no shared source
object, no Wikidata reference, no name in common (an English and a
local-script record of one museum). Such a pair is never merged on
proximity alone. Instead the places provider reports it as a SUSPECTED
collision -- co-located, compatible class, identity unproven -- after a
bounded attempt to get stronger evidence:

  * `merged`     conclusive evidence was found; one candidate remains;
  * `distinct`   the evidence says they are different entities;
  * `unresolved` neither: both candidates stay in the pool, and are marked
                 so that they are never scheduled on one itinerary.

This module carries those records from the generation's provider context
into `PlanningState` and answers the one question later stages ask: is an
unresolved pair scheduled together?
"""

from __future__ import annotations

from typing import Any

from app.core.provider_usage import GenerationProviderContext
from app.models.planning_state import DestinationContext, PlanningState

# Set on BOTH candidate dicts of an unresolved pair. Candidates sharing a
# group are never scheduled together (`experience_planner_service`).
SUSPECT_COLLISION_KEY = "suspect_collision_group"

UNRESOLVED = "unresolved"
MERGED = "merged"
DISTINCT = "distinct"


def apply_suspect_collisions(
    destination_context: DestinationContext, provider_context: GenerationProviderContext | None
) -> None:
    """Stores the generation's collision records on the destination context
    and marks the candidates of every unresolved pair with a shared group."""
    if provider_context is None:
        return
    records = [dict(record) for record in provider_context.suspect_collisions]
    destination_context.suspect_entity_collisions = records

    groups: dict[str, str] = {}
    for index, record in enumerate(records, start=1):
        if record.get("resolution") != UNRESOLVED:
            continue
        first, second = record["place_ids"]
        group = groups.get(first) or groups.get(second) or f"collision-{index}"
        groups[first] = groups[second] = group
    for poi in destination_context.candidate_pois:
        group = groups.get(str(poi.get("place_id")))
        if group:
            poi[SUSPECT_COLLISION_KEY] = group


def scheduled_unresolved_collisions(planning_state: PlanningState) -> list[dict[str, Any]]:
    """The unresolved suspected pairs whose two candidates are BOTH scheduled."""
    context, plan = planning_state.destination_context, planning_state.experience_plan
    if context is None or plan is None:
        return []
    scheduled = {
        experience.provider_place_id
        for day in plan.daily_plans
        for experience in day.experiences
        if experience.provider_place_id
    }
    return [
        record
        for record in context.suspect_entity_collisions
        if record.get("resolution") == UNRESOLVED and set(record.get("place_ids") or []) <= scheduled
    ]
