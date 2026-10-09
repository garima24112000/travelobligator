from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.providers.base import CurrencyProvider, HolidayProvider, WeatherProvider
from app.providers.gateway import provider_gateway
from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter
from app.providers.routing.geoapify_adapter import GeoapifyRoutingAdapter
from app.storage.provider_cache_store import ProviderCacheStore
from app.tests.services.test_final_v1_contracts_203c2b import _production_like_network

# Phase Q0 (docs/24_itinerary_quality_contract.md): the itinerary-quality
# benchmark data, its runner and the reported-only quality metrics. The
# runner is only ever run by hand against live providers; nothing here makes
# a live provider call, and no benchmark scenario is run.

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
_CITIES_PATH = _SCRIPTS_DIR / "benchmark" / "cities.json"
_QUALITY_PATH = _SCRIPTS_DIR / "benchmark" / "quality_v1.json"
_RUNNER_PATH = _SCRIPTS_DIR / "benchmark_quality.py"
_CANARY_PATH = _SCRIPTS_DIR / "canary_city.py"
_KEY = "SENTINEL_Q0_QUALITY_KEY_0001"

# The historical benchmark file, byte for byte (18 tuning / 10 holdout / 8 stress).
_CITIES_SHA256 = "21e412006f21ad8a004ef7a0c560b7ad0311b70e78e91da496163d1107a2ac8a"
# The frozen quality holdout (canonical JSON of its scenario list). A change
# here means the unseen set was edited after the freeze -- do not "fix" this
# value without recording why in docs/24_itinerary_quality_contract.md.
_QUALITY_HOLDOUT_SHA256 = "ff43d4bb99aba6c691e5f7ffc3ba3fc56e08afce73bbd3c00cb8f462af671191"

# Benchmark names that were ALREADY in production files before Q0 (two
# comments and a US-state lookup). Nothing may be added to this list.
_PREEXISTING_NAME_OCCURRENCES = {
    ("providers/holidays/nager_date_adapter.py", "Mumbai"): 1,
    ("services/candidate_quality_service.py", "Central Park"): 1,
    ("utils/destination_inference.py", "New York"): 1,
}


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runner = _load(_RUNNER_PATH, "benchmark_quality_under_test")


def _fold(text: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return plain.casefold()


def _city(destination: str) -> str:
    return _fold(destination.split(",")[0]).strip()


def _quality() -> dict[str, Any]:
    return json.loads(_QUALITY_PATH.read_text(encoding="utf-8"))


def _cities() -> dict[str, Any]:
    return json.loads(_CITIES_PATH.read_text(encoding="utf-8"))


def _production_files() -> list[Path]:
    return [path for path in sorted((_BACKEND_DIR / "app").rglob("*.py")) if "tests" not in path.parts]


# -- benchmark data ---------------------------------------------------------------------------


def test_existing_benchmark_history_is_unchanged() -> None:
    assert hashlib.sha256(_CITIES_PATH.read_bytes()).hexdigest() == _CITIES_SHA256
    data = _cities()
    assert (len(data["tuning"]), len(data["holdout"]), len(data["holdout_stress"])) == (18, 10, 8)
    assert data["canonical"] == {"trip_days": 3, "pace": "balanced"}
    assert "quality_tuning" not in data and "quality_holdout" not in data


def test_quality_sets_have_the_intended_shape() -> None:
    data = _quality()
    tuning, holdout = data["quality_tuning"], data["quality_holdout"]
    assert len(tuning) == 6 and len(holdout) == 10
    assert [scenario["id"] for scenario in tuning] == [f"Q-T{n}" for n in range(1, 7)]
    assert [scenario["id"] for scenario in holdout] == [f"Q-H{n}" for n in range(1, 11)]
    for scenario in (*tuning, *holdout):
        assert set(scenario) == {"id", "destination", "trip_days", "pace", "interests", "must_visit", "covers"}
        assert scenario["pace"] in {"relaxed", "balanced", "packed"} and 1 <= scenario["trip_days"] <= 5
        assert scenario["interests"] and scenario["covers"] and len(scenario["must_visit"]) <= 3


def test_quality_holdout_is_frozen_and_overlaps_no_earlier_city() -> None:
    data, history = _quality(), _cities()
    holdout = data["quality_holdout"]
    canonical = json.dumps(holdout, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() == _QUALITY_HOLDOUT_SHA256

    earlier = {_city(destination) for destination in (*history["tuning"], *history["holdout"])}
    earlier |= {_city(scenario["destination"]) for scenario in history["holdout_stress"]}
    holdout_cities = [_city(scenario["destination"]) for scenario in holdout]
    assert len(set(holdout_cities)) == len(holdout_cities) == 10
    assert not set(holdout_cities) & earlier
    # the tuning scenarios also name no holdout city
    assert not set(holdout_cities) & {_city(scenario["destination"]) for scenario in data["quality_tuning"]}


def test_quality_tuning_uses_only_existing_tuning_cities() -> None:
    tuning_set = set(_cities()["tuning"])
    for scenario in _quality()["quality_tuning"]:
        assert scenario["destination"] in tuning_set, scenario["id"]


def test_new_york_regression_scenario_states_the_observed_request() -> None:
    scenario = _quality()["quality_tuning"][0]
    assert scenario["id"] == "Q-T1" and scenario["destination"] == "New York, United States"
    assert (scenario["trip_days"], scenario["pace"]) == (4, "balanced")
    assert scenario["interests"] == ["museums", "piers"]
    assert scenario["must_visit"] == ["Statue of Liberty", "Central Park", "Empire State Building"]
    assert {"waterfront-interest normalization", "must-visit identity", "severe route burden"} <= set(scenario["covers"])


def test_scenarios_never_encode_an_answer_itinerary() -> None:
    forbidden = re.compile(r"day[_ ]?\d|expected|ideal|itinerary|neighbou?rhood|must_contain|day_plan|stops", re.IGNORECASE)

    def keys(value: Any) -> list[str]:
        if isinstance(value, dict):
            return [*value, *(key for item in value.values() for key in keys(item))]
        if isinstance(value, list):
            return [key for item in value for key in keys(item)]
        return []

    data = _quality()
    scenarios = [*data["quality_tuning"], *data["quality_holdout"]]
    assert not [key for key in keys(scenarios) if forbidden.search(key)]
    for scenario in scenarios:
        # `covers` names generic quality dimensions, never a place of the scenario
        for cover in scenario["covers"]:
            assert not any(_fold(name) in _fold(cover) for name in (scenario["destination"].split(",")[0], *scenario["must_visit"]))


# -- the holdout guard --------------------------------------------------------------------------


def _run_script(path: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in ("GEOAPIFY_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY")}
    return subprocess.run(
        [sys.executable, str(path), *args], capture_output=True, text=True, env={**base, **(env or {})},
        cwd=_BACKEND_DIR, timeout=120,
    )


def test_quality_holdout_refuses_without_the_explicit_flag_even_with_keys(tmp_path: Path) -> None:
    keys = {"GEOAPIFY_API_KEY": _KEY, "GROQ_API_KEY": _KEY}
    result = _run_script(
        _RUNNER_PATH, "--set", "quality-holdout", "--start-date", "2027-03-09", "--out", str(tmp_path / "out"), env=keys
    )
    assert result.returncode == 2
    assert "--allow-quality-holdout" in result.stdout and "Nothing was run" in result.stdout
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "extra", [["--scenarios", "Q-H1"], ["--limit", "2"], ["--start", "3"]], ids=["scenarios", "limit", "start"]
)
def test_quality_holdout_is_never_run_in_part(extra: list[str], tmp_path: Path) -> None:
    result = _run_script(
        _RUNNER_PATH, "--set", "quality-holdout", "--allow-quality-holdout", "--start-date", "2027-03-09",
        "--out", str(tmp_path / "out"), *extra, env={"GEOAPIFY_API_KEY": _KEY, "GROQ_API_KEY": _KEY},
    )
    assert result.returncode == 2
    assert "run whole" in result.stdout and "Nothing was run" in result.stdout
    assert not (tmp_path / "out").exists()


def test_guard_function_and_selection() -> None:
    refuse = runner.holdout_refusal
    assert refuse(runner.SET_TUNING, allow_quality_holdout=False, scenario_ids=["Q-T1"], start=2, limit=1) is None
    assert refuse(runner.SET_HOLDOUT, allow_quality_holdout=False, scenario_ids=[], start=1, limit=None)
    assert refuse(runner.SET_HOLDOUT, allow_quality_holdout=True, scenario_ids=[], start=1, limit=None) is None
    assert refuse(runner.SET_HOLDOUT, allow_quality_holdout=True, scenario_ids=["Q-H2"], start=1, limit=None)

    data = runner.load_data()
    tuning = runner.scenarios_of(data, runner.SET_TUNING)
    # one set at a time: the tuning set never contains a holdout scenario, and there is no combined set
    assert {scenario["id"][:3] for scenario in tuning} == {"Q-T"}
    assert {scenario["id"][:3] for scenario in runner.scenarios_of(data, runner.SET_HOLDOUT)} == {"Q-H"}
    assert [s["id"] for s in runner.select_scenarios(tuning, ["Q-T4", "Q-T1"])] == ["Q-T1", "Q-T4"]
    assert [s["id"] for s in runner.select_scenarios(tuning, None, start=5)] == ["Q-T5", "Q-T6"]
    with pytest.raises(ValueError):
        runner.select_scenarios(tuning, ["Q-H1"])


def test_nothing_runs_without_keys_and_the_default_set_is_tuning() -> None:
    result = _run_script(_RUNNER_PATH, "--start-date", "2027-03-09")
    assert result.returncode == 2 and "Missing required environment variable" in result.stdout
    assert "Refusing" not in result.stdout


def test_single_city_canary_refuses_a_quality_holdout_city() -> None:
    holdout = _quality()["quality_holdout"]
    for destination in (holdout[1]["destination"], holdout[6]["destination"].split(",")[0]):
        result = _run_script(_CANARY_PATH, "--city", destination, env={"GEOAPIFY_API_KEY": _KEY, "GROQ_API_KEY": _KEY})
        assert result.returncode == 2
        assert "frozen quality holdout" in result.stdout and "Nothing was run" in result.stdout
    # an ordinary city is not affected by the guard (it stops at the missing keys, as before)
    result = _run_script(_CANARY_PATH, "--city", "Anywhere, Nowhere")
    assert result.returncode == 2 and "Missing required environment variable" in result.stdout


def test_runner_records_the_three_contract_parts_separately(tmp_path: Path) -> None:
    scenarios = runner.scenarios_of(runner.load_data(), runner.SET_TUNING)[:2]
    quality = {"interests": {"uncovered": []}}

    def run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
        if scenario["id"] == "Q-T2":
            raise RuntimeError(f"boom {_KEY}")
        return {
            "technical_failure": None,
            "latency": {"total_generation_seconds": 12.5},
            "acceptance": {"outcome": "PASS", "checks": [{"check": "zero duplicates", "passed": True}]},
            "itinerary_quality": quality,
        }

    summary = runner.run_benchmark(
        scenarios, which=runner.SET_TUNING, all_scenarios=scenarios, out_dir=tmp_path, start_date=date(2027, 3, 9),
        run_scenario=run_scenario, render=lambda report: "detail", now=lambda: "2027-01-01T00:00:00Z",
        sleep=lambda seconds: None, log=lambda line: None,
    )
    assert summary["baseline_hard_correctness_passed"] == ["Q-T1"] and summary["technical_failures"] == ["Q-T2"]
    assert summary["baseline_hard_correctness_failed"] == []
    record = summary["records"][0]
    # The existing canary acceptance is the pre-Q1 BASELINE, and is named as such: it does not
    # include A1-A3, so no field may read as the contract's whole part A having passed.
    note = runner.BASELINE_HARD_CORRECTNESS_NOTE
    assert "pre-Q1 canary acceptance baseline" in note and "A1-A3" in note and "Q1 implementation" in note
    assert record["baseline_hard_correctness"] == {"note": note, "outcome": "PASS", "passed": True, "failed_checks": []}
    assert summary["baseline_hard_correctness_note"] == note
    assert not [key for key in (*record, *summary) if key.startswith("hard_correctness")]
    assert record["itinerary_quality"] == quality
    # the manual rubric is a person's job: never pre-filled, never derived
    assert record["manual_rubric"] == {dimension: None for dimension in runner.RUBRIC_DIMENSIONS}
    assert "passed" not in record and "score" not in record and "overall" not in summary
    written = "".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file())
    assert _KEY not in written and "boom" not in written

    # resume skips the completed scenario and reruns the failed one
    calls: list[str] = []
    runner.run_benchmark(
        scenarios, which=runner.SET_TUNING, all_scenarios=scenarios, out_dir=tmp_path, start_date=date(2027, 3, 9),
        run_scenario=lambda scenario: calls.append(scenario["id"]) or {"technical_failure": "X"}, resume=True,
        render=lambda report: "detail", now=lambda: "2027-01-01T00:00:00Z", sleep=lambda seconds: None, log=lambda line: None,
    )
    assert calls == ["Q-T2"]


# -- production code stays generic ----------------------------------------------------------------


def test_quality_benchmark_is_never_imported_by_the_app() -> None:
    for path in _production_files():
        text = path.read_text()
        for name in ("quality_v1", "benchmark_quality", "quality_metrics"):
            assert name not in text, (path, name)


def test_no_benchmark_city_or_place_name_is_added_to_production_code() -> None:
    data = _quality()
    names: set[str] = set()
    for scenario in (*data["quality_tuning"], *data["quality_holdout"]):
        names.add(scenario["destination"].split(",")[0])
        names.update(scenario["must_visit"])

    found: dict[tuple[str, str], int] = {}
    for path in _production_files():
        text = _fold(path.read_text())
        for name in names:
            count = len(re.findall(r"(?<![a-z0-9])" + re.escape(_fold(name)) + r"(?![a-z0-9])", text))
            if count:
                found[(path.relative_to(_BACKEND_DIR / "app").as_posix(), name)] = count
    assert found == _PREEXISTING_NAME_OCCURRENCES

    # the tooling itself knows no city either: every name comes from the data file
    for script in (_RUNNER_PATH, _SCRIPTS_DIR / "quality_metrics.py"):
        source = _fold(script.read_text())
        assert not [name for name in names if _fold(name) in source], script


# -- metric extraction ----------------------------------------------------------------------------


def test_quality_metrics_are_deterministic_and_read_stored_state_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inventory_sufficiency_gate_enabled: None
) -> None:
    monkeypatch.setenv("GEOAPIFY_API_KEY", _KEY)
    monkeypatch.setenv("GEOCODING_PROVIDER", "geoapify")
    get_settings.cache_clear()

    handler, _seen = _production_like_network()
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    monkeypatch.setattr(provider_gateway, "places", GeoapifyPlacesAdapter(cache_store=store, geocoder=GeoapifyGeocoder()))
    monkeypatch.setattr(provider_gateway, "routing", GeoapifyRoutingAdapter(cache_store=store))
    for slot, stub in (("weather", WeatherProvider()), ("holiday", HolidayProvider()), ("currency", CurrencyProvider())):
        monkeypatch.setattr(provider_gateway, slot, stub)

    canary = _load(_CANARY_PATH, "canary_city_under_q0_test")
    metrics_module = canary._quality_metrics_module()
    extract = metrics_module.extract_quality_metrics
    states: list[Any] = []
    monkeypatch.setattr(metrics_module, "extract_quality_metrics", lambda state: states.append(state) or extract(state))

    report = canary._run(
        argparse.Namespace(
            city="Testville, Testland", days=3, pace="balanced",
            interests=["history, museum", "piers"], must_visit=["Castle 3"],
        ),
        {},
    )
    state = states[0]
    metrics = report["itinerary_quality"]

    # reported only: the acceptance verdict does not read it
    assert canary._acceptance({key: value for key, value in report.items() if key != "itinerary_quality"}) == report["acceptance"]
    assert "ITINERARY QUALITY (reported only" in canary._render(report)

    # stored state only: with every way out closed, the same figures come back and nothing is written
    def _no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("quality metrics must not reach a provider")

    monkeypatch.setattr(httpx, "Client", _no_network)
    for slot in ("places", "routing", "weather", "holiday", "currency"):
        monkeypatch.setattr(provider_gateway, slot, None)
    before = state.model_dump_json()
    assert extract(state) == extract(state) == metrics
    assert state.model_dump_json() == before
    assert json.loads(json.dumps(metrics)) == metrics  # plain data

    interests = metrics["interests"]
    assert interests["requested_terms"] == ["history", "museum", "piers"]
    # Q1 (contract A2): the waterfront family is a tracked canonical interest, so "piers" is no
    # longer unrecognised; this pool has no waterfront place, so it is honestly without supply.
    assert interests["canonical"] == ["history", "museum", "waterfront"]
    assert interests["unrecognised_terms"] == []
    assert interests["uncovered_without_supply"] == ["waterfront"]
    assert set(interests["covered"]) | set(interests["uncovered"]) == {"history", "museum", "waterfront"}
    assert set(interests["uncovered"]) == set(interests["uncovered_with_supply"]) | set(interests["uncovered_without_supply"])
    assert all(interests["viable_supply_by_interest"][name] >= 1 for name in interests["covered"])

    must = metrics["must_visits"]
    assert must["grounded_scheduled"] == ["Castle 3"] and must["grounded_unscheduled"] == []
    assert must["terms_with_multiple_scheduled_places"] == []

    geography, burden = metrics["geography"], metrics["route_burden"]
    assert set(geography["day_spread_km"]) == {"1", "2", "3"}
    assert geography["max_day_spread_km"] >= geography["median_day_spread_km"] >= 0
    assert [day["day"] for day in burden["days"]] == [1, 2, 3]
    assert burden["long_travel_days"] == [] and burden["long_travel_days_with_viable_at_least_T"] == []
    assert burden["routing_coverage_percentage"] == report["routing"]["coverage_percentage"]
    assert burden["viable_at_least_T"] is True

    slots = metrics["limited_slots"]
    assert sum(slots["scheduled_by_tier"].values()) == sum(metrics["pace_fit"]["stops_per_day"])
    assert set(slots["scheduled_by_tier"]) <= {"primary_anchor", "good_candidate", "secondary_candidate", "low_priority", "rejected", "unknown"}
    assert metrics["baseline"]["meaningful_scheduled_stops"] == report["quality"]["meaningful_scheduled_stops"]
    assert metrics["baseline"]["empty_day_count"] == 0 and metrics["baseline"]["duplicate_scheduled_place_count"] == 0
    # nothing is estimated: the metrics that need the future composition work are named, not filled in
    assert metrics["future_metrics_not_computed"] == list(metrics_module.FUTURE_METRICS)
    # no key or value claims a popularity, a rating, opening hours or a ranking
    assert not {"popularity", "rating", "opening_hours", "ranking"} & set(json.dumps(metrics).split('"'))

    # A related place inheriting the user's term is visible as one term holding two itinerary slots.
    inherited = state.model_copy(deep=True)
    scheduled_ids = [
        stop.provider_place_id for day in inherited.experience_plan.daily_plans for stop in day.experiences
    ]
    other = next(
        poi for poi in inherited.destination_context.candidate_pois
        if poi.get("place_id") in scheduled_ids and "castle 3" not in str(poi.get("name", "")).lower()
        and not poi.get("must_visit_term")
    )
    other["must_visit_term"] = "Castle 3"
    changed = extract(inherited)["must_visits"]
    assert changed["terms_with_multiple_scheduled_places"] == ["Castle 3"]
    assert changed["detail"][0]["scheduled_place_count"] == 2
