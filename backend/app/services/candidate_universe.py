"""The one attraction candidate universe (Phase Q2 correction).

The deterministic planner and the bounded AI reasoning request must offer the
SAME places: a candidate the model may cite has to be one the planner can
schedule, or a perfectly valid AI plan is discarded for naming it. Both read
this module; neither keeps its own rule.

The universe is the schedulable broad-pool candidates plus the promoted
(grounded, AI-proposed) candidates that are not already in it. A promoted
candidate is left out only when it IS a place the universe already holds:

  1. the same provider place id as a broad-pool candidate -- the provider
     place id is the authoritative scheduling identity, so the pool record
     (and its own quality verdict) stands for the place;
  2. the same Wikidata entity as a SCHEDULABLE candidate that is also close
     by (`SAME_WIKIDATA_METERS`) -- the provider-entity rule of
     `app.providers.places.entity_identity`;
  3. the same comparable name as a SCHEDULABLE candidate that is also close
     by (`SAME_NAME_METERS`) -- the promotion stage's own same-place rule.

A shared name alone conflates nothing: two distinct places with one name
both stay, each under its own provider id. A broad-pool candidate that is
not schedulable (rejected or low-priority) suppresses nothing through rule 2
or 3 -- it cannot be scheduled, so the promoted place would otherwise be
schedulable by nobody. Nothing is transferred between the two records: a
must-visit is the provider identity it was grounded to (Q1) and a semantic
anchor is the provider identity that was promoted -- never a namesake.

Read-only: no provider, model or routing call, and nothing here ranks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.ai_candidate_promotion import PromotedAICandidate
from app.models.candidate_quality import CandidateQualityTier
from app.models.common import GeoPoint
from app.models.planning_state import PlanningState
from app.utils.geo import haversine_distance_km
from app.utils.names import same_name

# The tiers a candidate needs to be scheduled at all (the planner's and the
# promotion stage's own set).
SCHEDULABLE_TIERS = frozenset(
    {
        CandidateQualityTier.PRIMARY_ANCHOR,
        CandidateQualityTier.GOOD_CANDIDATE,
        CandidateQualityTier.SECONDARY_CANDIDATE,
    }
)
# Kept equal (by a test) to the rules they restate: the promotion stage's
# same-place distance and the entity-identity Wikidata distance.
SAME_NAME_METERS = 150.0
SAME_WIKIDATA_METERS = 1000.0

REASON_MISSING_COORDINATES = "missing_coordinates"
REASON_SAME_PLACE = "same_place"


def schedulable_broad_pois(planning_state: PlanningState) -> list[dict[str, Any]]:
    """The broad-pool attraction candidates quality lets compete, in pool
    order. With no quality report for this pool every candidate is kept
    (the planner's legacy behaviour for an unscored pool)."""
    context = planning_state.destination_context
    pois = list(context.candidate_pois) if context is not None else []
    report = planning_state.candidate_quality_report
    scores = report.attraction_scores if report is not None else []
    if not scores or len(scores) != len(pois):
        return pois
    return [poi for poi, score in zip(pois, scores) if score.quality_tier in SCHEDULABLE_TIERS]


def _point(value: Any) -> GeoPoint | None:
    if isinstance(value, GeoPoint):
        return value
    if isinstance(value, dict) and value.get("lat") is not None and value.get("lng") is not None:
        try:
            return GeoPoint(lat=value["lat"], lng=value["lng"])
        except (TypeError, ValueError):
            return None
    return None


def _wikidata(tags: Any) -> str | None:
    value = tags.get("wikidata") if isinstance(tags, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _same_entity_nearby(
    name: str, point: GeoPoint | None, tags: Any, other_name: Any, other_point: GeoPoint | None, other_tags: Any
) -> bool:
    """Rules 2 and 3: an identity signal AND proximity. Never one alone."""
    if point is None or other_point is None:
        return False
    distance_km = haversine_distance_km(point, other_point)
    if distance_km is None:
        return False
    meters = distance_km * 1000.0
    wikidata = _wikidata(tags)
    if wikidata and wikidata == _wikidata(other_tags) and meters <= SAME_WIKIDATA_METERS:
        return True
    return meters <= SAME_NAME_METERS and same_name(name, str(other_name or ""))


@dataclass(frozen=True)
class PromotedResolution:
    """Which promoted candidates join the universe, and why the others do not."""

    accepted: list[PromotedAICandidate] = field(default_factory=list)
    # `(candidate, reason)` in report order; reasons are the REASON_* values.
    left_out: list[tuple[PromotedAICandidate, str]] = field(default_factory=list)


def resolve_promoted_candidates(planning_state: PlanningState) -> PromotedResolution:
    report = planning_state.ai_candidate_promotion_report
    if report is None or not report.promoted_candidates:
        return PromotedResolution()

    context = planning_state.destination_context
    broad_place_ids = {
        str(poi["place_id"]) for poi in (context.candidate_pois if context is not None else []) if poi.get("place_id")
    }
    schedulable = schedulable_broad_pois(planning_state)

    accepted: list[PromotedAICandidate] = []
    left_out: list[tuple[PromotedAICandidate, str]] = []
    for promoted in report.promoted_candidates:
        if promoted.coordinates is None:
            left_out.append((promoted, REASON_MISSING_COORDINATES))
            continue
        place_id = str(promoted.provider_place_id) if promoted.provider_place_id else None
        same_place = (
            (place_id is not None and place_id in broad_place_ids)
            or any(
                _same_entity_nearby(
                    promoted.name, promoted.coordinates, promoted.provider_tags,
                    poi.get("name"), _point(poi.get("coordinates")), poi.get("provider_tags"),
                )
                for poi in schedulable
            )
            or any(
                (place_id is not None and place_id == str(earlier.provider_place_id or ""))
                or _same_entity_nearby(
                    promoted.name, promoted.coordinates, promoted.provider_tags,
                    earlier.name, earlier.coordinates, earlier.provider_tags,
                )
                for earlier in accepted
            )
        )
        if same_place:
            left_out.append((promoted, REASON_SAME_PLACE))
        else:
            accepted.append(promoted)
    return PromotedResolution(accepted=accepted, left_out=left_out)
