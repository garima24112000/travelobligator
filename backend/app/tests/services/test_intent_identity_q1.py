from __future__ import annotations

from typing import Any

import pytest

from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.planning_state import PlanningState
from app.models.providers import NormalizedPlace, ProviderResponse
from app.providers.base import PlacesProvider, unavailable_response
from app.services import experience_planner_service as planner_module
from app.services import place_taxonomy as tx
from app.services.candidate_quality_service import CandidateQualityService
from app.services.destination_context_service import DestinationContextService
from app.services.interest_coverage import interest_coverage
from app.services.must_visit_matching import (
    MUST_VISIT_GROUNDING_VERSION,
    grounded_terms,
    is_tagged_must_visit,
    legacy_must_visit_place_ids,
    must_visit_place_ids,
    resolve_must_visits,
)
from app.services.route_burden_repair_service import RouteBurdenRepairService
from app.tests.services.test_final_quality_correction_203c2b import (
    _GOOD_NEARBY,
    _NEAR_A,
    _NEAR_B,
    _Gateway,
    _day_names,
    _must_visit_issues,
    _poi,
    _point,
    _repair,
    _state,
    _with_routes,
)

# Q1 -- intent & identity correctness (docs/24_itinerary_quality_contract.md,
# A1 and A2). Every name here is synthetic: the rules are generic and no
# destination, landmark or provider-specific value is involved.


# =====================================================================================
# A2. The waterfront family is one canonical interest, backed by provider evidence
# =====================================================================================

_WATERFRONT_WORDS = ("waterfront", "pier", "piers", "riverfront", "harbor", "harbour", "promenade")


@pytest.mark.parametrize("word", _WATERFRONT_WORDS)
def test_every_waterfront_family_word_is_the_one_canonical_interest(word: str) -> None:
    assert tx.canonical_interests([word]) == ["waterfront"]
    assert tx.canonical_interests([word.upper()]) == ["waterfront"]
    assert tx.canonical_interests([f"the old {word} area"]) == ["waterfront"]


def test_plurals_and_repeats_give_one_interest_not_one_per_synonym() -> None:
    assert tx.canonical_interests(["harbors", "harbours", "promenades", "riverfronts", "waterfronts"]) == ["waterfront"]
    assert tx.canonical_interests(["pier", "piers", "harbour", "waterfront"]) == ["waterfront"]
    # first-seen order among different interests is kept
    assert tx.canonical_interests(["museums", "piers"]) == ["museum", "waterfront"]
    assert tx.canonical_interests(["piers", "parks", "museums"]) == ["waterfront", "outdoors", "museum"]


@pytest.mark.parametrize("tags", [{"man_made": "pier"}, {"leisure": "marina"}, {"natural": "beach"}])
def test_a_provider_waterfront_place_satisfies_waterfront_and_still_outdoors(tags: dict[str, str]) -> None:
    classification = tx.classify_place(tags)
    assert tx.WATERFRONT in classification.categories
    assert tx.matched_interests(classification, ["waterfront"]) == ["waterfront"]
    assert tx.matched_interests(classification, ["waterfront", "outdoors"]) == ["waterfront", "outdoors"]


@pytest.mark.parametrize(
    "tags",
    [{"leisure": "park"}, {"leisure": "garden"}, {"leisure": "nature_reserve"}, {"natural": "peak"},
     {"tourism": "viewpoint"}, {"tourism": "museum"}, {"tourism": "attraction"}],
)
def test_an_ordinary_outdoors_or_other_place_never_satisfies_waterfront(tags: dict[str, str]) -> None:
    classification = tx.classify_place(tags)
    assert tx.WATERFRONT not in classification.categories
    assert tx.matched_interests(classification, ["waterfront"]) == []


def test_existing_outdoors_behaviour_is_unchanged() -> None:
    for word in ("outdoors", "nature", "parks", "hiking", "gardens", "trail", "scenic", "beach"):
        assert tx.canonical_interests([word]) == ["outdoors"], word
    for tags in ({"leisure": "park"}, {"leisure": "garden"}, {"tourism": "viewpoint"}, {"man_made": "pier"}):
        assert tx.matched_interests(tx.classify_place(tags), ["outdoors"]) == ["outdoors"], tags
    assert tx.INTEREST_CATEGORIES["outdoors"] == frozenset({tx.PARK_NATURE, tx.WATERFRONT, tx.VIEWPOINT})
    assert tx.INTEREST_CATEGORIES["waterfront"] == frozenset({tx.WATERFRONT})


def test_an_unrecognised_interest_stays_unrecognised() -> None:
    assert tx.canonical_interests(["quantum chess"]) == []
    assert tx.canonical_interests(["seaplanes", "lighthouses"]) == []
    assert tx.canonical_interests(["quantum chess", "piers"]) == ["waterfront"]


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        (["museums"], ["museum"]),
        (["history"], ["history"]),
        (["art"], ["art"]),
        (["architecture"], ["architecture"]),
        (["local food"], ["food"]),
        (["nightlife"], ["nightlife"]),
        (["markets"], ["shopping"]),
        (["local food", "museums", "hiking", "quantum chess"], ["food", "museum", "outdoors"]),
        (["nightlife", "Architecture"], ["nightlife", "architecture"]),
    ],
)
def test_existing_interest_mappings_are_unchanged(terms: list[str], expected: list[str]) -> None:
    assert tx.canonical_interests(terms) == expected


def test_a_requested_waterfront_interest_is_tracked_and_covered_only_by_a_waterfront_stop() -> None:
    park = _poi("park", "Green Commons", _point(50.0, 10.0), category="park", provider_tags={"leisure": "park"})
    pier = _poi("pier", "North Landing", _point(50.001, 10.001), category="pier", provider_tags={"man_made": "pier"})

    def scheduled(*pois: dict[str, Any]) -> PlanningState:
        state = _state([park, pier], [list(pois)], interests=["piers"])
        for stop, poi in zip(state.experience_plan.daily_plans[0].experiences, pois):
            stop.matched_interests = tx.matched_interests(tx.classify_candidate(poi), ["waterfront"])
        return state

    assert interest_coverage(scheduled(park)) == {"waterfront": False}
    assert interest_coverage(scheduled(park, pier)) == {"waterfront": True}
    report = scheduled(park).candidate_quality_report
    by_name = {score.candidate_name: score for score in report.attraction_scores}
    # the pier is verified supply for the request; the park is not
    assert by_name["North Landing"].matched_interests == ["waterfront"]
    assert by_name["Green Commons"].matched_interests == []


# =====================================================================================
# A1. Must-visit identity is established once, by the destination-context stage
# =====================================================================================

_MONUMENT = _poi("monument", "Liberty Monument", _point(50.000, 10.000))
_MONUMENT_MUSEUM = _poi("monument-museum", "Liberty Monument Museum", _point(50.0005, 10.0005))
_UNRELATED = _poi("garden", "Monument Gardens of Liberty", _point(50.004, 10.004))


def _place(key: str, name: str, lat: float = 50.01, lng: float = 10.01, place_id: str | None = "") -> NormalizedPlace:
    return NormalizedPlace(
        place_id=f"geoapify/{key}" if place_id == "" else (place_id or ""), name=name, category="museum",
        coordinates=GeoPoint(lat=lat, lng=lng), source="geoapify_places", data_status=DataStatus.LIVE,
        confidence=0.6, provider_tags={"tourism": "museum"},
    )


class _Lookup:
    """A places provider's targeted named lookup: answers only what it was
    told to, and records what it was asked."""

    provider_name = "fixture_places"
    provider_type = PlacesProvider.provider_type

    def __init__(self, answers: dict[str, NormalizedPlace] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[str] = []

    def search_must_visit_place(self, must_visit_term: str, primary_destination: str, filters: Any = None) -> ProviderResponse[Any]:
        self.calls.append(must_visit_term)
        place = self.answers.get(must_visit_term)
        if place is None:
            return unavailable_response(self.provider_name, self.provider_type, unavailable_fields=["must_visit_place"])
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name, provider_type=self.provider_type, status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE, data=[place], confidence=0.5, message="found",
        )


def _ground(pool: list[dict[str, Any]], terms: list[str], lookup: _Lookup) -> tuple[list[dict[str, Any]], list[str]]:
    pois = [dict(poi) for poi in pool]
    state = _state(pois, [[]], must_visit=terms)
    return DestinationContextService()._append_must_visit_candidates(state, "Fixtureville, Fixtureland", pois, places=lookup)


def _tagged(pois: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {poi["place_id"]: grounded_terms(poi) for poi in pois if grounded_terms(poi)}


def _q1_state(pois: list[dict[str, Any]], days: list[list[dict[str, Any]]], **trip: Any) -> PlanningState:
    """A state whose context was built under Q1: downstream stages read the
    recorded identity and nothing else."""
    state = _state(pois, days, **trip)
    state.destination_context.must_visit_grounding_version = MUST_VISIT_GROUNDING_VERSION
    state.candidate_quality_report = CandidateQualityService().build_report(state)
    return state


def _must_visit_tier(state: PlanningState, terms: list[str]) -> list[str]:
    pois = state.destination_context.candidate_pois
    _, must_visit_ids, _ = planner_module._order_candidates(pois, terms, [], legacy_must_visit_place_ids(state))
    return [poi["name"] for poi in pois if id(poi) in must_visit_ids]


def _quality_must_visit_names(state: PlanningState) -> list[str]:
    return [
        score.candidate_name
        for score in state.candidate_quality_report.attraction_scores
        if any("must-visit" in signal for signal in score.positive_signals)
    ]


# -- grounding ------------------------------------------------------------------------------


def test_one_exact_pool_name_is_the_identity_and_a_related_longer_name_inherits_nothing() -> None:
    lookup = _Lookup()
    pois, ungrounded = _ground([_MONUMENT, _MONUMENT_MUSEUM, _UNRELATED], ["Liberty Monument"], lookup)

    assert lookup.calls == [] and ungrounded == []  # identity already established: no lookup
    assert _tagged(pois) == {"geoapify/monument": ["Liberty Monument"]}
    assert len(pois) == 3


def test_exact_match_ignores_case_accents_and_punctuation_but_never_extra_words() -> None:
    pool = [_poi("cafe", "Café Liberté", _point(50.0, 10.0)), _poi("annex", "Café Liberté Annex", _point(50.0, 10.001))]
    pois, ungrounded = _ground(pool, ["cafe  liberte!"], _Lookup())
    assert _tagged(pois) == {"geoapify/cafe": ["cafe  liberte!"]} and ungrounded == []


def test_a_substring_only_pool_hit_no_longer_stops_the_lookup() -> None:
    # Only the related place is in the pool: before Q1 it was taken for the must-visit.
    lookup = _Lookup({"Liberty Monument": _place("monument", "Liberty Monument")})
    pois, ungrounded = _ground([_MONUMENT_MUSEUM, _UNRELATED], ["Liberty Monument"], lookup)

    assert lookup.calls == ["Liberty Monument"] and ungrounded == []
    assert _tagged(pois) == {"geoapify/monument": ["Liberty Monument"]}
    assert [poi["name"] for poi in pois] == ["Liberty Monument Museum", "Monument Gardens of Liberty", "Liberty Monument"]


def test_an_alternate_provider_name_is_grounded_through_the_lookup_and_keeps_the_pool_identity() -> None:
    pool = [_poi("tower", "Torre Alta", _point(50.0, 10.0)), _NEAR_B]
    lookup = _Lookup({"the high tower": _place("tower", "Torre Alta", 50.0, 10.0)})
    pois, ungrounded = _ground(pool, ["the high tower"], lookup)

    assert lookup.calls == ["the high tower"] and ungrounded == []
    assert _tagged(pois) == {"geoapify/tower": ["the high tower"]}
    assert len(pois) == 2  # the provider's own entity; nothing duplicated


def test_a_lookup_result_under_another_provider_id_is_never_merged_into_a_same_named_candidate() -> None:
    # The pool holds provider/Y "Shared Museum"; the user's term is something else, and the
    # provider's targeted lookup answers with provider/X, which happens to share that name.
    pool_y = _poi("shared-y", "Shared Museum", _point(50.000, 10.000))
    lookup = _Lookup({"the collection downtown": _place("shared-x", "Shared Museum", 50.03, 10.03)})
    terms = ["the collection downtown"]
    pois, ungrounded = _ground([pool_y, _NEAR_B], terms, lookup)

    assert lookup.calls == terms and ungrounded == []
    assert [poi["place_id"] for poi in pois] == ["geoapify/shared-y", _NEAR_B["place_id"], "geoapify/shared-x"]
    assert _tagged(pois) == {"geoapify/shared-x": terms}  # X appended and tagged; Y untouched
    y, x = pois[0], pois[2]
    assert grounded_terms(y) == [] and "must_visit_term" not in y

    state = _q1_state(pois, [[y, pois[1], x]], must_visit=terms)
    resolution = resolve_must_visits(state)[0]
    assert resolution.grounded_place_ids == frozenset({"geoapify/shared-x"}) and resolution.scheduled
    assert must_visit_place_ids(state) == {"geoapify/shared-x"}

    scores = {score.candidate_id: score for score in state.candidate_quality_report.attraction_scores}
    treated = [cid for cid, score in scores.items() if any("must-visit" in s for s in score.positive_signals)]
    assert treated == ["geoapify/shared-x"]
    assert scores["geoapify/shared-x"].total_score > scores["geoapify/shared-y"].total_score

    candidates = state.destination_context.candidate_pois
    _, tier_ids, _ = planner_module._order_candidates(candidates, terms, [], legacy_must_visit_place_ids(state))
    assert [poi["place_id"] for poi in candidates if id(poi) in tier_ids] == ["geoapify/shared-x"]

    stops = state.experience_plan.daily_plans[0].experiences
    protection = [RouteBurdenRepairService._protection(stop, must_visit_place_ids(state), set(), set()) for stop in stops]
    assert protection == ["replaceable", "replaceable", "must_visit"]

    # scheduling only Y does not satisfy the term
    only_y = _q1_state(pois, [[y, pois[1]]], must_visit=terms)
    assert not resolve_must_visits(only_y)[0].scheduled and len(_must_visit_issues(only_y)) == 1


def test_an_ungroundable_term_stays_ungrounded_and_tags_nothing() -> None:
    lookup = _Lookup()
    pois, ungrounded = _ground([_MONUMENT_MUSEUM, _UNRELATED], ["Liberty Monument"], lookup)
    assert lookup.calls == ["Liberty Monument"] and ungrounded == ["Liberty Monument"]
    assert _tagged(pois) == {} and len(pois) == 2

    # a result with no provider identity cannot carry the term either
    no_id = _Lookup({"Liberty Monument": _place("x", "Liberty Monument", place_id=None)})
    pois, ungrounded = _ground([_MONUMENT_MUSEUM], ["Liberty Monument"], no_id)
    assert ungrounded == ["Liberty Monument"] and _tagged(pois) == {} and len(pois) == 1


def test_two_provider_entities_with_the_same_exact_name_are_resolved_by_the_lookup_not_by_name() -> None:
    """A: an ambiguous exact name is never tagged by the shortcut."""
    north = _poi("hall-north", "Founders Hall", _point(50.00, 10.00))
    south = _poi("hall-south", "Founders Hall", _point(50.05, 10.05))
    lookup = _Lookup({"Founders Hall": _place("hall-south", "Founders Hall", 50.05, 10.05)})
    pois, ungrounded = _ground([north, south, _NEAR_B], ["Founders Hall"], lookup)

    assert lookup.calls == ["Founders Hall"] and ungrounded == []
    assert _tagged(pois) == {"geoapify/hall-south": ["Founders Hall"]}  # only the resolved identity
    assert len(pois) == 3

    state = _q1_state(pois, [[pois[0], pois[1], pois[2]]], must_visit=["Founders Hall"])
    assert must_visit_place_ids(state) == {"geoapify/hall-south"}
    stops = state.experience_plan.daily_plans[0].experiences
    protection = [RouteBurdenRepairService._protection(stop, must_visit_place_ids(state), set(), set()) for stop in stops]
    assert protection == ["replaceable", "must_visit", "replaceable"]

    # and when the provider cannot resolve it, neither same-named place is protected
    pois, ungrounded = _ground([north, south, _NEAR_B], ["Founders Hall"], _Lookup())
    assert ungrounded == ["Founders Hall"] and _tagged(pois) == {}
    unresolved = _q1_state(pois, [[pois[0], pois[1]]], must_visit=["Founders Hall"])
    assert must_visit_place_ids(unresolved) == set() and not resolve_must_visits(unresolved)[0].grounded
    assert len(_must_visit_issues(unresolved)) == 1


def test_two_user_terms_grounded_to_one_provider_place_share_one_candidate_and_one_stop() -> None:
    """B: several terms, one provider identity."""
    lookup = _Lookup(
        {
            "the high tower": _place("tower", "Torre Alta", 50.0, 10.0),
            "tall tower of the old town": _place("tower", "Torre Alta", 50.0, 10.0),
        }
    )
    terms = ["the high tower", "tall tower of the old town"]
    pois, ungrounded = _ground([_NEAR_A, _NEAR_B], terms, lookup)

    assert ungrounded == [] and sorted(lookup.calls) == sorted(terms)
    assert len(pois) == 3  # appended once, not once per term
    tower = pois[2]
    assert tower["must_visit_terms"] == terms and tower["must_visit_term"] == terms[0]

    state = _q1_state(pois, [[pois[0], tower]], must_visit=terms)
    resolutions = resolve_must_visits(state)
    assert [(r.term, r.grounded, r.scheduled) for r in resolutions] == [(terms[0], True, True), (terms[1], True, True)]
    assert {r.grounded_place_ids for r in resolutions} == {frozenset({"geoapify/tower"})}
    assert must_visit_place_ids(state) == {"geoapify/tower"}  # protected once, by provider identity
    assert _day_names(state).count("Torre Alta") == 1
    assert _must_visit_tier(state, terms) == ["Torre Alta"]
    assert _must_visit_issues(state) == []

    # a term that names the pool place exactly joins the same candidate without a lookup
    pool = [_poi("tower", "Torre Alta", _point(50.0, 10.0))]
    again = _Lookup({"the high tower": _place("tower", "Torre Alta", 50.0, 10.0)})
    pois, ungrounded = _ground(pool, ["Torre Alta", "the high tower", "torre alta"], again)
    assert again.calls == ["the high tower"] and ungrounded == [] and len(pois) == 1
    assert pois[0]["must_visit_terms"] == ["Torre Alta", "the high tower"]


# -- downstream consumers read the recorded identity only ------------------------------------


def test_downstream_stages_give_no_must_visit_status_to_an_untagged_exact_name() -> None:
    """C: in a Q1 state a place is not a must-visit because of what it is called."""
    untagged = dict(_MONUMENT)  # exactly the user's words, but the context stage recorded nothing on it
    state = _q1_state([untagged, _NEAR_B], [[untagged, _NEAR_B]], must_visit=["Liberty Monument"])
    terms = ["Liberty Monument"]

    assert is_tagged_must_visit(untagged, terms) is False
    assert planner_module._matches_must_visit(untagged, ["liberty monument"]) is False
    assert _must_visit_tier(state, terms) == []
    assert _quality_must_visit_names(state) == []
    score = CandidateQualityService().score_attraction(untagged, must_visit_names=terms)
    assert not any("must-visit" in signal for signal in score.positive_signals)

    resolution = resolve_must_visits(state)[0]
    assert not resolution.grounded and not resolution.scheduled
    assert must_visit_place_ids(state) == set() and legacy_must_visit_place_ids(state) == frozenset()
    assert len(_must_visit_issues(state)) == 1
    stop = state.experience_plan.daily_plans[0].experiences[0]
    assert RouteBurdenRepairService._protection(stop, must_visit_place_ids(state), set(), set()) == "replaceable"


def test_every_consumer_agrees_on_the_one_grounded_identity() -> None:
    pois, _ = _ground([_MONUMENT, _MONUMENT_MUSEUM, _UNRELATED], ["Liberty Monument"], _Lookup())
    state = _q1_state(pois, [pois], must_visit=["Liberty Monument"])
    terms = ["Liberty Monument"]

    assert _must_visit_tier(state, terms) == ["Liberty Monument"]
    assert _quality_must_visit_names(state) == ["Liberty Monument"]
    resolution = resolve_must_visits(state)[0]
    assert resolution.grounded_place_ids == frozenset({"geoapify/monument"}) and resolution.scheduled
    assert resolution.grounded_names == ("Liberty Monument",)
    assert must_visit_place_ids(state) == {"geoapify/monument"}
    assert _must_visit_issues(state) == []

    scores = {score.candidate_name: score for score in state.candidate_quality_report.attraction_scores}
    # the related and the merely word-sharing place are scored as ordinary candidates
    assert scores["Liberty Monument"].total_score > scores["Liberty Monument Museum"].total_score
    assert scores["Liberty Monument Museum"].total_score == scores["Monument Gardens of Liberty"].total_score

    # scheduling only the related place does not satisfy the term
    related_only = _q1_state(pois, [[pois[1], pois[2]]], must_visit=terms)
    assert resolve_must_visits(related_only)[0].grounded and not resolve_must_visits(related_only)[0].scheduled
    assert len(_must_visit_issues(related_only)) == 1


def test_route_repair_keeps_the_grounded_must_visit_and_may_replace_the_related_place() -> None:
    monument = {**_NEAR_A, "name": "Liberty Monument", "must_visit_term": "Liberty Monument"}
    related_far = _poi("related", "Liberty Monument Museum", _point(50.000, 10.100))
    gateway = _Gateway()
    state = _with_routes(
        _q1_state(
            [monument, _NEAR_B, related_far, _GOOD_NEARBY], [[monument, _NEAR_B, related_far]],
            must_visit=["Liberty Monument"],
        ),
        gateway,
    )
    assert must_visit_place_ids(state) == {monument["place_id"]}

    attempt = _repair(state, gateway).attempts[0]

    assert attempt.accepted and attempt.replaced_place == "Liberty Monument Museum"
    assert "Liberty Monument" in _day_names(state) and "Liberty Monument Museum" not in _day_names(state)
    assert attempt.stop_protections[0] == "must_visit" and attempt.stop_protections[2] == "replaceable"


# -- states stored before Q1 ----------------------------------------------------------------


def test_a_legacy_state_resolves_a_unique_exact_name_once_for_every_consumer() -> None:
    legacy = _state([dict(_MONUMENT), _MONUMENT_MUSEUM], [[_MONUMENT, _MONUMENT_MUSEUM]], must_visit=["Liberty Monument"])
    assert legacy.destination_context.must_visit_grounding_version == 0

    assert legacy_must_visit_place_ids(legacy) == frozenset({"geoapify/monument"})
    assert must_visit_place_ids(legacy) == {"geoapify/monument"}  # never the related longer name
    assert _must_visit_tier(legacy, ["Liberty Monument"]) == ["Liberty Monument"]
    assert _quality_must_visit_names(legacy) == ["Liberty Monument"]
    assert _must_visit_issues(legacy) == []
    # the per-candidate functions themselves stay tag-only, even for a legacy state
    assert planner_module._matches_must_visit(legacy.destination_context.candidate_pois[0], ["liberty monument"]) is False


def test_a_legacy_state_with_an_ambiguous_exact_name_protects_nothing() -> None:
    north = _poi("hall-north", "Founders Hall", _point(50.00, 10.00))
    south = _poi("hall-south", "Founders Hall", _point(50.05, 10.05))
    legacy = _state([north, south], [[north, south]], must_visit=["Founders Hall"])

    assert legacy_must_visit_place_ids(legacy) == frozenset() and must_visit_place_ids(legacy) == set()
    assert _must_visit_tier(legacy, ["Founders Hall"]) == [] and _quality_must_visit_names(legacy) == []
    assert len(_must_visit_issues(legacy)) == 1

    # a term recorded before Q1 (a targeted-lookup result) is read as before
    tagged = {**north, "must_visit_term": "the founders' building"}
    recorded = _state([tagged, south], [[tagged, south]], must_visit=["the founders' building"])
    assert must_visit_place_ids(recorded) == {"geoapify/hall-north"} and legacy_must_visit_place_ids(recorded) == frozenset()
