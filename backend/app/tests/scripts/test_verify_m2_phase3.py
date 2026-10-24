"""M2 Phase 3: the bounded live-verification wrapper, hermetically."""

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
    spec = importlib.util.spec_from_file_location("verify_m2_phase3_under_test", _SCRIPTS_DIR / "verify_m2_phase3.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def test_dry_run_sends_nothing_and_states_the_budget(script: Any, capsys: pytest.CaptureFixture,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    assert script.main(["--scenarios", "1"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["requests_sent"] == 0 and len(output["scenarios"]) == 1
    plan = output["plan"]
    assert plan["max_geoapify_credits"] == 100 and plan["max_llm_requests_combined"] == 8
    assert plan["max_requests_by_service"]["kiwi"] == 0
    assert plan["max_requests_total"] == 100 + 2 + 3 + 2 + 8


def test_the_development_cities_are_not_frozen_and_the_trip_starts_on_a_monday(script: Any) -> None:
    frozen = script.phase1.frozen_destinations()
    assert not [s for s in script.DEV_SCENARIOS if script.phase1.normalize_city(s["city"]) in frozen]
    assert script.start_date(date(2026, 10, 24)).weekday() == 0


def test_production_is_refused(script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    assert script.main([]) == 2


def _report(**venue: Any) -> dict[str, Any]:
    base = {"scheduled_stated_not_operating": 0, "scheduled_stated_not_operating_not_must_visit": 0,
            "scheduled_on_a_listed_closing_day": 0, "scheduled_on_a_listed_closing_day_not_must_visit": 0,
            "stops_by_evidence": {"hours_not_listed": 4}, "share_with_hours_unchecked": 1.0,
            "remote_days_without_a_requested_place": []}
    return {"technical_failure": None, "acceptance": {"outcome": "PASS"},
            "itinerary_quality": {"venue": {**base, **venue}, "route_severity": {"days_by_severity": {}}}}


def _results(script: Any, report: dict[str, Any]) -> dict[str, str]:
    return {c["check"]: c["result"] for c in script.scenario_checks({"id": "D1"}, report)}


def test_checks(script: Any) -> None:
    assert "FAIL" not in _results(script, _report()).values()
    bad = _results(script, _report(scheduled_on_a_listed_closing_day_not_must_visit=1,
                                   scheduled_stated_not_operating_not_must_visit=1))
    assert bad["no stop on a listed closing day, except a must-visit"] == "FAIL"
    assert bad["no stop stated as not operating, except a must-visit"] == "FAIL"
    # a must-visit kept and reported is not a failure
    kept = _results(script, _report(scheduled_on_a_listed_closing_day=1))
    assert kept["no stop on a listed closing day, except a must-visit"] == "pass"
    assert _results(script, {"technical_failure": "Boom"})["generation completed"] == "FAIL"
    unmeasured = {"technical_failure": None, "itinerary_quality": {"unavailable": "X"}}
    assert _results(script, unmeasured)["venue figures were measured"] == "FAIL"


def test_version_2_reports_causes_and_flags_lost_anchor_evidence(script: Any) -> None:
    results = _results(script, _report(hours_unknown_by_cause={"no_hours_in_record": 2}, stops_by_record={"pool": 4}))
    assert results["no promoted anchor lost its venue evidence on the way to the plan"] == "pass"
    assert results["causes of unknown hours (informational)"] == "n/a"
    lost = _results(script, _report(hours_unknown_by_cause={"no_record": 1}))
    assert lost["no promoted anchor lost its venue evidence on the way to the plan"] == "FAIL"
