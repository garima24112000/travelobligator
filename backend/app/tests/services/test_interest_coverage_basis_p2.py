from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models.planning_state import InterestCoverageEntry, PlanningState
from app.repositories.planning_state_repository import PlanningStateRepository
from app.services import interest_coverage as coverage_module
from app.services import plan_quality_findings as findings_module
from app.services.interest_coverage import (
    BASIS_MUSEUM_SUBJECT_UNRECORDED,
    BASIS_NEARBY_SUGGESTIONS_ONLY,
    BASIS_SCHEDULED_PLACE,
    coverage_basis,
)
from app.services.plan_validator_service import PlanValidatorService
from app.storage.local_json_store import LocalJsonStore
from app.tests.services.test_candidate_usefulness_q2 import _at, _castle, _museum, _park, _planned, _state
from app.tests.services.test_final_quality_correction_203c2b import _poi, _restaurant

# Phase 2 (attraction quality and personalization): what a COVERED interest
# rests on, restated for the traveller. Two kinds of "covered" are weaker
# than the word: food met only by nearby restaurant suggestions, and history
# or art met only by museums whose provider data records no subject (the
# live places response carries no museum subtype, and the taxonomy lets any
# museum serve both). Presentation only: coverage, findings, readiness and
# the plan are unchanged. Synthetic fixtures; no city or landmark name.

_SERVICES = Path(__file__).resolve().parents[2] / "services"


def _validated(state: PlanningState) -> PlanningState:
    _planned(state)
    PlanValidatorService().run(state)
    return state


def _entry(state: PlanningState, interest: str) -> InterestCoverageEntry:
    (found,) = [entry for entry in state.validation_report.interest_coverage if entry.interest == interest]
    return found


def _gallery(key: str, index: int) -> dict[str, Any]:
    return _poi(key, f"Gallery {key}", _at(index), category="gallery", provider_tags={"tourism": "gallery"})


def _market(key: str, index: int, **tags: str) -> dict[str, Any]:
    return _poi(key, f"Market {key}", _at(index), category="marketplace", provider_tags={"amenity": "marketplace", **tags})


_MUSEUMS = [_museum(f"m{i}", i) for i in range(9)]


# -- history and art: museums whose subject is not recorded ------------------------------


@pytest.mark.parametrize("interest", ["history", "art"])
def test_coverage_by_museums_without_a_recorded_subject_says_so(interest: str) -> None:
    state = _validated(_state(_MUSEUMS, interests=[interest]))
    entry = _entry(state, interest)
    assert (entry.status, entry.basis) == ("covered", BASIS_MUSEUM_SUBJECT_UNRECORDED)


def test_a_museum_with_its_own_historic_evidence_is_a_real_history_match() -> None:
    pois = [_museum("castle_museum", 0, historic="castle"), *_MUSEUMS[1:]]
    state = _validated(_state(pois, interests=["history"]))
    scheduled = [stop.provider_place_id for day in state.experience_plan.daily_plans for stop in day.experiences]
    assert "geoapify/castle_museum" in scheduled
    assert _entry(state, "history").basis == BASIS_SCHEDULED_PLACE


def test_a_historic_site_covers_history_on_its_own_evidence() -> None:
    state = _validated(_state([_castle("c0", 0), *_MUSEUMS[1:]], interests=["history"]))
    assert _entry(state, "history").basis == BASIS_SCHEDULED_PLACE


def test_a_gallery_covers_art_on_its_own_evidence() -> None:
    state = _validated(_state([_gallery("g0", 0), *_MUSEUMS[1:]], interests=["art"]))
    assert _entry(state, "art").basis == BASIS_SCHEDULED_PLACE


def test_a_request_for_museums_is_met_by_any_museum() -> None:
    """The museum interest is genuinely served by the museum category: no
    subject is needed, and nothing is reported as uncertain."""
    state = _validated(_state(_MUSEUMS, interests=["museums"]))
    assert _entry(state, "museum").basis == BASIS_SCHEDULED_PLACE


def test_museums_still_count_for_planning_and_coverage() -> None:
    """Nothing is taken away: the same museums are scheduled and the
    interest is still covered; only the basis is stated."""
    state = _validated(_state(_MUSEUMS, interests=["history"]))
    assert all(
        "history" in stop.matched_interests for day in state.experience_plan.daily_plans for stop in day.experiences
    )
    assert _entry(state, "history").status == "covered"
    assert "INTEREST_UNDERCOVERAGE" not in state.validation_report.review_codes


def test_a_stop_without_a_recorded_classification_is_judged_by_its_category() -> None:
    state = _validated(_state(_MUSEUMS, interests=["history"]))
    # the pool record is gone (an older state): the stored category decides
    state.destination_context.candidate_pois = []
    assert coverage_basis(state)["history"] == BASIS_MUSEUM_SUBJECT_UNRECORDED


# -- food: suggestions are not a scheduled food experience ----------------------------------


def _food_state(*pois: dict[str, Any]) -> PlanningState:
    restaurants = [_restaurant(f"r{i}", _at(i)) for i in range(6)]
    parks = [_park(f"p{i}", i) for i in range(6)]
    return _state([*pois, *parks], interests=["food"], restaurants=restaurants)


def test_food_met_only_by_nearby_suggestions_is_not_called_a_food_experience() -> None:
    state = _validated(_food_state())
    assert coverage_module.scheduled_food_experiences(state) == []
    assert coverage_module.final_food_evidence(state)  # real, nearby suggestions exist
    entry = _entry(state, "food")
    assert (entry.status, entry.basis) == ("covered", BASIS_NEARBY_SUGGESTIONS_ONLY)


def test_a_market_with_a_recorded_food_trade_is_a_scheduled_food_experience() -> None:
    state = _validated(_food_state(_market("food_market", 0, shop="greengrocer")))
    assert coverage_module.scheduled_food_experiences(state) == ["Market food_market"]
    assert _entry(state, "food").basis == BASIS_SCHEDULED_PLACE


def test_a_marketplace_without_food_evidence_is_still_not_food() -> None:
    state = _validated(_food_state(_market("flowers", 0)))
    assert coverage_module.scheduled_food_experiences(state) == []
    assert _entry(state, "food").basis == BASIS_NEARBY_SUGGESTIONS_ONLY


def test_only_food_can_rest_on_suggestions_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-food interest reported covered with no scheduled stop serving it
    (not reachable today) gets no basis rather than `nearby_suggestions_only`."""
    state = _validated(_food_state())
    monkeypatch.setattr(coverage_module, "interest_coverage", lambda _state: {"food": True, "history": True})
    assert coverage_basis(state) == {"food": BASIS_NEARBY_SUGGESTIONS_ONLY}


def test_food_with_nothing_at_all_is_uncovered_and_has_no_basis() -> None:
    state = _validated(_state([_park(f"p{i}", i) for i in range(6)], interests=["food"]))
    entry = _entry(state, "food")
    assert entry.status != "covered" and entry.basis is None


# -- presentation only -------------------------------------------------------------------


def test_the_basis_changes_no_finding_code_or_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    def run() -> PlanningState:
        return _validated(_state(_MUSEUMS, interests=["history", "food"], restaurants=[_restaurant("r0", _at(0))]))

    with_basis = run()
    monkeypatch.setattr(findings_module, "coverage_basis", lambda _state: {})
    without = run()
    for field in ("readiness_status", "review_codes", "blocking_codes"):
        assert getattr(with_basis.validation_report, field) == getattr(without.validation_report, field)
    assert [(i.category, i.message) for i in with_basis.validation_report.warnings] == [
        (i.category, i.message) for i in without.validation_report.warnings
    ]
    assert [e.status for e in with_basis.validation_report.interest_coverage] == [
        e.status for e in without.validation_report.interest_coverage
    ]
    assert {e.interest: e.basis for e in without.validation_report.interest_coverage} == {"history": None, "food": None}


def test_nothing_that_plans_reads_the_basis() -> None:
    for name in ("experience_planner_service", "day_composition", "candidate_usefulness", "candidate_quality_service",
                 "route_recomposition_service", "usefulness_contract", "ai_itinerary_reasoning_request_builder",
                 "review_classification"):
        source = (_SERVICES / f"{name}.py").read_text()
        assert "coverage_basis" not in source and "BASIS_" not in source, name


def test_the_basis_survives_save_and_reload(tmp_path: Path) -> None:
    state = _validated(_state(_MUSEUMS, interests=["history"]))
    path = tmp_path / "state.json"
    PlanningStateRepository(store=LocalJsonStore(path)).save(state)
    reloaded = PlanningStateRepository(store=LocalJsonStore(path)).get_by_trip_id(state.trip_id)
    assert reloaded is not None and reloaded is not state
    assert _entry(reloaded, "history").basis == BASIS_MUSEUM_SUBJECT_UNRECORDED


def test_an_entry_stored_before_the_field_loads_with_no_basis() -> None:
    loaded = InterestCoverageEntry.model_validate({"interest": "food", "requested_as": ["food"], "status": "covered"})
    assert loaded.basis is None


def test_identical_inputs_give_identical_bases() -> None:
    first = _validated(_state(_MUSEUMS, interests=["history", "art"]))
    second = _validated(_state(_MUSEUMS, interests=["history", "art"]))
    assert [e.model_dump() for e in first.validation_report.interest_coverage] == [
        e.model_dump() for e in second.validation_report.interest_coverage
    ]
