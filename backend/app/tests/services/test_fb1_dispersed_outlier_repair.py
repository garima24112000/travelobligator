"""Final Batch 1 (development-verification follow-up): a dispersed day whose
other stop is an OUTLYING must-visit with no unused place in its immediate area.

Before: day composition offered replacements only from places near the day's
other stops (`NEAR_DAY_STOPS_KM`), so a must-visit far out paired with an
optional stop in the centre stayed dispersed although an unused place within
the validator's own spread boundary existed (the F2 development run, offline).
Now a dispersed day may also be repaired by such a place. The judge, the guards
and the must-visit protection are unchanged, and a dispersion nothing can
repair is still reported. Synthetic fixtures only (distances in km north of an
origin)."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import get_settings
from app.services import geographic_dispersion as gd
from app.services.plan_validator_service import PlanValidatorService
from app.tests.services.test_day_composition_q3 import _ai_state, _core, _place, _plan


@pytest.fixture(autouse=True)
def _fresh_settings() -> Any:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _requested(poi: dict[str, Any], term: str) -> dict[str, Any]:
    return {**poi, "_term": term}


def _state(extra: list[dict[str, Any]], model_days: list[list[str]], **trip: Any) -> Any:
    requested = {poi.pop("_term"): poi for poi in extra if "_term" in poi}
    return _ai_state([*_core(8), *extra], model_days, must_visit=requested, **trip)


def _names(days: list[list[str]]) -> set[str]:
    return {name for day in days for name in day}


def test_an_optional_stop_dispersing_an_outlying_must_visit_day_is_replaced_within_the_boundary() -> None:
    far = _requested(_place("mv", 12.0, name="Far Chapel"), "Far Chapel")  # beyond the core's 3 km "near" area
    halfway = _place("half", 7.0)  # 5 km from the chapel: not "near" it, but keeps the day within 8 km
    state = _state([far, halfway], [["c0", "c1", "c2"], ["mv", "c3"]])

    days = _plan(state)

    assert days[1][0] == "Far Chapel" or "Far Chapel" in days[1]  # the must-visit stays
    assert "Museum half" in days[1] and "Museum c3" not in days[1]
    assert gd.dispersion_by_day(state) == {}
    PlanValidatorService().run(state)
    assert "GEOGRAPHIC_DISPERSION" not in state.validation_report.review_codes


def test_a_dispersion_nothing_can_repair_is_still_reported() -> None:
    far = _requested(_place("mv", 30.0, name="Far Chapel"), "Far Chapel")  # nothing unused within 8 km of it
    state = _state([far], [["c0", "c1", "c2"], ["mv", "c3"]])

    days = _plan(state)

    assert "Far Chapel" in _names(days)  # never removed
    assert gd.dispersion_by_day(state)  # still dispersed...
    PlanValidatorService().run(state)
    # ...and still disclosed (no routes in this fixture: the validator's unverified variant)
    assert "GEOGRAPHIC_SPREAD" in state.validation_report.review_codes


def test_no_filler_two_tiers_below_is_brought_in() -> None:
    far = _requested(_place("mv", 12.0, name="Far Chapel"), "Far Chapel")
    weak = _place("weak", 7.0)
    state = _state([far, weak], [["c0", "c1", "c2"], ["mv", "c3"]])
    from app.models.candidate_quality import CandidateQualityTier

    for score in state.candidate_quality_report.attraction_scores:  # c3 two tiers above the only option
        if score.candidate_id.endswith("/c3"):
            score.quality_tier = CandidateQualityTier.PRIMARY_ANCHOR
        elif score.candidate_id.endswith("/weak"):
            score.quality_tier = CandidateQualityTier.SECONDARY_CANDIDATE

    days = _plan(state)

    assert "Museum weak" not in _names(days)


def test_under_an_active_lock_the_model_plan_is_untouched() -> None:
    far = _requested(_place("mv", 12.0, name="Far Chapel"), "Far Chapel")
    halfway = _place("half", 7.0)
    state = _state([far, halfway], [["c0", "c1", "c2"], ["mv", "c3"]], lock=True)

    days = _plan(state)

    assert "Museum c3" in days[1] and "Museum half" not in _names(days)
