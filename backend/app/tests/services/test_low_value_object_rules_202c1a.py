from __future__ import annotations

from test_itinerary_content_quality import (  # type: ignore[import-not-found]
    gallery,
    museum,
    names,
    plan,
    poi,
    state,
)

from app.models.candidate_quality import CandidateQualityTier
from app.models.planning_state import TripPace
from app.services import place_taxonomy as tx
from app.services.candidate_quality_service import CandidateQualityService
from app.services.plan_validator_service import PlanValidatorService

# Section 202C.1A: structural (provider-tag) rules for single low-value
# objects. No place names, no city expectations -- the tag patterns are the
# ones the 202C release-candidate run exposed (heritage-tree filler, small
# sculptures, memorial plaques, bare wikidata).

TREE = {"tourism": "attraction", "natural": "tree", "heritage": "2", "wikipedia": "xx:Tree", "wikidata": "Q1"}
MEMORIAL_NOTABLE = {"historic": "memorial", "memorial": "plaque", "wikipedia": "en:M", "wikidata": "Q2"}
SCULPTURE_PLAIN = {"tourism": "artwork", "artwork_type": "sculpture"}
SCULPTURE_NOTABLE = {"tourism": "artwork", "artwork_type": "sculpture", "historic": "heritage", "heritage": "2",
                     "wikipedia": "en:S"}


def _tier(tags: dict[str, str], **kw):
    return CandidateQualityService().score_attraction(poi("x", "Thing", tags), **kw)


def _obj(kind: str, i: int, tags: dict[str, str]) -> dict:
    return poi(f"{kind}{i}", f"{kind} {i}", tags, i * 1e-4, 0.0)


# -- trees -----------------------------------------------------------------------------


def test_isolated_tree_is_low_value_even_with_heritage_and_wikipedia_tags() -> None:
    c = tx.classify_place(TREE)
    assert c.low_value and c.object_kind == tx.OBJECT_TREE
    assert tx.LANDMARK not in c.categories
    assert tx.matched_interests(c, ["outdoors", "history", "architecture"]) == []
    score = _tier(TREE)
    assert score.quality_tier == CandidateQualityTier.LOW_PRIORITY  # never eligible filler


def test_tree_with_stronger_structural_evidence_is_not_treated_as_an_isolated_tree() -> None:
    assert not tx.classify_place({**TREE, "leisure": "park"}).low_value
    assert not tx.classify_place({"natural": "tree", "tourism": "viewpoint"}).low_value
    assert not tx.classify_place({"natural": "tree", "historic": "ruins"}).low_value


def test_parks_gardens_reserves_and_significant_natural_attractions_remain_valid() -> None:
    for tags in ({"leisure": "park"}, {"leisure": "garden"}, {"leisure": "nature_reserve"}, {"tourism": "viewpoint"},
                 {"natural": "peak", "tourism": "attraction", "wikipedia": "en:P"}):
        c = tx.classify_place(tags)
        assert not c.low_value and c.object_kind is None, tags
    assert "outdoors" in tx.matched_interests(tx.classify_place({"leisure": "garden"}), ["outdoors"])
    assert _tier({"leisure": "park"}).quality_tier != CandidateQualityTier.LOW_PRIORITY


def test_an_isolated_tree_can_still_be_scheduled_when_it_is_a_must_visit() -> None:
    score = _tier(TREE, must_visit_names=["thing"])
    assert score.quality_tier in (CandidateQualityTier.PRIMARY_ANCHOR, CandidateQualityTier.GOOD_CANDIDATE)


# -- sculptures / memorials ------------------------------------------------------------------


def test_generic_sculpture_is_low_value_and_does_not_satisfy_architecture_or_outdoors() -> None:
    c = tx.classify_place(SCULPTURE_PLAIN)
    assert c.low_value and c.object_kind == tx.OBJECT_ARTWORK
    assert tx.matched_interests(c, ["architecture", "outdoors", "history"]) == []
    assert tx.matched_interests(c, ["art"]) == ["art"]  # an art request is honestly served


def test_notable_object_does_not_become_a_landmark_and_never_matches_architecture() -> None:
    for tags in (MEMORIAL_NOTABLE, SCULPTURE_NOTABLE):
        c = tx.classify_place(tags)
        assert c.notable_object and not c.low_value
        assert tx.LANDMARK not in c.categories
        assert "architecture" not in tx.matched_interests(c, ["architecture", "outdoors"])
    assert tx.matched_interests(tx.classify_place(MEMORIAL_NOTABLE), ["history"]) == ["history"]
    assert _tier(MEMORIAL_NOTABLE).quality_tier == CandidateQualityTier.SECONDARY_CANDIDATE


def test_provider_designated_tourist_attraction_object_keeps_landmark_semantics() -> None:
    c = tx.classify_place({**MEMORIAL_NOTABLE, "tourism": "attraction"})
    assert not c.notable_object and tx.LANDMARK in c.categories


def test_bare_wikidata_is_not_significance_evidence_for_scoring_or_objects() -> None:
    bare = {"historic": "memorial", "memorial": "statue", "wikidata": "Q9"}
    assert tx.classify_place(bare).low_value
    plain = _tier({"tourism": "attraction"})
    with_id = _tier({"tourism": "attraction", "wikidata": "Q9"})
    assert with_id.total_score == plain.total_score
    assert _tier(bare).total_score <= 0.65 * 0.4 + 0.35 * 0.6 + 1e-9  # low-value cap, no wikidata floor


def test_a_documented_memorial_can_satisfy_history_but_a_plain_statue_cannot() -> None:
    assert tx.matched_interests(tx.classify_place(MEMORIAL_NOTABLE), ["history"]) == ["history"]
    assert tx.matched_interests(tx.classify_place({"historic": "memorial", "memorial": "statue"}), ["history"]) == []


# -- planner: concentration, art focus, sparse supply -----------------------------------------


def test_memorial_and_sculpture_concentration_is_controlled_when_alternatives_exist() -> None:
    objects = [_obj("m", i, MEMORIAL_NOTABLE) for i in range(8)] + [_obj("s", i, SCULPTURE_NOTABLE) for i in range(4)]
    good = [museum(i, 0.0005 * i) for i in range(6)]
    days = plan(state(objects + good, days=2, pace=TripPace.PACKED, interests=["history"]))
    scheduled = [e for d in days for e in d]
    small = [e for e in scheduled if e.notable_object or e.low_value_object]
    assert len(scheduled) == 8
    assert len(small) <= 3  # soft cap, not a ban: at most a quarter plus interest coverage


def test_art_focused_trip_may_contain_appropriate_artworks() -> None:
    art = [_obj("a", i, SCULPTURE_NOTABLE) for i in range(4)] + [_obj("p", i, SCULPTURE_PLAIN) for i in range(4)]
    days = plan(state(art + [museum(i, 0.001 * i) for i in range(2)], days=2, pace=TripPace.PACKED, interests=["art"]))
    scheduled = [e for d in days for e in d]
    assert sum(1 for e in scheduled if "art" in e.matched_interests) >= 4


def test_notable_memorials_still_serve_history_when_nothing_better_exists() -> None:
    days = plan(state([_obj("m", i, MEMORIAL_NOTABLE) for i in range(3)], days=1, interests=["history"]))
    scheduled = [e for d in days for e in d]
    assert scheduled and all("history" in e.matched_interests for e in scheduled)


def test_adequate_good_supply_leaves_no_empty_or_thin_day_even_with_junk_present() -> None:
    junk = [_obj("j", i, {"historic": "memorial", "memorial": "plaque"}) for i in range(6)]
    trees = [_obj("t", i, TREE) for i in range(4)]
    good = [museum(i, 0.001 * i) for i in range(6)]
    st = state(junk + trees + good, days=3, pace=TripPace.BALANCED)
    days = plan(st)
    assert all(len(d) >= 2 for d in days)
    assert not any(n.startswith("t ") for n in names(days))  # no isolated tree scheduled


def test_poor_supply_only_gives_an_honest_limitation_not_junk_padding() -> None:
    junk = [_obj("j", i, {"historic": "memorial", "memorial": "plaque"}) for i in range(6)] + [
        _obj("t", i, TREE) for i in range(4)
    ]
    st = state(junk, days=3, pace=TripPace.BALANCED)
    days = plan(st)
    scheduled = [e for d in days for e in d]
    assert len(scheduled) <= 2  # diluted share of capacity, trees excluded
    assert not any(e.name.startswith("t ") for e in scheduled)
    PlanValidatorService().run(st)
    categories = {i.category for i in (*st.validation_report.warnings, *st.validation_report.critical_issues)}
    assert "empty_day" in categories or "thin_day" in categories or "low_value_filler_skipped" in categories
    skipped = [i for i in st.validation_report.warnings if i.category == "low_value_filler_skipped"]
    assert skipped and skipped[0].severity.value == "suggestion"
    empty = [i for i in st.validation_report.warnings if i.category == "empty_day"]
    assert all("selection gap" not in i.message for i in empty)  # held-back junk is not a selection gap


def test_gallery_rule_from_202b2_is_unchanged() -> None:
    days = plan(state([gallery(i) for i in range(6)] + [museum(i, 0.001 * i) for i in range(6)], days=2,
                      pace=TripPace.PACKED, interests=["food"]))
    assert sum(1 for e in (x for d in days for x in d) if e.commercial_gallery) <= 2
