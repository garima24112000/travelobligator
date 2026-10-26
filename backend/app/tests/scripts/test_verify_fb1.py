"""Final Batch 1: the bounded live-verification wrapper, hermetically (no request)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"


@pytest.fixture(scope="module")
def script() -> Any:
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("verify_fb1_under_test", _SCRIPTS_DIR / "verify_fb1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def test_dry_run_sends_nothing_and_states_the_cycle_ceilings(script: Any, capsys: pytest.CaptureFixture,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    assert script.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["requests_sent"] == 0 and len(output["scenarios"]) == 4
    plan = output["plan"]
    assert plan["max_geoapify_credits"] == 400 and plan["max_llm_requests_combined"] == 32
    assert plan["cycle_ceilings"] == {"geoapify_credits": 400, "llm_requests": 32}
    assert plan["max_requests_by_service"]["kiwi"] == 0


def test_f2_alone_is_selected_with_one_generation_s_caps_and_pinned_dates(
    script: Any, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    assert script.main(["--scenario", "F2", "--start-date", "2026-11-16"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["requests_sent"] == 0 and output["start_date"] == "2026-11-16"
    assert [s["id"] for s in output["scenarios"]] == ["F2"]
    assert output["scenarios"][0] == next(s for s in script.SCENARIOS if s["id"] == "F2")  # unchanged
    plan = output["plan"]
    assert plan["max_geoapify_credits"] == 100 and plan["max_llm_requests_combined"] == 8
    assert plan["max_requests_by_service"]["kiwi"] == 0 and plan["max_requests_by_service"]["geoapify"] == 100


def test_an_unknown_scenario_is_refused_before_anything(script: Any, capsys: pytest.CaptureFixture) -> None:
    assert script.main(["--scenario", "F9"]) == 2
    assert "unknown scenario" in capsys.readouterr().err


def test_the_readiness_gate_is_the_canary_s_own(script: Any) -> None:
    assert script.unaccepted_review_codes(["GEOGRAPHIC_DISPERSION", "HOLIDAYS"]) == ["GEOGRAPHIC_DISPERSION"]
    assert script.unaccepted_review_codes(["HOLIDAYS", "ACCOMMODATION_INVENTORY"]) == []
    state = _state([], [], times=[("09:30", "fits_listed_hours")], findings=[])
    state.validation_report.review_codes = ["GEOGRAPHIC_DISPERSION", "HOLIDAYS"]
    results = _results(script, {"id": "X", "must_visit": []}, state)
    assert results["readiness gate: no unaccepted review code (e.g. GEOGRAPHIC_DISPERSION)"] == "FAIL"


def test_no_scenario_city_is_in_a_benchmark_file(script: Any) -> None:
    import verify_m2_integrations as phase1

    frozen = phase1.frozen_destinations()
    assert all(phase1.normalize_city(s["city"]) not in frozen for s in script.SCENARIOS)
    assert len({s["id"] for s in script.SCENARIOS}) == 4


@pytest.mark.parametrize("problem", ["production", "frozen", "inside_repo", "too_many"])
def test_refusals_come_before_any_request(script: Any, monkeypatch: pytest.MonkeyPatch, problem: str) -> None:
    scenarios = list(script.SCENARIOS)
    out = None
    if problem == "production":
        monkeypatch.setenv("APP_ENV", "production")
    elif problem == "frozen":
        import verify_m2_integrations as phase1

        # any benchmark-file city, read from the files (no frozen name is written here)
        scenarios[0] = {**scenarios[0], "city": sorted(phase1.frozen_destinations())[0]}
    elif problem == "inside_repo":
        out = _SCRIPTS_DIR / "fb1.json"
    else:
        scenarios = scenarios + [scenarios[0]]  # a fifth generation would exceed the cycle ceilings
    assert script.refusal(scenarios, out) is not None


def _state(must_visit: list[str], statuses: list[dict], *, times: list[tuple[str | None, str]],
           findings: list[tuple[str, str]], group_twice: bool = False) -> Any:
    from app.models.common import ReadinessStatus, ValidationSeverity
    from app.models.planning_state import (
        DailyPlan, DestinationContext, ExperienceItem, ExperiencePlan, MustVisitStatusEntry, PlanningState,
        TravelGroupType, TripRequest, ValidationIssue, ValidationReport,
    )

    day = date(2026, 11, 16)
    state = PlanningState(trip_request=TripRequest(primary_destination="Devtown", start_date=day, end_date=day,
                                                   travelers_count=2, travel_group_type=TravelGroupType.COUPLE,
                                                   must_visit=must_visit))
    pool = [{"place_id": f"p{i}", "name": f"P{i}", "same_site_group": "site:p0" if group_twice and i < 2 else None}
            for i in range(len(times))]
    state.destination_context = DestinationContext(destination_name="Devtown", candidate_pois=pool)
    items = [ExperienceItem(name=f"P{i}", category="museum", provider_place_id=f"p{i}", start_time=start,
                            visit_time_status=status, estimated_duration_minutes=60)
             for i, (start, status) in enumerate(times)]
    state.experience_plan = ExperiencePlan(daily_plans=[DailyPlan(day_number=1, date=day, experiences=items)])
    state.validation_report = ValidationReport(
        readiness_status=ReadinessStatus.NEEDS_REVIEW,
        warnings=[ValidationIssue(severity=ValidationSeverity.WARNING, category=c, message=m,
                                  affected_section="experience_plan") for c, m in findings],
        must_visit_status=[MustVisitStatusEntry(**entry) for entry in statuses],
    )
    return state


_REPORT = {"technical_failure": None, "provider_usage": {"total_geoapify_credits": 60},
           "itinerary_quality": {"venue": {"scheduled_stated_not_operating_not_must_visit": 0,
                                           "scheduled_on_a_listed_closing_day_not_must_visit": 0}}}


def _results(script: Any, scenario: dict, state: Any) -> dict[str, str]:
    summary = script.summarize(state, _REPORT)
    return {item["check"]: item["result"] for item in script.checks(scenario, state, summary, _REPORT)}


def test_a_clean_plan_passes_every_check(script: Any) -> None:
    scenario = {"id": "X", "must_visit": ["A"]}
    state = _state(["A"], [{"term": "A", "status": "scheduled"}], times=[("09:30", "fits_listed_hours")], findings=[])
    assert set(_results(script, scenario, state).values()) == {"pass"}


def test_silent_removal_untimed_order_and_transport_claims_fail(script: Any) -> None:
    scenario = {"id": "X", "must_visit": ["A", "B"]}
    state = _state(
        ["A", "B"], [{"term": "A", "status": "unresolved"}],  # B has no status; A not disclosed
        times=[(None, "not_timed"), ("10:00", "fits_listed_hours")],  # a time after an untimed stop
        findings=[("remote_day_access", "Take the bus there; it departs hourly.")],
    )
    results = _results(script, scenario, state)
    assert results["every requested must-visit has a status (none removed silently)"] == "FAIL"
    assert results["a must-visit not on the plan is disclosed"] == "FAIL"
    assert results["no time estimated after an unverified leg"] == "FAIL"
    assert results["no finding claims a transport service"] == "FAIL"


def test_an_undisclosed_hours_conflict_and_a_doubled_site_fail(script: Any) -> None:
    scenario = {"id": "X", "must_visit": []}
    state = _state([], [], times=[("09:30", "outside_listed_hours"), ("12:00", "fits_listed_hours")], findings=[],
                   group_twice=True)
    results = _results(script, scenario, state)
    assert results["every remaining hours conflict is disclosed"] == "FAIL"
    assert results["no two records of one site scheduled (unless both requested)"] == "FAIL"


def test_a_driving_route_worded_as_a_road_passes(script: Any) -> None:
    scenario = {"id": "X", "must_visit": []}
    message = ("Day 2: X is outside Devtown. By road it is about 30 minutes each way from the centre of Devtown; "
               "how you travel there is not arranged or checked.")
    state = _state([], [], times=[("09:30", "fits_listed_hours")], findings=[("remote_day_access", message)])
    assert _results(script, scenario, state)["no finding claims a transport service"] == "pass"
