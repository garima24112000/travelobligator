from __future__ import annotations

import argparse
import importlib.util
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.core import performance
from app.core.config import get_settings
from app.core.provider_usage import ProviderUsageTracker
from app.models.common import GeoPoint
from app.models.generation_performance import GenerationPerformanceReport
from app.models.planning_state import PlanningState
from app.providers import geoapify_client
from app.providers.base import CurrencyProvider, HolidayProvider, WeatherProvider
from app.providers.errors import ProviderRequestError
from app.providers.gateway import provider_gateway
from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter
from app.providers.routing.geoapify_adapter import GeoapifyRoutingAdapter
from app.providers.weather import open_meteo_adapter
from app.providers.weather.open_meteo_adapter import OpenMeteoWeatherAdapter
from app.repositories.factory import get_planning_state_repository
from app.storage.provider_cache_store import ProviderCacheStore
from app.tests.services.test_final_v1_contracts_203c2b import _production_like_network

# Section 1A: per-generation latency profiling (measurement only). The
# recorder is exercised directly with a fake clock, and end to end through
# the real pipeline against the same in-process fake network the canary
# test uses.

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _BACKEND_DIR / "scripts" / "canary_city.py"
_KEY = "SENTINEL_1A_PERFORMANCE_KEY_0001"
_CITY = "Testville, Testland"
_FIXED_KEY = re.compile(r"^[a-z0-9_]{1,48}$")


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _load_canary() -> Any:
    spec = importlib.util.spec_from_file_location("canary_city_under_performance_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def production_like_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inventory_sufficiency_gate_enabled: None
) -> dict[str, list[httpx.Request]]:
    monkeypatch.setenv("GEOAPIFY_API_KEY", _KEY)
    monkeypatch.setenv("GEOCODING_PROVIDER", "geoapify")
    get_settings.cache_clear()
    handler, seen = _production_like_network()
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    monkeypatch.setattr(provider_gateway, "places", GeoapifyPlacesAdapter(cache_store=store, geocoder=GeoapifyGeocoder()))
    monkeypatch.setattr(provider_gateway, "routing", GeoapifyRoutingAdapter(cache_store=store))
    for slot, stub in (("weather", WeatherProvider()), ("holiday", HolidayProvider()), ("currency", CurrencyProvider())):
        monkeypatch.setattr(provider_gateway, slot, stub)
    return seen


def _canary_args() -> argparse.Namespace:
    return argparse.Namespace(
        city=_CITY, days=3, pace="balanced", interests=["history, museum", "food"], must_visit=["Castle 3"]
    )


# -- the recorder ---------------------------------------------------------------------------------------------


def test_timers_use_the_monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    assert performance.PerformanceRecorder()._clock is time.monotonic

    # A wall-clock jump (NTP step, DST) cannot change a measurement.
    wall_clock = iter([1_000.0, 5.0, 999_999.0])
    monkeypatch.setattr(time, "time", lambda: next(wall_clock, 0.0))
    clock = _FakeClock()
    recorder = performance.PerformanceRecorder(clock=clock)
    with performance.activate(recorder):
        recorder.start()
        with performance.stage("validation"):
            clock.advance(2.0)
    report = recorder.snapshot()
    assert report["stage_ms"] == {"validation": 2000.0} and report["total_ms"] == 2000.0


def test_stage_time_is_exclusive_of_nested_stages_and_adds_up_to_the_total() -> None:
    clock = _FakeClock()
    recorder = performance.PerformanceRecorder(clock=clock)
    with performance.activate(recorder):
        recorder.start()
        clock.advance(0.5)  # outside every stage
        with performance.stage("destination_context"):
            clock.advance(1.0)
            with performance.stage("places_broad"):
                clock.advance(3.0)
                with performance.stage("destination_resolution"):
                    clock.advance(2.0)
            with performance.stage("places_broad"):
                clock.advance(1.0)
    report = recorder.snapshot()
    assert report["stage_ms"] == {"destination_context": 1000.0, "places_broad": 4000.0, "destination_resolution": 2000.0}
    assert report["stage_inclusive_ms"] == {
        "destination_context": 7000.0, "places_broad": 6000.0, "destination_resolution": 2000.0,
    }
    assert report["other_ms"] == 500.0
    assert sum(report["stage_ms"].values()) + report["other_ms"] == report["total_ms"] == 7500.0


def test_nothing_is_recorded_and_nothing_raises_without_an_active_recorder() -> None:
    assert performance.current() is None
    with performance.stage("validation"), performance.provider_call("groq_narrator"):
        performance.note_request("geocoding", "anything")
        performance.note_cache("geoapify_geocode", True)
        performance.count("route_memo_hits")
    assert performance.build_report(None) is None


def test_two_generations_never_share_a_recorder() -> None:
    first, second = performance.PerformanceRecorder(), performance.PerformanceRecorder()
    with performance.activate(first):
        performance.count("route_memo_hits")
        with performance.activate(second):
            performance.count("route_memo_hits", 5)
        performance.count("route_memo_hits")
    assert first.snapshot()["counts"]["route_memo_hits"] == 2
    assert second.snapshot()["counts"]["route_memo_hits"] == 5
    assert performance.current() is None


def test_only_fixed_keys_are_accepted_so_free_text_cannot_enter_the_report() -> None:
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        recorder.start()
        for name in ("Hawa Mahal, Jaipur", "https://api.example/v1?apiKey=secret", "", "a" * 49, "UPPER"):
            with performance.stage(name), performance.provider_call(name):
                pass
            performance.note_request(name, "x")
            performance.note_cache(name, True)
            performance.count(name)
    report = recorder.snapshot()
    assert report["stage_ms"] == {} and report["provider_ms"] == {} and report["request_totals"] == {}
    assert report["cache_hits"] == {} and "UPPER" not in report["counts"]


def test_a_stage_or_provider_timer_never_swallows_the_body_exception() -> None:
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder), pytest.raises(ValueError):
        with performance.stage("validation"), performance.provider_call("groq_narrator"):
            raise ValueError("body failure")
    report = recorder.snapshot()
    assert "validation" in report["stage_ms"] and report["provider_attempts"] == {"groq_narrator": 1}


# -- provider wall-clock ------------------------------------------------------------------------------------------


def test_provider_wall_time_accumulates_per_api_and_geoapify_counts_come_from_the_usage_tracker() -> None:
    clock = _FakeClock()
    recorder = performance.PerformanceRecorder(clock=clock)
    tracker = ProviderUsageTracker(budget=100)

    def handler(request: httpx.Request) -> httpx.Response:
        clock.advance(0.25)
        if request.url.params["text"] == "broken":
            return httpx.Response(500)
        return httpx.Response(200, json={"results": []})

    def geocode(text: str) -> None:
        geoapify_client.geoapify_get(
            client, base_url="https://geo.test", path="/v1/geocode/search", params={"text": text},
            api_key=_KEY, timeout=5.0, api="geocoding", usage=tracker,
        )

    with performance.activate(recorder), httpx.Client(transport=httpx.MockTransport(handler)) as client:
        recorder.start()
        geocode("first")
        geocode("second")
        geocode("first")  # the same request again
        with pytest.raises(ProviderRequestError):
            geocode("broken")

    report = recorder.snapshot(tracker.snapshot())
    # every attempt's wall-clock counts, the failed one included
    assert report["provider_ms"] == {"geoapify_geocoding": 1000.0}
    assert report["provider_attempts"] == {"geoapify_geocoding": 4}
    # request COUNTS are the usage tracker's own (successful, charged requests)
    assert report["counts"]["geocode_requests"] == tracker.calls_made("geocoding") == 3
    assert report["request_totals"] == {"geocoding": 4} and report["redundant_requests"] == {"geocoding": 1}
    assert _KEY not in json.dumps(report) and "first" not in json.dumps(report)


def test_a_retried_request_accumulates_the_wall_time_of_every_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()
    recorder = performance.PerformanceRecorder(clock=clock)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        clock.advance(1.5 if calls["count"] == 1 else 0.5)
        if calls["count"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"daily": {"time": []}})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(open_meteo_adapter.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("PROVIDER_CACHE_ENABLED", "false")
    get_settings.cache_clear()

    start = open_meteo_adapter._today()
    with performance.activate(recorder):
        recorder.start()
        OpenMeteoWeatherAdapter().get_weather_forecast(
            _CITY,
            {"start_date": start.isoformat(), "end_date": start.isoformat()},
            coordinates=GeoPoint(lat=50.0, lng=10.0),
        )
    report = recorder.snapshot()
    assert calls["count"] == 2
    assert report["provider_attempts"] == {"open_meteo": 2} and report["provider_ms"] == {"open_meteo": 2000.0}


def test_a_groq_structural_retry_counts_both_attempts_and_keeps_prompts_and_output_out() -> None:
    clock = _FakeClock()
    recorder = performance.PerformanceRecorder(clock=clock)
    prompts: list[str] = []

    class _Client:
        def invoke(self, prompt: str) -> Any:
            prompts.append(prompt)
            clock.advance(4.0)
            return "SENTINEL RAW MODEL OUTPUT"  # not a structured document -> one structural retry

    provider = GroqItineraryNarratorProvider(client=_Client())
    request = SimpleNamespace()
    with performance.activate(recorder), pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.providers.itinerary_narrator.groq_adapter._build_prompt", lambda _request: "SENTINEL PROMPT")
        recorder.start()
        provider.narrate(request)  # type: ignore[arg-type]

    report = recorder.snapshot()
    assert len(prompts) == 2
    assert report["provider_attempts"] == {"groq_narrator": 2} and report["provider_ms"] == {"groq_narrator": 8000.0}
    assert report["counts"]["groq_narrator_calls"] == 2
    assert "SENTINEL" not in json.dumps(report)


# -- the report model -----------------------------------------------------------------------------------------------


def test_the_report_fields_are_optional_and_backwards_compatible() -> None:
    empty = GenerationPerformanceReport()
    assert empty.total_ms is None and empty.stage_ms == {} and empty.includes_final_commit is False
    # a report written by an older build, with fewer fields
    assert GenerationPerformanceReport.model_validate({"total_ms": 12.5}).provider_ms == {}


def test_a_state_persisted_before_the_report_existed_loads_unchanged(client: Any, created_trip_id: str) -> None:
    state = get_planning_state_repository().get_by_trip_id(created_trip_id)
    assert state is not None and state.generation_performance_report is None

    stored = state.model_dump(mode="json")
    stored.pop("generation_performance_report")
    reloaded = PlanningState.model_validate(stored)
    assert reloaded.generation_performance_report is None
    assert reloaded.model_dump(mode="json") == state.model_dump(mode="json")


# -- end to end ------------------------------------------------------------------------------------------------------


def test_a_generation_carries_a_sanitized_performance_report_and_the_canary_renders_it(
    production_like_pipeline: dict[str, list[httpx.Request]]
) -> None:
    canary = _load_canary()
    report = canary._run(_canary_args(), {})
    assert report["technical_failure"] is None

    section = report["performance"]
    assert section["available"] is True and section["engine"] == "langgraph"
    stage_ms = section["stage_ms"]
    for stage in (
        "persistence", "destination_context", "places_broad", "destination_resolution", "must_visit_grounding",
        "candidate_quality", "inventory_sufficiency", "experience_planning", "food_suggestions",
        "walking_routing", "route_feasibility", "validation", "narrator",
    ):
        assert stage in stage_ms, stage
    assert all(value >= 0 for value in stage_ms.values())
    # the stage rows and "other" account for the whole generation
    accounted = sum(item["seconds"] for item in section["stages"]) + section["other_seconds"]
    assert accounted == pytest.approx(section["total_seconds"], abs=0.5)
    assert section["total_seconds"] <= report["latency"]["total_generation_seconds"] + 0.1

    # request counts are the provider usage tracker's own, not a second count
    usage_calls = report["provider_usage"]["geoapify_calls_by_api"]
    counts = section["counts"]
    assert counts["geocode_requests"] == usage_calls.get("geocoding", 0) == len(production_like_pipeline["geocode"])
    assert counts["places_requests"] == usage_calls.get("places", 0) == len(production_like_pipeline["places"])
    assert counts["walk_route_requests"] == usage_calls.get("routing", 0) == len(production_like_pipeline["routing"])
    assert section["provider_ms"]["geoapify_places"] >= 0 and section["provider_ms"]["geoapify_routing_walk"] >= 0
    # the destination is resolved for every search, but geocoded over the network once
    redundant = {item["label"]: item for item in section["redundant_work"]}
    assert redundant["repeated geocode calls"]["repeated"] == 0
    assert redundant["repeated destination resolutions (cache-served or live)"]["repeated"] >= 2

    text = canary._render(report)
    assert "PERFORMANCE" in text and "REDUNDANT WORK" in text
    for label in (
        "- broad places:", "- anchor proposal:", "- anchor grounding:", "- reasoning:", "- routing walk:",
        "- routing drive:", "- food:", "- validation:", "- narrator:", "- persistence:", "- other:",
        "- Geoapify geocode:", "- Geoapify places:", "- Geoapify details:", "- Geoapify walk routing:",
        "- Geoapify drive routing:", "- Groq anchor:", "- Groq reasoning:", "- Groq narrator:", "- Nager.Date:",
        "- repeated geocode calls:", "- repeated details calls:", "- repeated route calls:",
        "- repeated named lookup calls:",
    ):
        assert label in text, label
    # performance is reported, never judged
    assert report["acceptance"]["outcome"] == "PASS"
    assert not any("performance" in check["check"].lower() for check in report["acceptance"]["checks"])

    # nothing but numbers under fixed keys: no key, URL, place, query or prompt
    serialized = json.dumps(section)
    for forbidden in (_KEY, "apiKey", "geoapify.com", "http", "Testville", "Castle", "Cafe"):
        assert forbidden not in serialized, forbidden


def test_the_persisted_report_holds_only_numbers_under_fixed_keys(
    production_like_pipeline: dict[str, list[httpx.Request]]
) -> None:
    from app.models.planning_state import TravelGroupType, TripPace, TripRequest
    from app.services.planning_orchestrator import planning_orchestrator

    canary = _load_canary()
    created = planning_orchestrator.create_trip(
        TripRequest(
            primary_destination=_CITY,
            start_date=canary._START_DATE,
            end_date=canary._START_DATE,
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
            pace=TripPace.BALANCED,
            interests=["history"],
        )
    )
    returned = planning_orchestrator.generate_full_plan_via_langgraph(created.trip_id)
    stored = get_planning_state_repository().get_by_trip_id(created.trip_id)
    assert stored is not None and stored.generation_performance_report is not None
    # the report is written before the final commit and never changed after it,
    # so the returned state and the committed one are identical
    assert stored.generation_performance_report == returned.generation_performance_report
    assert stored.generation_performance_report.includes_final_commit is False
    assert stored.generation_performance_report.stage_ms["persistence"] >= 0

    dumped = stored.generation_performance_report.model_dump(mode="json")
    for field in (
        "stage_ms", "stage_inclusive_ms", "provider_ms", "provider_attempts", "counts", "cache_hits",
        "cache_misses", "request_totals", "redundant_requests",
    ):
        assert all(_FIXED_KEY.match(key) for key in dumped[field]), field
        assert all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in dumped[field].values())
    # Section 1B: concurrency diagnostics -- counts and batch sizes only
    assert all(_FIXED_KEY.match(key) for key in (*dumped["stage_task_ms"], *dumped["batch_sizes"]))
    assert all(isinstance(size, int) for sizes in dumped["batch_sizes"].values() for size in sizes)
    assert isinstance(dumped["peak_geoapify_concurrency"], int) and isinstance(dumped["concurrent_batches"], int)
    assert set(dumped) == {
        "engine", "total_ms", "stage_ms", "stage_inclusive_ms", "other_ms", "provider_ms", "provider_attempts",
        "counts", "cache_hits", "cache_misses", "request_totals", "redundant_requests", "includes_final_commit",
        "stage_task_ms", "peak_geoapify_concurrency", "concurrent_batches", "batch_sizes",
        "process_peak_geoapify_concurrency", "llm_stages",  # Section 1C
    }
    assert isinstance(dumped["process_peak_geoapify_concurrency"], int)
    assert all(_FIXED_KEY.match(key) for key in dumped["llm_stages"])


@pytest.mark.parametrize("engine", ["langgraph", "legacy"])
def test_a_timing_failure_never_breaks_a_generation(
    production_like_pipeline: dict[str, list[httpx.Request]], monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    from app.models.planning_state import TravelGroupType, TripPace, TripRequest
    from app.services.planning_orchestrator import planning_orchestrator

    canary = _load_canary()

    def _trip() -> str:
        return planning_orchestrator.create_trip(
            TripRequest(
                primary_destination=_CITY,
                start_date=canary._START_DATE,
                end_date=canary._START_DATE,
                travelers_count=2,
                travel_group_type=TravelGroupType.COUPLE,
                pace=TripPace.BALANCED,
                interests=["history"],
            )
        ).trip_id

    generate = (
        planning_orchestrator.generate_full_plan_via_langgraph
        if engine == "langgraph"
        else planning_orchestrator.generate_full_plan
    )
    healthy = generate(_trip())
    assert healthy.generation_performance_report is not None
    assert healthy.generation_performance_report.engine == engine

    def _broken_clock() -> float:
        raise RuntimeError("clock failure")

    # 1. the clock fails from the first reading: the generation runs unprofiled
    real_recorder = performance.PerformanceRecorder
    monkeypatch.setattr(performance, "PerformanceRecorder", lambda: real_recorder(clock=_broken_clock))
    assert performance.started_recorder() is None
    state = generate(_trip())
    assert state.experience_plan is not None and state.generation_performance_report is None

    # 2. the clock fails mid-generation, inside the stage/provider timers
    readings = {"count": 0}

    def _clock_that_breaks_later() -> float:
        readings["count"] += 1
        if readings["count"] > 3:
            raise RuntimeError("clock failure")
        return time.monotonic()

    monkeypatch.setattr(performance, "PerformanceRecorder", lambda: real_recorder(clock=_clock_that_breaks_later))
    state = generate(_trip())
    assert readings["count"] > 3
    assert state.experience_plan is not None
    assert [len(day.experiences) for day in state.experience_plan.daily_plans] == [
        len(day.experiences) for day in healthy.experience_plan.daily_plans
    ]
    assert state.validation_report is not None
    assert state.validation_report.readiness_status == healthy.validation_report.readiness_status
    assert state.generation_performance_report is None
