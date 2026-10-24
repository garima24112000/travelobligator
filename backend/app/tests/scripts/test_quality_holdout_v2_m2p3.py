"""M2 Phase 3: the second unseen acceptance set and the venue quality figures.

Nothing here runs a scenario or makes a provider call."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
_V2_PATH = _SCRIPTS_DIR / "benchmark" / "quality_v2.json"

# The frozen set (canonical JSON of its scenario list). A change here means the
# unseen set was edited after the freeze: do not "fix" this value without
# recording why in docs/34_m2_phase3_itinerary_quality.md.
_QUALITY_HOLDOUT_V2_SHA256 = "d301f605a08d9405afc1f2f2bdff5fb76150c0e760c3298c534fdb66fc17df58"


def _load(name: str) -> Any:
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(f"{name}_v2_under_test", _SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _city(destination: str) -> str:
    text = unicodedata.normalize("NFKD", destination.split(",")[0])
    return "".join(c for c in text if not unicodedata.combining(c)).strip().casefold()


def _scenarios() -> list[dict[str, Any]]:
    return json.loads(_V2_PATH.read_text(encoding="utf-8"))["quality_holdout_v2"]


def test_the_set_is_frozen() -> None:
    canonical = json.dumps(_scenarios(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() == _QUALITY_HOLDOUT_V2_SHA256


def test_every_city_is_new_and_none_was_a_development_destination() -> None:
    history = json.loads((_SCRIPTS_DIR / "benchmark" / "cities.json").read_text(encoding="utf-8"))
    quality = json.loads((_SCRIPTS_DIR / "benchmark" / "quality_v1.json").read_text(encoding="utf-8"))
    earlier = {_city(c) for c in (*history["tuning"], *history["holdout"])}
    earlier |= {_city(s["destination"]) for s in history["holdout_stress"]}
    earlier |= {_city(s["destination"]) for key in ("quality_tuning", "quality_holdout") for s in quality[key]}
    # development destinations of the M2 live runs and the planned Phase 3 run
    earlier |= {"gothenburg", "porto", "london", "jersey city", "orlando", "vienna", "budapest", "edinburgh", "montreal"}
    cities = [_city(s["destination"]) for s in _scenarios()]
    assert len(set(cities)) == len(cities) == 10
    assert not set(cities) & earlier


def test_no_name_of_the_set_is_in_production_code() -> None:
    names = {s["destination"].split(",")[0] for s in _scenarios()} | {m for s in _scenarios() for m in s["must_visit"]}
    sources = [p.read_text(encoding="utf-8") for p in (_BACKEND_DIR / "app").rglob("*.py") if "tests" not in p.parts]
    assert not [name for name in names if any(name in text for text in sources)]


def test_every_interest_term_is_tracked() -> None:
    from app.services.place_taxonomy import canonical_interests

    for scenario in _scenarios():
        for term in scenario["interests"]:
            assert canonical_interests([term]), (scenario["id"], term)


def test_the_runner_refuses_the_set_unless_explicit_and_whole() -> None:
    runner = _load("benchmark_quality")
    assert runner.SET_HOLDOUT_V2 in runner.FROZEN_SETS
    assert len(runner.scenarios_of(runner.load_data(), runner.SET_HOLDOUT_V2)) == 10
    assert runner.holdout_refusal(runner.SET_HOLDOUT_V2, allow_quality_holdout=False, scenario_ids=[], start=1,
                                  limit=None)
    assert runner.holdout_refusal(runner.SET_HOLDOUT_V2, allow_quality_holdout=True, scenario_ids=["Q2-H1"],
                                  start=1, limit=None)
    assert runner.holdout_refusal(runner.SET_HOLDOUT_V2, allow_quality_holdout=True, scenario_ids=[], start=1,
                                  limit=None) is None


def test_the_canary_and_the_verification_scripts_refuse_its_cities() -> None:
    canary = _load("canary_city")
    phase1 = _load("verify_m2_integrations")
    for scenario in _scenarios():
        assert canary._is_quality_holdout_city(scenario["destination"]), scenario["id"]
        assert phase1.normalize_city(scenario["destination"]) in phase1.frozen_destinations()
    assert not canary._is_quality_holdout_city("Vienna, Austria")


def test_venue_metrics_count_what_the_records_say() -> None:
    metrics = _load("quality_metrics")
    from app.models.planning_state import (
        DailyPlan,
        DestinationContext,
        ExperienceItem,
        ExperiencePlan,
        PlanningState,
        TravelGroupType,
        TripRequest,
    )

    day = date(2026, 8, 10)
    state = PlanningState(trip_request=TripRequest(primary_destination="Testville", start_date=day, end_date=day,
                                                   travelers_count=1, travel_group_type=TravelGroupType.SOLO))

    def poi(pid: str, venue: dict | None) -> dict[str, Any]:
        return {"place_id": pid, "name": pid, "provider_tags": {"tourism": "museum"}, "venue_tags": venue,
                "coordinates": {"lat": 0.0, "lng": 0.0}, "source": "test", "data_status": "live"}

    state.destination_context = DestinationContext(destination_name="Testville", candidate_pois=[
        poi("open", {"opening_hours": "24/7"}), poi("closed", {"opening_hours": "off", "check_date": "2020"}),
        poi("none", None),
    ])
    state.experience_plan = ExperiencePlan(daily_plans=[DailyPlan(day_number=1, date=day, experiences=[
        ExperienceItem(name=pid, category="museum", provider_place_id=pid) for pid in ("open", "closed", "none")
    ])])
    venue = metrics.venue_metrics(state)
    assert venue["scheduled_stops"] == 3
    assert venue["scheduled_on_a_listed_closing_day_not_must_visit"] == 1
    assert venue["stops_with_hours_unchecked"] == 1 and venue["share_with_hours_unchecked"] == pytest.approx(0.33)
    assert venue["stops_with_a_record_check_date"] == 1
