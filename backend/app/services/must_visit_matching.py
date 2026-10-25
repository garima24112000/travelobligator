"""Must-visit resolution by PROVIDER IDENTITY (Section 203C.2B; Q1).

    user must-visit term
      -> the destination-context stage establishes the provider identity
      -> the term is recorded on that provider candidate
      -> every consumer (planner, candidate quality, validation, repairs)
         reads the recorded identity

The destination-context stage is the ONLY place a term is turned into an
identity (see `DestinationContextService._append_must_visit_candidates`).
It records the user's own term on the provider candidate it grounded:

  * `must_visit_terms` -- every requested term grounded to this place
    (several terms may name one place);
  * `must_visit_term`  -- the first of them, kept for stored states and
    readers that predate the list.

This module is the one reader of that record. A candidate is a must-visit
only when it carries a requested term; nothing here, and no consumer,
compares a place NAME with a term -- a related place whose name merely
contains (or equals) the user's words inherits nothing. No place is ever
invented.

Legacy states. A context stored before Q1 (`must_visit_grounding_version`
0) never recorded the term on a broad-pool hit. For those states only, a
term with no recorded candidate falls back to an exact comparable name --
and only when EXACTLY ONE provider-backed candidate of the whole pool has
that name. Uniqueness is a property of the pool, so this decision is made
once, here, and consumers take the resulting provider ids
(`legacy_must_visit_place_ids`); a per-candidate function cannot make it.

Final itinerary (Final Batch 1, A). Whether a term is satisfied is decided
against what is SCHEDULED, by identity: the grounded place itself, or a
scheduled stop that is the same entity -- the same Wikidata reference close
by, the existing merge rule (`entity_identity.SAME_WIKIDATA_METERS`). Still no
name is compared, so a scheduled place is never reported missing merely
because it was scheduled under a different provider record of itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from app.models.common import GeoPoint
from app.models.planning_state import PlanningState
from app.providers.places.entity_identity import SAME_WIKIDATA_METERS
from app.utils.geo import haversine_distance_km
from app.utils.names import comparable_name

# The version the destination-context stage writes once it has established
# must-visit identities under the rules above.
MUST_VISIT_GROUNDING_VERSION = 1

# Final Batch 1 (A): how the destination-context stage found a term
# (`DestinationContext.must_visit_lookups[].outcome`).
FOUND_BY_POOL_NAME = "pool_name"  # the one pool place the provider names exactly so
FOUND_BY_POOL_OTHER_NAME = "pool_other_name"  # ...by another name of its own record
FOUND_BY_LOOKUP = "lookup"  # the targeted lookup inside the destination
FOUND_BY_WIDE_LOOKUP = "lookup_wide"  # the wider lookup, and the place IS in the destination
EXCURSION_VERIFIED = "excursion_verified"  # outside; a verified driving route within the limit
EXCURSION_TOO_FAR = "excursion_too_far"  # outside; the verified driving route exceeds the limit
EXCURSION_ROUTE_UNVERIFIED = "excursion_route_unverified"  # outside; no route could be verified
UNRESOLVED = "unresolved"
# Set (True) on the candidate of a must-visit grounded as a verified excursion
# OUTSIDE the destination, with `excursion_drive_seconds` / `excursion_distance_km`.
OUTSIDE_DESTINATION_KEY = "outside_destination"
# A lookup that came back without a place for one of these reasons may be
# repeated as the wider excursion lookup; a provider FAILURE never is.
WIDER_LOOKUP_REASONS = frozenset(
    {"no_result", "not_a_specific_place", "weak_name_match", "low_confidence", "no_coordinates_or_identity",
     "outside_destination"}
)


@dataclass(frozen=True)
class MustVisitResolution:
    term: str
    grounded_place_ids: frozenset[str]
    grounded_names: tuple[str, ...]
    scheduled: bool
    # Provider ids of the SCHEDULED stops that satisfy the term (the grounded
    # place, or a scheduled record of the same entity). Empty when unscheduled.
    scheduled_place_ids: frozenset[str] = frozenset()

    @property
    def grounded(self) -> bool:
        return bool(self.grounded_place_ids)


def _field(candidate: Any, name: str) -> Any:
    return candidate.get(name) if isinstance(candidate, dict) else getattr(candidate, name, None)


def grounded_terms(candidate: Any) -> list[str]:
    """The user terms recorded on a candidate, in the order they were grounded."""
    terms = _field(candidate, "must_visit_terms")
    if isinstance(terms, (list, tuple)):
        return [str(term) for term in terms if term]
    term = _field(candidate, "must_visit_term")
    return [str(term)] if term else []


def record_grounded_term(candidate: dict[str, Any], term: str) -> None:
    """Records `term` on the provider candidate it was grounded to. Only the
    destination-context stage calls this."""
    terms = grounded_terms(candidate)
    if comparable_name(term) not in {comparable_name(existing) for existing in terms}:
        terms.append(term)
    candidate["must_visit_terms"] = terms
    candidate["must_visit_term"] = terms[0]


def is_tagged_must_visit(candidate: Any, terms: Iterable[str]) -> bool:
    """Whether the candidate carries one of the requested terms. Tag only:
    the candidate's name is never looked at."""
    recorded = {comparable_name(term) for term in grounded_terms(candidate)} - {""}
    return bool(recorded) and any(comparable_name(term) in recorded for term in terms if term)


def exact_name_candidates(
    pois: Iterable[dict[str, Any]],
    term: str,
    other_names: dict[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    """Provider-backed candidates whose provider name IS the term (comparable
    equality; never containment). A fact about the pool only -- the caller
    decides what one, none or several of them mean.

    `other_names` (Final Batch 1, A): by place id, the OTHER names the
    provider's own record gives the same place (its English, international,
    official or local name -- `entity_identity.alternate_names`). A candidate
    matches when any of its names IS the term, so a place recorded under its
    local name is found by the name the traveller used. Still equality only."""
    wanted = comparable_name(term)
    if not wanted:
        return []

    def names(poi: dict[str, Any]) -> list[Any]:
        return [poi.get("name"), *(other_names or {}).get(str(poi.get("place_id")), ())]

    return [poi for poi in pois if poi.get("place_id") and any(comparable_name(n) == wanted for n in names(poi))]


def requested_must_visits(planning_state: PlanningState) -> list[str]:
    profile = planning_state.traveler_profile
    terms = profile.must_visit if profile else planning_state.trip_request.must_visit
    return [term for term in terms if term and term.strip()]


def _is_legacy(planning_state: PlanningState) -> bool:
    context = planning_state.destination_context
    return context is not None and context.must_visit_grounding_version < MUST_VISIT_GROUNDING_VERSION


def _grounded_candidates(
    pois: list[dict[str, Any]], term: str, *, legacy: bool
) -> list[dict[str, Any]]:
    tagged = [poi for poi in pois if poi.get("place_id") and is_tagged_must_visit(poi, [term])]
    if tagged or not legacy:
        return tagged
    exact = exact_name_candidates(pois, term)
    return exact if len(exact) == 1 else []


def _same_entity(record: dict[str, Any] | None, grounded: dict[str, Any]) -> bool:
    """Two provider records of ONE entity: the same Wikidata reference and no
    farther apart than the existing merge rule allows. Identity evidence only
    -- never a name."""
    if record is None or record is grounded:
        return False
    first = str(((record.get("provider_tags") or {}).get("wikidata")) or "").strip()
    second = str(((grounded.get("provider_tags") or {}).get("wikidata")) or "").strip()
    if not first or first != second:
        return False
    a, b = record.get("coordinates") or {}, grounded.get("coordinates") or {}
    if a.get("lat") is None or b.get("lat") is None:
        return False
    distance_km = haversine_distance_km(GeoPoint(lat=a["lat"], lng=a["lng"]), GeoPoint(lat=b["lat"], lng=b["lng"]))
    return distance_km is not None and distance_km * 1000.0 <= SAME_WIKIDATA_METERS


def resolve_must_visits(planning_state: PlanningState) -> list[MustVisitResolution]:
    context = planning_state.destination_context
    pois = context.candidate_pois if context is not None else []
    plan = planning_state.experience_plan
    scheduled = [experience for day in (plan.daily_plans if plan else []) for experience in day.experiences]
    scheduled_ids = {experience.provider_place_id for experience in scheduled if experience.provider_place_id}
    legacy = _is_legacy(planning_state)
    pool_by_id = {str(poi["place_id"]): poi for poi in pois if poi.get("place_id")}

    resolutions: list[MustVisitResolution] = []
    for term in requested_must_visits(planning_state):
        matches = _grounded_candidates(pois, term, legacy=legacy)
        place_ids = frozenset(str(poi["place_id"]) for poi in matches)
        # The grounded place itself, or a scheduled record of the same entity.
        satisfying = frozenset(
            str(scheduled_id)
            for scheduled_id in scheduled_ids
            if str(scheduled_id) in place_ids
            or any(_same_entity(pool_by_id.get(str(scheduled_id)), grounded) for grounded in matches)
        )
        resolutions.append(
            MustVisitResolution(
                term=term,
                grounded_place_ids=place_ids,
                grounded_names=tuple(str(poi.get("name") or "") for poi in matches),
                # by provider identity only: one scheduled place satisfies every term grounded to it
                scheduled=bool(satisfying),
                scheduled_place_ids=satisfying,
            )
        )
    return resolutions


def must_visit_place_ids(planning_state: PlanningState) -> set[str]:
    """Provider ids of every place grounded for a requested must-visit."""
    return {place_id for resolution in resolve_must_visits(planning_state) for place_id in resolution.grounded_place_ids}


def legacy_must_visit_place_ids(planning_state: PlanningState) -> frozenset[str]:
    """Provider ids grounded ONLY through the legacy unique-exact-name
    fallback. Always empty for a state whose context was built under Q1, so
    consumers that add these ids to their tag check stay tag-only there."""
    if not _is_legacy(planning_state):
        return frozenset()
    context = planning_state.destination_context
    pois = context.candidate_pois if context is not None else []
    return frozenset(
        str(poi["place_id"])
        for term in requested_must_visits(planning_state)
        for poi in _grounded_candidates(pois, term, legacy=True)
        if not is_tagged_must_visit(poi, [term])
    )
