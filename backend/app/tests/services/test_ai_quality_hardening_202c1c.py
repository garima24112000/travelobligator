from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalType,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.ai_provider_discovery import AIProviderDiscoveryAttemptStatus
from app.models.candidate_quality import CandidateQualityTier
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.planning_state import (
    DailyPlan,
    DestinationContext,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelerProfile,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.models.providers import NormalizedPlace, ProviderResponse
from app.models.targeted_regeneration_plan import TravelerProfileMutation
from app.providers.base import PlacesProvider, unavailable_response
from app.providers.gateway import ProviderGateway
from app.services import place_taxonomy as taxonomy
from app.services.ai_directed_provider_discovery_service import AIDirectedProviderDiscoveryService
from app.services.candidate_quality_service import CandidateQualityService
from app.services.plan_quality_findings import build_plan_quality_findings
from app.services.targeted_regeneration_executor import TargetedRegenerationExecutor

# Section 202C.1C: deterministic regression tests for the three product-quality
# blockers found by the 202C.1B live acceptance run. No network, no LLM.

_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "dc_candidate_pool_202c1c.json").read_text()
)
_QUALITY = CandidateQualityService()


def _place(name: str, tags: dict[str, str] | None, *, category: str | None = None, confidence: float = 0.6,
           place_id: str = "node/1") -> NormalizedPlace:
    return NormalizedPlace(
        place_id=place_id, name=name, category=category, coordinates=GeoPoint(lat=38.9, lng=-77.0),
        source="openstreetmap_places", data_status=DataStatus.LIVE, confidence=confidence, provider_tags=tags,
    )


# =====================================================================================
# Proposal grounding budget
# =====================================================================================


class _Places(PlacesProvider):
    provider_name = "fake_places_provider"

    def __init__(self, found: set[str]) -> None:
        self.found = found
        self.calls: list[str] = []

    def search_must_visit_place(self, must_visit_term, primary_destination, filters=None):
        self.calls.append(must_visit_term)
        if must_visit_term not in self.found:
            return unavailable_response(self.provider_name, self.provider_type, unavailable_fields=["x"], message="none")
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name, provider_type=self.provider_type, status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE, confidence=0.5, message="found",
            data=[_place(must_visit_term, {"tourism": "museum"}, place_id=f"way/{abs(hash(must_visit_term)) % 10**6}", confidence=0.5)],
        )


def _named(index: int, name: str, confidence: float) -> AICandidateProposal:
    return AICandidateProposal(
        proposal_id=f"proposal_{index:03d}", candidate_name=name, candidate_type=AICandidateType.ATTRACTION,
        why_consider="Worth checking.", confidence=confidence,
        verification_requirements=[AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION],
    )


def _query(index: int, search_query: str, confidence: float) -> AICandidateProposal:
    return AICandidateProposal(
        proposal_id=f"proposal_{index:03d}", proposal_type=AICandidateProposalType.DISCOVERY_QUERY,
        search_query=search_query, candidate_type=AICandidateType.ATTRACTION, why_consider="An idea.",
        confidence=confidence,
        verification_requirements=[AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION],
    )


def _state() -> PlanningState:
    return PlanningState(
        trip_request=TripRequest(
            primary_destination="Washington, DC, USA", start_date="2026-10-11", end_date="2026-10-13",
            travelers_count=2, travel_group_type=TravelGroupType.COUPLE, interests=["history"],
        )
    )


def _discover(proposals: list[AICandidateProposal], found: set[str], **bounds: int):
    places = _Places(found)
    result = AIDirectedProviderDiscoveryService(gateway=ProviderGateway(places=places)).discover(
        _state(), proposals, [], **bounds
    )
    return places, result


def test_lookups_are_ranked_by_proposal_confidence_not_list_position() -> None:
    proposals = [_named(1, "Low", 0.4), _named(2, "Mid", 0.6), _named(3, "High", 0.9), _named(4, "Top", 0.95)]
    places, result = _discover(proposals, {"High", "Top"}, max_searches=2, max_extra_searches=0)
    assert places.calls == ["Top", "High"]  # previously the first two by position: Low, Mid
    assert result.matched_count == 2 and result.searched_count == 2


def test_named_places_are_looked_up_before_free_text_ideas() -> None:
    proposals = [_query(1, "historic neighbourhood walk", 0.95), _named(2, "Named A", 0.7), _named(3, "Named B", 0.6)]
    places, _ = _discover(proposals, {"Named A", "Named B"}, max_searches=2, max_extra_searches=0)
    assert places.calls == ["Named A", "Named B"]


def test_duplicate_queries_cost_one_lookup() -> None:
    proposals = [_named(1, "National Gallery", 0.9), _named(2, "national  gallery", 0.8), _named(3, "Other", 0.7)]
    places, result = _discover(proposals, {"National Gallery", "Other"}, max_searches=5, max_extra_searches=3)
    assert places.calls == ["National Gallery", "Other"]
    duplicate = next(a for a in result.attempts if a.proposal_id == "proposal_002")
    assert duplicate.status == AIProviderDiscoveryAttemptStatus.NOT_SEARCHED and "same query" in duplicate.message


def test_reserve_is_spent_only_to_replace_an_empty_lookup_and_then_stops() -> None:
    """The 202C.1B shape: 5 base lookups, one comes back empty, and the next
    high-confidence named landmark (position 6) was never searched."""
    names = ["Lincoln", "WWII", "NMAAHC", "Gallery", "Archives", "Capitol", "Tidal Basin", "Georgetown"]
    confidences = [0.95, 0.9, 0.88, 0.85, 0.8, 0.78, 0.75, 0.7]
    proposals = [_named(i, n, c) for i, (n, c) in enumerate(zip(names, confidences), start=1)]
    places, result = _discover(proposals, set(names) - {"Lincoln"}, max_searches=5, max_extra_searches=3)
    assert places.calls == ["Lincoln", "WWII", "NMAAHC", "Gallery", "Archives", "Capitol"]  # 5 base + 1 reserve
    assert result.matched_count == 5  # enough -> the reserve stops; Tidal Basin/Georgetown stay honest not_searched
    assert [a.status.value for a in result.attempts[-2:]] == ["not_searched", "not_searched"]


def test_provider_calls_never_exceed_base_plus_reserve() -> None:
    proposals = [_named(i, f"Place {i}", 0.9) for i in range(1, 16)]
    places, result = _discover(proposals, set(), max_searches=5, max_extra_searches=3)  # nothing ever matches
    assert len(places.calls) == 8 and result.searched_count == 8
    assert sum(a.status == AIProviderDiscoveryAttemptStatus.NOT_SEARCHED for a in result.attempts) == 7


def test_reserve_is_not_spent_on_vague_queries_or_low_confidence_names() -> None:
    proposals = [_named(i, f"Base {i}", 0.9) for i in range(1, 6)]
    proposals += [_query(6, "day trip clusters", 0.9), _named(7, "Unsure Place", 0.5)]
    places, _ = _discover(proposals, set(), max_searches=5, max_extra_searches=3)
    assert len(places.calls) == 5  # nothing eligible for the reserve


def test_default_ceiling_is_eight_lookups() -> None:
    from app.core.config import Settings

    settings = Settings(_env_file=None)
    assert settings.ai_directed_provider_discovery_max_searches == 5
    assert settings.ai_directed_provider_discovery_max_extra_searches == 3


# =====================================================================================
# Promoted-anchor ranking
# =====================================================================================


def _score(place: NormalizedPlace, interests: list[str] | None = None):
    return _QUALITY.score_attraction(place, user_interests=interests or ["history", "museums"])


def test_corroborated_promoted_anchor_outranks_the_minor_museum_tail() -> None:
    promoted = _score(_place("National Gallery of Art", {"tourism": "museum", "wikidata": "Q1", "wikipedia": "en:X"}, confidence=0.5))
    minor = _score(_place("Small Local Museum", {"tourism": "museum", "wikidata": "Q2"}, confidence=0.6))
    documented_minor = _score(_place("Niche Museum", {"tourism": "museum", "wikidata": "Q3", "wikipedia": "en:Y"}, confidence=0.6))
    assert promoted.total_score < minor.total_score  # the 202C.1B weakness: lower lookup confidence alone decided it

    boosted = _QUALITY.apply_corroborated_anchor_boost(promoted, proposal_confidence=0.85)
    assert boosted.total_score > documented_minor.total_score > minor.total_score
    assert boosted.total_score - promoted.total_score == pytest.approx(0.05)
    assert boosted.quality_tier == CandidateQualityTier.PRIMARY_ANCHOR
    assert boosted.score_components["provider_confidence"] == 0.5  # provider facts untouched


@pytest.mark.parametrize(
    "tags, proposal_confidence",
    [
        ({"tourism": "museum", "wikidata": "Q1", "wikipedia": "en:X"}, 0.6),  # AI not confident enough
        ({"tourism": "museum", "wikidata": "Q1"}, 0.95),  # no STRONG provider evidence: AI confidence alone earns nothing
        ({"tourism": "museum"}, 0.95),
        ({"historic": "memorial", "memorial": "plaque"}, 0.95),  # low-value object
        ({"tourism": "artwork", "wikipedia": "en:X"}, 0.95),  # notable single object
        ({"amenity": "hospital"}, 0.95),  # unsuitable / rejected
    ],
)
def test_boost_never_applies_without_strong_provider_evidence_or_to_guarded_candidates(tags, proposal_confidence) -> None:
    score = _score(_place("Something", tags, confidence=0.5))
    assert _QUALITY.apply_corroborated_anchor_boost(score, proposal_confidence) == score


# =====================================================================================
# Low-value objects never displace a strong anchor
# =====================================================================================

_ANCHOR = {"tourism": "museum", "wikidata": "Q1", "wikipedia": "en:X"}


@pytest.mark.parametrize(
    "name, tags",
    [
        ("Traveling Carousel", {"tourism": "attraction", "historic": "yes", "wikidata": "Q4", "wikipedia": "en:C", "attraction": "carousel"}),
        ("Plain Carousel", {"tourism": "attraction", "attraction": "carousel"}),
        ("Unnamed Ruins", {"historic": "ruins"}),
        ("Undocumented Pillory", {"historic": "pillory", "wikidata": "Q5"}),
        ("Bronze Statue", {"tourism": "artwork", "artwork_type": "statue"}),
        ("Minor Memorial", {"historic": "memorial", "memorial": "plaque"}),
        ("Decorative Fountain", {"amenity": "fountain", "tourism": "attraction"}),
    ],
)
def test_minor_object_cannot_outrank_a_grounded_primary_anchor(name: str, tags: dict[str, str]) -> None:
    anchor = _QUALITY.apply_corroborated_anchor_boost(_score(_place("Anchor Museum", _ANCHOR, confidence=0.5)), 0.85)
    minor = _score(_place(name, tags))
    assert anchor.quality_tier == CandidateQualityTier.PRIMARY_ANCHOR
    assert minor.quality_tier != CandidateQualityTier.PRIMARY_ANCHOR, (name, minor.total_score)
    assert anchor.total_score - minor.total_score > 0.1  # not "a few hundredths"


def test_a_ride_is_a_single_object_not_a_landmark_but_a_theme_park_is_untouched() -> None:
    ride = taxonomy.classify_place({"tourism": "attraction", "attraction": "carousel", "wikipedia": "en:C", "historic": "yes"})
    assert ride.object_kind == taxonomy.OBJECT_RIDE and ride.notable_object
    assert taxonomy.LANDMARK not in ride.categories
    park = taxonomy.classify_place({"tourism": "theme_park", "attraction": "roller_coaster", "wikipedia": "en:P"})
    assert park.object_kind is None and taxonomy.ENTERTAINMENT in park.categories


def test_ruins_need_corroboration_to_count_as_significant() -> None:
    assert taxonomy.significance_signals({"historic": "ruins"}) == ()
    assert "major_historic_type" in taxonomy.significance_signals({"historic": "ruins", "wikipedia": "en:R"})
    assert "major_historic_type" in taxonomy.significance_signals({"historic": "castle"})  # unambiguous types unchanged


# =====================================================================================
# Architecture taxonomy
# =====================================================================================


def _matches_architecture(tags: dict[str, str]) -> bool:
    return "architecture" in taxonomy.matched_interests(taxonomy.classify_place(tags), ["architecture"])


@pytest.mark.parametrize(
    "tags",
    [
        {"tourism": "attraction", "heritage": "2", "historic": "pillory", "wikidata": "Q1", "wikipedia": "en:Pillory"},  # live case
        {"amenity": "restaurant", "historic": "yes", "wikidata": "Q2"},  # live case: historic dairy restaurant
        {"tourism": "attraction"},
        {"tourism": "attraction", "wikidata": "Q3", "wikipedia": "en:Something"},
        {"historic": "memorial", "wikipedia": "en:Memorial", "tourism": "attraction"},
        {"tourism": "artwork", "artwork_type": "sculpture", "wikipedia": "en:S"},
        {"historic": "yes"},
        {"historic": "monument", "wikipedia": "en:M"},
        {"historic": "archaeological_site", "wikipedia": "en:A"},
        {"tourism": "museum", "wikipedia": "en:Mu"},
        {"amenity": "place_of_worship"},
        {"tourism": "viewpoint"},
        {"place": "suburb", "heritage": "2", "historic": "heritage", "wikipedia": "en:District"},
    ],
)
def test_generic_attraction_or_history_evidence_is_not_architecture(tags: dict[str, str]) -> None:
    assert not _matches_architecture(tags), tags


@pytest.mark.parametrize(
    "tags",
    [
        {"building": "cathedral"},
        {"building": "church", "amenity": "place_of_worship"},
        {"historic": "castle"},
        {"historic": "palace", "tourism": "attraction"},
        {"historic": "building", "wikipedia": "en:B"},
        {"historic": "monastery"},
        {"man_made": "tower", "tourism": "attraction"},
        {"man_made": "lighthouse"},
        {"tourism": "attraction", "architect": "Someone"},
        {"building": "yes", "building:architecture": "art_nouveau"},
        {"amenity": "place_of_worship", "heritage": "1"},
    ],
)
def test_built_structure_evidence_is_architecture(tags: dict[str, str]) -> None:
    assert _matches_architecture(tags), tags


def test_architecture_tags_survive_the_provider_tag_whitelist() -> None:
    kept = taxonomy.filter_provider_tags({"architect": "X", "building:architecture": "baroque", "phone": "1", "opening_hours": "24/7"})
    assert kept == {"architect": "X", "building:architecture": "baroque"}


def test_history_matching_is_unchanged_for_the_same_places() -> None:
    pillory = taxonomy.classify_place({"tourism": "attraction", "historic": "pillory", "wikipedia": "en:P"})
    assert taxonomy.matched_interests(pillory, ["history", "architecture"]) == ["history"]


# =====================================================================================
# Interest change -> re-scoring, and honest zero coverage
# =====================================================================================

_LISBON_POOL = [
    {"place_id": "node/1", "name": "Pelourinho", "category": "attraction", "coordinates": {"lat": 38.71, "lng": -9.13},
     "source": "openstreetmap_places", "data_status": "live", "confidence": 0.6,
     "provider_tags": {"tourism": "attraction", "heritage": "2", "historic": "pillory", "wikidata": "Q1", "wikipedia": "en:P"}},
    {"place_id": "node/2", "name": "Leitaria", "category": "restaurant", "coordinates": {"lat": 38.712, "lng": -9.14},
     "source": "openstreetmap_places", "data_status": "live", "confidence": 0.6,
     "provider_tags": {"amenity": "restaurant", "historic": "yes", "wikidata": "Q2"}},
    {"place_id": "node/3", "name": "Museu", "category": "museum", "coordinates": {"lat": 38.713, "lng": -9.15},
     "source": "openstreetmap_places", "data_status": "live", "confidence": 0.6,
     "provider_tags": {"tourism": "museum", "wikidata": "Q3"}},
]
_CATHEDRAL = {"place_id": "way/4", "name": "Sé", "category": "cathedral", "coordinates": {"lat": 38.71, "lng": -9.133},
              "source": "openstreetmap_places", "data_status": "live", "confidence": 0.6,
              "provider_tags": {"building": "cathedral", "amenity": "place_of_worship", "heritage": "1", "wikipedia": "en:Se"}}


def _lisbon_state(pool: list[dict[str, Any]], interests: list[str]) -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Lisbon, Portugal", start_date="2026-09-10", end_date="2026-09-10",
            travelers_count=2, travel_group_type=TravelGroupType.COUPLE, interests=list(interests),
        )
    )
    state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE, travelers_count=2, pace=TripPace.BALANCED, interests=list(interests)
    )
    state.destination_context = DestinationContext(destination_name="Lisbon, Portugal", candidate_pois=pool)
    state.candidate_quality_report = _QUALITY.build_report(state)
    return state


def _by_name(state: PlanningState) -> dict[str, Any]:
    return {s.candidate_name: s for s in state.candidate_quality_report.attraction_scores}


def _apply_interest_change(state: PlanningState, add: list[str]) -> None:
    executor = TargetedRegenerationExecutor()
    before = executor._effective_interests(state)
    executor._apply_profile_mutation(state, TravelerProfileMutation(interests_to_add=add))
    assert executor._effective_interests(state) != before
    executor._rescore_candidates_for_changed_interests(state)


def test_interest_change_rescores_the_existing_pool_without_any_provider_call() -> None:
    state = _lisbon_state([*_LISBON_POOL, _CATHEDRAL], ["food", "history"])
    before = _by_name(state)
    generated_before = state.candidate_quality_report.generated_at
    pool_before = json.dumps(state.destination_context.candidate_pois, sort_keys=True)
    assert "architecture" not in before["Sé"].matched_interests

    _apply_interest_change(state, ["architecture"])  # the executor has no gateway call on this path

    after = _by_name(state)
    assert state.candidate_quality_report.generated_at > generated_before
    assert "architecture" in after["Sé"].matched_interests  # a genuine match gains relevance
    assert after["Sé"].total_score > before["Sé"].total_score
    assert after["Museu"].total_score == before["Museu"].total_score  # unrelated scores do not move
    assert json.dumps(state.destination_context.candidate_pois, sort_keys=True) == pool_before  # facts untouched


def test_zero_genuine_coverage_is_reported_and_nothing_is_relabelled() -> None:
    """The 202C.1B Lisbon shape: no architecture candidate exists. The pillory
    and the historic restaurant must NOT become architecture, and the plan must
    say so through the existing coverage finding."""
    state = _lisbon_state(list(_LISBON_POOL), ["food", "history"])
    _apply_interest_change(state, ["architecture"])

    scores = _by_name(state)
    assert all("architecture" not in s.matched_interests for s in scores.values())
    assert "history" in scores["Pelourinho"].matched_interests  # the factual taxonomy is preserved

    state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(
                day_number=1,
                date=date(2026, 9, 10),
                experiences=[
                    ExperienceItem(
                        experience_id=f"exp_{i}", name=poi["name"], category="attraction", day_number=1, stop_order=i,
                        provider_place_id=poi["place_id"], provider_source="openstreetmap_places",
                        matched_interests=list(scores[poi["name"]].matched_interests),
                    )
                    for i, poi in enumerate(_LISBON_POOL, start=1)
                ],
            )
        ]
    )
    issues, notes = build_plan_quality_findings(state)
    limited = [i for i in issues if i.category == "interest_supply_limited" and "'architecture'" in i.message]
    assert len(limited) == 1
    assert any("architecture" in note for note in notes)
    assert not [i for i in issues if i.category == "interest_undercoverage" and "'architecture'" in i.message]


# =====================================================================================
# Washington DC deterministic replay (regression fixture, no production rule)
# =====================================================================================


def _dc_scores():
    interests = _FIXTURE["interests"]
    broad = [_QUALITY.score_attraction(poi, user_interests=interests) for poi in _FIXTURE["broad_pool"]]
    promoted = []
    for row in _FIXTURE["targeted_lookups"]:
        place = NormalizedPlace(
            place_id=row["place_id"], name=row["name"], category=row["category"],
            coordinates=GeoPoint(**row["coordinates"]), source="openstreetmap_places",
            data_status=DataStatus.LIVE, confidence=0.5, provider_tags=row["provider_tags"],
        )
        score = _QUALITY.score_provider_backed_candidate(place, "attraction", user_interests=interests)
        promoted.append(_QUALITY.apply_corroborated_anchor_boost(score, row["proposal_confidence"]))
    return broad, promoted


def test_dc_replay_promoted_anchors_lead_and_minor_objects_do_not() -> None:
    broad, promoted = _dc_scores()
    ranked = sorted([*broad, *promoted], key=lambda s: -s.total_score)
    top_names = [s.candidate_name for s in ranked[:9]]  # a 3-day balanced trip schedules 9 stops

    assert all(p.quality_tier == CandidateQualityTier.PRIMARY_ANCHOR for p in promoted)
    must_visit = {row["name"] for row in _FIXTURE["targeted_lookups"] if row.get("must_visit")}
    for p in promoted:
        if p.candidate_name in must_visit:
            continue  # a must-visit is scheduled by the planner regardless of rank
        assert p.candidate_name in top_names, (p.candidate_name, p.total_score, top_names)

    by_name = {s.candidate_name: s for s in broad}
    for minor in ("All Hallows Guild Traveling Carousel", "Bay-Eva Castle"):  # the 202C.1B picks
        assert minor not in top_names
        assert by_name[minor].quality_tier != CandidateQualityTier.PRIMARY_ANCHOR
        anchors = [p for p in promoted if p.candidate_name not in must_visit]
        assert min(p.total_score for p in anchors) - by_name[minor].total_score > 0.1


def test_no_destination_specific_rule_exists_in_production_code() -> None:
    root = Path(__file__).resolve().parents[2]
    needles = ("washington", "lincoln memorial", "all hallows", "bay-eva", "national gallery", "lisbon", "pelourinho")
    offenders = []
    changed = [
        root / "services" / name
        for name in (
            "place_taxonomy.py", "candidate_quality_service.py", "ai_directed_provider_discovery_service.py",
            "targeted_regeneration_executor.py", "ai_candidate_discovery_service.py",
        )
    ]
    for path in [*changed, *(root / "providers" / "ai_itinerary_reasoning").glob("*.py")]:
        text = path.read_text().lower()
        code = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
        for needle in needles:
            if f'"{needle}' in code or f"'{needle}" in code:
                offenders.append((path.name, needle))
    assert offenders == []
