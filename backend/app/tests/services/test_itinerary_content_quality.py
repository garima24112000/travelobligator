from __future__ import annotations

from typing import Any

import pytest

from app.models.candidate_quality import CandidateQualityTier, CandidateRejectReason
from app.models.planning_state import (
    DestinationContext,
    PlanningState,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.providers.places import openstreetmap_adapter as osm
from app.services import place_taxonomy as tx
from app.services.candidate_quality_service import CandidateQualityService
from app.services.destination_context_service import DestinationContextService
from app.services.experience_planner_service import (
    ExperiencePlannerService,
    _fill_empty_days,
    _group_candidates_into_balanced_days,
)
from app.services.plan_validator_service import PlanValidatorService

# Section 202B.2 (Tasks 2-13, 17-29, 33, 36, 41): deterministic tests built
# from the PATTERNS the 202A baseline exposed, using small synthetic
# provider candidate sets. No city names, no place-specific expectations.


def poi(pid: str, name: str, tags: dict[str, str], lat: float = 0.0, lng: float = 0.0, **extra: Any) -> dict[str, Any]:
    category = tags.get("tourism") or tags.get("amenity") or tags.get("historic") or tags.get("leisure")
    return {
        "place_id": pid,
        "name": name,
        "category": category,
        "provider_tags": tags,
        "coordinates": {"lat": lat, "lng": lng},
        "address": None,
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": 0.6,
        **extra,
    }


def statue(i: int, lat: float = 0.0, lng: float = 0.0) -> dict[str, Any]:
    return poi(f"s{i}", f"Small Statue {i}", {"historic": "memorial", "memorial": "statue"}, lat + i * 1e-4, lng)


def museum(i: int, lat: float = 0.0, lng: float = 0.0, **tags: str) -> dict[str, Any]:
    return poi(f"m{i}", f"Museum {i}", {"tourism": "museum", **tags}, lat + i * 1e-4, lng)


def gallery(i: int, lat: float = 0.0, lng: float = 0.0) -> dict[str, Any]:
    return poi(f"g{i}", f"Gallery {i}", {"tourism": "gallery"}, lat + i * 1e-4, lng)


def state(
    pois: list[dict[str, Any]],
    *,
    days: int = 2,
    pace: TripPace = TripPace.BALANCED,
    interests: list[str] | None = None,
    restaurants: list[dict[str, Any]] | None = None,
) -> PlanningState:
    st = PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville",
            start_date="2026-08-10",
            end_date=f"2026-08-{9 + days}",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
            pace=pace,
            interests=interests or [],
        )
    )
    st.destination_context = DestinationContext(
        destination_name="Testville", candidate_pois=pois, candidate_restaurants=restaurants or []
    )
    st.candidate_quality_report = CandidateQualityService().build_report(st)
    return st


def plan(st: PlanningState) -> list[list[Any]]:
    ExperiencePlannerService().run(st)
    return [d.experiences for d in st.experience_plan.daily_plans]


def names(days: list[list[Any]]) -> list[str]:
    return [e.name for d in days for e in d]


# -- taxonomy -----------------------------------------------------------------------


def test_medical_facility_without_tourist_evidence_is_unsuitable_and_rejected() -> None:
    hospital = poi("h1", "Homeopathic Hospital", {"amenity": "hospital", "historic": "building"})

    assert tx.classify_candidate(hospital).unsuitable_reason == "amenity=hospital"
    score = CandidateQualityService().score_attraction(hospital)
    assert score.quality_tier == CandidateQualityTier.REJECTED
    assert CandidateRejectReason.UNSUITABLE_PLACE_TYPE in score.reject_reasons


def test_functional_place_with_provider_tourist_or_significance_evidence_is_not_unsuitable() -> None:
    as_museum = poi("h2", "Old Infirmary", {"amenity": "hospital", "tourism": "museum"})
    significant = poi("h3", "Old Hospital", {"amenity": "hospital", "historic": "building", "wikipedia": "en:Old"})
    assert not tx.classify_candidate(as_museum).is_unsuitable
    assert not tx.classify_candidate(significant).is_unsuitable


@pytest.mark.parametrize(
    "tags",
    [{"amenity": "pharmacy"}, {"amenity": "school"}, {"office": "company"}, {"building": "residential"},
     {"amenity": "parking"}, {"man_made": "storage_tank"}],
)
def test_other_functional_types_are_unsuitable_structurally(tags: dict[str, str]) -> None:
    assert tx.classify_place(tags).is_unsuitable


def test_a_must_visit_can_override_an_unsuitable_type() -> None:
    hospital = poi("h1", "Museum Hospital", {"amenity": "hospital"})
    score = CandidateQualityService().score_attraction(hospital, must_visit_names=["museum hospital"])
    assert CandidateRejectReason.UNSUITABLE_PLACE_TYPE not in score.reject_reasons


def test_small_objects_are_low_value_unless_provider_carries_significance() -> None:
    assert tx.classify_place({"tourism": "artwork", "artwork_type": "sculpture"}).low_value
    assert tx.classify_place({"historic": "memorial", "memorial": "plaque"}).low_value
    assert not tx.classify_place({"tourism": "artwork", "wikipedia": "en:X"}).low_value
    # A bare wikidata tag (carried by most small statues) is only a weak signal.
    assert tx.classify_place({"tourism": "artwork", "wikidata": "Q9"}).low_value


def test_commercial_gallery_and_sub_feature_flags_use_structured_tags() -> None:
    assert tx.classify_place({"tourism": "gallery"}).commercial_gallery
    assert not tx.classify_place({"tourism": "gallery", "wikipedia": "en:G"}).commercial_gallery
    assert tx.classify_place({"tourism": "attraction", "zoo": "enclosure"}).sub_feature_kind == "zoo"
    assert tx.classify_place({"tourism": "attraction"}).sub_feature_kind is None


def test_classification_falls_back_to_the_category_value_but_never_to_the_name() -> None:
    assert tx.classify_place(None, "hospital").is_unsuitable
    assert tx.classify_place(None, "artwork").low_value
    # A name never influences classification.
    assert not tx.classify_candidate({"name": "General Hospital Museum", "category": "attraction"}).is_unsuitable


def test_interest_vocabulary_maps_only_recognised_terms() -> None:
    assert tx.canonical_interests(["local food", "museums", "hiking", "quantum chess"]) == ["food", "museum", "outdoors"]
    assert tx.canonical_interests(["nightlife", "Architecture"]) == ["nightlife", "architecture"]


def test_matched_interests_come_only_from_provider_categories() -> None:
    park = tx.classify_place({"leisure": "park"})
    market = tx.classify_place({"amenity": "marketplace"})
    assert tx.matched_interests(park, ["outdoors", "food"]) == ["outdoors"]
    assert tx.matched_interests(market, ["outdoors", "food"]) == ["food"]
    assert tx.matched_interests(tx.classify_place({"amenity": "hospital"}), ["food", "outdoors"]) == []


# -- quality scoring ---------------------------------------------------------------------


def test_significance_signals_boost_but_never_guarantee() -> None:
    svc = CandidateQualityService()
    plain = svc.score_attraction(poi("a", "Plain", {"tourism": "attraction"}))
    bare_id = svc.score_attraction(poi("b", "Bare", {"tourism": "attraction", "wikidata": "Q1"}))
    signalled = svc.score_attraction(
        poi("c", "Signalled", {"tourism": "attraction", "wikidata": "Q1", "wikipedia": "en:Signalled"})
    )
    museum_match = svc.score_attraction(museum(1), user_interests=["museums"])
    # Section 202C.1A: a bare wikidata id is reported as a signal but does NOT
    # lift the score; a wikipedia article / heritage designation does.
    assert "wikidata" in bare_id.significance_signals
    assert bare_id.total_score == plain.total_score
    assert signalled.total_score > plain.total_score
    assert "not a claim" in " ".join(signalled.positive_signals)
    # A significance signal is not a guarantee: a matching museum still competes on its own merits.
    assert museum_match.total_score >= plain.total_score


def test_small_objects_rank_below_a_major_attraction_but_remain_candidates() -> None:
    svc = CandidateQualityService()
    small = svc.score_attraction(statue(1))
    major = svc.score_attraction(museum(1))
    assert small.total_score < major.total_score
    assert small.quality_tier in (CandidateQualityTier.SECONDARY_CANDIDATE, CandidateQualityTier.LOW_PRIORITY)


def test_matched_interests_are_recorded_and_raise_the_score() -> None:
    svc = CandidateQualityService()
    park = poi("p", "Green", {"leisure": "park"})
    without = svc.score_attraction(park)
    with_interest = svc.score_attraction(park, user_interests=["outdoors"])
    assert with_interest.matched_interests == ["outdoors"]
    assert with_interest.total_score > without.total_score


# -- planner: diversity, interest coverage, balance ------------------------------------------


def test_many_small_objects_cannot_dominate_when_higher_quality_alternatives_exist() -> None:
    pois = [statue(i) for i in range(10)] + [museum(i, 0.05) for i in range(3)] + [
        poi(f"a{i}", f"Attraction {i}", {"tourism": "attraction"}, 0.1 + i * 0.001) for i in range(3)
    ]
    days = plan(state(pois, days=2, pace=TripPace.PACKED))  # capacity 8
    scheduled = [e for d in days for e in d]

    small = [e for e in scheduled if e.low_value_object]
    assert len(scheduled) == 8
    assert len(small) <= 2  # soft cap: 25% of capacity
    assert sum(1 for e in scheduled if not e.low_value_object) >= 6


def test_small_objects_do_not_pad_an_itinerary_when_nothing_else_exists() -> None:
    # Section 202C.1A (replaces the 202B.2 "fill with junk" expectation): with
    # ONLY plain low-value objects available, a general itinerary keeps at
    # most the diluted share and is honestly lighter instead of padded.
    days = plan(state([statue(i) for i in range(4)], days=2))
    assert 1 <= len(names(days)) <= 1


def test_commercial_galleries_do_not_dominate_a_non_art_request() -> None:
    others = [museum(i, 0.001 * i) for i in range(4)] + [poi("p", "Green Park", {"leisure": "park"}, 0.002),
                                                          poi("a", "Sight", {"tourism": "attraction"}, 0.003)]
    pois = [gallery(i) for i in range(8)] + others
    days = plan(state(pois, days=2, pace=TripPace.PACKED, interests=["food", "outdoors"]))
    scheduled = [e for d in days for e in d]
    assert sum(1 for e in scheduled if e.commercial_gallery) <= 2
    assert any("outdoors" in e.matched_interests for e in scheduled)


def test_an_art_focused_request_may_use_many_galleries() -> None:
    pois = [gallery(i) for i in range(6)] + [museum(1, 0.2)]
    days = plan(state(pois, days=2, pace=TripPace.PACKED, interests=["art"]))
    scheduled = [e for d in days for e in d]
    assert sum(1 for e in scheduled if e.commercial_gallery) >= 4


def test_requested_interest_with_viable_supply_is_scheduled_even_if_lower_ranked() -> None:
    market = poi("mk", "Market Hall", {"amenity": "marketplace"}, 0.03)
    pois = [museum(i, i * 0.001) for i in range(6)] + [market]
    days = plan(state(pois, days=2, pace=TripPace.RELAXED, interests=["food"]))  # capacity 4
    scheduled = [e for d in days for e in d]
    assert any("food" in e.matched_interests for e in scheduled)


def test_the_only_place_serving_a_requested_interest_is_never_swapped_out_as_an_outlier() -> None:
    market = poi("mk", "Far Market", {"amenity": "marketplace"}, 0.0, 2.0)  # far from the cluster
    pois = [museum(i, 0.0, 0.0) for i in range(6)] + [market]
    days = plan(state(pois, days=2, pace=TripPace.RELAXED, interests=["food"]))
    assert "Far Market" in names(days)


def test_interest_without_supply_schedules_nothing_fabricated() -> None:
    days = plan(state([museum(i, i * 0.01) for i in range(4)], days=2, interests=["nightlife"]))
    assert all("nightlife" not in e.matched_interests for d in days for e in d)


def test_sub_features_of_one_tagged_complex_collapse_to_one() -> None:
    exhibits = [poi(f"z{i}", f"Exhibit {i}", {"tourism": "attraction", "zoo": "enclosure"}, 0.0 + i * 1e-4, 0.0) for i in range(4)]
    others = [museum(i, 0.5) for i in range(3)]
    days = plan(state(exhibits + others, days=2, pace=TripPace.BALANCED))  # capacity 6, supply 7
    assert sum(1 for n in names(days) if n.startswith("Exhibit")) == 1


def test_parent_child_collapse_needs_structural_evidence_not_mere_proximity() -> None:
    close = [poi(f"c{i}", f"Sight {i}", {"tourism": "attraction"}, 0.0 + i * 1e-4, 0.0) for i in range(4)]
    days = plan(state(close, days=2))
    assert len(names(days)) == 4  # nearby but untagged: never merged


def test_balanced_allocation_never_leaves_only_the_final_day_short() -> None:
    pois = [museum(i, i * 0.01) for i in range(5)]
    days = plan(state(pois, days=3))
    assert [len(d) for d in days] == [2, 2, 1]


def test_empty_day_is_avoided_when_supply_covers_the_days() -> None:
    days = plan(state([museum(i, i * 0.01) for i in range(3)], days=3, pace=TripPace.RELAXED))
    assert all(len(d) == 1 for d in days)


def test_group_helper_is_deterministic_and_respects_the_cap() -> None:
    selected = [museum(i, i * 0.01) for i in range(7)]
    assert _group_candidates_into_balanced_days(selected, 3, 3) == _group_candidates_into_balanced_days(selected, 3, 3)
    assert [len(g) for g in _group_candidates_into_balanced_days(selected, 3, 3)] == [3, 2, 2]


def test_empty_day_in_an_ai_grouping_is_filled_only_from_unused_eligible_candidates() -> None:
    a, b, c = museum(1), museum(2, 0.1), museum(3, 0.2)
    from app.services.experience_planner_service import _candidate_profile

    profiles = {id(p): _candidate_profile(p, None, []) for p in (a, b, c)}
    filled = _fill_empty_days([[a], [], []], [a, b, c], profiles)
    assert [len(g) for g in filled] == [1, 1, 1]
    assert len({id(p) for g in filled for p in g}) == 3  # no duplicate
    # Nothing left to add -> the day stays empty (reported by the validator, never padded).
    assert [len(g) for g in _fill_empty_days([[a], []], [a], {id(a): profiles[id(a)]})] == [1, 0]


def test_geographic_outlier_is_replaced_when_a_clustered_alternative_exists() -> None:
    cluster = [museum(i, 0.0, 0.0) for i in range(5)]
    far = poi("far", "Far Sight", {"tourism": "museum", "wikidata": "Q7"}, 0.0, 2.0)  # ~220 km away
    days = plan(state(cluster + [far], days=2, pace=TripPace.RELAXED))  # capacity 4, 6 candidates
    assert "Far Sight" not in names(days)


def test_outlier_is_kept_when_no_clustered_alternative_exists() -> None:
    cluster = [museum(i, 0.0, 0.0) for i in range(3)]
    far = poi("far", "Far Sight", {"tourism": "museum"}, 0.0, 2.0)
    days = plan(state(cluster + [far], days=2, pace=TripPace.RELAXED))
    assert "Far Sight" in names(days)


def test_same_inputs_give_the_same_plan() -> None:
    pois = [museum(i, i * 0.01) for i in range(6)] + [statue(i) for i in range(4)]
    assert names(plan(state(pois, days=3))) == names(plan(state(pois, days=3)))


# -- validator findings ----------------------------------------------------------------------------


def findings(st: PlanningState, category: str) -> list[Any]:
    return [i for i in st.validation_report.warnings if i.category == category]


def validated(st: PlanningState) -> PlanningState:
    ExperiencePlannerService().run(st)
    PlanValidatorService().run(st)
    return st


def test_empty_day_with_no_supply_is_reported_as_a_data_limit() -> None:
    st = validated(state([museum(1)], days=2))
    issue = findings(st, "empty_day")[0]
    assert "No further quality-approved" in issue.message


def test_empty_day_with_unscheduled_supply_is_reported_as_a_selection_gap() -> None:
    st = state([museum(i, i * 0.01) for i in range(4)], days=3)
    ExperiencePlannerService().run(st)
    st.experience_plan.daily_plans[2].experiences = []  # simulate a plan that left a day empty
    PlanValidatorService().run(st)
    assert "selection gap" in findings(st, "empty_day")[0].message


def test_thin_day_is_flagged_only_when_viable_candidates_were_left_unscheduled() -> None:
    st = state([museum(i, i * 0.01) for i in range(4)], days=2, pace=TripPace.PACKED)
    ExperiencePlannerService().run(st)
    # Cap 4/day; 4 candidates balanced [2,2]; below the packed minimum of 3, but nothing is unscheduled.
    PlanValidatorService().run(st)
    assert findings(st, "thin_day") == []
    st.experience_plan.daily_plans[0].experiences = st.experience_plan.daily_plans[0].experiences[:1]
    PlanValidatorService().run(st)
    assert findings(st, "thin_day")


def test_interest_undercoverage_is_a_plan_finding_only_when_supply_existed() -> None:
    st = state([museum(1), poi("mk", "Market", {"amenity": "marketplace"}, 0.2)], days=1, pace=TripPace.RELAXED,
               interests=["food"])
    ExperiencePlannerService().run(st)
    covered = [e for d in st.experience_plan.daily_plans for e in d.experiences]
    assert any("food" in e.matched_interests for e in covered)  # planner covers it
    st.experience_plan.daily_plans[0].experiences = [e for e in covered if "food" not in e.matched_interests]
    PlanValidatorService().run(st)
    assert findings(st, "interest_undercoverage")


def test_interest_with_no_viable_supply_is_a_limitation_not_a_plan_failure() -> None:
    st = validated(state([museum(i) for i in range(2)], days=1, interests=["nightlife"]))
    assert findings(st, "interest_undercoverage") == []
    limited = [i for i in st.validation_report.warnings if i.category == "interest_supply_limited"]
    assert limited and limited[0].severity.value == "suggestion"


def test_category_concentration_is_a_needs_review_finding() -> None:
    st = state([museum(i, i * 0.001) for i in range(12)], days=2, pace=TripPace.PACKED)
    ExperiencePlannerService().run(st)
    scheduled = [e for d in st.experience_plan.daily_plans for e in d.experiences]
    assert len(scheduled) == 8
    for experience in scheduled[:4]:  # a plan (e.g. an older/AI grouping) dominated by small objects
        experience.low_value_object = True
    PlanValidatorService().run(st)
    issue = findings(st, "category_concentration")[0]
    assert issue.severity.value == "warning"
    assert "needs review" in issue.message
    assert "bad" not in issue.message.lower()


def test_concentration_is_not_reported_when_no_alternatives_existed() -> None:
    st = state([statue(i) for i in range(6)], days=2, pace=TripPace.PACKED)
    ExperiencePlannerService().run(st)
    PlanValidatorService().run(st)
    assert findings(st, "category_concentration") == []


def test_validator_still_reports_stable_identity_duplicates() -> None:
    st = state([museum(1), museum(2, 0.1)], days=2)
    ExperiencePlannerService().run(st)
    day1, day2 = st.experience_plan.daily_plans[:2]
    day2.experiences = [day1.experiences[0].model_copy(deep=True)]
    PlanValidatorService().run(st)
    assert [i for i in st.validation_report.critical_issues if i.category == "duplicate_experience"]


# -- adapter: tags, ranking, resolved destination --------------------------------------------------


def test_adapter_retains_only_whitelisted_structured_tags() -> None:
    kept = tx.filter_provider_tags(
        {"tourism": "museum", "name": "X", "phone": "123", "opening_hours": "24/7", "wikidata": "Q1", "heritage:operator": "y"}
    )
    assert kept == {"tourism": "museum", "wikidata": "Q1", "heritage": "y"}


def test_adapter_ranks_significant_and_ordinary_places_ahead_of_small_objects_before_capping() -> None:
    adapter = osm.OpenStreetMapPlacesAdapter.__new__(osm.OpenStreetMapPlacesAdapter)
    elements = [
        {"type": "node", "id": i, "lat": 0.0, "lon": 0.0, "tags": {"name": f"Statue {i}", "historic": "memorial", "memorial": "statue"}}
        for i in range(30)
    ] + [
        {"type": "node", "id": 900, "lat": 0.0, "lon": 0.0, "tags": {"name": "Landmark", "tourism": "attraction", "wikidata": "Q1"}},
        {"type": "node", "id": 901, "lat": 0.0, "lon": 0.0, "tags": {"name": "Museum", "tourism": "museum"}},
    ]

    ranked = adapter._normalize(elements, limit=5, rank=True)

    assert [p.name for p in ranked[:2]] == ["Landmark", "Museum"]
    assert ranked[0].provider_tags == {"tourism": "attraction", "wikidata": "Q1"}


def test_attraction_query_emits_significance_elements_first_in_one_request() -> None:
    posted: dict[str, str] = {}

    class _Client:
        def post(self, url: str, data: dict[str, str]) -> Any:
            posted.update(data)

            class _R:
                def raise_for_status(self) -> None: ...
                def json(self) -> dict[str, Any]:
                    return {"elements": []}

            return _R()

    adapter = osm.OpenStreetMapPlacesAdapter.__new__(osm.OpenStreetMapPlacesAdapter)
    adapter._overpass_url = "http://x"
    adapter._query_overpass(_Client(), osm.GeoPoint(lat=0, lng=0), osm._ATTRACTION_TAG_FILTERS, 6000, significance_first=True)

    query = posted["data"]
    families = len(osm._ATTRACTION_TAG_FILTERS)
    # One request; per tag family: a significance statement, then an ordinary one.
    assert query.count("out center") == 2 * families
    statements = query.split("out center")
    assert '["wikidata"]' in statements[0]  # a family's significance statement comes first
    assert '["wikidata"]' not in statements[1]
    assert f"out center {osm._SIGNIFICANT_FETCH_LIMIT}" in query and f"out center {osm._GENERAL_FETCH_LIMIT}" in query
    # Museums, sights and historic elements each get their own quota.
    assert '"tourism"="museum"' in query and '"historic"' in query and '"leisure"' in query


def test_pool_ranking_round_robins_categories_so_one_category_cannot_crowd_out_supply() -> None:
    museums = [
        osm.NormalizedPlace(place_id=f"m{i}", name=f"Museum {i}", category="museum",
                            source="openstreetmap_places", data_status="live",
                            provider_tags={"tourism": "museum", "wikidata": f"Q{i}", "wikipedia": "en:x"})
        for i in range(30)
    ]
    parks = [
        osm.NormalizedPlace(place_id=f"p{i}", name=f"Park {i}", category="park",
                            source="openstreetmap_places", data_status="live", provider_tags={"leisure": "park"})
        for i in range(2)
    ]
    statues = [
        osm.NormalizedPlace(place_id=f"s{i}", name=f"Statue {i}", category="memorial",
                            source="openstreetmap_places", data_status="live",
                            provider_tags={"historic": "memorial", "memorial": "statue"})
        for i in range(5)
    ]
    hospital = osm.NormalizedPlace(place_id="h", name="Clinic", category="hospital",
                                   source="openstreetmap_places", data_status="live", provider_tags={"amenity": "hospital"})

    ranked = osm._rank_attractions(museums + statues + parks + [hospital], limit=8)

    names_ = [p.name for p in ranked]
    assert {"Park 0", "Park 1"} <= set(names_)  # the interest supply survives the cap
    assert names_.index("Park 0") < 4  # round-robin, not appended after 30 museums
    assert "Clinic" not in names_  # unsuitable places only fill leftover space, last
    assert not any(n.startswith("Statue") for n in names_[:6])  # small objects only after every category


# -- Section 202B.3 (Task 38): truthful nightlife labelling ---------------------------------


@pytest.mark.parametrize(
    "tags",
    [{"amenity": "theatre"}, {"amenity": "cinema"}, {"amenity": "arts_centre"}, {"tourism": "zoo"},
     {"tourism": "museum"}, {"tourism": "attraction"}],
)
def test_cultural_or_entertainment_venues_are_not_labelled_nightlife(tags: dict[str, str]) -> None:
    classification = tx.classify_place(tags)
    assert tx.matched_interests(classification, ["nightlife"]) == []


@pytest.mark.parametrize("tags", [{"amenity": "nightclub"}, {"amenity": "bar"}, {"amenity": "pub"}, {"amenity": "casino"}])
def test_provider_tagged_nightlife_venues_match_nightlife(tags: dict[str, str]) -> None:
    assert tx.matched_interests(tx.classify_place(tags), ["nightlife"]) == ["nightlife"]


def test_theatre_supply_alone_is_reported_as_supply_limited_not_covered() -> None:
    theatres = [poi(f"t{i}", f"Theatre {i}", {"amenity": "theatre"}, i * 0.001) for i in range(3)]
    st = state(theatres + [museum(i, 0.1 + i * 0.001) for i in range(3)], days=2, interests=["nightlife"])
    ExperiencePlannerService().run(st)
    PlanValidatorService().run(st)
    scheduled = [e for d in st.experience_plan.daily_plans for e in d.experiences]
    assert all("nightlife" not in e.matched_interests for e in scheduled)
    assert findings(st, "interest_supply_limited")
    assert findings(st, "interest_undercoverage") == []
