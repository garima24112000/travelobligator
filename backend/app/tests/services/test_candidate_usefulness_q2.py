from __future__ import annotations

import importlib.util
import inspect
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.config import get_settings
from app.models import DestinationContext, PlanningState, TravelGroupType, TripPace, TripRequest
from app.models.ai_itinerary_reasoning import (
    DEFAULT_ITINERARY_REASONING_INSTRUCTIONS,
    AIItineraryReasoningStatus,
    ItineraryReasoningCategory,
)
from app.models.ai_itinerary_repair import DEFAULT_ITINERARY_REPAIR_INSTRUCTIONS
from app.models.candidate_quality import CandidateQualityTier
from app.providers.ai_itinerary_reasoning import anthropic_adapter, groq_adapter
from app.services import candidate_quality_service as quality_module
from app.services import candidate_universe as universe
from app.services import candidate_usefulness as usefulness
from app.services import experience_planner_service as planner_module
from app.services import schedule_diversity as diversity
from app.services.ai_itinerary_reasoning_request_builder import AIItineraryReasoningRequestBuilder
from app.services.ai_itinerary_reasoning_service import (
    MUST_VISIT_EXCEEDS_REASONING_BOUND,
    AIItineraryReasoningService,
)
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.interest_coverage import interest_coverage
from app.services.must_visit_matching import MUST_VISIT_GROUNDING_VERSION, record_grounded_term
from app.services.plan_validator_service import PlanValidatorService
from app.tests.services.test_batch1_tuning_fixes_3b import _promoted, _promotion, _scheduled
from app.tests.services.test_experience_planner_ai_guided import _completed_result, _day
from app.tests.services.test_final_quality_correction_203c2b import _poi, _point, _restaurant

# Phase Q2: candidate usefulness / limited-slot selection. Every fixture is
# synthetic and provider-shaped; no city, landmark or real coordinate appears.

_START = date(2026, 9, 1)
_SOURCE = "geoapify_places"
_BACKEND_DIR = Path(__file__).resolve().parents[3]
_FORBIDDEN_CLAIM_WORDS = ("popular", "top rated", "top-rated", "best reviewed", "famous", "fame", "tourist ranking")


def _at(index: int) -> Any:
    """A point of one compact synthetic area (a ~200 m grid)."""
    return _point(50.000 + 0.002 * (index // 4), 10.000 + 0.002 * (index % 4))


def _museum(key: str, index: int, **tags: str) -> dict[str, Any]:
    return _poi(key, f"Museum {key}", _at(index), provider_tags={"tourism": "museum", **tags})


def _park(key: str, index: int, **tags: str) -> dict[str, Any]:
    return _poi(key, f"Park {key}", _at(index), category="park", provider_tags={"leisure": "park", **tags})


def _castle(key: str, index: int) -> dict[str, Any]:
    return _poi(key, f"Castle {key}", _at(index), category="castle", provider_tags={"historic": "castle"})


def _anchor_for(poi: dict[str, Any]) -> Any:
    """A grounded, promoted proposal for a place ALREADY in the broad pool
    (same provider place id), so it keeps its pool identity."""
    key = poi["place_id"].split("/", 1)[1]
    point = _point(poi["coordinates"]["lat"], poi["coordinates"]["lng"])
    return _promoted(key, poi["name"], point, category=poi["category"], provider_tags=poi["provider_tags"])


def _state(
    pois: list[dict[str, Any]],
    *,
    anchors: list[dict[str, Any]] | None = None,
    days: int = 3,
    pace: TripPace = TripPace.BALANCED,
    interests: list[str] | None = None,
    must_visit: dict[str, dict[str, Any]] | None = None,
    restaurants: list[dict[str, Any]] | None = None,
) -> PlanningState:
    """`must_visit` maps a user's term to the provider place it was grounded
    to (the Q1 identity tag) -- never matched by name."""
    for term, poi in (must_visit or {}).items():
        record_grounded_term(poi, term)
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Fixtureville", start_date=_START, end_date=_START + timedelta(days=days - 1),
            travelers_count=2, travel_group_type=TravelGroupType.COUPLE, pace=pace,
            interests=["history"] if interests is None else interests, must_visit=list(must_visit or {}),
        )
    )
    state.destination_context = DestinationContext(
        destination_name="Fixtureville", candidate_pois=pois, candidate_restaurants=restaurants or [],
        must_visit_grounding_version=MUST_VISIT_GROUNDING_VERSION,
    )
    state.candidate_quality_report = CandidateQualityService().build_report(state)
    if anchors is not None:
        state.ai_candidate_promotion_report = _promotion([_anchor_for(poi) for poi in anchors])
    return state


def _names(state: PlanningState) -> list[str]:
    return [name for day in _scheduled(state) for name in day]


def _planned(state: PlanningState) -> PlanningState:
    ExperiencePlannerService().run(state)
    return state


def _allowed(state: PlanningState) -> list[Any]:
    return AIItineraryReasoningRequestBuilder().build_request(state).allowed_candidates


def _allowed_names(state: PlanningState) -> list[str]:
    return [candidate.name for candidate in _allowed(state)]


@pytest.fixture
def reasoning_cap(monkeypatch: pytest.MonkeyPatch) -> Any:
    def set_cap(value: int) -> None:
        monkeypatch.setenv("AI_ITINERARY_REASONING_MAX_CANDIDATES", str(value))
        get_settings.cache_clear()

    yield set_cap
    get_settings.cache_clear()


# =====================================================================================
# 1. The canonical ordering: precedence validated pair by pair
# =====================================================================================

_PRIMARY = usefulness.TIER_RANK[CandidateQualityTier.PRIMARY_ANCHOR]
_GOOD = usefulness.TIER_RANK[CandidateQualityTier.GOOD_CANDIDATE]
_SECONDARY = usefulness.TIER_RANK[CandidateQualityTier.SECONDARY_CANDIDATE]


def _assess(
    name: str,
    *,
    tier: int = _GOOD,
    score: float = 0.6,
    significance: tuple[str, ...] = (),
    matched: tuple[str, ...] = (),
    must_visit: bool = False,
    anchor: bool = False,
    interests: tuple[str, ...] = ("history",),
    **flags: bool,
) -> usefulness.CandidateUsefulness:
    return usefulness.assess(
        usefulness.QualityEvidence(
            tier_rank=tier, score=score, significance_signals=significance, matched_interests=matched, **flags
        ),
        must_visit=must_visit, grounded_anchor=anchor, canonical_interests=interests, name=name, place_id=f"id/{name}",
    )


def _winner(*candidates: usefulness.CandidateUsefulness) -> str:
    return min(candidates, key=lambda candidate: candidate.sort_key).name


def test_a_grounded_must_visit_outranks_every_amount_of_other_evidence() -> None:
    requested = _assess("requested", tier=_GOOD, must_visit=True)
    richest = _assess("richest", tier=_PRIMARY, score=0.9, significance=("wikipedia", "heritage"),
                      matched=("history",), anchor=True)
    assert richest.evidence_band == 3 and requested.evidence_band == 0
    assert _winner(richest, requested) == "requested"


def test_more_independent_evidence_outranks_a_higher_tier_reached_by_place_type_alone() -> None:
    # A documented place that serves the request (two evidences, good tier) against a place whose primary
    # tier comes from its type and which only serves the request (one evidence). The tier of the second
    # says "this type of place is usually worth a slot"; the first is backed twice for THIS traveller.
    documented = _assess("documented", tier=_GOOD, score=0.62, significance=("wikipedia",), matched=("history",))
    by_type = _assess("by type", tier=_PRIMARY, score=0.78, matched=("history",))
    assert (documented.evidence_band, by_type.evidence_band) == (2, 1)
    assert _winner(by_type, documented) == "documented"
    # ... and three evidences against a primary-tier place with none
    backed = _assess("backed", tier=_GOOD, significance=("heritage",), matched=("history",), anchor=True)
    bare = _assess("bare", tier=_PRIMARY, score=0.8)
    assert (backed.evidence_band, bare.evidence_band) == (3, 0) and _winner(bare, backed) == "backed"


def test_at_equal_evidence_a_semantic_anchor_comes_before_provider_significance() -> None:
    # Both carry two evidences. The anchor was proposed for this request and then grounded; the other is
    # documented in general. The request-specific signal leads.
    anchor = _assess("anchor", matched=("history",), anchor=True)
    documented = _assess("documented", significance=("wikipedia",), matched=("history",))
    assert anchor.evidence_band == documented.evidence_band == 2
    assert _winner(documented, anchor) == "anchor"
    # with no interest requested the same holds for one evidence each
    anchor_only = _assess("anchor only", anchor=True, interests=())
    documented_only = _assess("documented only", significance=("heritage",), interests=())
    assert anchor_only.evidence_band == documented_only.evidence_band == 1
    assert _winner(documented_only, anchor_only) == "anchor only"


def test_beyond_coverage_provider_significance_comes_before_interest_fit_alone() -> None:
    # One evidence each. Interest COVERAGE is reserved separately (one place per requested interest), so
    # this pair only competes for the slots after that: there a documented place of another kind is
    # preferred to one more undocumented place of the kind already covered.
    documented = _assess("documented", significance=("wikipedia",))
    fits = _assess("fits", matched=("history",))
    assert documented.evidence_band == fits.evidence_band == 1
    assert _winner(fits, documented) == "documented"


def test_without_evidence_the_quality_tier_then_the_score_decide() -> None:
    primary = _assess("primary", tier=_PRIMARY, score=0.76)
    good_high = _assess("good high", tier=_GOOD, score=0.7)
    good_low = _assess("good low", tier=_GOOD, score=0.6)
    assert [c.name for c in sorted([good_low, good_high, primary], key=lambda c: c.sort_key)] == [
        "primary", "good high", "good low"
    ]


def test_the_tail_is_a_neutral_total_tie_break_so_input_order_decides_nothing() -> None:
    twins = [_assess(name) for name in ("delta", "alpha", "charlie", "bravo")]
    forward = sorted(twins, key=lambda c: c.sort_key)
    backward = sorted(reversed(twins), key=lambda c: c.sort_key)
    assert [c.name for c in forward] == [c.name for c in backward] == ["alpha", "bravo", "charlie", "delta"]
    assert len({c.sort_key for c in twins}) == len(twins)  # total: no two candidates tie


def test_the_band_gate_gives_no_preference_to_a_restricted_candidate() -> None:
    strong = {"significance": ("wikipedia", "heritage"), "matched": ("history",), "anchor": True}
    assert _assess("ok", **strong).evidence_band == 3
    for restricted in (
        _assess("secondary tier", tier=_SECONDARY, **strong),
        _assess("reject reason", has_reject_reason=True, **strong),
        _assess("low-value object", low_value_object=True, **strong),
        _assess("notable single object", notable_object=True, **strong),
        _assess("commercial gallery", commercial_gallery=True, **strong),
    ):
        assert restricted.evidence_band == 0, restricted.name
        assert not (restricted.semantic_anchor or restricted.provider_significance or restricted.interest_fit)
        assert restricted.provider_evidence == () and restricted.grounded_anchor  # the raw fact is kept
        # a restricted anchor ranks below an ordinary candidate of a higher tier
        assert _winner(restricted, _assess("ordinary", tier=_PRIMARY)) == "ordinary"
    # an art-focused request keeps galleries in, as the planner's dilution rule does
    gallery = _assess("gallery", commercial_gallery=True, matched=("art",), anchor=True, interests=("art",))
    assert gallery.evidence_band == 2


def test_anchor_and_significance_evidence_follow_the_existing_rules() -> None:
    # an anchor that serves none of the requested interests earns nothing (the seeding rule)
    off_request = _assess("off request", anchor=True, matched=())
    assert off_request.grounded_anchor and not off_request.semantic_anchor and off_request.evidence_band == 0
    # a bare wikidata id is not significance
    assert _assess("bare id", significance=("wikidata",)).evidence_band == 0
    assert _assess("bare id", significance=("wikidata", "tourism_attraction")).provider_evidence == ()
    # the KIND of place is not evidence about the place: a major historic type earns nothing here ...
    by_type = _assess("by type", significance=("major_historic_type",), matched=("history",))
    assert not by_type.provider_significance and by_type.provider_evidence == () and by_type.evidence_band == 1
    # ... while the quality stage keeps using it exactly as before (its own set is untouched)
    assert quality_module._STRONG_SIGNIFICANCE == {"wikipedia", "heritage", "major_historic_type"}
    assert usefulness.PLACE_LEVEL_SIGNIFICANCE_SIGNALS == {"wikipedia", "heritage"}
    assert usefulness.PLACE_LEVEL_SIGNIFICANCE_SIGNALS < quality_module._STRONG_SIGNIFICANCE
    # the model's own confidence / priority is not an input at all
    parameters = set(inspect.signature(usefulness.assess).parameters) | set(usefulness.QualityEvidence.__dataclass_fields__)
    assert not [name for name in parameters if "confidence" in name or "priority" in name]


# =====================================================================================
# 2. Deterministic selection (no AI stage)
# =====================================================================================


def test_a_grounded_must_visit_is_scheduled_whatever_evidence_its_rivals_carry() -> None:
    plain = _park("requested", 0)  # serves no requested interest, no significance, not an anchor
    rivals = [_castle(f"c{index}", index + 1) for index in range(5)]
    state = _planned(
        _state([*rivals, plain], anchors=rivals, days=1, pace=TripPace.RELAXED, must_visit={"the old garden": plain})
    )
    names = _names(state)
    assert len(names) == 2 and "Park requested" in names
    stop = next(stop for stop in state.experience_plan.daily_plans[0].experiences if stop.name == "Park requested")
    assert stop.why_included == "Matches your must-visit request."


def test_a_grounded_anchor_takes_the_limited_slot_from_equally_eligible_filler() -> None:
    museums = [_museum(f"m{index}", index) for index in range(6)]
    # identical category, tier and score; two of them (last in provider order) are grounded anchors
    state = _state(museums, anchors=museums[4:], days=1, pace=TripPace.RELAXED)
    scores = {score.candidate_name: score for score in state.candidate_quality_report.attraction_scores}
    assert len({(score.quality_tier, score.total_score) for score in scores.values()}) == 1
    assert planner_module.anchor_seed_count(1, 2) == 1  # only one of the two is a seed
    assert sorted(_names(_planned(state))) == ["Museum m4", "Museum m5"]
    # without the promotion report nothing prefers them
    assert sorted(_names(_planned(_state([_museum(f"m{index}", index) for index in range(6)], days=1,
                                         pace=TripPace.RELAXED)))) == ["Museum m0", "Museum m1"]


def test_an_anchor_cannot_rescue_a_restricted_or_rejected_candidate() -> None:
    museums = [_museum(f"m{index}", index) for index in range(4)]
    private = _museum("private", 4, access="private")
    gallery = _poi("gallery", "Gallery shop", _at(5), category="gallery", provider_tags={"tourism": "gallery"})
    statue = _poi("statue", "Statue", _at(6), category="memorial", provider_tags={"historic": "memorial"})
    office = _poi("office", "Registry", _at(7), category="office", provider_tags={"office": "government"})
    restricted = [private, gallery, statue, office]
    state = _state([*restricted, *museums], anchors=restricted, days=1, pace=TripPace.BALANCED)

    tiers = {score.candidate_name: score.quality_tier for score in state.candidate_quality_report.attraction_scores}
    assert tiers["Museum private"] == CandidateQualityTier.REJECTED and tiers["Registry"] == CandidateQualityTier.REJECTED
    assessed = usefulness.usefulness_by_place_id(state)
    assert all(assessed[poi["place_id"]].evidence_band == 0 for poi in restricted)
    assert all(assessed[poi["place_id"]].grounded_anchor for poi in restricted)

    # slots constrained: the ordinary museums keep them
    assert sorted(_names(_planned(state))) == ["Museum m0", "Museum m1", "Museum m2"]
    # slots to spare: a rejected candidate is still never scheduled, whoever proposed it
    spare = _planned(_state([private, office, *museums[:2]], anchors=[private, office], days=3))
    assert not {"Museum private", "Registry"} & set(_names(spare))
    # nor does it reach the reasoning model
    assert not {"Museum private", "Registry"} & set(_allowed_names(state))


def test_a_requested_interest_keeps_a_slot_when_stronger_candidates_of_another_kind_compete() -> None:
    castles = [_castle(f"c{index}", index) for index in range(8)]  # anchors serving "history": two evidences each
    park = _park("p", 9)  # one evidence, the only supply for "outdoors"
    state = _planned(
        _state([*castles, park], anchors=castles, days=1, pace=TripPace.BALANCED, interests=["history", "outdoors"])
    )
    names = _names(state)
    assert len(names) == 3 and "Park p" in names
    assert interest_coverage(state) == {"history": True, "outdoors": True}


def test_strong_provider_significance_outranks_a_generic_place_of_the_same_category() -> None:
    generic = [_museum(f"m{index}", index) for index in range(5)]
    documented = [_museum("d0", 5, wikipedia="xx:Fixture"), _museum("d1", 6, heritage="2")]
    state = _state([*generic, *documented], days=1, pace=TripPace.RELAXED)
    assert sorted(_names(_planned(state))) == ["Museum d0", "Museum d1"]
    # a significance signal, never a claim: nothing shown to the traveller calls it popular or top rated
    shown = " ".join(
        str(text) for stop in state.experience_plan.daily_plans[0].experiences for text in (stop.why_included, stop.name)
    ).lower()
    assert not [word for word in _FORBIDDEN_CLAIM_WORDS if word in shown]
    # a bare wikidata id earns nothing
    bare = _planned(_state([*generic, _museum("w", 5, wikidata="Q0")], days=1, pace=TripPace.RELAXED))
    assert "Museum w" not in _names(bare)


def test_provider_order_never_decides_the_selection_or_the_bounded_request(reasoning_cap: Any) -> None:
    def pool() -> list[dict[str, Any]]:
        return [*[_museum(f"m{index}", index) for index in range(10)], _castle("c0", 10), _park("p0", 11)]

    forward = _state(pool(), days=2, interests=["history", "outdoors"])
    backward = _state(list(reversed(pool())), days=2, interests=["history", "outdoors"])
    assert _scheduled(_planned(forward)) == _scheduled(_planned(backward))
    reasoning_cap(6)
    assert _allowed_names(forward) == _allowed_names(backward) and len(_allowed_names(forward)) == 6


def test_without_any_ai_stage_the_deterministic_plan_is_valid_and_ordered_by_provider_evidence() -> None:
    generic = [_museum(f"m{index}", index) for index in range(8)]
    documented = [_museum(f"d{index}", 8 + index, wikipedia="xx:Fixture") for index in range(3)]
    state = _state([*generic, *documented], days=1, pace=TripPace.BALANCED)
    assert state.ai_candidate_promotion_report is None and state.ai_itinerary_reasoning_result is None
    _planned(state)
    assert sorted(_names(state)) == ["Museum d0", "Museum d1", "Museum d2"]
    for stop in state.experience_plan.daily_plans[0].experiences:
        assert stop.provider_place_id and stop.provider_source and stop.coordinates


def test_thin_inventory_without_evidence_is_still_fully_used() -> None:
    # 7 viable candidates for a 3-day balanced trip (T = 9), none with any usefulness evidence
    parks = [_park(f"p{index}", index) for index in range(7)]
    state = _state(parks, days=3, interests=["history"])
    assert {item.evidence_band for item in usefulness.usefulness_by_place_id(state).values()} == {0}
    _planned(state)
    assert sorted(_names(state)) == sorted(poi["name"] for poi in parks)
    assert all(day.experiences for day in state.experience_plan.daily_plans)


def test_a_museum_heavy_high_evidence_pool_does_not_crowd_out_viable_alternatives() -> None:
    # The traveller asked for the outdoors. Most candidates WITH evidence are documented museums.
    museums = [_museum(f"m{index}", index, wikipedia="xx:Fixture") for index in range(12)]
    parks = [_park(f"p{index}", 12 + index) for index in range(6)]
    views = [
        _poi(f"v{index}", f"View v{index}", _at(18 + index), category="viewpoint", provider_tags={"tourism": "viewpoint"})
        for index in range(3)
    ]
    state = _planned(_state([*museums, *parks, *views], days=3, interests=["outdoors"]))
    days = _scheduled(state)
    names = [name for day in days for name in day]
    cap = diversity.plan_class_cap(diversity.MUSEUM_CULTURE, False, diversity.justified_classes(["outdoors"]), 3)
    assert cap is not None and len(names) == 9
    museum_count = sum(name.startswith("Museum") for name in names)
    # the existing plan-level class cap holds: the class nobody asked for cannot fill the plan ...
    assert museum_count <= cap < 9
    # ... the slots it may not take go to the viable alternatives, and the requested interest is covered
    assert len(names) - museum_count >= 9 - cap
    assert interest_coverage(state) == {"outdoors": True}
    # (How those stops are composed into days is the existing geographic grouping plus the day-diversity
    # pass, whose soft rule only swaps in a candidate of the same tier or better. Day composition is Q3.)

    # an AI-chosen museum-only plan: the existing coverage pass still serves the requested interest
    chosen = _state([*museums, *parks, *views], days=3, interests=["outdoors"])
    chosen.ai_itinerary_reasoning_result = _completed_result(
        [
            _day(day, [planner_module.build_candidate_id(_SOURCE, f"geoapify/m{3 * (day - 1) + slot}") for slot in range(3)])
            for day in (1, 2, 3)
        ]
    )
    _planned(chosen)
    assert interest_coverage(chosen) == {"outdoors": True}
    assert len(_names(chosen)) == 9 and sum(name.startswith("Museum") for name in _names(chosen)) < 9


def _far_pool(near_count: int, *, distance_degrees: float = 8.0) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # documented and serving the request: on evidence alone it outranks every plain museum
    far = _poi("far", "Museum far", _point(50.0 + distance_degrees, 10.0), provider_tags={"tourism": "museum", "wikipedia": "xx:Fixture"})
    return far, [_museum(f"m{index}", index) for index in range(near_count)]


def test_a_place_beyond_the_pool_reach_is_deferred_whether_or_not_it_is_an_anchor() -> None:
    for as_anchor in (True, False):
        far, near = _far_pool(4)
        state = _planned(_state([far, *near], anchors=[far] if as_anchor else None, days=1, pace=TripPace.BALANCED))
        # the same deferral either way: the nearby viable candidates are preferred
        assert _names(state) and "Museum far" not in _names(state), as_anchor
        assert len(_names(state)) == 3
        # the ordering itself is untouched -- the far place is still the strongest candidate by evidence
        assessed = usefulness.usefulness_by_place_id(state)
        assert min(assessed.values(), key=lambda item: item.sort_key).place_id == far["place_id"]


def test_the_reach_guard_is_the_existing_outlier_rule_and_never_underfills(monkeypatch: pytest.MonkeyPatch) -> None:
    # Q3: this pins the Q2 SELECTION guard on its own. With day composition on, a place ~3 km from a
    # compact group may additionally give way to a nearer one of at most one evidence band less
    # (`test_day_composition_q3`); that later step is switched off here.
    monkeypatch.setenv("DAY_COMPOSITION_ENABLED", "false")
    get_settings.cache_clear()
    # thin inventory: with only two nearby candidates for three slots the far place is used, anchor or not
    for as_anchor in (True, False):
        far, near = _far_pool(2)
        state = _planned(_state([far, *near], anchors=[far] if as_anchor else None, days=1, pace=TripPace.BALANCED))
        assert sorted(_names(state)) == ["Museum far", "Museum m0", "Museum m1"], as_anchor

    # the existing reach: nothing within the minimum outlier distance is "far" (~0.03 deg is ~3 km)
    assert planner_module._OUTLIER_MIN_KM == 8.0
    close, near = _far_pool(4, distance_degrees=0.03)
    assert "Museum far" in _names(_planned(_state([close, *near], days=1, pace=TripPace.BALANCED)))

    # a selection-time guard only: with nobody beyond reach the plan is the usefulness order, untouched
    compact = [_museum(f"m{index}", index, **({"wikipedia": "xx:Fixture"} if index >= 5 else {})) for index in range(8)]
    assert sorted(_names(_planned(_state(compact, days=1, pace=TripPace.BALANCED)))) == ["Museum m5", "Museum m6", "Museum m7"]


def test_a_distant_grounded_must_visit_is_never_deferred() -> None:
    far, near = _far_pool(4)
    state = _planned(_state([far, *near], anchors=[far], days=1, pace=TripPace.BALANCED, must_visit={"that museum": far}))
    assert "Museum far" in _names(state) and len(_names(state)) == 3
    # also as the only supply for a requested interest, a far place gives way to nothing nearer -- so it is used
    park = _poi("park", "Park far", _point(58.0, 10.0), category="park", provider_tags={"leisure": "park"})
    state = _planned(_state([park, *near], days=1, pace=TripPace.BALANCED, interests=["history", "outdoors"]))
    assert "Park far" in _names(state)


# =====================================================================================
# 3. The bounded reasoning request
# =====================================================================================


def _reference(state: PlanningState, name: str) -> Any:
    return next(candidate for candidate in _allowed(state) if candidate.name == name)


def test_must_visit_is_marked_by_provider_identity_never_by_name() -> None:
    grounded = _museum("g", 0)  # the provider calls it something else entirely
    lookalike = _poi("l", "the old garden", _at(1), provider_tags={"tourism": "museum"})  # shares the user's words
    state = _state([grounded, lookalike, _museum("m", 2)], must_visit={"the old garden": grounded})
    assert _reference(state, "Museum g").must_visit is True
    assert _reference(state, "the old garden").must_visit is False
    assert _reference(state, "Museum m").must_visit is False


def test_a_broad_pool_reference_that_is_also_an_anchor_carries_every_signal() -> None:
    both = _museum("b", 0, wikipedia="xx:Fixture")
    state = _state([both, _museum("m", 1)], anchors=[both], must_visit={"that museum": both})
    reference = _reference(state, "Museum b")
    assert [candidate.candidate_id for candidate in _allowed(state)].count(reference.candidate_id) == 1
    assert reference.origin.value == "broad_provider_discovery"
    assert reference.must_visit and reference.semantic_anchor
    assert reference.provider_evidence == ["wikipedia"] and reference.matched_interests == ["history"]
    plain = _reference(state, "Museum m")
    assert not (plain.must_visit or plain.semantic_anchor) and plain.provider_evidence == []


def test_a_targeted_lookup_reference_carries_its_own_category_and_interest_evidence() -> None:
    from app.tests.services import test_ai_itinerary_reasoning_request_builder as builder_fixtures

    state = builder_fixtures._planning_state(interests=["history"])
    proposal = builder_fixtures._proposal("p1", "Hilltop Keep")
    builder_fixtures._apply_ai_directed_promotion(
        state, proposal, provider_place_id="node/900", tier=CandidateQualityTier.GOOD_CANDIDATE
    )
    report = state.candidate_quality_report
    state.candidate_quality_report = report.model_copy(
        update={
            "ai_directed_scores": [
                score.model_copy(
                    update={"normalized_category": "landmark", "matched_interests": ["history"],
                            "significance_signals": ["major_historic_type", "heritage"]}
                )
                for score in report.ai_directed_scores
            ]
        }
    )
    (reference,) = _allowed(state)
    assert reference.origin.value == "ai_directed_provider_discovery"
    assert reference.normalized_category == "landmark" and reference.matched_interests == ["history"]
    # only the place-level signal is listed; the kind of place is not
    assert reference.semantic_anchor and reference.provider_evidence == ["heritage"]
    assert reference.must_visit is False


def _bound_pool() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    museums = [_museum(f"m{index:02d}", index) for index in range(20)]  # a long equally scored plateau
    anchors = [_castle(f"a{index}", 20 + index) for index in range(3)]
    park = _park("p", 23)
    requested = _park("r", 24)
    pool = [*museums, park, *anchors, requested]
    return pool, {"anchors": anchors, "park": park, "requested": requested}


def test_the_cap_is_hard_and_keeps_must_visits_coverage_and_anchors(reasoning_cap: Any) -> None:
    pool, parts = _bound_pool()
    restaurants = [_restaurant(f"r{index}", _at(index)) for index in range(8)]
    state = _state(pool, anchors=parts["anchors"], days=2, interests=["history", "outdoors"],
                   must_visit={"the old garden": parts["requested"]}, restaurants=restaurants)
    for cap in (40, 12, 8, 5, 3, 1):
        reasoning_cap(cap)
        allowed = _allowed(state)
        ids = [candidate.candidate_id for candidate in allowed]
        assert len(ids) == min(cap, len(pool) + min(len(restaurants), 4)) and len(set(ids)) == len(ids), cap
        names = [candidate.name for candidate in allowed]
        assert names[0] == "Park r"  # the must-visit is retained first at every cap

    reasoning_cap(8)
    allowed = _allowed(state)
    names = [candidate.name for candidate in allowed]
    # A must-visit, B one per requested interest (the must-visit park already serves "outdoors"),
    # C the anchors: all inside the cap
    assert {"Park r", "Castle a0", "Castle a1", "Castle a2"} <= set(names)
    assert any("history" in candidate.matched_interests for candidate in allowed)
    assert any("outdoors" in candidate.matched_interests for candidate in allowed)
    # attractions in canonical order, restaurants last
    categories = [candidate.category for candidate in allowed]
    assert categories == sorted(categories, key=lambda category: category == ItineraryReasoningCategory.RESTAURANT)


def test_coverage_gets_one_candidate_first_and_a_second_only_while_space_remains(reasoning_cap: Any) -> None:
    pool, parts = _bound_pool()
    extra_park = _park("q", 25)
    state = _state([*pool, extra_park], anchors=parts["anchors"], days=2, interests=["history", "outdoors"])

    def outdoors(cap: int) -> int:
        reasoning_cap(cap)
        return sum("outdoors" in candidate.matched_interests for candidate in _allowed(state))

    # 3 anchors serve history; the first coverage pass adds one park, and only that one, when space is short
    assert outdoors(4) == 1
    # with space, the second pass keeps another park ahead of the museum plateau
    assert outdoors(12) >= 2


def test_restaurants_take_only_a_bounded_allowance_and_never_a_protected_place(reasoning_cap: Any) -> None:
    pool, parts = _bound_pool()
    restaurants = [_restaurant(f"r{index}", _at(index)) for index in range(10)]
    state = _state(pool, anchors=parts["anchors"], days=2, interests=["history", "outdoors"],
                   must_visit={"the old garden": parts["requested"]}, restaurants=restaurants)

    def restaurant_count(cap: int) -> int:
        reasoning_cap(cap)
        allowed = _allowed(state)
        protected = {"Park r", "Castle a0", "Castle a1", "Castle a2"}
        if cap >= len(protected):
            assert protected <= {candidate.name for candidate in allowed}, cap
        return sum(candidate.category == ItineraryReasoningCategory.RESTAURANT for candidate in allowed)

    assert restaurant_count(40) == 4  # the whole pool fits: spare space, at most two per day
    assert restaurant_count(12) == 2  # one preference per trip day, off the tail of the remaining attractions
    assert restaurant_count(5) == 1  # only what the protected attractions left
    assert restaurant_count(4) == 0  # protected attractions fill the cap: no restaurant displaces one
    # a restaurant used to outrank every 0.6-category attraction in a shared sort; the park is kept now
    reasoning_cap(12)
    assert "Park p" in _allowed_names(_state(pool, days=2, interests=["history", "outdoors"], restaurants=restaurants))


def test_must_visits_beyond_the_cap_skip_the_ai_request_and_the_deterministic_plan_keeps_them(
    reasoning_cap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = [_park(f"r{index}", index) for index in range(3)]
    pool = [*requested, *[_museum(f"m{index}", 4 + index) for index in range(8)]]
    state = _state(pool, days=3, must_visit={f"request {index}": poi for index, poi in enumerate(requested)})
    monkeypatch.setenv("AI_ITINERARY_REASONING_ENABLED", "true")
    reasoning_cap(2)

    class _Provider:
        provider_name = "spy"
        calls = 0

        def reason(self, request: Any) -> Any:
            type(self).calls += 1
            raise AssertionError("a request missing a mandatory identity must never be sent")

    service = AIItineraryReasoningService(provider=_Provider())
    assert service.request_builder.must_visit_overflow(state) == 1
    service.apply(state)
    result = state.ai_itinerary_reasoning_result
    assert _Provider.calls == 0 and result.status == AIItineraryReasoningStatus.SKIPPED and not result.days
    assert result.guardrail_report.blocked_reasons == [MUST_VISIT_EXCEEDS_REASONING_BOUND]

    # the deterministic planner has no candidate cap: every grounded must-visit is scheduled
    _planned(state)
    assert {"Park r0", "Park r1", "Park r2"} <= set(_names(state))
    # ... and the repair call, which rebuilds the same request, has nothing to repair without a completed result
    from app.services.ai_itinerary_repair_request_builder import AIItineraryRepairRequestBuilder

    assert AIItineraryRepairRequestBuilder().build_request(state) is None

    # when they fit, nothing is skipped
    reasoning_cap(3)
    assert service.request_builder.must_visit_overflow(state) == 0


def test_more_must_visits_than_the_pace_allows_are_reported_not_silently_dropped() -> None:
    requested = [_park(f"r{index}", index) for index in range(3)]
    pool = [*requested, *[_museum(f"m{index}", 4 + index) for index in range(4)]]
    state = _state(pool, days=1, pace=TripPace.RELAXED,
                   must_visit={f"request {index}": poi for index, poi in enumerate(requested)})
    _planned(state)
    names = _names(state)
    assert len(names) == 2 and all(name.startswith("Park r") for name in names)  # the pace cap is not exceeded
    PlanValidatorService().run(state)
    (issue,) = [issue for issue in state.validation_report.warnings if issue.category == "must_visit"]
    missing = next(f"request {index}" for index, poi in enumerate(requested) if poi["name"] not in names)
    assert "Found by the provider but not scheduled" in issue.message and missing in issue.message


def test_a_distant_grounded_must_visit_survives_the_outlier_control() -> None:
    far = _poi("far", "Park far", _point(50.9, 10.9), category="park", provider_tags={"leisure": "park"})
    near = [_museum(f"m{index}", index) for index in range(8)]
    state = _planned(_state([*near, far], days=2, must_visit={"the far garden": far}))
    assert "Park far" in _names(state)
    # the same place without the request is an ordinary outlier and gives way
    unrequested = _poi("far", "Park far", _point(50.9, 10.9), category="park", provider_tags={"leisure": "park"})
    assert "Park far" not in _names(_planned(_state([*[_museum(f"m{index}", index) for index in range(8)], unrequested], days=2)))


# =====================================================================================
# 4. One ordering for the AI request and the deterministic path
# =====================================================================================


def test_the_reasoning_bound_the_planner_and_the_metrics_agree_on_one_ordering() -> None:
    pool, parts = _bound_pool()
    state = _state(pool, anchors=parts["anchors"], days=3, interests=["history", "outdoors"],
                   must_visit={"the old garden": parts["requested"]})
    stored = usefulness.usefulness_by_place_id(state)

    # the request builder's own assessment
    builder = AIItineraryReasoningRequestBuilder()
    attractions, _ = builder._candidate_universe(state)
    assert {reference.provider_place_id: assessed.sort_key for reference, assessed in attractions} == {
        place_id: item.sort_key for place_id, item in stored.items()
    }
    allowed = [candidate.provider_place_id for candidate in builder.build_request(state).allowed_candidates]
    assert allowed == sorted(allowed, key=lambda place_id: stored[place_id].sort_key)

    # the planner's profiles, built inside `run`
    captured: dict[str, tuple] = {}
    original = planner_module._select_diverse_scheduling_set

    def spy(pool_arg: Any, profiles: Any, *args: Any, **kwargs: Any) -> Any:
        captured.update({str(poi["place_id"]): profiles[id(poi)].usefulness.sort_key for poi in pool_arg})
        return original(pool_arg, profiles, *args, **kwargs)

    planner_module._select_diverse_scheduling_set = spy
    try:
        _planned(state)
    finally:
        planner_module._select_diverse_scheduling_set = original
    assert captured == {place_id: item.sort_key for place_id, item in stored.items()}

    # the deterministic plan and the head of the bounded request choose the same strongest candidates
    strongest = sorted(stored, key=lambda place_id: stored[place_id].sort_key)[:4]
    scheduled_ids = {stop.provider_place_id for day in state.experience_plan.daily_plans for stop in day.experiences}
    assert set(strongest) <= scheduled_ids and allowed[:4] == strongest


def test_diversity_refines_only_among_candidates_of_equal_usefulness_preference() -> None:
    # two documented museums and a castle (all serve the request) against parks that do not, 3 slots
    documented = [_museum("d0", 0, wikipedia="xx:Fixture"), _museum("d1", 1, wikipedia="xx:Fixture"), _castle("c", 2)]
    plain = [_park(f"p{index}", 3 + index) for index in range(4)]
    state = _planned(_state([*plain, *documented], days=1, pace=TripPace.BALANCED))
    # variety among the parks would be "more diverse", but no park is lifted over stronger evidence
    assert sorted(_names(state)) == ["Castle c", "Museum d0", "Museum d1"]


# =====================================================================================
# 5. Prompt semantics
# =====================================================================================


def test_every_prompt_prints_the_planning_signals_and_explains_them() -> None:
    both = _museum("b", 0, heritage="2")
    state = _state([both, _museum("m", 1)], anchors=[both], must_visit={"that museum": both},
                   restaurants=[_restaurant("r", _at(2))])
    request = AIItineraryReasoningRequestBuilder().build_request(state)
    for adapter in (groq_adapter, anthropic_adapter):
        prompt = adapter._build_prompt(request)
        marked = next(line for line in prompt.splitlines() if "'Museum b'" in line)
        assert "must_visit=yes semantic_anchor=yes provider_evidence=[heritage]" in marked
        for line in (line for line in prompt.splitlines() if line.startswith("- candidate_id=")):
            assert re.search(r" must_visit=(yes|no) semantic_anchor=(yes|no) provider_evidence=\[[a-z_, ]*\]$", line), line
        assert "must_visit=yes represents a provider-grounded place the traveler explicitly requested" in prompt
        assert "Never infer must-visit identity from a candidate name" in prompt
        assert "not facts about popularity, rating or ranking" in prompt
    # the repair call prints the same lines and carries the same two instructions
    assert set(DEFAULT_ITINERARY_REASONING_INSTRUCTIONS[-2:]) <= set(DEFAULT_ITINERARY_REPAIR_INSTRUCTIONS)
    # the signals stay inside the reasoning request: nothing reaches the plan shown to the traveller
    _planned(state)
    dumped = state.experience_plan.model_dump_json()
    assert "semantic_anchor" not in dumped and "provider_evidence" not in dumped and "evidence_band" not in dumped


# =====================================================================================
# 6. Reported-only metrics
# =====================================================================================


def _quality_metrics() -> Any:
    spec = importlib.util.spec_from_file_location("quality_metrics", _BACKEND_DIR / "scripts" / "quality_metrics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metrics_report_evidence_neutrally_and_only_over_discretionary_slots() -> None:
    metrics_module = _quality_metrics()
    requested = _park("r", 0)  # no evidence at all, but the traveller asked for it
    documented = [_museum(f"d{index}", 1 + index, wikipedia="xx:Fixture") for index in range(4)]
    state = _planned(_state([requested, *documented], days=1, pace=TripPace.BALANCED,
                            must_visit={"the old garden": requested}))
    PlanValidatorService().run(state)
    slots = metrics_module.extract_quality_metrics(state)["limited_slots"]

    assert slots["scheduled_by_usefulness_evidence_band"] == {"0": 1, "2": 2}
    assert slots["zero_usefulness_evidence_scheduled_share"] == pytest.approx(0.333, abs=0.001)
    assert (slots["higher_evidence_available_count"], slots["higher_evidence_scheduled_count"]) == (4, 2)
    # the must-visit is not a discretionary choice, so its missing evidence is not held against the plan
    assert slots["discretionary_scheduled_count"] == 2
    assert slots["unused_with_more_evidence_than_weakest_discretionary_scheduled"] == 0
    assert slots["reasoning_bound_must_visit_overflow"] == 0
    assert "says nothing about a place being poor" in slots["usefulness_evidence_band_meaning"]

    # naming: evidence is never called popularity, fame, ranking or filler
    q2_keys = [key for key in slots if "evidence" in key or "discretionary" in key or "reasoning_bound" in key]
    assert sorted(q2_keys) == [
        "discretionary_scheduled_count",
        "eligible_outside_reasoning_bound_by_band",
        "higher_evidence_available_count",
        "higher_evidence_scheduled_count",
        "reasoning_bound_must_visit_overflow",
        "scheduled_by_usefulness_evidence_band",
        "unused_viable_by_usefulness_evidence_band",
        "unused_with_more_evidence_than_weakest_discretionary_scheduled",
        "usefulness_evidence_band_meaning",
        "zero_usefulness_evidence_scheduled_share",
    ]
    for key in q2_keys:
        assert not re.search(r"popular|fame|famous|rank|filler|obscure|top_|tourist", key), key
    # the honest future metric is still future
    assert "tourist_value_ranking_and_obscure_filler_share" in metrics_module.FUTURE_METRICS
    # Q3 added day geography, Q4 route severity; the tuning corrections added dispersion causes and food evidence
    assert metrics_module.SCHEMA_VERSION == 5


# =====================================================================================
# 7. Q2 correction: the kind of place is not place-level evidence
# =====================================================================================


def _class_counts(state: PlanningState) -> dict[str, int]:
    names = _names(state)
    return {kind: sum(name.startswith(kind) for name in names) for kind in ("Castle", "Museum", "Tomb")}


def test_an_undocumented_place_type_does_not_dominate_equally_suitable_candidates() -> None:
    # Both kinds serve the requested interest and neither is documented: one evidence each. Before the
    # correction the type alone gave every castle a second evidence and all nine slots.
    castles = [_castle(f"c{index}", 12 + index) for index in range(12)]
    museums = [_museum(f"m{index}", index) for index in range(12)]
    state = _state([*museums, *castles])
    bands = {item.evidence_band for item in usefulness.usefulness_by_place_id(state).values()}
    assert bands == {1}
    counts = _class_counts(_planned(state))
    assert counts["Castle"] + counts["Museum"] == 9
    assert counts["Castle"] >= 3 and counts["Museum"] >= 3  # a real mix, by the equal-preference refinement
    # provider order does not decide which kind leads
    assert _class_counts(_planned(_state([*castles, *museums]))) == counts
    # the quality stage is untouched: a castle is still classified and scored through its major historic type
    castle_score = next(s for s in state.candidate_quality_report.attraction_scores if s.candidate_name == "Castle c0")
    assert "major_historic_type" in castle_score.significance_signals
    assert castle_score.quality_tier == CandidateQualityTier.PRIMARY_ANCHOR


def test_genuine_place_level_evidence_raises_priority_within_and_across_kinds() -> None:
    # documented museums against undocumented tombs: the documentation, not the kind, decides
    tombs = [_poi(f"t{index}", f"Tomb t{index}", _at(12 + index), category="tomb", provider_tags={"historic": "tomb"})
             for index in range(12)]
    documented = [_museum(f"d{index}", index, wikipedia="xx:Fixture") for index in range(4)]
    counts = _class_counts(_planned(_state([*tombs, *documented], days=1, pace=TripPace.BALANCED)))
    assert counts == {"Castle": 0, "Museum": 3, "Tomb": 0}
    # and within one kind: the castle the provider carries a heritage designation for leads the plain ones
    castles = [_castle(f"c{index}", index) for index in range(6)]
    designated = _poi("h", "Castle h", _at(7), category="castle", provider_tags={"historic": "castle", "heritage": "2"})
    assert "Castle h" in _names(_planned(_state([*castles, designated], days=1, pace=TripPace.RELAXED)))


def test_heritage_evidence_comes_from_a_provider_designation_not_from_the_kind_of_place() -> None:
    from app.providers.places.geoapify_categories import taxonomy_tags_from_categories
    from app.services import place_taxonomy as taxonomy

    # the production adapter sets `heritage` only from the provider's own heritage marker ...
    assert taxonomy_tags_from_categories(["tourism.sights.castle", "heritage"]).get("heritage") == "yes"
    assert taxonomy_tags_from_categories(["tourism.sights.castle", "heritage.unesco"]).get("heritage") == "yes"
    # ... never from an attraction category alone
    for categories in (["tourism.sights.castle"], ["tourism.attraction"], ["entertainment.museum"], ["tourism.sights"]):
        assert "heritage" not in taxonomy_tags_from_categories(categories), categories
    # and the taxonomy reports the signal only when that tag is present
    assert "heritage" not in taxonomy.significance_signals({"historic": "castle"})
    assert "heritage" in taxonomy.significance_signals({"historic": "castle", "heritage": "yes"})


def test_grounded_anchors_stay_valuable_without_any_anchor_count_limit() -> None:
    castles = [_castle(f"c{index}", 12 + index) for index in range(12)]
    museums = [_museum(f"m{index}", index) for index in range(12)]
    # five of the twelve castles are grounded anchors: those five lead, the rest compete as equals
    state = _planned(_state([*museums, *castles], anchors=castles[:5]))
    names = _names(state)
    assert {f"Castle c{index}" for index in range(5)} <= set(names)
    assert not {f"Castle c{index}" for index in range(5, 12)} & set(names)  # no evidence beyond the museums'
    assert sum(name.startswith("Museum") for name in names) == 4
    # every anchor counts on its own evidence -- nothing caps how many may be scheduled
    assert _class_counts(_planned(_state([*museums, *castles], anchors=castles)))["Castle"] == 9


# =====================================================================================
# 8. Q2 correction: one candidate universe for the planner and the reasoning request
# =====================================================================================

_NEAR = (38.69, -9.21)  # where the fixture's targeted lookup grounds its place
_FAR = (38.75, -9.10)  # several km away


def _universe_state(broad: list[dict[str, Any]], *, promoted_tags: dict[str, str] | None = None,
                    proposal_name: str = "Old Mill", promoted_id: str = "node/900") -> PlanningState:
    """A broad pool plus ONE promoted targeted-lookup candidate (grounded at `_NEAR`)."""
    from app.tests.services import test_ai_itinerary_reasoning_request_builder as builder_fixtures

    state = builder_fixtures._planning_state(interests=["history"], start_date="2026-08-10", end_date="2026-08-10")
    state.destination_context = DestinationContext(destination_name="Fixtureville", candidate_pois=broad)
    state.candidate_quality_report = CandidateQualityService().build_report(state)
    builder_fixtures._apply_ai_directed_promotion(
        state, builder_fixtures._proposal("p1", proposal_name), provider_place_id=promoted_id,
        tier=CandidateQualityTier.GOOD_CANDIDATE,
    )
    if promoted_tags is not None:
        report = state.ai_candidate_promotion_report
        state.ai_candidate_promotion_report = report.model_copy(
            update={"promoted_candidates": [c.model_copy(update={"provider_tags": promoted_tags}) for c in report.promoted_candidates]}
        )
    return state


def _broad(place_id: str, name: str, where: tuple[float, float], **extra: Any) -> dict[str, Any]:
    tags = {"tourism": "museum", **extra.pop("tags", {})}
    return {"place_id": place_id, "name": name, "category": "museum", "coordinates": {"lat": where[0], "lng": where[1]},
            "source": "openstreetmap_places", "data_status": "live", "confidence": 0.8, "provider_tags": tags, **extra}


def _request_ids(state: PlanningState) -> list[str]:
    return [candidate.candidate_id for candidate in _allowed(state)]


def _planner_ids(state: PlanningState) -> set[str]:
    promoted_pois, _ = planner_module._build_promoted_candidate_pois(state)
    pool = [*universe.schedulable_broad_pois(state), *promoted_pois]
    return set(planner_module._build_ai_candidate_reverse_index(pool, state.destination_context.candidate_restaurants))


def _assert_one_universe(state: PlanningState) -> None:
    """Every id the model may cite resolves in the planner, and a plan citing ALL of them is honoured."""
    ids = _request_ids(state)
    assert ids and set(ids) <= _planner_ids(state)
    state.ai_itinerary_reasoning_result = _completed_result([_day(1, ids[:2])])
    _planned(state)
    assert any("chosen by AI itinerary reasoning" in text for text in state.experience_plan.assumptions)
    scheduled = {stop.provider_place_id for stop in state.experience_plan.daily_plans[0].experiences}
    assert scheduled == {candidate_id.split(":", 1)[1] for candidate_id in ids[:2]}


def test_a_shared_name_alone_never_conflates_two_distinct_places() -> None:
    # the broad "Old Mill" is several km from the promoted "Old Mill": two real places, two provider ids
    state = _universe_state([_broad("node/1", "Old Mill", _FAR), _broad("node/2", "Town Museum", _FAR)])
    resolution = universe.resolve_promoted_candidates(state)
    assert [c.provider_place_id for c in resolution.accepted] == ["node/900"] and not resolution.left_out
    assert {"openstreetmap_places:node/1", "openstreetmap_places:node/900"} <= set(_request_ids(state))
    # the model cites the promoted namesake first: the plan is honoured and BOTH can be scheduled
    state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, ["openstreetmap_places:node/900", "openstreetmap_places:node/1"])]
    )
    _planned(state)
    assert any("chosen by AI itinerary reasoning" in text for text in state.experience_plan.assumptions)
    assert [stop.provider_place_id for stop in state.experience_plan.daily_plans[0].experiences] == ["node/900", "node/1"]


def test_the_same_name_close_by_is_the_same_place_for_the_planner_and_the_request_alike() -> None:
    state = _universe_state([_broad("node/1", "Old Mill", _NEAR), _broad("node/2", "Town Museum", _FAR)])
    resolution = universe.resolve_promoted_candidates(state)
    assert not resolution.accepted and [reason for _, reason in resolution.left_out] == [universe.REASON_SAME_PLACE]
    # neither side offers the suppressed id (it used to reach the model and void its whole plan)
    assert "openstreetmap_places:node/900" not in _request_ids(state)
    assert "openstreetmap_places:node/900" not in _planner_ids(state)
    _assert_one_universe(state)
    assert any("duplicates an existing provider candidate" in text for text in state.experience_plan.assumptions)
    # nothing is transferred to the namesake: the broad record is not an anchor because the other id was promoted
    assert _reference(state, "Old Mill").semantic_anchor is False


def test_a_shared_wikidata_entity_counts_only_together_with_proximity() -> None:
    tags = {"wikidata": "Q1"}
    near = _universe_state([_broad("node/1", "Alte Muehle", (38.694, -9.21), tags=tags), _broad("node/2", "Town Museum", _FAR)],
                           promoted_tags=tags)
    assert not universe.resolve_promoted_candidates(near).accepted  # one entity ~450 m apart: the same place
    assert "openstreetmap_places:node/900" not in _request_ids(near)
    _assert_one_universe(near)
    # the same entity id far away is a separate record of a large site (the entity-identity rule): both stay
    apart = _universe_state([_broad("node/1", "Alte Muehle", _FAR, tags=tags), _broad("node/2", "Town Museum", _FAR)],
                            promoted_tags=tags)
    assert [c.provider_place_id for c in universe.resolve_promoted_candidates(apart).accepted] == ["node/900"]
    _assert_one_universe(apart)


def test_a_rejected_broad_candidate_does_not_suppress_an_eligible_promoted_one() -> None:
    # same name, same spot, but the broad record is rejected (provider confidence too low to schedule)
    for rejected in (_broad("node/1", "Old Mill", _NEAR, confidence=0.05),
                     _broad("node/1", "Alte Muehle", _NEAR, confidence=0.05, tags={"wikidata": "Q1"})):
        state = _universe_state([rejected, _broad("node/2", "Town Museum", _FAR)], promoted_tags={"wikidata": "Q1"})
        assert state.candidate_quality_report.attraction_scores[0].quality_tier == CandidateQualityTier.REJECTED
        assert [c.provider_place_id for c in universe.resolve_promoted_candidates(state).accepted] == ["node/900"]
        assert "openstreetmap_places:node/1" not in _request_ids(state)  # the rejected record is offered to nobody
        state.ai_itinerary_reasoning_result = _completed_result([_day(1, ["openstreetmap_places:node/900"])])
        _planned(state)
        # the place is schedulable (before, the planner dropped it and nobody could schedule it)
        assert [stop.provider_place_id for stop in state.experience_plan.daily_plans[0].experiences] == ["node/900"]


def test_the_provider_place_id_is_the_authoritative_identity() -> None:
    # promoted with the SAME provider id as a broad candidate (its name differs): one place, the pool record stands
    state = _universe_state([_broad("node/900", "Moinho Velho", _FAR), _broad("node/2", "Town Museum", _FAR)])
    assert not universe.resolve_promoted_candidates(state).accepted
    ids = _request_ids(state)
    assert ids.count("openstreetmap_places:node/900") == 1
    assert _reference(state, "Moinho Velho").origin.value == "broad_provider_discovery"
    _assert_one_universe(state)


def test_must_visit_and_anchor_identity_are_never_transferred_to_a_namesake() -> None:
    # the user's must-visit was grounded to the BROAD record; a promoted namesake elsewhere is another place
    requested = _broad("node/1", "Old Mill", _FAR)
    record_grounded_term(requested, "the mill")
    state = _universe_state([requested, _broad("node/2", "Town Museum", _FAR)])
    state.trip_request.must_visit = ["the mill"]
    state.destination_context.must_visit_grounding_version = MUST_VISIT_GROUNDING_VERSION
    by_id = {candidate.provider_place_id: candidate for candidate in _allowed(state)}
    assert by_id["node/1"].must_visit is True and by_id["node/900"].must_visit is False
    assert by_id["node/1"].semantic_anchor is False  # the promoted id is the anchor, not its namesake
    _planned(state)
    assert "node/1" in {stop.provider_place_id for day in state.experience_plan.daily_plans for stop in day.experiences}
    PlanValidatorService().run(state)
    assert not [issue for issue in state.validation_report.warnings if issue.category == "must_visit"]

    # when the namesake IS the same place (close by), the promoted record is left out and the grounded
    # must-visit keeps its own provider identity untouched
    requested = _broad("node/1", "Old Mill", _NEAR)
    record_grounded_term(requested, "the mill")
    state = _universe_state([requested, _broad("node/2", "Town Museum", _FAR)])
    state.trip_request.must_visit = ["the mill"]
    state.destination_context.must_visit_grounding_version = MUST_VISIT_GROUNDING_VERSION
    assert [candidate.provider_place_id for candidate in _allowed(state) if candidate.must_visit] == ["node/1"]
    _assert_one_universe(state)


def test_the_universe_rules_restate_existing_thresholds_and_the_planner_reads_the_shared_pool() -> None:
    from app.providers.places import entity_identity
    from app.services import ai_candidate_promotion_service as promotion_module

    assert universe.SAME_NAME_METERS == promotion_module._SAME_PLACE_METERS
    assert universe.SAME_WIKIDATA_METERS == entity_identity._SAME_WIKIDATA_METERS
    assert universe.SCHEDULABLE_TIERS == planner_module._ELIGIBLE_SCHEDULING_TIERS
    # the shared broad pool is exactly the planner's own quality selection
    state = _state([_museum("a", 0), _museum("private", 1, access="private"), _park("b", 2)])
    pois = state.destination_context.candidate_pois
    lookup = planner_module._build_quality_lookup(pois, state.candidate_quality_report.attraction_scores)
    expected = {id(poi) for poi in planner_module._select_candidates_by_quality(pois, lookup)}
    assert {id(poi) for poi in universe.schedulable_broad_pois(state)} == expected and len(expected) == 2
    # one decision, two readers
    source = (_BACKEND_DIR / "app" / "services" / "experience_planner_service.py").read_text()
    builder_source = (_BACKEND_DIR / "app" / "services" / "ai_itinerary_reasoning_request_builder.py").read_text()
    assert "universe.resolve_promoted_candidates(" in source and "universe.resolve_promoted_candidates(" in builder_source


# =====================================================================================
# 9. Scope
# =====================================================================================


def test_q2_production_code_names_no_destination_and_reads_no_geography() -> None:
    import json

    data = json.loads((_BACKEND_DIR / "scripts" / "benchmark" / "quality_v1.json").read_text())
    names: set[str] = set()
    for scenario in (*data["quality_tuning"], *data["quality_holdout"]):
        names.add(scenario["destination"].split(",")[0].casefold())
        names.update(term.casefold() for term in scenario["must_visit"])
    for entry in json.loads((_BACKEND_DIR / "scripts" / "benchmark" / "cities.json").read_text()).values():
        for city in entry if isinstance(entry, list) else []:
            if isinstance(city, dict) and isinstance(city.get("destination"), str):
                names.add(city["destination"].split(",")[0].casefold())
    names.discard("")

    source = (_BACKEND_DIR / "app" / "services" / "candidate_usefulness.py").read_text()
    builder_source = (_BACKEND_DIR / "app" / "services" / "ai_itinerary_reasoning_request_builder.py").read_text()
    for text in (source, builder_source):
        folded = text.casefold()
        assert not [name for name in names if re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", folded)]
    # the usefulness ordering is geography-free (grouping and routes are Q3/Q4) and calls nothing
    for forbidden in ("haversine", "coordinates", "GeoPoint", "app.utils.geo", "app.providers", "gateway"):
        assert forbidden not in source, forbidden
    # no claim vocabulary leaves the module as a field or signal name
    fields = set(usefulness.CandidateUsefulness.__dataclass_fields__) | set(usefulness.QualityEvidence.__dataclass_fields__)
    assert not [name for name in fields if re.search(r"popular|fame|rating|rank(?!$)|review", name)]
