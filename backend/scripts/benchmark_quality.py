"""Phase Q0 itinerary-quality benchmark (manual, live providers).

Runs the single-city canary (`canary_city._run`, the REAL generation
pipeline) once per scenario of `scripts/benchmark/quality_v1.json`, one
scenario at a time, and writes each scenario's canary report together with
its itinerary-quality figures (`quality_metrics`). The contract these
figures belong to is `docs/24_itinerary_quality_contract.md`. Benchmark
tooling only: nothing here is imported by `backend/app`, no planner logic
lives here, and there is no single overall score.

    cd backend
    python scripts/benchmark_quality.py --start-date 2026-11-10 --out ../benchmark_results/quality_tuning_run1
    python scripts/benchmark_quality.py --start-date 2026-11-10 --out ../benchmark_results/quality_tuning_run1 \
        --scenarios Q-T1 --scenarios Q-T4

    # ONCE, after the tuning freeze -- never while tuning:
    python scripts/benchmark_quality.py --set quality-holdout --allow-quality-holdout \
        --start-date 2026-11-10 --out ../benchmark_results/quality_holdout

`quality-tuning` (the default) may be run as often as needed. The
`quality-holdout` set is FROZEN and UNSEEN: it refuses to run without
`--allow-quality-holdout`, and it can only be run whole (no `--scenarios`,
`--start` or `--limit`; `--resume` continues an interrupted run), so it can
neither be touched by accident nor sampled a few cities at a time. There is
no "all" set.

Every scenario result records what produced it (`run_provenance`): the
EFFECTIVE planner configuration (feature flags, budgets, limits) and the
source identity (git HEAD, dirty status, a fingerprint of the working tree's
relevant files). A run whose provenance cannot be captured does not start.
The summary lists generation failures, baseline failures and quality-metric
extraction failures apart; only a scenario whose figures were extracted is
`quality_measured`. A directory that mixes configurations or sources is
marked `run_valid: false`. A second arm is the same command with other
settings in the environment and its OWN `--out` directory, e.g.

    DAY_COMPOSITION_ENABLED=false ROUTE_RECOMPOSITION_ENABLED=false \
        python scripts/benchmark_quality.py --start-date 2026-11-10 --out ../benchmark_results/quality_tuning_run1_q3q4_off

Two arms are never combined into one score.

Requires GEOAPIFY_API_KEY and at least one LLM provider (GROQ_API_KEY, or
GEMINI_API_KEY with GEMINI_MODEL). Storage is a throwaway Local JSON store
and a throwaway SQLite provider cache. Nothing written carries a key, a
URL, a prompt or a raw model response; `benchmark_results/` is git-ignored.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
_REPO_DIR = _BACKEND_DIR.parent
_DATA_PATH = _SCRIPTS_DIR / "benchmark" / "quality_v1.json"
sys.path.insert(0, str(_SCRIPTS_DIR))

import benchmark_tuning_cities as tuning  # noqa: E402  (the secret scrubber and file writers)
import canary_city  # noqa: E402  (the report builder and renderer this benchmark reuses)
import run_provenance  # noqa: E402  (effective configuration and source identity of a run)

# 2: every record carries its provenance (effective planner configuration,
# source identity) and says whether its quality figures were measured; the
# summary reports generation, baseline and metric-extraction failures apart.
SCHEMA_VERSION = 2

SET_TUNING = "quality-tuning"
SET_HOLDOUT = "quality-holdout"
_SET_KEYS = {SET_TUNING: "quality_tuning", SET_HOLDOUT: "quality_holdout"}

# What `baseline_hard_correctness` is -- and is not. It is NOT the whole of
# part A of the contract: A1-A3 are not acceptance checks during Q0.
BASELINE_HARD_CORRECTNESS_NOTE = (
    "the pre-Q1 canary acceptance baseline (existing factual-safety/infrastructure checks); A1-A3 are reported "
    "by itinerary_quality during Q0 and become enforceable after their Q1 implementation."
)

# The manual rubric of the contract (part C): 1-5 each, scored by a person.
RUBRIC_DIMENSIONS: tuple[str, ...] = (
    "attraction_usefulness",
    "geographic_day_coherence",
    "personalization",
    "pace_realism",
    "redundancy_avoidance",
    "overall_would_use",
)


# Whether a scenario's quality figures exist. A generation that completed but
# whose figures could not be extracted is NOT a measured quality result.
QUALITY_MEASURED = "measured"
QUALITY_EXTRACTION_FAILED = "extraction_failed"
QUALITY_NOT_MEASURED_GENERATION_FAILED = "not_measured_generation_failed"


def quality_metrics_outcome(report: dict[str, Any], *, generation_failed: bool) -> dict[str, Any]:
    """Whether `report` carries extracted quality figures, and if not, why
    (a fixed code or an exception TYPE -- never text)."""
    if generation_failed:
        return {"status": QUALITY_NOT_MEASURED_GENERATION_FAILED, "failure": None}
    figures = report.get("itinerary_quality")
    if not isinstance(figures, dict) or not figures:
        return {"status": QUALITY_EXTRACTION_FAILED, "failure": "MissingQualityMetrics"}
    if "unavailable" in figures:
        return {"status": QUALITY_EXTRACTION_FAILED, "failure": str(figures["unavailable"])}
    if not isinstance(figures.get("schema_version"), int):
        return {"status": QUALITY_EXTRACTION_FAILED, "failure": "UnversionedQualityMetrics"}
    return {"status": QUALITY_MEASURED, "failure": None}


def load_data(path: Path = _DATA_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scenarios_of(data: dict[str, Any], which: str) -> list[dict[str, Any]]:
    """The scenarios of ONE set, in file order. There is no combined set."""
    return [dict(scenario) for scenario in data[_SET_KEYS[which]]]


def holdout_refusal(
    which: str, *, allow_quality_holdout: bool, scenario_ids: list[str], start: int, limit: int | None
) -> str | None:
    """Why this invocation may not run, or None. The frozen holdout needs its
    explicit flag and is only ever run whole."""
    if which != SET_HOLDOUT:
        return None
    if not allow_quality_holdout:
        return (
            "Refusing to run the frozen quality holdout without --allow-quality-holdout "
            "(it is not run while tuning). Nothing was run."
        )
    if scenario_ids or start != 1 or limit is not None:
        return (
            "Refusing to run part of the frozen quality holdout: it is run whole, once "
            "(no --scenarios / --start / --limit; use --resume to continue an interrupted run). Nothing was run."
        )
    return None


def select_scenarios(
    scenarios: list[dict[str, Any]], scenario_ids: list[str] | None = None, start: int = 1, limit: int | None = None
) -> list[dict[str, Any]]:
    """The scenarios to run, always in file order."""
    selected = scenarios
    if scenario_ids:
        known = {scenario["id"] for scenario in scenarios}
        unknown = [scenario_id for scenario_id in scenario_ids if scenario_id not in known]
        if unknown:
            raise ValueError(f"not a scenario of this set: {', '.join(unknown)}")
        selected = [scenario for scenario in scenarios if scenario["id"] in set(scenario_ids)]
    if start < 1:
        raise ValueError("--start is a 1-based position")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")
    selected = selected[start - 1 :]
    return selected[:limit] if limit is not None else selected


def scenario_slug(scenario: dict[str, Any]) -> str:
    return canary_city._slug(f"{scenario['id']} {scenario['destination']}")


def build_record(
    scenario: dict[str, Any],
    report: dict[str, Any],
    *,
    which: str,
    run_timestamp: str,
    provenance: dict[str, Any],
    source_unchanged: bool | None = None,
) -> dict[str, Any]:
    """One scenario's result. Three things stay apart: the BASELINE hard
    correctness (the canary's existing acceptance -- see
    `BASELINE_HARD_CORRECTNESS_NOTE`; it does not include A1-A3 of the
    contract), the reported quality figures, and an EMPTY manual rubric for a
    person to fill in. `provenance` says which configuration and which source
    produced it; `quality_metrics` says whether the figures were measured."""
    acceptance = report.get("acceptance") if isinstance(report.get("acceptance"), dict) else {}
    failed = bool(report.get("technical_failure")) or not acceptance
    return {
        "schema_version": SCHEMA_VERSION,
        "set": which,
        "id": scenario["id"],
        "scenario": dict(scenario),
        "run_timestamp": run_timestamp,
        "provenance": {
            **provenance,
            # the source fingerprint taken again when the scenario finished (None: not checked)
            "source_unchanged_during_scenario": source_unchanged,
        },
        "quality_metrics": quality_metrics_outcome(report, generation_failed=failed),
        "status": tuning.STATUS_TECHNICAL_FAILURE if failed else tuning.STATUS_COMPLETED,
        "technical_failure": report.get("technical_failure") or ("IncompleteReport" if failed else None),
        "latency_seconds": (report.get("latency") or {}).get("total_generation_seconds"),
        "baseline_hard_correctness": {
            "note": BASELINE_HARD_CORRECTNESS_NOTE,
            "outcome": "FAIL" if failed else acceptance.get("outcome"),
            "passed": (not failed) and str(acceptance.get("outcome", "")).startswith("PASS"),
            "failed_checks": [check["check"] for check in acceptance.get("checks") or [] if not check.get("passed")],
        },
        "itinerary_quality": report.get("itinerary_quality"),
        "manual_rubric": {dimension: None for dimension in RUBRIC_DIMENSIONS},
    }


def load_record(out_dir: Path, scenario: dict[str, Any]) -> dict[str, Any] | None:
    try:
        stored = json.loads((out_dir / "scenarios" / f"{scenario_slug(scenario)}.json").read_text(encoding="utf-8"))
        record = stored["record"]
        return record if record.get("id") == scenario["id"] else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def is_measured(record: dict[str, Any] | None) -> bool:
    """A completed generation WITH extracted quality figures."""
    return (
        record is not None
        and record.get("status") == tuning.STATUS_COMPLETED
        and (record.get("quality_metrics") or {}).get("status") == QUALITY_MEASURED
    )


def is_completed(out_dir: Path, scenario: dict[str, Any]) -> bool:
    """Whether `--resume` may skip the scenario: its generation completed and
    its quality figures were measured (an unmeasured scenario is not done)."""
    return is_measured(load_record(out_dir, scenario))


def run_invalid_reasons(records: list[dict[str, Any]]) -> list[str]:
    """Why the results in a directory may not be read as ONE run (fixed
    wording, scenario ids only). Empty when every record carries a complete
    provenance, all of them the same configuration and source."""
    reasons: list[str] = []
    for record in records:
        for problem in run_provenance.problems(record.get("provenance")):
            reasons.append(f"{record['id']}: {problem}")
        if (record.get("provenance") or {}).get("source_unchanged_during_scenario") is False:
            reasons.append(f"{record['id']}: the source changed while the scenario ran")
    identities = {run_provenance.identity(record.get("provenance")) for record in records}
    if len({configuration for configuration, _ in identities}) > 1:
        reasons.append("scenarios were run with different effective configurations")
    if len({source for _, source in identities}) > 1:
        reasons.append("scenarios were run from different source fingerprints")
    return reasons


def write_summary(out_dir: Path, *, which: str, scenarios: list[dict[str, Any]], start_date: date, generated_at: str) -> dict[str, Any]:
    """Rebuilds summary.json from every scenario result in `out_dir` (file
    order). Counts only: the contract has no combined PASS. Generation
    failures, baseline failures and quality-metric extraction failures are
    listed apart, and only a scenario with extracted figures is `measured`."""
    records = [record for scenario in scenarios if (record := load_record(out_dir, scenario)) is not None]
    completed = [record for record in records if record["status"] == tuning.STATUS_COMPLETED]
    generation_failures = [record["id"] for record in records if record["status"] != tuning.STATUS_COMPLETED]
    invalid_reasons = run_invalid_reasons(records)
    shared = records[0].get("provenance") if records and not invalid_reasons else None
    summary = {
        "schema_version": SCHEMA_VERSION,
        "set": which,
        "generated_at": generated_at,
        "trip_start_date": start_date.isoformat(),
        # False: the directory mixes configurations or sources, or a record's
        # provenance is incomplete -- its figures must not be read as one run.
        "run_valid": not invalid_reasons,
        "run_invalid_reasons": invalid_reasons,
        # the one configuration and source every record shares (None when they differ)
        "provenance": (
            {key: value for key, value in shared.items() if key != "source_unchanged_during_scenario"}
            if isinstance(shared, dict)
            else None
        ),
        "scenarios_in_set": len(scenarios),
        "scenarios_not_run": [scenario["id"] for scenario in scenarios if load_record(out_dir, scenario) is None],
        "scenarios_completed": len(completed),
        "generation_failures": generation_failures,
        "technical_failures": generation_failures,  # the Q0 name of the same list, kept for older readers
        "baseline_hard_correctness_note": BASELINE_HARD_CORRECTNESS_NOTE,
        "baseline_hard_correctness_passed": [
            record["id"] for record in completed if record["baseline_hard_correctness"]["passed"]
        ],
        "baseline_hard_correctness_failed": [
            record["id"] for record in completed if not record["baseline_hard_correctness"]["passed"]
        ],
        # a completed generation whose quality figures could not be extracted
        "quality_metric_extraction_failures": [
            {"id": record["id"], "failure": (record.get("quality_metrics") or {}).get("failure")}
            for record in completed
            if not is_measured(record)
        ],
        "quality_measured": [record["id"] for record in completed if is_measured(record)],
        "manual_rubric": "not scored by this tool; see docs/24_itinerary_quality_contract.md part C",
        "records": records,
    }
    tuning._write(out_dir / "summary.json", tuning._dump(summary))
    return summary


def run_benchmark(
    scenarios: list[dict[str, Any]],
    *,
    which: str,
    all_scenarios: list[dict[str, Any]],
    out_dir: Path,
    start_date: date,
    run_scenario: Callable[[dict[str, Any]], dict[str, Any]],
    provenance: dict[str, Any],
    source_fingerprint: Callable[[], str] | None = None,
    resume: bool = False,
    pause_seconds: float = 0.0,
    render: Callable[[dict[str, Any]], str] | None = None,
    now: Callable[[], str] = tuning._utc_now,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Runs `run_scenario` for each scenario, strictly one after another,
    writing each result as soon as it finishes. One scenario's failure is
    recorded and the run continues. `provenance` (the effective configuration
    and the source identity of this process) is required and must be
    complete: without it nothing is run. `source_fingerprint`, when given, is
    taken again after each scenario to show the source did not change."""
    incomplete = run_provenance.problems(provenance)
    if incomplete:
        raise run_provenance.ProvenanceCaptureError("; ".join(incomplete))
    started_from = run_provenance.identity(provenance)[1]
    render = render or canary_city._render
    (out_dir / "scenarios").mkdir(parents=True, exist_ok=True)
    pending = [scenario for scenario in scenarios if not (resume and is_completed(out_dir, scenario))]
    for scenario in scenarios:
        if scenario not in pending:
            log(f"[skip] {scenario['id']}: already completed in {out_dir.name}")
    for index, scenario in enumerate(pending):
        run_timestamp, started = now(), time.monotonic()
        try:
            report = run_scenario(scenario)
        except Exception as exc:  # recorded by TYPE only -- never the exception text
            report = {
                "technical_failure": type(exc).__name__,
                "latency": {"total_generation_seconds": round(time.monotonic() - started, 1)},
            }
        source_unchanged: bool | None = None
        if source_fingerprint is not None:
            try:
                source_unchanged = source_fingerprint() == started_from
            except Exception:  # an unreadable source is not a confirmed one
                source_unchanged = False
        record = build_record(
            scenario, report, which=which, run_timestamp=run_timestamp, provenance=provenance,
            source_unchanged=source_unchanged,
        )
        try:
            detail = render(report)
        except Exception as exc:
            detail = f"(detailed canary report unavailable: {type(exc).__name__})"
        secrets = tuning._secret_values()
        slug = scenario_slug(scenario)
        configuration_id, source_id = run_provenance.identity(provenance)
        header = (
            f"QUALITY BENCHMARK ({which}): {scenario['id']} | run {run_timestamp} | trip start {start_date.isoformat()}\n"
            f"configuration {configuration_id} | source {source_id} | quality metrics: {record['quality_metrics']['status']}"
        )
        tuning._write(out_dir / "scenarios" / f"{slug}.txt", tuning._scrub_text(f"{header}\n\n{detail}", secrets) + "\n")
        # the JSON is written last: its presence is what marks the scenario as done
        tuning._write(
            out_dir / "scenarios" / f"{slug}.json",
            tuning._dump({"record": tuning.scrub(record, secrets), "canary_report": tuning.scrub(report, secrets)}),
        )
        log(
            f"[{index + 1}/{len(pending)}] {scenario['id']}: baseline hard correctness {record['baseline_hard_correctness']['outcome']}"
            f" | quality metrics {record['quality_metrics']['status']}"
            + (f" ({record['quality_metrics']['failure']})" if record["quality_metrics"]["failure"] else "")
            + f" | {record['latency_seconds']}s"
        )
        write_summary(out_dir, which=which, scenarios=all_scenarios, start_date=start_date, generated_at=now())
        if index < len(pending) - 1 and pause_seconds > 0:
            sleep(pause_seconds)
    return write_summary(out_dir, which=which, scenarios=all_scenarios, start_date=start_date, generated_at=now())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", dest="which", choices=[SET_TUNING, SET_HOLDOUT], default=SET_TUNING)
    parser.add_argument(
        "--allow-quality-holdout", action="store_true",
        help="required for --set quality-holdout (the frozen, unseen set; never while tuning)",
    )
    parser.add_argument(
        "--start-date", required=True, type=date.fromisoformat,
        help="trip start date (YYYY-MM-DD) used for EVERY scenario of the run",
    )
    parser.add_argument("--out", type=Path, default=None, help="result directory; reuse it across batches")
    parser.add_argument("--scenarios", action="append", help="scenario ids to run (tuning only); comma-separated and/or repeated")
    parser.add_argument("--start", type=int, default=1, help="1-based position of the first scenario (tuning only)")
    parser.add_argument("--limit", type=int, default=None, help="run at most N scenarios (tuning only)")
    parser.add_argument("--resume", action="store_true", help="skip scenarios already completed in --out")
    parser.add_argument("--pause-seconds", type=float, default=8.0, help="pause between scenarios (LLM rate limits)")
    args = parser.parse_args(argv)

    scenario_ids = [part.strip() for value in args.scenarios or [] for part in value.split(",") if part.strip()]
    # The holdout guard comes before everything else: no key, path or data is looked at first.
    refusal = holdout_refusal(
        args.which, allow_quality_holdout=args.allow_quality_holdout, scenario_ids=scenario_ids,
        start=args.start, limit=args.limit,
    )
    if refusal:
        print(refusal)
        return 2
    if args.resume and args.out is None:
        parser.error("--resume needs the --out directory of the run to continue")

    all_scenarios = scenarios_of(load_data(), args.which)
    try:
        selected = select_scenarios(all_scenarios, scenario_ids, args.start, args.limit)
    except ValueError as exc:
        parser.error(str(exc))
    if not selected:
        parser.error("no scenario selected")

    missing = canary_city._missing_environment()
    if missing:
        print("Missing required environment variable(s): " + ", ".join(missing) + ". Nothing was run.")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or _REPO_DIR / "benchmark_results" / f"{args.which.replace('-', '_')}_{stamp}"
    workdir = Path(tempfile.mkdtemp(prefix="travelobligator_quality_"))
    cache_hits = canary_city._prepare_live_environment(workdir)
    travelers = (load_data().get("defaults") or {}).get("travelers", 2)

    # What this run is: the planner configuration in effect and the source it
    # runs from. Without a complete capture no scenario is run -- a result
    # that cannot say what produced it is not a result.
    try:
        provenance = run_provenance.capture(_REPO_DIR)
    except run_provenance.ProvenanceCaptureError as exc:
        print(f"Run provenance could not be captured: {exc}. Nothing was run.")
        return 2
    flags = provenance["configuration"]["settings"]["quality_phase_flags"]
    source = provenance["source"]
    print("Effective quality-phase flags: " + ", ".join(f"{name}={value}" for name, value in flags.items()))
    print(
        f"Configuration {provenance['configuration']['configuration_sha256']} | git HEAD {source['git_head']}"
        f" | working tree {'DIRTY' if source['working_tree_dirty'] else 'clean'}"
        f" | source fingerprint {source['fingerprint_sha256']}"
    )

    def run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
        cache_hits.clear()  # per-scenario figures
        return canary_city._run(
            argparse.Namespace(
                city=scenario["destination"], days=scenario["trip_days"], pace=scenario["pace"],
                interests=list(scenario.get("interests", [])), must_visit=list(scenario.get("must_visit", [])),
                start_date=args.start_date, origin=None, travelers=travelers,
            ),
            cache_hits,
        )

    summary = run_benchmark(
        selected, which=args.which, all_scenarios=all_scenarios, out_dir=out_dir, start_date=args.start_date,
        run_scenario=run_scenario, provenance=provenance,
        source_fingerprint=lambda: run_provenance.source_fingerprint(_REPO_DIR),
        resume=args.resume, pause_seconds=args.pause_seconds,
    )
    print(
        f"\nCompleted {summary['scenarios_completed']} of {summary['scenarios_in_set']}"
        f" | generation failures: {len(summary['generation_failures'])}"
        f" | baseline hard correctness passed: {len(summary['baseline_hard_correctness_passed'])}"
        f" | failed: {len(summary['baseline_hard_correctness_failed'])}"
        f" | quality-metric extraction failures: {len(summary['quality_metric_extraction_failures'])}"
        f" | quality measured: {len(summary['quality_measured'])}"
    )
    if not summary["run_valid"]:
        print("RUN INVALID (do not read these results as one run): " + "; ".join(summary["run_invalid_reasons"]))
    print(f"Baseline = {BASELINE_HARD_CORRECTNESS_NOTE}")
    print("Itinerary quality figures are reported per scenario; the manual rubric is scored by a person.")
    print(f"Results: {out_dir / 'summary.json'}")
    ran = {scenario["id"] for scenario in selected}
    clean = (
        summary["run_valid"]
        and ran <= set(summary["baseline_hard_correctness_passed"])
        and ran <= set(summary["quality_measured"])
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
