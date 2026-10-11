"""Candidate usefulness (Phase Q2): limited-slot selection.

Candidate quality (`CandidateQualityService`) answers "may this grounded
place compete for a slot?". This module answers the other question: "among
the places that may, which deserve a limited itinerary slot for THIS
traveller?". It owns the ONE usefulness ordering every limited-slot decision
reads -- the bounded reasoning request, the deterministic selection and the
bounded post-passes. Nothing else may define one.

Evidence (each a yes/no read from stored data; no provider, model, routing
or distance is involved):

  * `must_visit`            -- the provider identity grounded for a user's
                               must-visit term (Q1); never a name comparison;
  * `semantic_anchor`       -- a grounded, promoted AI proposal, by provider
                               place id (`grounded_anchor_place_ids`); when
                               interests were requested it counts only if it
                               serves one (the planner's existing anchor rule);
  * `provider_significance` -- PLACE-LEVEL provider evidence only
                               (`PLACE_LEVEL_SIGNIFICANCE_SIGNALS`): never
                               the kind of place, never a bare wikidata id;
  * `interest_fit`          -- provider-derived `matched_interests`; counted
                               for nobody when no interest was requested.

`evidence_band` is how many of the last three hold (0-3), and is 0 unless the
candidate passes the band safety gate (`_passes_band_gate`). An anchor or a
significance tag on a candidate the gate holds back earns no preference at
all, so usefulness can never lift a place the quality rules restricted.

Usefulness only ORDERS eligible candidates. It never changes a tier, a
score, a reject reason, a cap or the eligible set. A band is a count of
stored planning evidence -- never popularity, a rating or a ranking, and
band 0 only means "no such evidence is stored", not that a place is poor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.models.candidate_quality import CandidateQualityScore, CandidateQualityTier
from app.models.planning_state import PlanningState
from app.services import place_taxonomy as taxonomy
from app.services.grounded_anchors import grounded_anchor_place_ids
from app.services.must_visit_matching import must_visit_place_ids

# Provider evidence about THIS place: a Wikipedia article, or a heritage
# designation the provider carries for it. Deliberately absent:
#   * `major_historic_type` -- a fact about the KIND of place (castle, fort,
#     tomb, temple ...). The quality stage already rewards the kind in its
#     score and keeps using that signal; counted here it would let every
#     place of such a kind outrank equally suitable places by type alone;
#   * a bare `wikidata` id -- carried by countless small local features.
# A subset of the quality stage's own strong set (kept so by a test).
PLACE_LEVEL_SIGNIFICANCE_SIGNALS = frozenset({"wikipedia", "heritage"})

TIER_RANK: dict[CandidateQualityTier, int] = {
    CandidateQualityTier.PRIMARY_ANCHOR: 4,
    CandidateQualityTier.GOOD_CANDIDATE: 3,
    CandidateQualityTier.SECONDARY_CANDIDATE: 2,
    CandidateQualityTier.LOW_PRIORITY: 1,
    CandidateQualityTier.REJECTED: 0,
}
# Usefulness evidence is honoured only in the two top quality tiers.
BAND_MIN_TIER_RANK = TIER_RANK[CandidateQualityTier.GOOD_CANDIDATE]
_ART_INTEREST = "art"


@dataclass(frozen=True)
class QualityEvidence:
    """The stored quality facts usefulness reads about one candidate."""

    tier_rank: int
    score: float
    significance_signals: tuple[str, ...] = ()
    matched_interests: tuple[str, ...] = ()
    has_reject_reason: bool = False
    low_value_object: bool = False
    notable_object: bool = False
    commercial_gallery: bool = False


def evidence_from_score(score: CandidateQualityScore) -> QualityEvidence:
    return QualityEvidence(
        tier_rank=TIER_RANK.get(score.quality_tier, 0),
        score=score.total_score,
        significance_signals=tuple(score.significance_signals),
        matched_interests=tuple(score.matched_interests),
        has_reject_reason=bool(score.reject_reasons),
        low_value_object=score.low_value_object,
        notable_object=score.notable_object,
        commercial_gallery=score.commercial_gallery,
    )


@dataclass(frozen=True)
class CandidateUsefulness:
    must_visit: bool
    # Raw fact: the place is a grounded, promoted AI proposal.
    grounded_anchor: bool
    # The three evidences as they COUNT (false whenever the band gate fails).
    semantic_anchor: bool
    provider_significance: bool
    interest_fit: bool
    evidence_band: int
    # Place-level provider signal names, listed only when they count.
    provider_evidence: tuple[str, ...]
    tier_rank: int
    score: float
    name: str
    place_id: str

    @property
    def preference(self) -> tuple[int, int, int, int, int]:
        """The usefulness preference, best first when sorted ascending:
        must-visit, evidence band, semantic anchor, provider significance,
        quality tier."""
        return (
            0 if self.must_visit else 1,
            -self.evidence_band,
            0 if self.semantic_anchor else 1,
            0 if self.provider_significance else 1,
            -self.tier_rank,
        )

    @property
    def tail(self) -> tuple[float, str, str]:
        """Quality score, then a NEUTRAL deterministic tie-break (normalised
        name, provider place id). The tie-break carries no tourism-preference
        meaning: it exists only so that provider result order and list
        position never decide anything."""
        return (-self.score, self.name, self.place_id)

    @property
    def sort_key(self) -> tuple:
        """The canonical ordering: `preference` then `tail`, ascending."""
        return (*self.preference, *self.tail)

    @property
    def weakness_key(self) -> tuple:
        """Ascending = the stop that should give way first (the reverse of
        `preference`, then the lower score). No tie-break: callers add their
        own (e.g. which exchange keeps a day tighter)."""
        return (*(-part for part in self.preference), self.score)


def _passes_band_gate(evidence: QualityEvidence, art_focused: bool) -> bool:
    """The safety restrictions under which usefulness evidence counts: a
    primary/good quality tier, no reject reason, and not a single object
    (low-value or documented) or a commercial gallery -- an art-focused
    request keeps galleries in, as the planner's own dilution rule does.

    Known limitation, left for a later review: the planner also keeps
    ARTWORKS in for an art-focused request, but a stored quality score does
    not record the object's kind, so a single object never counts here --
    even a documented artwork on an art trip. Eligibility is unaffected.

    These are the same RESTRICTIONS the corroborated-anchor boost applies,
    not that boost's logic: the boost also requires a high proposal
    confidence and strong significance, and neither is a condition here.
    """
    if evidence.tier_rank < BAND_MIN_TIER_RANK or evidence.has_reject_reason:
        return False
    if evidence.low_value_object or evidence.notable_object:
        return False
    return not (evidence.commercial_gallery and not art_focused)


def assess(
    evidence: QualityEvidence,
    *,
    must_visit: bool,
    grounded_anchor: bool,
    canonical_interests: Iterable[str],
    name: str,
    place_id: str,
) -> CandidateUsefulness:
    """The usefulness of ONE already-eligible candidate. Pure."""
    interests = list(canonical_interests)
    gated = _passes_band_gate(evidence, _ART_INTEREST in interests)
    strong = sorted(set(evidence.significance_signals) & PLACE_LEVEL_SIGNIFICANCE_SIGNALS)
    fits = bool(set(evidence.matched_interests) & set(interests))
    # An anchor counts as the planner's anchor rules already require: when
    # interests were requested it must serve one of them.
    anchor = gated and grounded_anchor and (fits or not interests)
    significance = gated and bool(strong)
    interest_fit = gated and fits
    return CandidateUsefulness(
        must_visit=must_visit,
        grounded_anchor=grounded_anchor,
        semantic_anchor=anchor,
        provider_significance=significance,
        interest_fit=interest_fit,
        evidence_band=int(anchor) + int(significance) + int(interest_fit),
        provider_evidence=tuple(strong) if significance else (),
        tier_rank=evidence.tier_rank,
        score=evidence.score,
        name=" ".join(str(name or "").casefold().split()),
        place_id=str(place_id or ""),
    )


def requested_canonical_interests(planning_state: PlanningState) -> list[str]:
    profile = planning_state.traveler_profile
    terms = profile.interests if profile else planning_state.trip_request.interests
    return taxonomy.canonical_interests(terms)


def usefulness_by_place_id(planning_state: PlanningState) -> dict[str, CandidateUsefulness]:
    """`{provider place id: usefulness}` for every scored attraction
    candidate of a stored state (the broad pool first, then targeted-lookup
    anchors). Read-only; what the reporting tools use."""
    report = planning_state.candidate_quality_report
    if report is None:
        return {}
    must_visit_ids = must_visit_place_ids(planning_state)
    anchor_ids = grounded_anchor_place_ids(planning_state)
    interests = requested_canonical_interests(planning_state)
    result: dict[str, CandidateUsefulness] = {}
    for score in (*report.attraction_scores, *report.ai_directed_scores):
        if score.candidate_id in result:
            continue
        result[score.candidate_id] = assess(
            evidence_from_score(score),
            must_visit=score.candidate_id in must_visit_ids,
            grounded_anchor=score.candidate_id in anchor_ids,
            canonical_interests=interests,
            name=score.candidate_name,
            place_id=score.candidate_id,
        )
    return result
