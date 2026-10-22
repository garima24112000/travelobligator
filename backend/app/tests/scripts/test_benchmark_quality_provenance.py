from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import get_settings

# Evaluation tooling only (docs/24_itinerary_quality_contract.md): a quality
# benchmark result records the effective planner configuration and the source
# it was produced from, and says whether its quality figures were measured.
# Nothing here makes a live provider call, and no benchmark scenario is run.

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
_KEY = "SENTINEL_PROVENANCE_KEY_0001"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runner = _load(_SCRIPTS_DIR / "benchmark_quality.py", "benchmark_quality_provenance_under_test")
provenance_module = runner.run_provenance


def _provenance(**settings_overrides: Any) -> dict[str, Any]:
    """A complete provenance on a synthetic source identity."""
    settings = get_settings()
    if settings_overrides:
        settings = settings.model_copy(update=settings_overrides)
    return {
        "configuration": provenance_module.capture_configuration(settings),
        "source": {"git_head": "0" * 40, "fingerprint_sha256": "f" * 64},
    }


def _report(*, outcome: str = "PASS", quality: Any = "measured") -> dict[str, Any]:
    figures: Any = {"schema_version": 4, "interests": {"uncovered": []}} if quality == "measured" else quality
    return {
        "technical_failure": None,
        "latency": {"total_generation_seconds": 1.0},
        "acceptance": {"outcome": outcome, "checks": [{"check": "zero duplicates", "passed": outcome == "PASS"}]},
        "itinerary_quality": figures,
    }


def _run(out_dir: Path, scenarios: list[dict[str, Any]], run_scenario: Any, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("provenance", _provenance())
    kwargs.setdefault("all_scenarios", scenarios)
    return runner.run_benchmark(
        scenarios, which=runner.SET_TUNING, out_dir=out_dir, start_date=date(2027, 3, 9), run_scenario=run_scenario,
        render=lambda report: "detail", now=lambda: "2027-01-01T00:00:00Z", sleep=lambda seconds: None,
        log=lambda line: None, **kwargs,
    )


def _tuning(count: int) -> list[dict[str, Any]]:
    return runner.scenarios_of(runner.load_data(), runner.SET_TUNING)[:count]


# -- effective configuration ----------------------------------------------------------------------


def test_configuration_records_the_effective_settings_not_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOAPIFY_API_KEY", _KEY)
    monkeypatch.setenv("GROQ_API_KEY", _KEY)
    get_settings.cache_clear()
    baseline = provenance_module.capture_configuration()
    flags = baseline["settings"]["quality_phase_flags"]
    assert flags["day_composition_enabled"] is True and flags["route_recomposition_enabled"] is True
    assert flags["route_burden_repair_enabled"] is True
    # every listed setting is there, grouped: flags, budgets, limits
    assert {group: set(names) for group, names in provenance_module.SETTING_GROUPS.items()} == {
        group: set(values) for group, values in baseline["settings"].items()
    }
    assert baseline["settings"]["routing_budgets_and_limits"]["route_recomposition_max_requests_per_generation"] == 8
    assert baseline["settings"]["routing_budgets_and_limits"]["route_alternate_mode_max_requests_per_generation"] == 6
    assert baseline["settings"]["provider_budgets"]["geoapify_max_credits_per_generation"] == 100
    assert baseline["settings"]["reasoning_budgets"]["ai_itinerary_reasoning_max_candidates"] == 40
    assert baseline["code_constants"]["GEOGRAPHIC_SPREAD_THRESHOLD_KM"] == 8.0
    assert baseline["code_constants"]["PACE_TARGET_PER_DAY"] == {"relaxed": 2, "balanced": 3, "packed": 4}
    assert baseline["llm_providers_configured"] == {"groq": True, "gemini": False}
    assert "DAY_COMPOSITION_ENABLED" not in baseline["set_in_process_environment"]
    # plain data, and never a key
    assert json.loads(json.dumps(baseline)) == baseline
    assert _KEY not in json.dumps(baseline)

    # the comparison arm: the same capture, read from the settings the planner will use
    monkeypatch.setenv("DAY_COMPOSITION_ENABLED", "false")
    monkeypatch.setenv("ROUTE_RECOMPOSITION_ENABLED", "false")
    get_settings.cache_clear()
    arm = provenance_module.capture_configuration()
    assert arm["settings"]["quality_phase_flags"]["day_composition_enabled"] is False
    assert arm["settings"]["quality_phase_flags"]["route_recomposition_enabled"] is False
    assert {"DAY_COMPOSITION_ENABLED", "ROUTE_RECOMPOSITION_ENABLED"} <= set(arm["set_in_process_environment"])
    assert arm["configuration_sha256"] != baseline["configuration_sha256"]
    # nothing else moved between the two arms
    differing = {
        name
        for group, values in baseline["settings"].items()
        for name, value in values.items()
        if arm["settings"][group][name] != value
    }
    assert differing == {"day_composition_enabled", "route_recomposition_enabled"}
    get_settings.cache_clear()


def test_configuration_capture_is_all_or_nothing() -> None:
    names = [name for group in provenance_module.SETTING_GROUPS.values() for name in group]
    partial = SimpleNamespace(**{name: 1 for name in names if name != "route_recomposition_enabled"})
    with pytest.raises(provenance_module.ProvenanceCaptureError) as caught:
        provenance_module.capture_configuration(partial, environ={})
    assert "route_recomposition_enabled" in str(caught.value)

    assert provenance_module.problems(None) == ["no provenance recorded"]
    complete = _provenance()
    assert provenance_module.problems(complete) == []
    del complete["configuration"]["settings"]["quality_phase_flags"]["day_composition_enabled"]
    assert any("day_composition_enabled" in problem for problem in provenance_module.problems(complete))
    assert "source identity not captured" in provenance_module.problems({"configuration": _provenance()["configuration"]})


# -- source identity --------------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True, capture_output=True,
    )


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for relative, text in {
        ".gitignore": "__pycache__/\n",
        "backend/requirements.txt": "x==1\n",
        "backend/app/services/planner.py": "A = 1\n",
        "backend/app/tests/test_planner.py": "T = 1\n",
        "backend/scripts/runner.py": "R = 1\n",
        "docs/notes.md": "notes\n",
    }.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_source_fingerprint_covers_modified_and_untracked_code(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clean = provenance_module.capture_source(repo)
    assert len(clean["git_head"]) == 40 and len(clean["fingerprint_sha256"]) == 64
    assert clean["working_tree_dirty"] is False and clean["fingerprinted_paths_dirty"] is False
    assert clean["fingerprint_covers"]["file_count"] == 3  # planner.py, runner.py, requirements.txt
    assert provenance_module.capture_source(repo) == clean  # reproducible

    # an uncommitted edit of tracked code: same HEAD, different fingerprint
    (repo / "backend/app/services/planner.py").write_text("A = 2\n")
    edited = provenance_module.capture_source(repo)
    assert edited["git_head"] == clean["git_head"]
    assert edited["fingerprint_sha256"] != clean["fingerprint_sha256"]
    assert edited["working_tree_dirty"] is True and edited["modified_files"] == ["backend/app/services/planner.py"]
    assert set(edited["changed_file_sha256"]) == {"backend/app/services/planner.py"}

    # a NEW, untracked module (how the quality-phase modules exist today) is covered as well
    (repo / "backend/app/services/day_composition.py").write_text("C = 1\n")
    added = provenance_module.capture_source(repo)
    assert added["fingerprint_sha256"] not in (clean["fingerprint_sha256"], edited["fingerprint_sha256"])
    assert added["untracked_files"] == ["backend/app/services/day_composition.py"]
    assert added["fingerprint_covers"]["file_count"] == 4

    # what cannot change a plan does not move the fingerprint: tests, docs, ignored files
    (repo / "backend/app/tests/test_planner.py").write_text("T = 2\n")
    (repo / "docs/notes.md").write_text("more\n")
    (repo / "backend/app/services/__pycache__").mkdir()
    (repo / "backend/app/services/__pycache__/planner.pyc").write_text("x")
    unrelated = provenance_module.capture_source(repo)
    assert unrelated["fingerprint_sha256"] == added["fingerprint_sha256"]
    assert unrelated["working_tree_dirty"] is True
    assert unrelated["modified_files"] == ["backend/app/services/planner.py"]

    # restoring the content restores the fingerprint; a deleted tracked file changes it
    (repo / "backend/app/services/planner.py").write_text("A = 1\n")
    (repo / "backend/app/services/day_composition.py").unlink()
    assert provenance_module.source_fingerprint(repo) == clean["fingerprint_sha256"]
    (repo / "backend/scripts/runner.py").unlink()
    assert provenance_module.source_fingerprint(repo) != clean["fingerprint_sha256"]


def test_source_capture_fails_outside_a_repository(tmp_path: Path) -> None:
    with pytest.raises(provenance_module.ProvenanceCaptureError):
        provenance_module.capture_source(tmp_path)


def test_this_repository_fingerprint_includes_the_quality_phase_modules() -> None:
    files = provenance_module.fingerprinted_files()
    for module in ("candidate_usefulness", "candidate_universe", "day_composition", "route_recomposition_service"):
        assert f"backend/app/services/{module}.py" in files
    assert "backend/scripts/benchmark/quality_v1.json" in files and "backend/scripts/quality_metrics.py" in files
    assert not [path for path in files if path.startswith("backend/app/tests/") or "__pycache__" in path]
    assert provenance_module.source_fingerprint() == provenance_module.source_fingerprint()


# -- the runner ---------------------------------------------------------------------------------


def test_quality_metric_outcome_is_explicit() -> None:
    outcome = runner.quality_metrics_outcome
    assert outcome(_report(), generation_failed=False) == {"status": "measured", "failure": None}
    assert outcome(_report(quality={"unavailable": "KeyError"}), generation_failed=False) == {
        "status": "extraction_failed", "failure": "KeyError",
    }
    assert outcome(_report(quality=None), generation_failed=False)["failure"] == "MissingQualityMetrics"
    assert outcome(_report(quality={"interests": {}}), generation_failed=False)["failure"] == "UnversionedQualityMetrics"
    assert outcome({}, generation_failed=True) == {"status": "not_measured_generation_failed", "failure": None}


def test_summary_reports_each_kind_of_failure_separately(tmp_path: Path) -> None:
    scenarios = _tuning(4)

    def run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
        if scenario["id"] == "Q-T1":
            return _report()
        if scenario["id"] == "Q-T2":
            raise RuntimeError(f"boom {_KEY}")
        if scenario["id"] == "Q-T3":  # the generation completed and passed; its figures could not be extracted
            return _report(quality={"unavailable": "KeyError"})
        return _report(outcome="FAIL")

    summary = _run(tmp_path, scenarios, run_scenario, all_scenarios=_tuning(6))
    assert summary["schema_version"] == 2 and summary["run_valid"] is True and summary["run_invalid_reasons"] == []
    assert summary["generation_failures"] == ["Q-T2"] == summary["technical_failures"]
    assert summary["baseline_hard_correctness_passed"] == ["Q-T1", "Q-T3"]
    assert summary["baseline_hard_correctness_failed"] == ["Q-T4"]
    assert summary["quality_metric_extraction_failures"] == [{"id": "Q-T3", "failure": "KeyError"}]
    # a passed baseline is not a measured quality result
    assert summary["quality_measured"] == ["Q-T1", "Q-T4"]
    assert summary["scenarios_not_run"] == ["Q-T5", "Q-T6"] and summary["scenarios_completed"] == 3
    assert "passed" not in summary and "score" not in summary and "overall" not in summary

    by_id = {record["id"]: record for record in summary["records"]}
    assert by_id["Q-T3"]["status"] == "completed" and by_id["Q-T3"]["quality_metrics"]["status"] == "extraction_failed"
    assert by_id["Q-T2"]["quality_metrics"]["status"] == "not_measured_generation_failed"
    for record in summary["records"]:
        assert provenance_module.problems(record["provenance"]) == []
        assert record["provenance"]["configuration"]["settings"]["quality_phase_flags"]["day_composition_enabled"] is True
        assert record["provenance"]["source"]["fingerprint_sha256"] == "f" * 64
    assert summary["provenance"]["configuration"] == summary["records"][0]["provenance"]["configuration"]
    written = "".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file())
    assert _KEY not in written and "boom" not in written
    assert "quality metrics: extraction_failed" in (tmp_path / "scenarios" / f"{runner.scenario_slug(scenarios[2])}.txt").read_text()

    # --resume reruns what was not measured: the failed generation AND the unmeasured scenario
    calls: list[str] = []
    _run(tmp_path, scenarios, lambda scenario: calls.append(scenario["id"]) or _report(), resume=True)
    assert calls == ["Q-T2", "Q-T3"]


def test_nothing_runs_without_a_complete_provenance(tmp_path: Path) -> None:
    calls: list[str] = []
    incomplete = _provenance()
    del incomplete["configuration"]["settings"]["quality_phase_flags"]["route_recomposition_enabled"]
    for bad in (None, {}, incomplete, {"configuration": _provenance()["configuration"]}):
        with pytest.raises(provenance_module.ProvenanceCaptureError):
            _run(tmp_path / "out", _tuning(1), lambda scenario: calls.append(scenario["id"]) or _report(), provenance=bad)
    assert calls == [] and not (tmp_path / "out").exists()


def test_a_directory_mixing_arms_or_sources_is_marked_invalid(tmp_path: Path) -> None:
    scenarios = _tuning(2)
    _run(tmp_path, scenarios[:1], lambda scenario: _report(), all_scenarios=scenarios)
    # the comparison arm written into the SAME directory by mistake
    other_arm = _provenance(day_composition_enabled=False, route_recomposition_enabled=False)
    summary = _run(tmp_path, scenarios[1:], lambda scenario: _report(), all_scenarios=scenarios, provenance=other_arm)
    assert summary["run_valid"] is False and summary["provenance"] is None
    assert summary["run_invalid_reasons"] == ["scenarios were run with different effective configurations"]
    # the per-scenario figures are still listed, never merged into a verdict
    assert summary["quality_measured"] == ["Q-T1", "Q-T2"]

    # a different source for one scenario
    other_source = _provenance()
    other_source["source"]["fingerprint_sha256"] = "e" * 64
    mixed = _run(tmp_path / "b", scenarios[:1], lambda scenario: _report(), all_scenarios=scenarios)
    assert mixed["run_valid"] is True
    mixed = _run(tmp_path / "b", scenarios[1:], lambda scenario: _report(), all_scenarios=scenarios, provenance=other_source)
    assert mixed["run_invalid_reasons"] == ["scenarios were run from different source fingerprints"]

    # the source changed while a scenario ran
    changed = _run(tmp_path / "c", scenarios[:1], lambda scenario: _report(), source_fingerprint=lambda: "d" * 64)
    assert changed["run_valid"] is False
    assert changed["run_invalid_reasons"] == ["Q-T1: the source changed while the scenario ran"]
    steady = _run(tmp_path / "d", scenarios[:1], lambda scenario: _report(), source_fingerprint=lambda: "f" * 64)
    assert steady["run_valid"] is True
    assert steady["records"][0]["provenance"]["source_unchanged_during_scenario"] is True

    # a record written before provenance existed makes its directory invalid too
    path = tmp_path / "d" / "scenarios" / f"{runner.scenario_slug(scenarios[0])}.json"
    stored = json.loads(path.read_text())
    del stored["record"]["provenance"]
    path.write_text(json.dumps(stored))
    legacy = runner.write_summary(
        tmp_path / "d", which=runner.SET_TUNING, scenarios=scenarios, start_date=date(2027, 3, 9), generated_at="now"
    )
    assert legacy["run_invalid_reasons"] == ["Q-T1: no provenance recorded"]


def _stub_live_run(monkeypatch: pytest.MonkeyPatch, report: Any) -> list[str]:
    """`main` with the live parts replaced: no environment change, no generation."""
    monkeypatch.setenv("GEOAPIFY_API_KEY", _KEY)
    monkeypatch.setenv("GROQ_API_KEY", _KEY)
    ran: list[str] = []
    monkeypatch.setattr(runner.canary_city, "_prepare_live_environment", lambda workdir: {})
    monkeypatch.setattr(runner.canary_city, "_run", lambda args, cache_hits: ran.append(args.city) or report())
    monkeypatch.setattr(runner.canary_city, "_render", lambda report: "detail")
    return ran


def test_main_does_not_start_when_provenance_cannot_be_captured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = _stub_live_run(monkeypatch, _report)

    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise provenance_module.ProvenanceCaptureError("settings not found: route_recomposition_enabled")

    monkeypatch.setattr(provenance_module, "capture", _fail)
    code = runner.main(["--start-date", "2027-03-09", "--out", str(tmp_path / "out"), "--scenarios", "Q-T2"])
    printed = capsys.readouterr().out
    assert code == 2 and "Nothing was run" in printed and "route_recomposition_enabled" in printed
    assert ran == [] and not (tmp_path / "out").exists() and _KEY not in printed


def test_main_records_provenance_and_does_not_report_unmeasured_as_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = _stub_live_run(monkeypatch, lambda: _report(quality={"unavailable": "KeyError"}))
    code = runner.main(
        ["--start-date", "2027-03-09", "--out", str(tmp_path / "out"), "--scenarios", "Q-T2", "--pause-seconds", "0"]
    )
    printed = capsys.readouterr().out
    assert len(ran) == 1 and code == 1  # baseline PASS, yet not a clean run: nothing was measured
    assert "Effective quality-phase flags: day_composition_enabled=True" in printed
    assert "quality-metric extraction failures: 1 | quality measured: 0" in printed and _KEY not in printed
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["baseline_hard_correctness_passed"] == ["Q-T2"] and summary["quality_measured"] == []
    source = summary["records"][0]["provenance"]["source"]
    assert source["fingerprint_sha256"] == provenance_module.source_fingerprint()
    assert summary["records"][0]["provenance"]["source_unchanged_during_scenario"] is True and summary["run_valid"] is True
