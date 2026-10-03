from __future__ import annotations

import argparse
import copy
import csv
import importlib.util
import io
import json
import os
import subprocess
import sys
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

# Section 3A: the 18-city tuning benchmark harness
# (backend/scripts/benchmark_tuning_cities.py). The harness is only ever run
# by hand against live providers; these tests drive it with fake canary
# reports (and once through the real pipeline on an in-process fake network),
# so nothing here makes a live provider call.

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _BACKEND_DIR / "scripts" / "benchmark_tuning_cities.py"
_KEY = "SENTINEL_3A_TUNING_KEY_0001"
_START = date(2027, 3, 9)


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("benchmark_tuning_cities_under_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bench = _load_script()


def _fake_report(**overrides: Any) -> dict[str, Any]:
    """A canary report in the shape `canary_city._run` produces (clean run)."""
    stops = [
        {"order": 1, "name": "Old Fort", "category": "fort", "provider_identity_present": True, "low_value": False},
        {"order": 2, "name": "City Museum", "category": "museum", "provider_identity_present": True, "low_value": False},
        {"order": 3, "name": "Grand Hall", "category": "architecture", "provider_identity_present": True, "low_value": False},
    ]
    report: dict[str, Any] = {
        "request": {"city": "X", "days": 3, "pace": "balanced", "interests": ["history"], "must_visit": []},
        "technical_failure": None,
        "latency": {"total_generation_seconds": 40.0},
        "performance": {
            "available": True,
            "stages": [{"label": "reasoning", "seconds": 6.5}, {"label": "narrator", "seconds": 0.0}],
            "llm_stages": [
                {"label": "Groq anchor", "attempts": 1, "deadline_exceeded": False, "result": "success"},
                {"label": "Groq reasoning", "attempts": 2, "deadline_exceeded": False, "result": "success"},
            ],
            "concurrency": {"peak_geoapify_concurrency": 4, "process_peak_geoapify_concurrency": 4},
        },
        "destination": {"resolved_destination": "X, Land", "geocode_success": True},
        "inventory": {
            "T": 9, "R": 8, "H": 21, "broad_candidate_count": 120, "viable_meaningful_candidate_count": 40,
            "inventory_status": "healthy",
        },
        "anchors": {"proposal_status": "completed", "failure_kind": None, "proposed": 12, "grounded": 9, "promoted": 7},
        "final_itinerary": [
            {"day": number, "attractions": copy.deepcopy(stops), "warnings": []} for number in (1, 2, 3)
        ],
        "quality": {
            "meaningful_scheduled_stops": 9, "target_attainment_ratio": 1.0, "empty_days": [], "duplicates": [],
            "low_value_scheduled_objects": [], "interest_coverage": {"history": True}, "readiness": "ready",
            "blocking_codes": [], "review_codes": [],
        },
        "routing": {"required_legs": 6, "factual_routed_legs": 6, "coverage_percentage": 100.0, "failures": {}},
        "movement_modes": {"legs_by_mode": {"drive": 1, "walk": 5}, "legs_with_unverified_movement_data": 0},
        "diversity": {"days": [{"day": 1, "hard_violation": False}]},
        "suspect_entity_collisions": {"scheduled_unresolved_collision_count": 0, "zero_distance_scheduled_pairs": []},
        "food_locality": {"repeated_suggestion_count": 0, "suggestions_beyond_radius": 0},
        "factual_safety": {
            "fabricated_or_unverified_scheduled_identities": [], "unsupported_factual_claims_in_stored_narrative": 0,
            "narrative_ai_attempt_status": "success", "narrative_source": "ai",
        },
        "provider_usage": {
            "geoapify_calls_by_api": {"places": 6, "routing": 3}, "total_geoapify_credits": 40,
            "geoapify_credit_budget": 100, "geoapify_calls_refused_by_budget": 0,
            "groq_stage_calls": {"anchor_proposal": 1}, "itinerary_reasoning_status": "completed",
        },
        "persistence": {"save_succeeded": True, "reload_succeeded": True},
        "acceptance": {"outcome": "PASS", "checks": [{"check": "zero duplicates", "passed": True}], "failed_stages": []},
    }
    for path, value in overrides.items():
        section, _, key = path.partition("__")
        if key:
            report[section][key] = value
        else:
            report[section] = value
    return report


def _metrics(**overrides: Any) -> dict[str, Any]:
    return bench.extract_metrics(
        _fake_report(**overrides), city=bench.CANONICAL_CITIES[0], scenario=bench.DEFAULT_SCENARIO,
        run_timestamp="2027-03-01T00:00:00Z",
    )


def _run(tmp_path: Path, cities: list[str], run_city: Any, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("render", lambda report: "fake canary detail")
    return bench.run_benchmark(
        cities, out_dir=tmp_path, scenario=bench.DEFAULT_SCENARIO, start_date=_START, run_city=run_city,
        now=lambda: "2027-03-01T00:00:00Z", log=lambda line: None, **kwargs,
    )


# -- canonical scenarios ------------------------------------------------------------------------


def test_canonical_city_list_has_exactly_18_unique_cities_and_one_fixed_scenario() -> None:
    cities = bench.CANONICAL_CITIES
    assert len(cities) == 18 == len(set(cities)) == len({bench.city_slug(city) for city in cities})
    assert all("," in city for city in cities)
    assert bench.DEFAULT_SCENARIO == {
        "days": 3, "pace": "balanced", "travelers": 2, "interests": ["architecture", "history", "food"],
        "must_visit": [], "origin": "New York",
    }
    # the start date is a command-line input: the benchmark never reads today's date for a scenario
    assert "date.today" not in _SCRIPT_PATH.read_text()


def test_selection_is_always_in_canonical_order() -> None:
    cities = list(bench.CANONICAL_CITIES)
    assert bench.select_cities() == cities
    assert bench.select_cities(start=7, limit=6) == cities[6:12]
    assert bench.select_cities(start=13) == cities[12:]
    assert bench.select_cities([cities[10].split(",")[0].lower(), cities[2]]) == [cities[2], cities[10]]
    with pytest.raises(ValueError):
        bench.select_cities(["Nowhere At All"])
    with pytest.raises(ValueError):
        bench.select_cities(start=0)


def test_start_date_is_required_and_nothing_runs_without_keys() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("GEOAPIFY_API_KEY", "GROQ_API_KEY")}

    def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), *arguments], capture_output=True, text=True, env=env,
            cwd=_BACKEND_DIR, timeout=60,
        )

    assert invoke().returncode == 2 and "--start-date" in invoke().stderr
    without_keys = invoke("--start-date", "2027-03-09", "--limit", "1")
    assert without_keys.returncode == 2 and "Nothing was run" in without_keys.stdout
    assert invoke("--start-date", "2027-03-09", "--resume").returncode == 2  # nothing to resume without --out


# -- the runner ---------------------------------------------------------------------------------


def test_cities_run_sequentially_and_one_failure_does_not_abort_the_rest(tmp_path: Path) -> None:
    cities = list(bench.CANONICAL_CITIES[:4])
    events: list[str] = []
    active = 0

    def run_city(city: str) -> dict[str, Any]:
        nonlocal active
        active += 1
        assert active == 1  # never two generations at once
        events.append(city)
        try:
            if city == cities[1]:
                raise RuntimeError(f"boom https://api.example.test/v1?apiKey={_KEY}")
            if city == cities[2]:
                return {"technical_failure": "TimeoutError", "latency": {"total_generation_seconds": 12.0}}
            return _fake_report()
        finally:
            active -= 1

    pauses: list[float] = []
    summary = _run(tmp_path, cities, run_city, pause_seconds=8.0, sleep=pauses.append)

    assert events == cities and pauses == [8.0, 8.0, 8.0]
    assert [record["city"] for record in summary["cities"]] == cities
    assert [record["status"] for record in summary["cities"]] == [
        "completed", "technical_failure", "technical_failure", "completed",
    ]
    # a failure is recorded by exception TYPE only
    assert summary["cities"][1]["technical_failure"] == "RuntimeError"
    assert summary["cities"][1]["manual_review_flags"][:2] == ["TECHNICAL_FAILURE", "ACCEPTANCE_FAIL"]
    assert summary["aggregate"]["acceptance_pass_count"] == 2 and summary["aggregate"]["cities_run"] == 4
    assert summary["aggregate"]["technical_failures"] == cities[1:3]
    written = "".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file())
    assert "boom" not in written and _KEY not in written


def test_output_tree_and_deterministic_ordering(tmp_path: Path) -> None:
    cities = list(bench.CANONICAL_CITIES)
    # run out of order across two "batches" into the same directory
    _run(tmp_path, cities[12:], lambda city: _fake_report())
    summary = _run(tmp_path, cities[:6], lambda city: _fake_report())

    assert sorted(path.name for path in tmp_path.iterdir()) == ["cities", "summary.csv", "summary.json", "summary.md"]
    slugs = [bench.city_slug(city) for city in cities[:6] + cities[12:]]
    assert sorted(path.name for path in (tmp_path / "cities").iterdir()) == sorted(
        f"{slug}.{suffix}" for slug in slugs for suffix in ("json", "txt")
    )
    assert [record["city"] for record in summary["cities"]] == cities[:6] + cities[12:]
    assert summary["aggregate"]["cities_not_run"] == cities[6:12]
    assert json.loads((tmp_path / "summary.json").read_text()) == summary

    stored = json.loads((tmp_path / "cities" / f"{slugs[0]}.json").read_text())
    assert set(stored) == {"metrics", "canary_report"} and stored["metrics"]["position"] == 1
    text = (tmp_path / "cities" / f"{slugs[0]}.txt").read_text()
    assert "MANUAL REVIEW FLAGS: none" in text and "fake canary detail" in text

    # the same results give byte-identical summaries
    before = {name: (tmp_path / name).read_text() for name in ("summary.json", "summary.csv", "summary.md")}
    bench.write_summary(tmp_path, scenario=bench.DEFAULT_SCENARIO, start_date=_START, generated_at="2027-03-01T00:00:00Z")
    assert before == {name: (tmp_path / name).read_text() for name in before}


def test_resume_skips_completed_cities_and_reruns_failed_or_incomplete_ones(tmp_path: Path) -> None:
    cities = list(bench.CANONICAL_CITIES[:4])
    _run(
        tmp_path, cities[:3],
        lambda city: {"technical_failure": "TimeoutError", "latency": {}} if city == cities[1] else _fake_report(),
    )
    # an interrupted write leaves no usable result
    (tmp_path / "cities" / f"{bench.city_slug(cities[2])}.json").write_text("{not json")
    assert [bench.is_completed(tmp_path, city) for city in cities] == [True, False, False, False]

    calls: list[str] = []

    def run_city(city: str) -> dict[str, Any]:
        calls.append(city)
        return _fake_report()

    summary = _run(tmp_path, cities, run_city, resume=True)
    assert calls == cities[1:]
    assert [record["status"] for record in summary["cities"]] == ["completed"] * 4

    # without --resume an explicitly selected city is run again
    calls.clear()
    _run(tmp_path, cities[:1], run_city)
    assert calls == cities[:1]


# -- metrics ------------------------------------------------------------------------------------


def test_metrics_extraction_from_a_fake_canary_result() -> None:
    record = _metrics()
    assert record["city"] == bench.CANONICAL_CITIES[0] and record["position"] == 1
    assert record["resolved_destination"] == "X, Land" and record["destination_resolved"] is True
    assert record["run_timestamp"] == "2027-03-01T00:00:00Z" and record["scenario"] == bench.DEFAULT_SCENARIO
    assert record["status"] == "completed" and record["technical_failure"] is None
    assert record["acceptance"] == {
        "passed": True, "outcome": "PASS", "failed_checks": [], "failed_stages": [], "readiness": "ready",
        "blocking_codes": [], "review_codes": [],
    }
    assert record["inventory"] == {
        "T": 9, "R": 8, "H": 21, "broad_candidates": 120, "viable_meaningful_candidates": 40,
        "inventory_status": "healthy",
    }
    assert record["quality"] == {
        "meaningful_scheduled_stops": 9, "target_attainment_ratio": 1.0, "empty_days": [], "duplicate_count": 0,
        "low_value_scheduled_count": 0, "interest_coverage": {"history": True},
    }
    assert record["factual_safety"] == {"fabricated_or_unverified_scheduled_identities": 0, "unsupported_factual_claims": 0}
    assert record["anchors"] == {"proposed": 12, "grounded": 9, "promoted": 7, "failure_kind": None}
    assert record["routing"] == {
        "required_legs": 6, "factual_routed_legs": 6, "coverage_percentage": 100.0, "walk_legs": 5,
        "vehicle_transfer_legs": 1, "unresolved_movement_data_failures": 0, "failures_by_status": {},
    }
    assert record["diversity_collisions"] == {
        "hard_diversity_violations": 0, "unresolved_scheduled_collision_count": 0,
        "zero_distance_same_class_scheduled_pairs": 0,
    }
    assert record["food"] == {"repeated_suggestions": 0, "suggestions_beyond_radius": 0}
    assert record["persistence"] == {"save": True, "reload": True}
    assert record["providers"] == {
        "geoapify_credits_total": 40, "geoapify_credit_budget": 100, "geoapify_calls_refused_by_budget": 0,
        "geoapify_calls_by_api": {"places": 6, "routing": 3},
        "groq_attempts_by_stage": {"Groq anchor": 1, "Groq reasoning": 2},
        "deadline_exceeded_by_stage": {"Groq anchor": False, "Groq reasoning": False},
    }
    assert record["performance"] == {
        "total_generation_seconds": 40.0, "stage_seconds": {"reasoning": 6.5},
        "generation_peak_geoapify_concurrency": 4, "process_peak_geoapify_concurrency": 4,
    }
    assert record["narrative"] == {"source": "ai", "ai_attempt_status": "success", "fallback": False}
    assert record["reasoning"] == {"status": "completed", "fallback": False}
    assert len(record["scheduled_places"]) == 9
    assert record["scheduled_places"][0] == {
        "day": 1, "order": 1, "name": "Old Fort", "category": "fort", "low_value": False,
        "provider_identity_present": True,
    }
    assert record["canary_warnings"] == [] and record["manual_review_flags"] == []

    # without a recorded performance report the stage-level Groq count is used
    assert _metrics(performance={"available": False})["providers"]["groq_attempts_by_stage"] == {"anchor_proposal": 1}


def test_metrics_extraction_matches_the_real_canary_report(
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

    scenario = {**bench.DEFAULT_SCENARIO, "interests": ["history", "museum", "food"], "origin": "Origin City"}

    def run_city(city: str) -> dict[str, Any]:
        return bench.canary_city._run(
            argparse.Namespace(
                city="Testville, Testland", days=scenario["days"], pace=scenario["pace"],
                interests=scenario["interests"], must_visit=[], start_date=_START, origin=scenario["origin"],
                travelers=scenario["travelers"],
            ),
            {},
        )

    out_dir = tmp_path / "run"
    city = bench.CANONICAL_CITIES[0]
    summary = bench.run_benchmark(
        [city], out_dir=out_dir, scenario=scenario, start_date=_START, run_city=run_city, log=lambda line: None
    )

    stored = json.loads((out_dir / "cities" / f"{bench.city_slug(city)}.json").read_text())
    # the fixed scenario reaches the real trip request
    assert stored["canary_report"]["request"]["start_date"] == "2027-03-09"
    assert stored["canary_report"]["request"]["origin"] == "Origin City"
    assert stored["canary_report"]["request"]["travelers"] == 2

    record = summary["cities"][0]
    assert record["status"] == "completed" and record["acceptance"]["passed"] is True
    assert record["resolved_destination"] == "Testville, Testland"
    assert (record["inventory"]["T"], record["inventory"]["R"], record["inventory"]["H"]) == (9, 8, 21)
    assert record["quality"]["meaningful_scheduled_stops"] >= 8 and record["quality"]["empty_days"] == []
    assert record["routing"]["coverage_percentage"] == 100.0
    assert record["routing"]["walk_legs"] + record["routing"]["vehicle_transfer_legs"] == record["routing"]["required_legs"]
    assert record["persistence"] == {"save": True, "reload": True}
    assert record["providers"]["geoapify_credits_total"] > 0 and record["providers"]["geoapify_calls_by_api"]
    assert record["performance"]["total_generation_seconds"] is not None and record["performance"]["stage_seconds"]
    assert record["narrative"]["source"] is not None and record["reasoning"]["status"] is not None
    assert len(record["scheduled_places"]) >= 8 and all(place["name"] for place in record["scheduled_places"])
    for flag in ("ACCEPTANCE_FAIL", "MEANINGFUL_STOPS_BELOW_R", "EMPTY_DAY", "DUPLICATE", "FABRICATED_IDENTITY"):
        assert flag not in record["manual_review_flags"]

    text = (out_dir / "cities" / f"{bench.city_slug(city)}.txt").read_text()
    assert "FINAL ITINERARY" in text and "ACCEPTANCE: PASS" in text
    written = "".join(path.read_text() for path in out_dir.rglob("*") if path.is_file())
    assert _KEY not in written and "apiKey" not in written and "api.geoapify.com" not in written


# -- manual review ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "flag"),
    [
        ({"acceptance": {"outcome": "FAIL", "checks": [{"check": "zero duplicates", "passed": False}]}}, "ACCEPTANCE_FAIL"),
        ({"quality__meaningful_scheduled_stops": 7}, "MEANINGFUL_STOPS_BELOW_R"),
        ({"quality__empty_days": [3]}, "EMPTY_DAY"),
        ({"quality__duplicates": ["place-1"]}, "DUPLICATE"),
        ({"factual_safety__fabricated_or_unverified_scheduled_identities": ["Ghost"]}, "FABRICATED_IDENTITY"),
        ({"factual_safety__unsupported_factual_claims_in_stored_narrative": 1}, "UNSUPPORTED_FACTUAL_CLAIM"),
        ({"routing__coverage_percentage": 83.3}, "ROUTING_COVERAGE_BELOW_90"),
        ({"suspect_entity_collisions__scheduled_unresolved_collision_count": 1}, "UNRESOLVED_SCHEDULED_COLLISION"),
        (
            {"suspect_entity_collisions__zero_distance_scheduled_pairs": [{"day": 1, "stops": ["A", "B"]}]},
            "UNRESOLVED_SCHEDULED_COLLISION",
        ),
        ({"provider_usage__geoapify_calls_refused_by_budget": 2}, "PROVIDER_BUDGET_REFUSED_CALLS"),
        ({"latency": {"total_generation_seconds": 90.1}}, "LATENCY_OVER_90S"),
        (
            {"performance__llm_stages": [{"label": "Groq narrator", "attempts": 2, "deadline_exceeded": True}]},
            "LLM_STAGE_DEADLINE_EXCEEDED",
        ),
        ({"factual_safety__narrative_source": "deterministic_fallback"}, "NARRATOR_FALLBACK"),
        ({"quality__low_value_scheduled_objects": ["Old Fort"]}, "GENERIC_OR_LOW_VALUE_SCHEDULED_NAMES"),
    ],
)
def test_each_manual_review_rule_raises_exactly_its_flag(overrides: dict[str, Any], flag: str) -> None:
    assert _metrics(**overrides)["manual_review_flags"] == [flag]


def test_boundaries_and_generic_names_use_only_the_pipelines_own_signals() -> None:
    assert _metrics(latency={"total_generation_seconds": 90.0})["manual_review_flags"] == []
    assert _metrics(routing__coverage_percentage=90.0)["manual_review_flags"] == []
    assert _metrics(quality__meaningful_scheduled_stops=8)["manual_review_flags"] == []

    itinerary = _fake_report()["final_itinerary"]
    itinerary[0]["attractions"][0].update({"name": "Viewpoint", "category": "viewpoint"})  # named after its category
    itinerary[1]["attractions"][1]["low_value"] = True
    itinerary[2]["attractions"][2]["name"] = ""
    record = _metrics(final_itinerary=itinerary)
    assert record["generic_or_low_value_scheduled_names"] == ["Viewpoint", "City Museum", "(unnamed)"]
    assert record["manual_review_flags"] == ["GENERIC_OR_LOW_VALUE_SCHEDULED_NAMES"]
    # generic tooling: the rule knows no place and no city
    source = _SCRIPT_PATH.read_text().split("DEFAULT_SCENARIO", 1)[1]
    assert not any(city.split(",")[0] in source.replace('"origin": "New York"', "") for city in bench.CANONICAL_CITIES)


def test_a_narrator_fallback_and_a_reasoning_fallback_are_counted_separately() -> None:
    record = _metrics(provider_usage__itinerary_reasoning_status="rejected")
    assert record["reasoning"] == {"status": "rejected", "fallback": True}
    assert record["narrative"]["fallback"] is False and record["manual_review_flags"] == []


# -- aggregation ---------------------------------------------------------------------------------


def test_percentiles_are_nearest_rank_observed_values() -> None:
    values = [float(value) for value in range(1, 21)]
    assert bench.percentile(values, 50) == 10.0
    assert bench.percentile(values, 95) == 19.0
    assert bench.percentile(values, 100) == 20.0
    assert bench.percentile([7.0], 95) == 7.0
    assert bench.percentile([30.0, 10.0, 20.0], 95) == 30.0
    assert bench.percentile([], 95) is None


def _records(tmp_path: Path) -> dict[str, Any]:
    cities = list(bench.CANONICAL_CITIES[:5])
    reports = {
        cities[0]: _fake_report(),
        cities[1]: _fake_report(
            latency={"total_generation_seconds": 100.0}, provider_usage__total_geoapify_credits=80,
            factual_safety__narrative_source="deterministic_fallback",
            provider_usage__itinerary_reasoning_status="rejected",
        ),
        cities[2]: _fake_report(
            acceptance={"outcome": "FAIL", "checks": [{"check": "zero empty days", "passed": False}], "failed_stages": ["x"]},
            quality__meaningful_scheduled_stops=5, quality__empty_days=[3], routing__coverage_percentage=50.0,
            latency={"total_generation_seconds": 60.0}, provider_usage__total_geoapify_credits=60,
            suspect_entity_collisions__scheduled_unresolved_collision_count=1,
            factual_safety__fabricated_or_unverified_scheduled_identities=["Ghost"],
            persistence={"save_succeeded": True, "reload_succeeded": False},
        ),
        cities[3]: {"technical_failure": "TimeoutError", "latency": {"total_generation_seconds": 20.0}},
        cities[4]: _fake_report(destination={"resolved_destination": None, "geocode_success": False}),
    }
    return _run(tmp_path, cities, lambda city: reports[city])


def test_summary_aggregation(tmp_path: Path) -> None:
    summary = _records(tmp_path)
    cities = list(bench.CANONICAL_CITIES[:5])
    figures = summary["aggregate"]
    assert "score" not in json.dumps(summary).lower()
    assert figures["canonical_total"] == 18 and figures["cities_run"] == 5 and figures["acceptance_pass_count"] == 3
    assert figures["cities_not_run"] == list(bench.CANONICAL_CITIES[5:])
    assert figures["technical_failures"] == [cities[3]]
    assert figures["destination_resolution"] == {"count": 3, "of": 5, "rate": 0.6}
    assert figures["factual_safety_pass"] == {"count": 3, "of": 5, "rate": 0.6}
    assert figures["routing_at_least_90_percent"] == {"count": 3, "of": 5, "rate": 0.6}
    assert figures["persistence_success"] == {"count": 3, "of": 5, "rate": 0.6}
    # latencies 40, 100, 60, 20, 40
    assert figures["latency_seconds"] == {"average": 52.0, "median": 40.0, "p95": 100.0}
    # credits 40, 80, 60, 40 (the technical failure has none)
    assert figures["geoapify_credits"] == {"average": 55.0, "max": 80}
    assert figures["narrator_fallback_count"] == 1 and figures["reasoning_fallback_count"] == 1
    assert figures["cities_below_R"] == [cities[2]] and figures["cities_with_empty_days"] == [cities[2]]
    assert figures["cities_with_suspicious_collision_outcomes"] == [cities[2]]
    assert figures["cities_flagged_for_manual_review"] == cities[1:4]


def test_markdown_and_csv_generation(tmp_path: Path) -> None:
    _records(tmp_path)
    cities = list(bench.CANONICAL_CITIES)

    rows = list(csv.DictReader(io.StringIO((tmp_path / "summary.csv").read_text())))
    assert [row["city"] for row in rows] == cities[:5]
    assert [row["result"] for row in rows] == ["PASS", "PASS", "FAIL", "FAIL", "PASS"]
    assert rows[0]["meaningful_stops"] == "9" and rows[0]["routing_coverage_percent"] == "100.0"
    assert rows[1]["narrator_fallback"] == "True" and rows[1]["latency_seconds"] == "100.0"
    assert rows[2]["manual_review_flags"].split("; ")[:3] == ["ACCEPTANCE_FAIL", "MEANINGFUL_STOPS_BELOW_R", "EMPTY_DAY"]
    assert rows[3]["technical_failure"] == "TimeoutError" and rows[3]["readiness"] == ""

    markdown = (tmp_path / "summary.md").read_text()
    assert (
        "| City | PASS/FAIL | Meaningful stops | Empty days | Duplicates | Routing % | Fabricated | Unsupported claims "
        "| Credits | Latency (s) | Narrative fallback | Readiness |"
    ) in markdown
    assert f"| {cities[0]} | PASS | 9/9 (R=8) | 0 | 0 | 100.0 | 0 | 0 | 40 | 40.0 | no | ready |" in markdown
    assert f"| {cities[2]} | FAIL | 5/9 (R=8) | 1 | 0 | 50.0 | 1 | 0 | 60 | 60.0 | no | ready |" in markdown
    assert f"| {cities[3]} | FAIL (TimeoutError) | - | - | - | - | - | - | - | 20.0 | - | - |" in markdown
    assert f"| {cities[17]} | not run |" in markdown
    table = [line for line in markdown.splitlines() if line.startswith("| ") and not line.startswith("| City |")]
    assert [line.split(" | ")[0][2:] for line in table] == cities  # canonical order, run or not
    for line in (
        "- Acceptance: 3 / 4 completed cities passed",
        "- Progress: 4 / 18 tuning cities completed",
        "- destination resolution rate: 3 / 5 (60.0%)",
        "- latency (s): average 52.0 / median 40.0 / p95 100.0",
        "- Geoapify credits: average 55.0 / max 80",
        "- narrator fallback count: 1",
        f"- cities below R: {cities[2]}",
        f"- **{cities[1]}**: LATENCY_OVER_90S, NARRATOR_FALLBACK",
        "- day 1: Old Fort (fort)",
    ):
        assert line in markdown, line
    assert "score" not in markdown.lower()


# -- partial-run progress ---------------------------------------------------------------------------

_FAILED_ACCEPTANCE = {"outcome": "FAIL", "checks": [{"check": "zero empty days", "passed": False}], "failed_stages": ["x"]}
_TECHNICAL_FAILURE = {"technical_failure": "TimeoutError", "latency": {"total_generation_seconds": 20.0}}


def _progress(figures: dict[str, Any]) -> dict[str, int]:
    return {
        key: figures[key]
        for key in (
            "canonical_total", "completed_count", "technical_failure_count", "acceptance_pass_count",
            "acceptance_fail_count", "remaining_count",
        )
    }


def test_partial_run_of_6_reports_acceptance_over_completed_cities_only(tmp_path: Path) -> None:
    cities = list(bench.CANONICAL_CITIES[:6])
    # one completed city fails acceptance: it is completed, and it is not a pass
    summary = _run(
        tmp_path, cities, lambda city: _fake_report(acceptance=_FAILED_ACCEPTANCE) if city == cities[3] else _fake_report()
    )
    figures = summary["aggregate"]
    assert _progress(figures) == {
        "canonical_total": 18, "completed_count": 6, "technical_failure_count": 0, "acceptance_pass_count": 5,
        "acceptance_fail_count": 1, "remaining_count": 12,
    }
    assert bench.progress_lines(figures) == [
        "Acceptance: 5 / 6 completed cities passed", "Progress: 6 / 18 tuning cities completed", "Remaining: 12",
    ]
    markdown = (tmp_path / "summary.md").read_text()
    assert "- Acceptance: 5 / 6 completed cities passed\n- Progress: 6 / 18 tuning cities completed\n" in markdown
    assert "5 / 18" not in markdown and "/ 18\n" not in markdown.replace("/ 18 tuning cities completed\n", "")
    # rates keep the cities run as their denominator
    assert figures["destination_resolution"] == {"count": 6, "of": 6, "rate": 1.0}
    assert json.loads((tmp_path / "summary.json").read_text())["aggregate"] == figures
    assert "pass_count" not in {key for key in figures if key != "acceptance_pass_count"}


def test_technical_failure_in_a_partial_run_is_neither_completed_nor_an_acceptance_fail(tmp_path: Path) -> None:
    cities = list(bench.CANONICAL_CITIES[:6])
    summary = _run(tmp_path, cities, lambda city: _TECHNICAL_FAILURE if city == cities[2] else _fake_report())
    figures = summary["aggregate"]
    assert _progress(figures) == {
        "canonical_total": 18, "completed_count": 5, "technical_failure_count": 1, "acceptance_pass_count": 5,
        "acceptance_fail_count": 0, "remaining_count": 13,
    }
    assert figures["cities_run"] == 6 and figures["technical_failures"] == [cities[2]]
    assert bench.progress_lines(figures) == [
        "Acceptance: 5 / 5 completed cities passed",
        "Progress: 5 / 18 tuning cities completed",
        "Technical failures: 1 (not completed; run again)",
        "Remaining: 13",
    ]
    assert figures["destination_resolution"] == {"count": 5, "of": 6, "rate": 0.833}
    assert "/ 18\n" not in (tmp_path / "summary.md").read_text().replace("/ 18 tuning cities completed\n", "")


def test_full_18_of_18_run_also_shows_acceptance_over_18(tmp_path: Path) -> None:
    cities = list(bench.CANONICAL_CITIES)
    summary = _run(
        tmp_path, cities, lambda city: _fake_report(acceptance=_FAILED_ACCEPTANCE) if city == cities[8] else _fake_report()
    )
    figures = summary["aggregate"]
    assert _progress(figures) == {
        "canonical_total": 18, "completed_count": 18, "technical_failure_count": 0, "acceptance_pass_count": 17,
        "acceptance_fail_count": 1, "remaining_count": 0,
    }
    assert bench.progress_lines(figures) == [
        "Acceptance: 17 / 18 completed cities passed", "Progress: 18 / 18 tuning cities completed", "Acceptance: 17 / 18",
    ]
    assert "- Acceptance: 17 / 18\n" in (tmp_path / "summary.md").read_text()

    # all 18 were run but one did not complete: still no "P / 18"
    summary = _run(tmp_path, cities[:1], lambda city: _TECHNICAL_FAILURE)
    assert summary["aggregate"]["completed_count"] == 17 and summary["aggregate"]["remaining_count"] == 1
    assert "Acceptance: 16 / 17 completed cities passed" in bench.progress_lines(summary["aggregate"])
    assert not any(line.endswith("/ 18") for line in bench.progress_lines(summary["aggregate"]))


# -- secret safety --------------------------------------------------------------------------------


def test_no_secret_fields_or_values_are_written(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database_url = "postgresql://bench:hunter2hunter2@db.internal:5432/app"
    monkeypatch.setenv("GEOAPIFY_API_KEY", _KEY)
    monkeypatch.setenv("GROQ_API_KEY", "SENTINEL_GROQ_KEY_0002")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("REDIS_URL", "rediss://default:sentinelpass@cache.internal:6379")

    report = _fake_report()
    report["final_itinerary"][0]["warnings"] = [
        f"fetched https://api.example.test/v2/places?apiKey={_KEY}", f"plain key {_KEY} in text",
    ]
    report["final_itinerary"][0]["attractions"][0]["name"] = "rediss://default:sentinelpass@cache.internal:6379"
    report["provider_usage"].update(
        {"api_key": _KEY, "DATABASE_URL": database_url, "redis_url": "x", "raw_prompt": "p", "raw_response": "r"}
    )
    report["debug"] = {"model_response": "text", "authorization": "Bearer abc", "kept": "SENTINEL_GROQ_KEY_0002"}

    _run(tmp_path, [bench.CANONICAL_CITIES[0]], lambda city: report, render=lambda r: json.dumps(r))

    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(files) == 5
    for path in files:
        text = path.read_text()
        for forbidden in (
            _KEY, "SENTINEL_GROQ_KEY_0002", "hunter2", "sentinelpass", "db.internal", "cache.internal", "apiKey=",
            "://",
        ):
            assert forbidden not in text, (path.name, forbidden)
    stored = json.loads((tmp_path / "cities" / f"{bench.city_slug(bench.CANONICAL_CITIES[0])}.json").read_text())
    usage = stored["canary_report"]["provider_usage"]
    assert not {"api_key", "DATABASE_URL", "redis_url", "raw_prompt", "raw_response"} & set(usage)
    assert stored["canary_report"]["debug"] == {"kept": "[redacted]"}
    assert stored["metrics"]["canary_warnings"] == ["day 1: fetched [url removed]", "day 1: plain key [redacted] in text"]


def test_benchmark_output_is_git_ignored_and_never_imported_by_the_app() -> None:
    repo = _BACKEND_DIR.parent
    assert "benchmark_results/" in (repo / ".gitignore").read_text().splitlines()
    for path in (_BACKEND_DIR / "app").rglob("*.py"):
        if "tests" not in path.parts:
            assert "benchmark_tuning_cities" not in path.read_text(), path
