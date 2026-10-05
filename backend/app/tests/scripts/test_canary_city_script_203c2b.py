from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
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

# Section 203C.2B: the single-city canary tool (backend/scripts/canary_city.py).
# The tool itself is only ever run by hand against live providers; this test
# drives its report builder through the real pipeline against an in-process
# fake network, so its field access and its sanitized output stay correct.

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _BACKEND_DIR / "scripts" / "canary_city.py"
_KEY = "SENTINEL_203C2B_CANARY_KEY_0001"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("canary_city_under_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_canary_refuses_to_run_without_keys_and_names_no_city() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("GEOAPIFY_API_KEY", "GROQ_API_KEY")}
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--city", "Anywhere, Nowhere"],
        capture_output=True, text=True, env=env, cwd=_BACKEND_DIR, timeout=60,
    )
    assert result.returncode == 2
    assert "Nothing was run" in result.stdout

    # generic tooling: it takes the city from the command line and knows none itself
    data = json.loads((_BACKEND_DIR / "scripts" / "benchmark" / "cities.json").read_text())
    source = _SCRIPT_PATH.read_text()
    for destination in (*data["tuning"], *data["holdout"]):
        assert destination.split(",")[0] not in source


def test_canary_report_covers_every_section_and_is_sanitized(
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

    canary = _load_script()
    args = argparse.Namespace(
        city="Testville, Testland", days=3, pace="balanced",
        interests=["history, museum", "food"], must_visit=["Castle 3"],
    )
    # An ungroundable must-visit is disclosed, never fabricated, and is a
    # machine-readable review reason -- so the canary does not call it clean.
    flagged = canary._run(
        argparse.Namespace(**{**vars(args), "must_visit": ["Castle 3", "An Invented Palace"]}), {}
    )
    assert flagged["must_visits"]["unresolved"] == ["An Invented Palace"]
    assert flagged["must_visits"]["scheduled"] == ["Castle 3"]
    assert "MUST_VISIT" in flagged["quality"]["review_codes"]
    assert flagged["acceptance"]["outcome"] == "FAIL"
    assert flagged["acceptance"]["failed_stages"] == ["validation / readiness"]

    report = canary._run(args, {})

    assert report["technical_failure"] is None
    assert report["destination"] == {"resolved_destination": "Testville, Testland", "geocode_success": True}
    inventory = report["inventory"]
    assert (inventory["T"], inventory["R"], inventory["H"]) == (9, 8, 21)
    assert inventory["inventory_status"] == "healthy" and inventory["broad_candidate_count"] >= 21

    must = report["must_visits"]
    assert must["requested"] == ["Castle 3"]
    assert must["grounded"] == ["Castle 3"] and must["scheduled"] == ["Castle 3"]
    assert must["unresolved"] == []
    assert report["quality"]["readiness"] == "ready" and report["quality"]["review_codes"] == []
    assert [day["long_route"] for day in report["daily_travel_burden"]] == [False, False, False]
    assert report["food_locality"]["repeated_suggestion_count"] == 0
    assert all(day["proximity_available"] for day in report["food_locality"]["days"])
    assert report["rationale_consistency"]["stale_rationale_detected"] is False

    top = report["top_candidates"]
    assert len(top) == 20 and top[0]["name"] == "Castle 3" and top[0]["source"] == "must-visit"
    assert {candidate["source"] for candidate in top} <= {"broad", "grounded anchor", "must-visit"}

    assert len(report["final_itinerary"]) == 3
    for day in report["final_itinerary"]:
        assert day["attractions"] and all(stop["provider_identity_present"] for stop in day["attractions"])
        assert day["nearby_food"] and day["route_duration_seconds"] is not None

    quality = report["quality"]
    assert quality["meaningful_scheduled_stops"] >= 8 and quality["empty_days"] == [] and quality["duplicates"] == []
    assert quality["usefulness_verdict"] == "pass" and quality["blocking_codes"] == []
    assert set(quality["interest_coverage"]) == {"history", "museum", "food"}

    routing = report["routing"]
    assert routing["required_legs"] == routing["factual_routed_legs"] and routing["coverage_percentage"] == 100.0
    assert report["factual_safety"]["fabricated_or_unverified_scheduled_identities"] == []
    usage = report["provider_usage"]
    assert usage["total_geoapify_credits"] == sum(usage["geoapify_credits_by_api"].values()) <= 100
    assert set(usage["llm_stage_runs"]) == {"anchor_proposal", "itinerary_reasoning", "itinerary_repair_attempts", "narrator"}
    # requests actually sent, per provider and stage (no model provider is connected in this fixture)
    assert usage["llm_calls_by_provider"] == {
        provider: {"anchor": 0, "reasoning": 0, "repair": 0, "narrator": 0} for provider in ("groq", "gemini")
    }
    assert not [key for key in usage if "groq" in key]
    assert report["persistence"] == {"save_succeeded": True, "reload_succeeded": True}
    assert report["latency"]["total_generation_seconds"] >= 0

    acceptance = report["acceptance"]
    assert [check["check"] for check in acceptance["checks"] if not check["passed"]] == []
    assert acceptance["outcome"] == "PASS" and acceptance["failed_stages"] == []
    assert {check["check"] for check in acceptance["checks"]} >= {
        "destination geocoded", "zero duplicates", "zero empty days", "routing coverage >= 90%",
        "every grounded must-visit scheduled", "persistence and reload",
    }

    text = canary._render(report)
    for heading in (
        "DESTINATION", "INVENTORY", "ANCHOR QUALITY", "MUST-VISITS", "TOP CANDIDATES", "FINAL ITINERARY", "QUALITY",
        "DAILY TRAVEL BURDEN", "FOOD LOCALITY", "RATIONALE CONSISTENCY", "READINESS",
        "ROUTING", "FACTUAL SAFETY", "PROVIDER USAGE", "PERSISTENCE", "LATENCY", "ACCEPTANCE: PASS",
    ):
        assert heading in text, heading
    serialized = text + json.dumps(report, default=str)
    assert _KEY not in serialized and "apiKey" not in serialized and "api.geoapify.com" not in serialized


def test_canary_acceptance_names_the_generic_stage_that_failed() -> None:
    canary = _load_script()
    report = {
        "destination": {"geocode_success": True},
        "inventory": {"viable_meaningful_candidate_count": 24, "R": 8},
        "quality": {
            "duplicates": [], "meaningful_scheduled_stops": 6, "empty_days": [3], "blocking_codes": [],
            "review_codes": [], "readiness": "ready",
        },
        "routing": {"coverage_percentage": 50.0},
        "must_visits": {"grounded": ["A"], "scheduled": []},
        "factual_safety": {"fabricated_or_unverified_scheduled_identities": [], "unsupported_factual_claims_in_stored_narrative": 0},
        "persistence": {"save_succeeded": True, "reload_succeeded": True},
        "provider_usage": {"geoapify_credit_budget": 100, "total_geoapify_credits": 40},
    }
    acceptance = canary._acceptance(report)
    assert acceptance["outcome"] == "FAIL"
    assert acceptance["failed_stages"] == [
        "candidate quality / experience planning", "itinerary reasoning / repair / fallback", "routing",
    ]

    blocked = {**report, "inventory": {"viable_meaningful_candidate_count": 3, "R": 8},
               "quality": {**report["quality"], "blocking_codes": ["INSUFFICIENT_VERIFIED_INVENTORY"]}}
    assert canary._acceptance(blocked)["outcome"] == "PASS (honest INSUFFICIENT_VERIFIED_INVENTORY)"
