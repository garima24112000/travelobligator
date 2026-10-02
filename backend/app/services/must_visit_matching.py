"""Must-visit resolution by IDENTITY (Section 203C.2B, final correction).

A user's must-visit term and the provider's name for the same place often
differ ("Torre de Belém" vs "Tower of Belém"). Matching the term against a
scheduled NAME therefore reports a grounded, scheduled must-visit as
missing. This module resolves each requested term to the provider places
grounded for it, so every consumer (validation, route repair, diagnostics)
can ask the same three questions:

  * was the term grounded to a real place?
  * is one of those places scheduled?
  * is a given scheduled place a must-visit (and therefore never replaced)?

A place is grounded for a term when the destination-context stage attached
the user's own term to it (`must_visit_term`, set only on a real provider
result) or when its provider name contains the term. Nothing is inferred
beyond that, and no place is ever invented.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.planning_state import PlanningState


@dataclass(frozen=True)
class MustVisitResolution:
    term: str
    grounded_place_ids: frozenset[str]
    grounded_names: tuple[str, ...]
    scheduled: bool

    @property
    def grounded(self) -> bool:
        return bool(self.grounded_place_ids)


def requested_must_visits(planning_state: PlanningState) -> list[str]:
    profile = planning_state.traveler_profile
    terms = profile.must_visit if profile else planning_state.trip_request.must_visit
    return [term for term in terms if term and term.strip()]


def resolve_must_visits(planning_state: PlanningState) -> list[MustVisitResolution]:
    context = planning_state.destination_context
    pois = context.candidate_pois if context is not None else []
    plan = planning_state.experience_plan
    scheduled = [experience for day in (plan.daily_plans if plan else []) for experience in day.experiences]
    scheduled_ids = {experience.provider_place_id for experience in scheduled if experience.provider_place_id}

    resolutions: list[MustVisitResolution] = []
    for term in requested_must_visits(planning_state):
        lowered = term.strip().lower()
        matches = [
            poi
            for poi in pois
            if poi.get("place_id")
            and (
                str(poi.get("must_visit_term") or "").strip().lower() == lowered
                or lowered in str(poi.get("name") or "").lower()
            )
        ]
        place_ids = frozenset(str(poi["place_id"]) for poi in matches)
        # A scheduled place also counts when its own name contains the term
        # (a must-visit that reached the schedule without a pool entry).
        is_scheduled = bool(place_ids & scheduled_ids) or any(
            lowered in experience.name.lower() for experience in scheduled
        )
        resolutions.append(
            MustVisitResolution(
                term=term,
                grounded_place_ids=place_ids,
                grounded_names=tuple(str(poi.get("name") or "") for poi in matches),
                scheduled=is_scheduled,
            )
        )
    return resolutions


def must_visit_place_ids(planning_state: PlanningState) -> set[str]:
    """Provider ids of every place grounded for a requested must-visit."""
    return {place_id for resolution in resolve_must_visits(planning_state) for place_id in resolution.grounded_place_ids}
