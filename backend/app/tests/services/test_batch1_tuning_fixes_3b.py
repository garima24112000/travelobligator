from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any

import groq
import httpx
import pytest
from pydantic import ValidationError

from app.core import performance
from app.core.config import get_settings
from app.models import (
    ai_feedback_interpretation,
    ai_itinerary_reasoning,
    ai_itinerary_repair,
    ai_reasoning,
    candidate_grounding,
    itinerary_narrative,
)
from app.models import ai_candidate_proposal as proposal_models
from app.models.ai_candidate_promotion import AICandidatePromotionReport, PromotedAICandidate
from app.models.ai_candidate_proposal import AICandidateType
from app.models.ai_provider_discovery import AIProviderDiscoveryAttemptStatus
from app.models.candidate_grounding import (
    CandidateGroundingRejectReason,
    CandidateGroundingRequest,
    ProviderCandidateForGrounding,
)
from app.models.candidate_quality import CandidateQualityScore, CandidateQualityTier
from app.models.common import DataStatus, GeoPoint
from app.models.forbidden_text import compile_forbidden_patterns, find_forbidden_pattern
from app.models.planning_state import (
    DestinationContext,
    PlanningState,
    TravelGroupType,
    TripPace,
    TripRequest,
    UserLock,
)
from app.providers import ai_failure, ai_stage_budget
from app.providers.ai_failure import retry_after_seconds, transport_failure_subtype
from app.providers.ai_stage_budget import MAX_RETRY_AFTER_SECONDS_WITHOUT_BUDGET, MIN_ATTEMPT_SECONDS, StageRun

TRANSPORT_RETRY_BACKOFF_SECONDS = 1.0  # the production value, pinned by `_production_backoff` below
from app.services import ai_directed_provider_discovery_service as discovery_module
from app.services import experience_planner_service as planner_module
from app.services import place_taxonomy as taxonomy
from app.services.ai_candidate_discovery_service import apply_discovery_to_state
from app.services.candidate_grounding_service import CandidateGroundingService
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService, recompute_food_suggestions
from app.services.grounded_anchors import grounded_anchor_place_ids
from app.services.route_burden import day_route_burdens
from app.tests.services.test_final_quality_correction_203c2b import (
    _GOOD_NEARBY,
    _NEAR_A,
    _NEAR_B,
    _FAR,
    _Gateway,
    _anchor,
    _day_names,
    _discover,
    _place,
    _poi,
    _point,
    _repair,
    _restaurant,
    _state,
    _with_routes,
)

# Section 3B: the generic fixes exposed by tuning batch 1. No city, landmark
# or coordinate of a real destination appears here: every fixture is
# synthetic, and one real place NAME is used only as a string that must not
# be mistaken for a forbidden claim.

_START = date(2026, 8, 10)


# =====================================================================================
# A. Forbidden patterns match whole words/phrases, never substrings
# =====================================================================================

_PATTERN_MODULES = (
    candidate_grounding, proposal_models, ai_itinerary_reasoning, ai_itinerary_repair, itinerary_narrative,
    ai_feedback_interpretation, ai_reasoning,
)


def _candidate(name: str, **overrides: Any) -> ProviderCandidateForGrounding:
    fields: dict[str, Any] = {
        "provider_name": "geoapify_places", "provider_place_id": "geoapify/x", "name": name,
        "category": "attraction", "coordinates": GeoPoint(lat=50.0, lng=10.0), "data_status": DataStatus.LIVE,
        "confidence": 0.6, **overrides,
    }
    return ProviderCandidateForGrounding(**fields)


def test_preservation_hall_is_a_valid_provider_candidate() -> None:
    assert _candidate("Preservation Hall").name == "Preservation Hall"
    assert candidate_grounding._find_forbidden_pattern("Preservation Hall") is None


@pytest.mark.parametrize("name", ["Reservation Center", "reservation", "Central Reservations", "Book Now Tours"])
def test_an_actual_forbidden_token_or_phrase_is_still_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        _candidate(name)


@pytest.mark.parametrize("module", _PATTERN_MODULES, ids=lambda module: module.__name__.rsplit(".", 1)[-1])
def test_every_forbidden_pattern_list_matches_tokens_not_substrings(module: Any) -> None:
    find = module._find_forbidden_pattern
    # every pattern still matches itself, alone and inside a sentence
    for pattern in module._FORBIDDEN_TEXT_PATTERNS:
        assert find(pattern) is not None
        assert find(f"it says {pattern} here") is not None
    # an ordinary word that merely CONTAINS a pattern is not a claim
    for word, pattern in (
        ("Preservation Hall", "reservation"), ("celebrating local craft", "rating"), ("a priceless view", "price"),
        ("Cheapside Passage", "cheap"), ("unverified by any provider", "verified"), ("suboptimal", "optimal"),
        ("superstars of the stage", "stars"),
    ):
        if pattern in module._FORBIDDEN_TEXT_PATTERNS:
            assert find(word) is None, (module.__name__, word)


def test_the_shared_matcher_handles_plurals_case_and_symbol_patterns() -> None:
    compiled = compile_forbidden_patterns(("reservation", "highly rated", "$", "09:", "rating:"))
    assert find_forbidden_pattern("Two RESERVATIONS needed", compiled) == "reservation"
    assert find_forbidden_pattern("it is Highly Rated.", compiled) == "highly rated"
    assert find_forbidden_pattern("costs $20", compiled) == "$"  # a symbol needs no word boundary
    assert find_forbidden_pattern("opens 09:30", compiled) == "09:"
    assert find_forbidden_pattern("opens 109:30", compiled) is None
    assert find_forbidden_pattern("rating: 4", compiled) == "rating:"
    assert find_forbidden_pattern("preservation, celebrating: yes", compiled) is None


# =====================================================================================
# B. One rejected candidate never aborts the discovery pass
# =====================================================================================


def test_preservation_hall_grounds_and_does_not_abort_discovery() -> None:
    hall = _place("ph", "Preservation Hall", "attraction", {"tourism": "attraction"})
    museum = _place("m", "City History Museum", "museum", {"tourism": "museum"}, _point(50.01, 10.01))
    result = _discover(
        [
            _anchor("proposal_001", "Preservation Hall", AICandidateType.LANDMARK),
            _anchor("proposal_002", "City History Museum", AICandidateType.MUSEUM),
        ],
        {"Preservation Hall": hall, "City History Museum": museum},
    )
    assert [attempt.status for attempt in result.attempts] == [AIProviderDiscoveryAttemptStatus.MATCHED] * 2
    assert result.matches_by_proposal_id()["proposal_001"].name == "Preservation Hall"


def test_one_invalid_provider_record_is_rejected_alone_and_the_rest_proceed() -> None:
    invalid = _place("bad", "Reservation Center", "attraction", {"tourism": "attraction"})
    museum = _place("m", "City History Museum", "museum", {"tourism": "museum"}, _point(50.01, 10.01))
    gallery = _place("g", "Old Town Gallery", "museum", {"tourism": "museum"}, _point(50.02, 10.02))
    result = _discover(
        [
            _anchor("proposal_001", "City History Museum", AICandidateType.MUSEUM),
            _anchor("proposal_002", "Booking Office", AICandidateType.LANDMARK),
            _anchor("proposal_003", "Old Town Gallery", AICandidateType.MUSEUM),
        ],
        {"City History Museum": museum, "Booking Office": invalid, "Old Town Gallery": gallery},
    )
    assert [attempt.status.value for attempt in result.attempts] == [
        "matched", "candidate_validation_rejected", "matched",
    ]
    rejected = result.attempts[1]
    assert rejected.match is None
    # a fixed, safe reason: never the validation text or the provider's name for the place
    assert rejected.message == "Provider found a place, but its record failed candidate validation."
    assert set(result.matches_by_proposal_id()) == {"proposal_001", "proposal_003"}


def test_an_unexpected_programming_error_in_discovery_still_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("a bug, not a validation rejection")

    monkeypatch.setattr(discovery_module, "anchor_category_compatible", broken)
    museum = _place("m", "City History Museum", "museum", {"tourism": "museum"})
    with pytest.raises(RuntimeError):
        _discover([_anchor("proposal_001", "City History Museum", AICandidateType.MUSEUM)], {"City History Museum": museum})


def test_grounding_rejects_one_invalid_match_and_grounds_the_others() -> None:
    # A directed match that bypassed validation (as a stored / hand-built record could).
    invalid = ProviderCandidateForGrounding.model_construct(
        provider_name="geoapify_places", provider_place_id="geoapify/bad", name="Reservation Center",
        category="attraction", coordinates=GeoPoint(lat=50.0, lng=10.0), data_status=DataStatus.LIVE,
        confidence=0.6, provider_tags=None,
    )
    request = CandidateGroundingRequest.model_construct(
        trip_id="trip_x", destination_name="Fixtureville",
        proposals=[
            _anchor("proposal_001", "Booking Office", AICandidateType.LANDMARK),
            _anchor("proposal_002", "Preservation Hall", AICandidateType.LANDMARK),
        ],
        provider_candidates=[_candidate("Preservation Hall", provider_place_id="geoapify/ph")],
        provider_candidate_summary={}, unavailable_data=[],
        ai_directed_matches={"proposal_001": invalid},
    )
    result = CandidateGroundingService().ground(request)
    assert [grounded.matched_name for grounded in result.grounded_candidates] == ["Preservation Hall"]
    assert [(r.proposal_id, r.reject_reason) for r in result.rejected_proposals] == [
        ("proposal_001", CandidateGroundingRejectReason.CANDIDATE_VALIDATION_FAILED)
    ]
    assert "Reservation" not in result.model_dump_json()

    class _Broken(CandidateGroundingService):
        def _ground_one(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("a bug, not a validation rejection")

    with pytest.raises(RuntimeError):
        _Broken().ground(request)


def test_a_failed_discovery_attempt_is_recorded_as_rejected_never_as_not_run() -> None:
    class _RaisingDiscovery:
        def dry_run(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("unexpected failure with secret sk-should-not-leak")

    state = _state([_NEAR_A], [])
    assert state.ai_candidate_proposal_batch is None
    apply_discovery_to_state(state, _RaisingDiscovery())  # type: ignore[arg-type]

    batch = state.ai_candidate_proposal_batch
    assert batch is not None and batch.result is not None
    assert batch.result.status.value == "rejected" and batch.result.failure_kind.value == "internal_error"
    assert batch.result.proposals == [] and state.candidate_grounding_batch is None
    assert "sk-should-not-leak" not in batch.model_dump_json()

    # no destination context: the step was never attempted, so nothing is recorded
    untouched = PlanningState(trip_request=state.trip_request)
    apply_discovery_to_state(untouched, _RaisingDiscovery())  # type: ignore[arg-type]
    assert untouched.ai_candidate_proposal_batch is None


# =====================================================================================
# C. Route repair: an ordinary broad stop is replaceable, whatever its tier
# =====================================================================================


def _mark_primary_anchor(state: PlanningState) -> None:
    """Batch 1: nearly every broad candidate carries the top quality tier."""
    for day in state.experience_plan.daily_plans:
        for stop in day.experiences:
            stop.quality_tier = CandidateQualityTier.PRIMARY_ANCHOR.value


def test_one_excessive_walking_leg_with_replaceable_broad_candidates_is_repaired() -> None:
    gateway = _Gateway()
    state = _with_routes(_state([_NEAR_A, _NEAR_B, _FAR, _GOOD_NEARBY], [[_NEAR_A, _NEAR_B, _FAR]]), gateway)
    _mark_primary_anchor(state)
    before = day_route_burdens(state)[0]
    assert before.excessive_walking and before.drive_legs == 0

    attempt = _repair(state, gateway).attempts[0]

    # every stop was an ordinary broad candidate: none is protected by its tier
    assert attempt.stop_protections == ["replaceable", "replaceable", "replaceable"]
    assert attempt.accepted and attempt.reason == "accepted"
    assert (attempt.replaced_place, attempt.replacement_place) == ("Museum Faraway", "Museum Gamma")
    assert len(gateway.calls) == 1  # still ONE routing request for the changed day
    after = day_route_burdens(state)[0]
    assert not after.long_route and after.routed_legs == after.required_legs == 2
    assert sorted(_day_names(state)) == ["Museum Alpha", "Museum Beta", "Museum Gamma"]
    replacement = next(s for s in state.experience_plan.daily_plans[0].experiences if s.name == "Museum Gamma")
    assert replacement.provider_place_id == "geoapify/good" and replacement.provider_source == "geoapify_places"


def test_an_extreme_cross_city_transfer_caused_by_an_isolated_candidate_is_repaired() -> None:
    settings = get_settings()
    isolated = _poi("isolated", "Museum Isolated", _point(50.400, 10.000))  # tens of km from the others
    gateway = _Gateway()
    # day order: isolated -> alpha -> beta (one long transfer, then a short walk)
    state = _with_routes(_state([isolated, _NEAR_A, _NEAR_B, _GOOD_NEARBY], [[isolated, _NEAR_A, _NEAR_B]]), gateway)
    _mark_primary_anchor(state)
    # the long leg was adapted to a factual vehicle transfer, and it is unreasonably long
    transfer = state.route_feasibility_report.legs[0]
    transfer.mode = "drive"
    transfer.duration_seconds = settings.route_burden_max_drive_leg_seconds + 600.0
    transfer.distance_meters = 42_000.0
    before = day_route_burdens(state)[0]
    assert before.over_transfer_limit and not before.excessive_walking and before.long_route

    attempt = _repair(state, gateway).attempts[0]

    assert attempt.stop_protections == ["replaceable"] * 3
    assert attempt.accepted and attempt.replaced_place == "Museum Isolated"
    assert attempt.replacement_place == "Museum Gamma"
    after = day_route_burdens(state)[0]
    # the drive became short walks: more walking than before, but well inside the walking limits
    assert after.drive_legs == 0 and after.walking_duration_seconds > before.walking_duration_seconds
    assert not after.long_route and after.total_duration_seconds < before.total_duration_seconds
    assert attempt.after_distance_meters < attempt.before_distance_meters


def test_walking_that_was_already_excessive_may_not_get_longer() -> None:
    # A "replacement" that is nearer the others in a straight line but walks longer in fact.
    class _SlowerNearGamma(_Gateway):
        def get_route_sequence(self, points: Any, provider_context: Any = None) -> Any:
            results = super().get_route_sequence(points, provider_context)
            if any(abs(lat - 50.003) < 1e-9 for lat, _ in points):  # any day holding Museum Gamma
                for result in results:
                    result.duration_seconds *= 40
                    result.distance_meters *= 40
            return results

    gateway = _SlowerNearGamma()
    state = _with_routes(_state([_NEAR_A, _NEAR_B, _FAR, _GOOD_NEARBY], [[_NEAR_A, _NEAR_B, _FAR]]), gateway)
    attempt = _repair(state, gateway).attempts[0]
    assert attempt.reason == "no_material_improvement" and not attempt.accepted
    assert _day_names(state) == ["Museum Alpha", "Museum Beta", "Museum Faraway"]


def test_no_replaceable_stop_always_names_the_genuine_protection_of_each_stop() -> None:
    gateway = _Gateway()
    state = _with_routes(
        _state(
            [{**_NEAR_A, "must_visit_term": "Alpha Tower"}, _NEAR_B, _FAR, _GOOD_NEARBY],
            [[_NEAR_A, _NEAR_B, _FAR]],
            must_visit=["Alpha Tower"],
        ),
        gateway,
    )
    _mark_primary_anchor(state)
    stops = state.experience_plan.daily_plans[0].experiences
    state.user_locks = [UserLock(locked_item_type="experience", locked_item_id=stops[1].experience_id)]
    # the far stop is a grounded semantic anchor that kept its broad-pool identity
    state.ai_candidate_promotion_report = _promotion([_promoted("far", "Museum Faraway", _point(50.0, 10.1))])
    assert grounded_anchor_place_ids(state) == {"geoapify/far"}

    attempt = _repair(state, gateway).attempts[0]

    assert attempt.reason == "no_replaceable_stop" and gateway.calls == []
    assert attempt.stop_protections == ["must_visit", "user_lock", "grounded_anchor"]
    assert _day_names(state) == ["Museum Alpha", "Museum Beta", "Museum Faraway"]


def test_a_replacement_never_uncovers_a_requested_interest_but_real_nearby_food_still_counts() -> None:
    near_c = _poi("c", "Museum Delta", _point(50.050, 10.050))
    near_d = _poi("d", "Museum Epsilon", _point(50.052, 10.052))
    pois = [_NEAR_A, _NEAR_B, _FAR, _GOOD_NEARBY, near_c, near_d]
    days = [[_NEAR_A, _NEAR_B, _FAR], [near_c, near_d]]

    def build(restaurants: list[dict[str, Any]]) -> tuple[PlanningState, _Gateway]:
        gateway = _Gateway()
        state = _with_routes(_state(pois, days, restaurants, interests=["food"]), gateway)
        # the far stop is the only scheduled stop serving the requested interest
        state.experience_plan.daily_plans[0].experiences[2].matched_interests = ["food"]
        recompute_food_suggestions(state)
        return state, gateway

    # no food evidence anywhere else: the stop may not be traded for a place that does not serve food
    state, gateway = build([])
    attempt = _repair(state, gateway).attempts[0]
    assert attempt.reason == "no_suitable_candidate" and "Museum Faraway" in _day_names(state)

    # real nearby food on ANOTHER day already covers the interest (the validator's own rule)
    state, gateway = build([_restaurant("day2", _point(50.051, 10.051))])
    assert state.experience_plan.daily_plans[1].restaurant_suggestions
    attempt = _repair(state, gateway).attempts[0]
    assert attempt.accepted and attempt.replaced_place == "Museum Faraway"


# =====================================================================================
# D. Grounded anchors reach the schedule
# =====================================================================================


def _promoted(key: str, name: str, point: GeoPoint, **extra: Any) -> PromotedAICandidate:
    return PromotedAICandidate(
        candidate_id=f"promoted_{key}", name=name, category=extra.pop("category", "castle"),
        provider_place_id=f"geoapify/{key}", provider_source="geoapify_places", original_ai_candidate_id=key,
        quality_bucket=extra.pop("quality_bucket", "primary_anchor"), grounding_status="targeted_lookup",
        coordinates=point, confidence=0.5, data_status="live",
        provider_tags=extra.pop("provider_tags", {"historic": "castle"}), **extra,
    )


def _promotion(candidates: list[PromotedAICandidate]) -> AICandidatePromotionReport:
    from datetime import datetime, timezone

    return AICandidatePromotionReport(
        trip_id="trip_test", status="promoted" if candidates else "no_eligible_candidates",
        total_reviewed_candidates=len(candidates), promoted_count=len(candidates), skipped_count=0,
        promoted_candidates=candidates, skipped_candidate_ids=[], generated_at=datetime.now(timezone.utc),
    )


def _broad_museums(count: int = 12) -> list[dict[str, Any]]:
    """A long tail of equally scored broad candidates in one compact area."""
    return [
        _poi(f"m{index}", f"Museum {index:02d}", _point(50.000 + 0.002 * (index // 4), 10.000 + 0.002 * (index % 4)))
        for index in range(count)
    ]


def _plan_state(
    pois: list[dict[str, Any]],
    anchors: list[PromotedAICandidate] | None = None,
    *,
    days: int = 3,
    pace: TripPace = TripPace.BALANCED,
    interests: list[str] | None = None,
    must_visit: list[str] | None = None,
) -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Fixtureville", start_date=_START, end_date=_START + timedelta(days=days - 1),
            travelers_count=2, travel_group_type=TravelGroupType.COUPLE, pace=pace,
            interests=["history"] if interests is None else interests, must_visit=must_visit or [],
        )
    )
    state.destination_context = DestinationContext(destination_name="Fixtureville", candidate_pois=pois)
    state.candidate_quality_report = CandidateQualityService().build_report(state)
    if anchors is not None:
        state.ai_candidate_promotion_report = _promotion(anchors)
    return state


def _scheduled(state: PlanningState) -> list[list[str]]:
    return [[stop.name for stop in day.experiences] for day in state.experience_plan.daily_plans]


def _anchors(count: int) -> list[PromotedAICandidate]:
    return [
        _promoted(f"anchor{index}", f"Castle {index}", _point(50.001 + 0.002 * index, 10.001)) for index in range(count)
    ]


def test_several_grounded_anchors_seed_a_three_day_plan_but_not_every_anchor_is_scheduled() -> None:
    state = _plan_state(_broad_museums(), _anchors(5))
    ExperiencePlannerService().run(state)

    names = [name for day in _scheduled(state) for name in day]
    scheduled_anchors = [name for name in names if name.startswith("Castle")]
    assert len(names) == 9  # T is unchanged: 3 days x balanced
    # one seed per day -- several anchors, never all of them, never a quota by city
    assert planner_module.anchor_seed_count(3, 9) == 3
    assert scheduled_anchors == sorted(scheduled_anchors) and len(scheduled_anchors) == 3
    assert set(scheduled_anchors) == {"Castle 0", "Castle 1", "Castle 2"}  # the best-ranked ones, in rank order
    # every scheduled anchor carries its provider identity
    for day in state.experience_plan.daily_plans:
        for stop in day.experiences:
            assert stop.provider_place_id and stop.provider_source and stop.coordinates


def test_without_anchors_the_plan_is_exactly_what_it_was() -> None:
    baseline = _plan_state(_broad_museums())
    ExperiencePlannerService().run(baseline)
    with_empty_report = _plan_state(_broad_museums(), [])
    ExperiencePlannerService().run(with_empty_report)
    assert _scheduled(with_empty_report) == _scheduled(baseline)
    # the selection function itself: no anchors / a seed count of 0 change nothing
    pois = _broad_museums()
    profiles = {id(p): planner_module._candidate_profile(p, None, ["history"]) for p in pois}
    plain = planner_module._select_diverse_scheduling_set(pois, profiles, 9, ["history"], set())
    for kwargs in ({"anchor_ids": set(), "anchor_seed_count": 3}, {"anchor_ids": {id(pois[11])}, "anchor_seed_count": 0}):
        assert planner_module._select_diverse_scheduling_set(pois, profiles, 9, ["history"], set(), **kwargs) == plain


def test_identical_inputs_give_an_identical_plan() -> None:
    results = []
    for _ in range(3):
        state = _plan_state(_broad_museums(), _anchors(5), interests=["history", "architecture"])
        ExperiencePlannerService().run(state)
        results.append(_scheduled(state))
    assert results[0] == results[1] == results[2]


def test_an_anchor_that_kept_its_broad_pool_identity_is_seeded_too() -> None:
    pois = _broad_museums()
    # the lowest-ranked broad candidate: on ranking alone it would not be scheduled
    weak = {**_poi("weak", "Old Fort", _point(50.003, 10.003)), "category": "attraction", "confidence": 0.35,
            "provider_tags": {"historic": "fort"}}
    baseline = _plan_state([*copy.deepcopy(pois), copy.deepcopy(weak)])
    ExperiencePlannerService().run(baseline)
    assert "Old Fort" not in [name for day in _scheduled(baseline) for name in day]

    state = _plan_state([*pois, weak], [_promoted("weak", "Old Fort", _point(50.003, 10.003))])
    ExperiencePlannerService().run(state)
    assert "Old Fort" in [name for day in _scheduled(state) for name in day]


def test_must_visits_stay_ahead_of_anchor_seeds() -> None:
    pois = _broad_museums(4)
    state = _plan_state(pois, _anchors(3), days=1, pace=TripPace.RELAXED, must_visit=["Museum 03"])
    ExperiencePlannerService().run(state)
    names = _scheduled(state)[0]
    assert planner_module.anchor_seed_count(1, 2) == 1
    assert "Museum 03" in names and len(names) == 2
    assert sum(name.startswith("Castle") for name in names) == 1

    # two must-visits fill a two-slot day: no anchor displaces either
    state = _plan_state(pois, _anchors(3), days=1, pace=TripPace.RELAXED, must_visit=["Museum 03", "Museum 01"])
    ExperiencePlannerService().run(state)
    assert sorted(_scheduled(state)[0]) == ["Museum 01", "Museum 03"]


def test_an_anchor_is_not_seeded_when_it_is_far_away_incompatible_or_low_quality() -> None:
    far = _promoted("far", "Castle Faraway", _point(51.5, 11.5))  # far outside the pool's own spread
    park = _promoted("park", "Quiet Park", _point(50.002, 10.002), category="park", provider_tags={"leisure": "park"})
    weak = _promoted("weak", "Castle Minor", _point(50.002, 10.003), quality_bucket="secondary_candidate")
    state = _plan_state(_broad_museums(), [far, park, weak])
    ExperiencePlannerService().run(state)
    names = [name for day in _scheduled(state) for name in day]
    assert len(names) == 9 and not {"Castle Faraway", "Quiet Park", "Castle Minor"} & set(names)


def test_a_promoted_anchor_is_ranked_on_its_own_quality_score() -> None:
    anchor = _promoted("a1", "Castle One", _point(50.001, 10.001))
    state = _plan_state(_broad_museums(), [anchor])
    scored = CandidateQualityScore.model_validate(
        {**state.candidate_quality_report.attraction_scores[0].model_dump(), "candidate_id": "geoapify/a1",
         "candidate_name": "Castle One", "total_score": 0.93, "quality_tier": "primary_anchor"}
    )
    state.candidate_quality_report = state.candidate_quality_report.model_copy(update={"ai_directed_scores": [scored]})
    promoted_pois, _ = planner_module._build_promoted_candidate_pois(state, state.destination_context.candidate_pois)
    scores = planner_module._promoted_quality_scores(promoted_pois, state.candidate_quality_report)
    profile = planner_module._candidate_profile(promoted_pois[0], scores.get(id(promoted_pois[0])), ["history"])
    assert profile.score == 0.93 and profile.tier == "primary_anchor"
    # an anchor without a recorded score keeps the neutral profile it had
    assert planner_module._promoted_quality_scores(promoted_pois, None) == {}
    assert planner_module._candidate_profile(promoted_pois[0], None, ["history"]).score == 0.6


# =====================================================================================
# E. Requested-interest coverage
# =====================================================================================

_HALL_TAGS = {"building:architecture": "gothic"}


def _cover(
    day_groups: list[list[dict[str, Any]]],
    pool: list[dict[str, Any]],
    interests: list[str],
    *,
    must_visit: tuple[dict[str, Any], ...] = (),
    anchors: tuple[dict[str, Any], ...] = (),
) -> list[list[str]]:
    canonical = taxonomy.canonical_interests(interests)
    profiles = {id(poi): planner_module._candidate_profile(poi, None, canonical) for poi in pool}
    result = planner_module._cover_requested_interests(
        day_groups, pool, profiles, {id(poi) for poi in must_visit}, {id(poi) for poi in anchors}, canonical,
        markets_requested=False,
    )
    return [[poi["name"] for poi in group] for group in result]


def _hall(key: str, point: GeoPoint) -> dict[str, Any]:
    return {**_poi(key, f"Hall {key}", point), "category": "architecture", "provider_tags": _HALL_TAGS}


def test_an_uncovered_interest_is_covered_by_a_nearby_compatible_candidate() -> None:
    museums = _broad_museums(6)
    hall = _hall("near", _point(50.001, 10.001))
    days = [museums[:3], museums[3:]]
    before = [[poi["name"] for poi in group] for group in days]

    after = _cover(days, [*museums, hall], ["architecture", "history"])

    flat = [name for group in after for name in group]
    assert "Hall near" in flat and len(flat) == 6  # an exchange: nothing added, nothing dropped
    assert [len(group) for group in after] == [3, 3]
    assert sum(a != b for a, b in zip([n for g in before for n in g], flat)) == 1  # exactly one stop changed
    assert [[poi["name"] for poi in group] for group in days] == before  # the input is not mutated
    # deterministic
    assert _cover(days, [*museums, hall], ["architecture", "history"]) == after


def test_coverage_is_never_fabricated_or_forced() -> None:
    museums = _broad_museums(6)
    days = [museums[:3], museums[3:]]
    unchanged = [[poi["name"] for poi in group] for group in days]

    # no candidate serves the interest: the plan is left alone and the interest stays uncovered
    assert _cover(days, museums, ["architecture", "history"]) == unchanged
    # the only candidate is far from every day
    far_hall = _hall("far", _point(50.9, 10.9))
    assert _cover(days, [*museums, far_hall], ["architecture", "history"]) == unchanged
    # an already covered interest changes nothing
    near_hall = _hall("near", _point(50.001, 10.001))
    assert _cover(days, [*museums, near_hall], ["history"]) == unchanged
    # food is covered by real nearby food, never by scheduling a market
    market = {**_poi("mk", "Central Market", _point(50.001, 10.001)), "category": "marketplace",
              "provider_tags": {"amenity": "marketplace"}}
    assert _cover(days, [*museums, market], ["food"]) == unchanged
    # must-visits and grounded anchors are never the stop that gives way
    assert _cover(days, [*museums, near_hall], ["architecture", "history"], must_visit=tuple(museums)) == unchanged
    assert _cover(days, [*museums, near_hall], ["architecture", "history"], anchors=tuple(museums)) == unchanged
    # the only stop serving another requested interest is kept
    lone_history = [[museums[0]]]
    assert _cover(lone_history, [museums[0], near_hall], ["architecture", "history"]) == [["Museum 00"]]


def test_the_planner_covers_each_requested_interest_when_a_candidate_exists() -> None:
    pois = [*_broad_museums(), _hall("near", _point(50.003, 10.002))]
    state = _plan_state(pois, interests=["architecture", "history"])
    ExperiencePlannerService().run(state)
    scheduled = [stop for day in state.experience_plan.daily_plans for stop in day.experiences]
    assert any("architecture" in stop.matched_interests for stop in scheduled)
    assert any("history" in stop.matched_interests for stop in scheduled)


# =====================================================================================
# F. Groq transport failure subtype and Retry-After
# =====================================================================================


class _Response:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class _HTTPError(Exception):
    def __init__(self, status_code: int, headers: dict[str, str] | None = None, code: str | None = None) -> None:
        super().__init__("RAW PROVIDER BODY org_SECRET must never surface")
        self.status_code = status_code
        self.code = code
        self.response = _Response(headers or {})


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _groq_error(cls: Any, status: int, headers: dict[str, str] | None = None) -> Exception:
    request = httpx.Request("POST", "https://provider.invalid/v1/chat")
    return cls("failed", response=httpx.Response(status, headers=headers or {}, request=request), body=None)


@pytest.mark.parametrize(
    ("exc", "subtype"),
    [
        (_HTTPError(429), "rate_limit"),
        (_HTTPError(408), "timeout"), (_HTTPError(504), "timeout"), (TimeoutError("t"), "timeout"),
        (ConnectionError("c"), "network"),
        (_HTTPError(500), "server_error"), (_HTTPError(503), "server_error"),
        (_HTTPError(401), "other_transport"), (_HTTPError(400), "other_transport"),
        # a schema-invalid model answer and a local validation rejection are not transport failures
        (_HTTPError(400, code="json_validate_failed"), None),
        (ValueError("deterministic validation rejection"), None),
    ],
)
def test_transport_failure_subtype(exc: Exception, subtype: str | None) -> None:
    assert transport_failure_subtype(exc) == subtype
    assert subtype is None or subtype in ai_failure.TRANSPORT_FAILURE_SUBTYPES


def test_the_installed_groq_exceptions_are_classified_and_expose_retry_after() -> None:
    request = httpx.Request("POST", "https://provider.invalid/v1/chat")
    rate_limited = _groq_error(groq.RateLimitError, 429, {"retry-after": "7"})
    assert transport_failure_subtype(rate_limited) == "rate_limit" and retry_after_seconds(rate_limited) == 7.0
    assert transport_failure_subtype(_groq_error(groq.InternalServerError, 503)) == "server_error"
    assert transport_failure_subtype(groq.APITimeoutError(request=request)) == "timeout"
    assert transport_failure_subtype(groq.APIConnectionError(request=request)) == "network"
    assert retry_after_seconds(groq.APIConnectionError(request=request)) is None


def test_retry_after_is_read_from_headers_only() -> None:
    assert retry_after_seconds(_HTTPError(429, {"retry-after": "2.5"})) == 2.5
    assert retry_after_seconds(_HTTPError(429, {"retry-after-ms": "1500", "retry-after": "9"})) == 1.5
    assert retry_after_seconds(_HTTPError(429, {"retry-after": "-3"})) == 0.0
    assert retry_after_seconds(_HTTPError(429)) is None
    assert retry_after_seconds(_HTTPError(429, {"retry-after": "soon"})) is None
    assert retry_after_seconds(ValueError("no response at all")) is None
    http_date = retry_after_seconds(_HTTPError(429, {"retry-after": "Wed, 21 Oct 2099 07:28:00 GMT"}))
    assert http_date is not None and http_date > 0


@pytest.fixture(autouse=True)
def _production_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real transport backoff (the suite's default fixture shortens it);
    it is taken from a fake clock here, never actually slept."""
    monkeypatch.setattr(ai_stage_budget, "TRANSPORT_RETRY_BACKOFF_SECONDS", 1.0)


def _run_stage(budget: float = 25.0) -> tuple[StageRun, _Clock]:
    clock = _Clock()
    run = StageRun(
        "groq_anchor", total_budget_seconds=budget, request_timeout_seconds=20, clock=clock, sleep=clock.sleep
    )
    run.next_attempt_timeout()
    return run, clock


def test_retry_after_is_obeyed_when_it_fits_the_stage_budget() -> None:
    run, clock = _run_stage()
    assert run.allow_transport_retry(_HTTPError(429, {"retry-after": "5"})) is True
    assert clock.slept == [5.0] and run.retry_after == "obeyed" and run.transport_failure == "rate_limit"
    assert run.transport_retries == 1 and run.deadline_exceeded is False
    # the retry is the stage's second and last request
    assert run.next_attempt_timeout() == 20 and run.attempts == 2
    assert run.allow_transport_retry(_HTTPError(429, {"retry-after": "1"})) is False and run.attempts == 2


def test_a_retry_after_shorter_than_the_backoff_is_clamped_up_to_the_backoff() -> None:
    run, clock = _run_stage()
    assert run.allow_transport_retry(_HTTPError(429, {"retry-after": "0.2"})) is True
    assert clock.slept == [TRANSPORT_RETRY_BACKOFF_SECONDS] and run.retry_after == "clamped"


@pytest.mark.parametrize(
    ("elapsed", "retry_after"),
    [
        (0.0, "60"),  # longer than the whole budget
        (15.0, "8"),  # 10 s left: waiting 8 s would leave less than the minimum attempt window
        (0.0, "23"),  # fits the budget alone, but not together with a meaningful attempt
    ],
)
def test_a_retry_after_that_does_not_fit_is_skipped_without_sleeping(elapsed: float, retry_after: str) -> None:
    run, clock = _run_stage()
    clock.now += elapsed
    assert run.allow_transport_retry(_HTTPError(429, {"retry-after": retry_after})) is False
    # fail / fall back immediately: no sleep, no second request, and it is a rate limit -- not a deadline
    assert clock.slept == [] and run.retry_after == "skipped" and run.transport_retries == 0
    assert run.deadline_exceeded is False and run.attempts == 1 and run.transport_failure == "rate_limit"


def test_a_stage_never_sleeps_beyond_its_budget() -> None:
    for retry_after in ("0", "1", "3", "10", "21", "21.9", "22.1", "30", "600"):
        run, clock = _run_stage(budget=25.0)
        allowed = run.allow_transport_retry(_HTTPError(429, {"retry-after": retry_after}))
        slept = sum(clock.slept)
        assert slept <= 25.0 - MIN_ATTEMPT_SECONDS
        assert allowed is (slept > 0)
    # a stage with its deadline disabled still never waits longer than the fixed ceiling
    run, clock = _run_stage(budget=0)
    assert run.allow_transport_retry(_HTTPError(429, {"retry-after": str(MAX_RETRY_AFTER_SECONDS_WITHOUT_BUDGET + 1)})) is False
    assert clock.slept == [] and run.retry_after == "skipped"
    run, clock = _run_stage(budget=0)
    assert run.allow_transport_retry(_HTTPError(429, {"retry-after": "15"})) is True and clock.slept == [15.0]


def test_other_transient_failures_keep_the_short_backoff_and_output_rejections_are_never_retried() -> None:
    # a rate limit without a Retry-After, and a 5xx that carries one: the ordinary bounded backoff
    for exc, subtype in (
        (_HTTPError(429), "rate_limit"), (_HTTPError(503, {"retry-after": "9"}), "server_error"),
        (TimeoutError("t"), "timeout"), (ConnectionError("c"), "network"),
    ):
        run, clock = _run_stage()
        assert run.allow_transport_retry(exc) is True
        assert clock.slept == [TRANSPORT_RETRY_BACKOFF_SECONDS] and run.retry_after is None
        assert run.transport_failure == subtype
    for exc in (_HTTPError(400, code="json_validate_failed"), ValueError("validation rejection")):
        run, clock = _run_stage()
        assert run.allow_transport_retry(exc) is False
        assert clock.slept == [] and run.transport_failure is None and run.transport_retries == 0
    # rejected credentials are reported, never retried
    run, clock = _run_stage()
    assert run.allow_transport_retry(_HTTPError(401)) is False
    assert run.transport_failure == "other_transport" and clock.slept == []


def test_the_subtype_and_retry_after_outcome_reach_the_performance_report_as_fixed_labels() -> None:
    recorder = performance.started_recorder()
    with performance.activate(recorder):
        run, _ = _run_stage()
        run.allow_transport_retry(_HTTPError(429, {"retry-after": "60"}))
        run.close(completed=False)
        performance.note_llm_stage(
            "groq_narrator", attempts=1, structural_retries=0, transport_retries=0, deadline_exceeded=False,
            result="failed", transport_failure="RAW PROVIDER BODY", retry_after="org_SECRET",
        )
    stages = recorder.snapshot()["llm_stages"]
    assert stages["groq_anchor"] == {
        "attempts": 1, "structural_retries": 0, "transport_retries": 0, "deadline_exceeded": False,
        "result": "failed", "transport_failure": "rate_limit", "retry_after": "skipped",
    }
    # anything that is not one of the fixed labels is dropped
    assert "transport_failure" not in stages["groq_narrator"] and "retry_after" not in stages["groq_narrator"]
    assert "SECRET" not in str(recorder.snapshot())
