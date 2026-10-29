"""Final Batch 3: the independent quality acceptance of the final corrected planner (evaluation tooling only).

The ONLY way to run the frozen, unseen set `quality-holdout-v3`
(`scripts/benchmark/quality_v3.json`). Everything it is allowed to do is
fixed in advance by the frozen protocol, `evaluation/quality_v3/protocol.json`
(explained in `docs/37_final_quality_acceptance_v3.md`): the scenarios, the
trip dates, the window in which the run may happen, the exact source
fingerprint and planner configuration, the request limits, the interruption
rule, the review procedure and the acceptance rule.

    cd backend
    python scripts/quality_acceptance_v3.py preflight              # offline: checks only, sends nothing
    python scripts/quality_acceptance_v3.py run --allow-quality-holdout --execute
    python scripts/quality_acceptance_v3.py run --allow-quality-holdout --execute --resume   # after an interruption
    python scripts/quality_acceptance_v3.py declare --declaration <signed declaration.md>    # offline
    python scripts/quality_acceptance_v3.py packets                # offline: blinded review material
    python scripts/quality_acceptance_v3.py submit --scoresheet <completed.csv> --packets <returned packets dir>
    python scripts/quality_acceptance_v3.py score                  # offline: the acceptance calculation

`run` refuses -- before any request -- unless every preflight check passes.
It is always the whole set: there is no option to choose scenarios. Each
scenario runs the real generation pipeline through the single-city canary
(`canary_city._run`), exactly as the earlier quality holdouts did, behind a
process-wide request guard (allowlisted hosts, per-attempt request caps,
stop on 401/403 or a data-provider 429).

Every ATTEMPT has its own write-once record under `attempts/` (a start marker
before its first request, then the final record), and every one is entered in
an append-only, hash-chained audit log kept OUTSIDE the result folders. An
attempt during which the guard stopped is not a result: its scenario, and
every scenario not yet run, is run again by `--resume` (at most `max_resumes`
times), and the interrupted attempt's evidence stays. Every other outcome --
including a technical failure and an INSUFFICIENT_VERIFIED_INVENTORY plan --
is the result, recorded once and never re-run. Cumulative request limits over
every attempt of every session are checked before each attempt; an attempt
that might not fit is never started, and the run is then void.

After the run: the reviewer's independence declaration is locked BEFORE any
review material exists; the reviewer receives only `reviewer_bundle/` (no
scenario id, no verdict, no Part B figure); the completed scoresheet and the
returned packets are locked by `submit` before anything is unblinded; and
`score` re-verifies every manifest, the packet mapping and every locked file
against the audit log before it computes anything.

Nothing here is imported by `backend/app`, and nothing here changes how a
plan is made. No key, URL, prompt or raw model response is written.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
_REPO_DIR = _BACKEND_DIR.parent
for _path in (str(_BACKEND_DIR), str(_SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import benchmark_quality as runner  # noqa: E402  (the record, summary and run loop the holdouts share)
import benchmark_tuning_cities as tuning  # noqa: E402  (the secret scrubber and file writers)
import quality_review_packets as packets  # noqa: E402
import run_provenance  # noqa: E402

PROTOCOL_PATH = _REPO_DIR / "evaluation" / "quality_v3" / "protocol.json"
INSTRUCTIONS_PATH = _REPO_DIR / "evaluation" / "quality_v3" / "REVIEWER_INSTRUCTIONS.md"
DATA_PATH = _SCRIPTS_DIR / "benchmark" / "quality_v3.json"
SET_NAME = runner.SET_HOLDOUT_V3
SET_KEY = "quality_holdout_v3"
DEFAULT_OUT = _REPO_DIR / "benchmark_results" / "quality_holdout_v3"
# Beside the result folders, never inside one: deleting or rewriting a result folder leaves it standing.
DEFAULT_AUDIT_LOG = _REPO_DIR / "benchmark_results" / "quality_holdout_v3.audit.jsonl"
MANIFEST_FILE = "MANIFEST.sha256"
TRAVELER_VIEW_DIR = "traveler_view"
ATTEMPTS_DIR = "attempts"
USAGE_FILE = "request_usage.json"
INTERRUPTED = "RunInterruptedByGuard"
NOT_STARTED = "RunNotStartedGuardStopped"
BUDGET_EXHAUSTED = "RunBudgetExhausted"
# Recorded in place of a result; such a scenario has none yet.
NOT_RESULTS = frozenset({INTERRUPTED, NOT_STARTED, BUDGET_EXHAUSTED})
_LLM_SERVICES = frozenset({"groq", "gemini"})
_REQUIRED_KEYS = ("GEOAPIFY_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY")
_GENESIS = "0" * 64

# The reviewer ticks every one of these in the signed declaration (evaluation/quality_v3/INDEPENDENCE_DECLARATION.md).
DECLARATION_STATEMENTS: tuple[str, ...] = (
    "I am not the execution operator of this evaluation.",
    "I have not developed, tuned or reviewed planner changes for this release.",
    "Before receiving the blinded packets I have seen no output of this evaluation, no automated verdict and no "
    "scenario identity (which packet is which scenario).",
    "I will not discuss the plans with anyone who worked on the planner until my scoresheet is submitted.",
)


class RunInterruptedByGuard(RuntimeError):
    """The request guard stopped during this attempt: not a result."""


class RunNotStartedGuardStopped(RuntimeError):
    """The guard had already stopped in this session: the attempt was never started and sent nothing."""


class RunBudgetExhausted(RuntimeError):
    """A full attempt no longer fits the cumulative limits: it was never started and sent nothing."""


class IntegrityError(RuntimeError):
    """An audit record, an attempt record or a manifest does not match. Fixed wording, no path or value."""


# -- the frozen inputs ------------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scenarios(path: Path = DATA_PATH) -> list[dict[str, Any]]:
    return [dict(scenario) for scenario in json.loads(path.read_text(encoding="utf-8"))[SET_KEY]]


def set_sha256(path: Path = DATA_PATH) -> str:
    """SHA-256 of the canonical JSON of the scenario list (the same construction as the earlier pins)."""
    canonical = json.dumps(scenarios(path), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def apply_frozen_environment(protocol: dict[str, Any], workdir: Path) -> None:
    """The release configuration (render.yaml's literal values, as frozen in the protocol), then the
    benchmark's own throwaway storage on top -- the same `_configure_environment` every holdout used."""
    os.environ.update({name: str(value) for name, value in protocol["configuration"]["environment"].items()})
    from benchmark_cities import _configure_environment

    _configure_environment(workdir)
    from app.core.config import get_settings

    get_settings.cache_clear()


def _person(name: Any) -> str:
    """A person's name as compared: whitespace collapsed, case ignored."""
    return " ".join(str(name or "").split()).casefold()


# -- write-once files and the audit log ----------------------------------------------------------------


def write_once(path: Path, text: str) -> str:
    """Creates `path` (never replaces one), flushes it to disk and makes it read-only. Returns its SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o444)
    return hashlib.sha256(data).hexdigest()


class AuditLog:
    """Append-only, hash-chained JSON lines: every entry carries `seq` and the SHA-256 of the previous line, so
    an edited, removed or reordered entry breaks the chain. It records digests, counts and fixed labels only."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> tuple[list[dict[str, Any]], str]:
        if not self.path.exists():
            return [], _GENESIS
        entries: list[dict[str, Any]] = []
        previous = _GENESIS
        for index, line in enumerate(self.path.read_text(encoding="utf-8").splitlines()):
            try:
                entry = json.loads(line)
            except ValueError:
                raise IntegrityError(f"audit log line {index + 1} is not a record") from None
            if not isinstance(entry, dict) or entry.get("seq") != index or entry.get("prev") != previous:
                raise IntegrityError(f"audit log chain broken at line {index + 1}")
            previous = _sha256_text(line)
            entries.append(entry)
        return entries, previous

    def entries(self) -> list[dict[str, Any]]:
        return self._read()[0]

    def head(self) -> str:
        return self._read()[1]

    def append(self, event: str, **fields: Any) -> str:
        entries, previous = self._read()
        entry = {"seq": len(entries), "at": tuning._utc_now(), "event": event, **fields, "prev": previous}
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return _sha256_text(line)


def _events(entries: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in entries if entry.get("event") == event]


def _last(entries: list[dict[str, Any]], event: str) -> dict[str, Any] | None:
    found = _events(entries, event)
    return found[-1] if found else None


# -- preflight (offline) ------------------------------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(_REPO_DIR), *args], capture_output=True, text=True, timeout=60, check=False)


def _pinned_requirements() -> list[str]:
    """Installed packages that differ from `backend/requirements.txt`'s `name==version` pins (names only)."""
    different: list[str] = []
    for raw in (_BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if "==" not in line or line.startswith("-"):
            continue
        name, version = (part.strip() for part in line.split("==", 1))
        name = name.split("[", 1)[0]
        try:
            if metadata.version(name) != version.split(";", 1)[0].strip():
                different.append(name)
        except metadata.PackageNotFoundError:
            different.append(name)
    return different


def preflight_checks(
    protocol: dict[str, Any],
    *,
    today: date | None = None,
    out_dir: Path | None = None,
    audit_path: Path | None = None,
    resume: bool = False,
    for_run: bool = False,
    environ: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Every condition the protocol sets before a run, as `{check, passed, observed}`. Sends nothing.
    `observed` is a fixed label, a count, a digest or a date -- never a credential, a name or a path."""
    environ = dict(os.environ) if environ is None else environ
    today = today or datetime.now(timezone.utc).date()
    results: list[dict[str, Any]] = []

    def check(name: str, passed: bool, observed: Any = None) -> None:
        results.append({"check": name, "passed": bool(passed), "observed": observed})

    # the set
    frozen_set = protocol["set"]
    actual_set = set_sha256()
    check("scenario set unchanged since the freeze", actual_set == frozen_set["sha256"], actual_set)
    check("scenario count", len(scenarios()) == frozen_set["scenario_count"], len(scenarios()))

    # dates
    dates = protocol["dates"]
    first, last = date.fromisoformat(dates["execution_window"]["first_day"]), date.fromisoformat(dates["execution_window"]["last_day"])
    check("today (UTC) is inside the execution window", first <= today <= last, today.isoformat())
    check("trip start date is the frozen one and still in the future",
          date.fromisoformat(dates["trip_start_date"]) > today, dates["trip_start_date"])

    # source
    source = protocol["source"]
    ancestor = _git("merge-base", "--is-ancestor", source["base_commit"], "HEAD").returncode == 0
    check("HEAD contains the frozen base commit", ancestor, source["base_commit"][:12])
    try:
        captured = run_provenance.capture_source(_REPO_DIR)
        check("working tree clean", not captured["working_tree_dirty"],
              "clean" if not captured["working_tree_dirty"] else "dirty")
        check("source fingerprint equals the frozen one",
              captured["fingerprint_sha256"] == source["fingerprint_sha256"], captured["fingerprint_sha256"])
    except run_provenance.ProvenanceCaptureError as exc:
        check("source identity captured", False, type(exc).__name__)

    # runtime
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    check("Python version", version == protocol["runtime"]["python"], version)
    different = _pinned_requirements()
    check("installed packages match backend/requirements.txt", not different, different or "all pinned versions")

    # configuration (computed in a throwaway environment copy; restored afterwards)
    saved = dict(os.environ)
    try:
        with tempfile.TemporaryDirectory(prefix="travelobligator_v3_preflight_") as workdir:
            apply_frozen_environment(protocol, Path(workdir))
            configuration = run_provenance.capture_configuration()
        check("effective planner configuration equals the frozen one",
              configuration["configuration_sha256"] == protocol["configuration"]["configuration_sha256"],
              configuration["configuration_sha256"])
    except Exception as exc:  # by TYPE only
        check("effective planner configuration captured", False, type(exc).__name__)
    finally:
        os.environ.clear()
        os.environ.update(saved)
        from app.core.config import get_settings

        get_settings.cache_clear()

    # people and credentials (presence only; no name is printed)
    reviewer, operator = protocol["review"].get("designated_reviewer"), protocol["execution"].get("operator")
    check("designated reviewer recorded in the protocol", bool(_person(reviewer)), "recorded" if _person(reviewer) else "missing")
    check("execution operator recorded in the protocol", bool(_person(operator)), "recorded" if _person(operator) else "missing")
    both_named = bool(_person(reviewer)) and bool(_person(operator))
    check("designated reviewer is not the execution operator", both_named and _person(reviewer) != _person(operator),
          ("different" if _person(reviewer) != _person(operator) else "same") if both_named else "not both recorded")
    missing = [name for name in _REQUIRED_KEYS if not environ.get(name)]
    check("provider credentials present (names only)", not missing, missing or "all present")

    # the result directory and the audit log
    if out_dir is not None:
        if resume:
            check("--resume continues an existing result directory", out_dir.exists(), "present" if out_dir.exists() else "absent")
        else:
            check("result directory is new", not out_dir.exists(), "new" if not out_dir.exists() else "exists")
        if for_run:
            # the review material may never exist before the run that it reviews
            check("no review material exists yet", not review_dir_of(out_dir).exists(), None)
    if audit_path is not None:
        if resume:
            try:
                intact = bool(AuditLog(audit_path).entries())
                observed = "intact" if intact else "absent"
            except IntegrityError:
                intact, observed = False, "broken"
            check("audit log present and intact", intact, observed)
        else:
            check("audit log is new", not audit_path.exists(), "new" if not audit_path.exists() else "exists")
    return results


def failed(results: list[dict[str, Any]]) -> list[str]:
    return [result["check"] for result in results if not result["passed"]]


# -- attempts and the cumulative budget ---------------------------------------------------------------------


def _attempt_file(scenario_id_slug: str, number: int, kind: str = "") -> str:
    """Result-folder-relative path of an attempt file (`kind`: '' final record, 'started', 'abandoned')."""
    suffix = f".{kind}" if kind else ""
    return f"{ATTEMPTS_DIR}/{scenario_id_slug}/attempt-{number:02d}{suffix}.json"


_ATTEMPT_EVENTS = ("attempt_started", "attempt_recorded", "attempt_abandoned")


def read_ledger(out_dir: Path, entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Every attempt the audit log knows, each with its final record (None if it never finished), and every
    mismatch: a file missing or changed since it was recorded, an attempt file the log does not know, or an
    attempt whose numbering or start is missing. A non-empty problem list means the run cannot continue."""
    slots: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in entries:
        if entry.get("event") in _ATTEMPT_EVENTS:
            slot = slots.setdefault((entry["scenario_id"], entry["attempt"]),
                                    {"scenario_id": entry["scenario_id"], "attempt": entry["attempt"], "record": None})
            slot[entry["event"]] = entry
    problems: list[str] = []
    referenced: set[str] = set()
    for (scenario_id, number), slot in sorted(slots.items()):
        if "attempt_started" not in slot:
            problems.append(f"{scenario_id} attempt {number}: no start in the audit log")
        for event in _ATTEMPT_EVENTS:
            entry = slot.get(event)
            if entry is None:
                continue
            referenced.add(entry["file"])
            path = out_dir / entry["file"]
            if not path.is_file():
                problems.append(f"missing: {entry['file']}")
            elif sha256_file(path) != entry["sha256"]:
                problems.append(f"changed since recorded: {entry['file']}")
            elif event == "attempt_recorded":
                slot["record"] = json.loads(path.read_text(encoding="utf-8"))
    for scenario_id in {key[0] for key in slots}:
        numbers = sorted(number for key, number in slots if key == scenario_id)
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append(f"{scenario_id}: attempt numbers are not 1..{len(numbers)}")
    attempts_root = out_dir / ATTEMPTS_DIR
    on_disk = {path.relative_to(out_dir).as_posix() for path in attempts_root.rglob("*") if path.is_file()} if attempts_root.exists() else set()
    problems += [f"not in the audit log: {name}" for name in sorted(on_disk - referenced)]
    return [slots[key] for key in sorted(slots)], problems


def ledger_problems(out_dir: Path, attempts: list[dict[str, Any]], all_scenarios: list[dict[str, Any]]) -> list[str]:
    """The stored scenario records against the attempts: a result is recorded by exactly one finished attempt,
    and a scenario with a finished result attempt has that result stored."""
    problems: list[str] = []
    for scenario in all_scenarios:
        finals = [a for a in attempts if a["scenario_id"] == scenario["id"] and (a["record"] or {}).get("outcome") in ("result", "technical_failure")]
        record = runner.load_record(out_dir, scenario)
        stored_result = record is not None and record.get("technical_failure") not in NOT_RESULTS
        if len(finals) > 1:
            problems.append(f"{scenario['id']}: more than one attempt produced a result")
        if finals and not stored_result:
            problems.append(f"{scenario['id']}: an attempt produced a result that is not stored; the run is void")
        if stored_result and not finals:
            problems.append(f"{scenario['id']}: a stored result has no recorded attempt")
    return problems


def _empty_usage() -> dict[str, Any]:
    return {"attempts": 0, "requests_by_service": {}, "llm_requests_combined": 0, "geoapify_credits": 0}


def _add_attempt_usage(usage: dict[str, Any], record: dict[str, Any] | None, protocol: dict[str, Any]) -> None:
    """Adds one attempt. An attempt without a final record (abandoned) counts at the full per-attempt caps."""
    limits = protocol["execution"]["limits"]
    requests = Counter(usage["requests_by_service"])
    if record is None:
        for service, cap in limits["max_requests_by_service_per_scenario"].items():
            if service not in _LLM_SERVICES:
                requests[service] += cap
        usage["llm_requests_combined"] += limits["max_llm_requests_per_scenario"]
        usage["geoapify_credits"] += limits["geoapify_credits_per_generation"]
    else:
        for service, count in record["requests_by_service"].items():
            if service not in _LLM_SERVICES:
                requests[service] += count
        usage["llm_requests_combined"] += record["llm_requests_combined"]
        usage["geoapify_credits"] += record["geoapify_credits_counted"]
    usage["requests_by_service"] = dict(sorted(requests.items()))
    usage["attempts"] += 1


def usage_of(attempts: list[dict[str, Any]], protocol: dict[str, Any]) -> dict[str, Any]:
    usage = _empty_usage()
    for attempt in attempts:
        _add_attempt_usage(usage, attempt["record"], protocol)
    return usage


def budget_shortfall(usage: dict[str, Any], scenario_attempts: int, protocol: dict[str, Any]) -> list[str]:
    """What one more FULL attempt would overrun (fixed labels; empty = it fits). An attempt is started only
    when every per-attempt cap still fits inside the cumulative limits, so the totals can never be passed."""
    execution = protocol["execution"]
    limits, totals, attempts = execution["limits"], execution["cumulative_totals_at_most"], execution["attempts"]
    short: list[str] = []
    for service, cap in limits["max_requests_by_service_per_scenario"].items():
        if service not in _LLM_SERVICES and cap and usage["requests_by_service"].get(service, 0) + cap > totals[service]:
            short.append(service)
    if usage["llm_requests_combined"] + limits["max_llm_requests_per_scenario"] > totals["llm_combined"]:
        short.append("llm_combined")
    if usage["geoapify_credits"] + limits["geoapify_credits_per_generation"] > totals["geoapify_credits"]:
        short.append("geoapify_credits")
    if usage["attempts"] + 1 > attempts["max_attempts_total"]:
        short.append("attempts_total")
    if scenario_attempts + 1 > attempts["max_attempts_per_scenario"]:
        short.append("attempts_per_scenario")
    return short


def to_run(out_dir: Path, all_scenarios: list[dict[str, Any]], *, resume: bool) -> list[dict[str, Any]]:
    """A fresh run: every scenario. A resume: the scenarios with no record, or whose record says no result was
    produced (interrupted, never started, or refused by the budget). A recorded result is never run again."""
    if not resume:
        return list(all_scenarios)
    return [scenario for scenario in all_scenarios if scenario["id"] in not_result_ids(out_dir, all_scenarios)]


def not_result_ids(out_dir: Path, all_scenarios: list[dict[str, Any]]) -> list[str]:
    return [
        scenario["id"]
        for scenario in all_scenarios
        if (record := runner.load_record(out_dir, scenario)) is None or record.get("technical_failure") in NOT_RESULTS
    ]


def write_manifest(out_dir: Path) -> str:
    """SHA-256 of every file of a folder, written before anyone reads it. Returns the manifest's own digest."""
    lines = [
        f"{sha256_file(path)}  {path.relative_to(out_dir).as_posix()}"
        for path in sorted(out_dir.rglob("*"))
        if path.is_file() and path.name != MANIFEST_FILE
    ]
    text = "\n".join(lines) + "\n"
    (out_dir / MANIFEST_FILE).write_text(text, encoding="utf-8")
    return _sha256_text(text)


def verify_manifest(out_dir: Path) -> list[str]:
    """Files whose content differs from the manifest, or that it does not list (empty = intact)."""
    manifest = out_dir / MANIFEST_FILE
    if not manifest.exists():
        return ["the folder has no manifest (it is incomplete or was interrupted)"]
    listed = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        listed[name] = digest
    present = {path.relative_to(out_dir).as_posix(): path for path in out_dir.rglob("*") if path.is_file() and path.name != MANIFEST_FILE}
    problems = [name for name, path in sorted(present.items()) if listed.get(name) != sha256_file(path)]
    problems += [f"missing: {name}" for name in sorted(set(listed) - set(present))]
    return problems


def _latest_state(store_path: Path, destination: str) -> Any:
    from app.models.planning_state import PlanningState

    data = json.loads(store_path.read_text(encoding="utf-8"))
    states = [PlanningState.model_validate(raw.get("state", raw)) for raw in (data.get("planning_states") or {}).values()]
    matches = [state for state in states if state.trip_request.primary_destination == destination]
    return max(matches, key=lambda state: state.metadata.updated_at) if matches else None


def make_scenario_runner(
    *,
    guard: Any,
    protocol: dict[str, Any],
    out_dir: Path,
    audit: AuditLog,
    session: int,
    attempts: list[dict[str, Any]],
    run_canary: Callable[[dict[str, Any]], dict[str, Any]],
    load_state: Callable[[str], Any],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Wraps one scenario attempt: the cumulative budget check, the per-attempt caps, the write-once start
    marker and final record (both entered in the audit log), the interruption rule, and the traveller-view
    projection written beside the result (before the result, whose presence marks it done). `attempts` is
    the verified ledger at the start of this session."""
    limits = protocol["execution"]["limits"]
    usage = usage_of(attempts, protocol)
    per_scenario = Counter(attempt["scenario_id"] for attempt in attempts)
    exhausted: list[str] = []

    def run_one(scenario: dict[str, Any]) -> dict[str, Any]:
        if guard.stopped_by is not None:
            raise RunNotStartedGuardStopped()
        number = per_scenario[scenario["id"]] + 1
        short = budget_shortfall(usage, number - 1, protocol)
        if short:
            if not exhausted:
                exhausted.extend(short)
                audit.append("budget_exhausted", session=session, scenario_id=scenario["id"], short=short)
            raise RunBudgetExhausted()
        slug = runner.scenario_slug(scenario)
        caps = dict(limits["max_requests_by_service_per_scenario"])
        started = {
            "scenario_id": scenario["id"], "attempt": number, "session": session, "started_at": tuning._utc_now(),
            "caps": caps, "llm_cap": limits["max_llm_requests_per_scenario"],
            "geoapify_credit_cap": limits["geoapify_credits_per_generation"],
        }
        started_file = _attempt_file(slug, number, "started")
        digest = write_once(out_dir / started_file, tuning._dump(started))
        audit.append("attempt_started", session=session, scenario_id=scenario["id"], attempt=number, file=started_file, sha256=digest)
        # counted from here on: if the process dies now, the next session counts this attempt at the full caps
        per_scenario[scenario["id"]] = number

        guard.caps = caps
        guard.llm_cap = limits["max_llm_requests_per_scenario"]
        for counter in (guard.sent, guard.refused, guard.statuses):
            counter.clear()
        report: dict[str, Any] | None = None
        failure: Exception | None = None
        try:
            report = run_canary(scenario)
        except Exception as exc:  # a technical failure is a result (unless the guard stopped); recorded by TYPE
            failure = exc
        sent = dict(guard.sent)
        reported = ((report or {}).get("provider_usage") or {}).get("total_geoapify_credits")
        outcome = "interrupted" if guard.stopped_by is not None else ("technical_failure" if failure is not None else "result")
        record = {
            "scenario_id": scenario["id"],
            "attempt": number,
            "session": session,
            "started_at": started["started_at"],
            "completed_at": tuning._utc_now(),
            "outcome": outcome,
            "interruption_reason": guard.stopped_by,
            "technical_failure": type(failure).__name__ if failure is not None else (report or {}).get("technical_failure"),
            "requests_by_service": sent,
            "llm_requests_combined": sum(count for service, count in sent.items() if service in _LLM_SERVICES),
            "geoapify_credits_reported": reported if isinstance(reported, int) else None,
            # an unknown credit count is taken at the per-generation cap, never at zero
            "geoapify_credits_counted": reported if isinstance(reported, int) else limits["geoapify_credits_per_generation"],
            "refused_locally": dict(guard.refused),
            "provider_http_statuses": dict(guard.statuses),
            # what the interrupted attempt produced, for audit; a result's own output is the scenario record
            "partial_output": report if outcome == "interrupted" else None,
        }
        final_file = _attempt_file(slug, number)
        digest = write_once(out_dir / final_file, tuning._dump(tuning.scrub(record)))
        audit.append("attempt_recorded", session=session, scenario_id=scenario["id"], attempt=number, outcome=outcome,
                     file=final_file, sha256=digest)
        _add_attempt_usage(usage, record, protocol)
        if outcome == "interrupted":
            raise RunInterruptedByGuard()
        if failure is not None:
            raise failure
        try:
            state = load_state(scenario["destination"])
            projection = packets.traveler_projection(state) if state is not None else {"unavailable": "NoStoredState"}
        except Exception as exc:  # a reporting extra; by TYPE only
            projection = {"unavailable": type(exc).__name__}
        (out_dir / TRAVELER_VIEW_DIR).mkdir(parents=True, exist_ok=True)
        tuning._write(
            out_dir / TRAVELER_VIEW_DIR / f"{slug}.json",
            tuning._dump(tuning.scrub(projection)),
        )
        return report

    return run_one


def _note_abandoned(out_dir: Path, audit: AuditLog, attempts: list[dict[str, Any]], all_scenarios: list[dict[str, Any]], session: int) -> None:
    """An attempt that started and never finished (the process died) gets a write-once note; it is already
    counted at the full caps."""
    slugs = {scenario["id"]: runner.scenario_slug(scenario) for scenario in all_scenarios}
    for attempt in attempts:
        if attempt["record"] is None and "attempt_abandoned" not in attempt:
            note_file = _attempt_file(slugs[attempt["scenario_id"]], attempt["attempt"], "abandoned")
            note = {"scenario_id": attempt["scenario_id"], "attempt": attempt["attempt"], "noted_in_session": session,
                    "noted_at": tuning._utc_now(), "outcome": "abandoned", "counted_at_full_caps": True}
            digest = write_once(out_dir / note_file, tuning._dump(note))
            audit.append("attempt_abandoned", session=session, scenario_id=attempt["scenario_id"], attempt=attempt["attempt"],
                         file=note_file, sha256=digest)
            attempt["attempt_abandoned"] = {"file": note_file, "sha256": digest}


def run_session(
    *,
    out_dir: Path,
    audit: AuditLog,
    protocol: dict[str, Any],
    protocol_sha256: str,
    all_scenarios: list[dict[str, Any]],
    guard: Any,
    run_canary: Callable[[dict[str, Any]], dict[str, Any]],
    load_state: Callable[[str], Any],
    provenance: dict[str, Any],
    resume: bool,
    start_date: date,
    pause_seconds: float = 0.0,
) -> int:
    """One session of the run: a fresh start or a resume, fail-closed against the audit log. 0 = the run is
    complete and its manifest is recorded; 1 = interrupted (resumable) or void; 2 = refused, nothing sent."""
    try:
        entries = audit.entries()
    except IntegrityError as exc:
        print(f"Refused: {exc}. Nothing was run.")
        return 2
    sessions = _events(entries, "session_started")
    max_resumes = protocol["execution"]["max_resumes"]
    if resume:
        if not sessions:
            print("Refused: the audit log records no run to resume. Nothing was run.")
            return 2
        if sessions[0]["protocol_sha256"] != protocol_sha256:
            print("Refused: the run was started under a different protocol. Nothing was run.")
            return 2
        if _last(entries, "run_manifest") or _last(entries, "run_void"):
            print("Refused: the run is already complete or void; it is never resumed. Nothing was run.")
            return 2
        if len(sessions) > max_resumes:
            audit.append("run_void", reason="resume_limit_reached")
            print("Refused: the protocol's resume limit is reached; the run is incomplete and void. Nothing was run.")
            return 2
        attempts, problems = read_ledger(out_dir, entries)
        problems += ledger_problems(out_dir, attempts, all_scenarios)
        if problems:
            print("Refused: the run's evidence does not match the audit log: " + "; ".join(problems[:5]) + ". Nothing was run.")
            return 2
    else:
        if entries or out_dir.exists():
            print("Refused: a fresh run needs a new result directory and a new audit log. Nothing was run.")
            return 2
        out_dir.mkdir(parents=True)
        attempts = []
    session = len(sessions) + 1
    if resume:
        _note_abandoned(out_dir, audit, attempts, all_scenarios, session)
    pending = to_run(out_dir, all_scenarios, resume=resume)
    usage = usage_of(attempts, protocol)
    if pending and budget_shortfall(usage, 0, protocol):
        audit.append("run_void", reason="cumulative_limit_reached", short=budget_shortfall(usage, 0, protocol))
        print("Refused: no further full attempt fits the cumulative limits; the run is incomplete and void. Nothing was run.")
        return 2
    configuration_id, source_id = run_provenance.identity(provenance)
    audit.append("session_started", session=session, protocol_sha256=protocol_sha256, resume=resume,
                 configuration_sha256=configuration_id, source_fingerprint_sha256=source_id,
                 pending=[scenario["id"] for scenario in pending], cumulative_usage_before=usage)

    run_one = make_scenario_runner(guard=guard, protocol=protocol, out_dir=out_dir, audit=audit, session=session,
                                   attempts=attempts, run_canary=run_canary, load_state=load_state)
    summary = runner.run_benchmark(
        pending, which=SET_NAME, all_scenarios=all_scenarios, out_dir=out_dir, start_date=start_date,
        run_scenario=run_one, provenance=provenance,
        source_fingerprint=lambda: run_provenance.source_fingerprint(_REPO_DIR),
        pause_seconds=pause_seconds,
    )
    attempts, problems = read_ledger(out_dir, audit.entries())
    problems += ledger_problems(out_dir, attempts, all_scenarios)
    usage = usage_of(attempts, protocol)
    tuning._write(out_dir / USAGE_FILE, tuning._dump({
        "cumulative": usage,
        "cumulative_totals_at_most": protocol["execution"]["cumulative_totals_at_most"],
        "attempts": [{"scenario_id": a["scenario_id"], "attempt": a["attempt"],
                      "outcome": (a["record"] or {}).get("outcome", "abandoned")} for a in attempts],
    }))
    remaining = not_result_ids(out_dir, all_scenarios)
    exhausted = any((runner.load_record(out_dir, s) or {}).get("technical_failure") == BUDGET_EXHAUSTED for s in all_scenarios)
    if problems:
        audit.append("run_void", reason="evidence_mismatch", session=session)
        print("VOID: the run's evidence does not match the audit log: " + "; ".join(problems[:5]))
        return 1
    if remaining:
        if exhausted or session > max_resumes:
            audit.append("run_void", reason="cumulative_limit_reached" if exhausted else "resume_limit_reached", session=session,
                         not_results=remaining)
            print(f"VOID: no result for {', '.join(remaining)} and no further attempt is allowed. No manifest was written.")
            return 1
        audit.append("session_ended", session=session, outcome="interrupted", not_results=remaining,
                     interruption_reason=guard.stopped_by)
        print(
            f"INTERRUPTED by the request guard ({guard.stopped_by}). Not results: {', '.join(remaining)}. "
            "Fix the cause, then run again with --resume. No manifest was written."
        )
        return 1
    digest = write_manifest(out_dir)
    audit.append("run_manifest", session=session, sha256=digest, scenarios_completed=summary["scenarios_completed"],
                 run_valid=bool(summary["run_valid"]), cumulative_usage=usage)
    print(
        f"Run complete: {summary['scenarios_completed']} of {summary['scenarios_in_set']} completed; "
        f"run valid: {summary['run_valid']}. Manifest {MANIFEST_FILE} sha256 {digest}. "
        "Nobody reads a plan before the review packets are built (`packets`)."
    )
    return 0


def execute(args: argparse.Namespace, protocol: dict[str, Any]) -> int:
    out_dir: Path = args.out
    checks = preflight_checks(protocol, out_dir=out_dir, audit_path=args.audit_log, resume=args.resume, for_run=True)
    if failed(checks):
        print("Refused before any request. Failed checks: " + "; ".join(failed(checks)))
        return 2

    import canary_city
    import verify_m2_integrations as phase1
    import verify_m2_phase2 as phase2

    workdir = Path(tempfile.mkdtemp(prefix="travelobligator_quality_v3_"))
    apply_frozen_environment(protocol, workdir)
    cache_hits = canary_city._prepare_live_environment(workdir)
    apply_frozen_environment(protocol, workdir)  # the canary's preparation must not have moved anything
    provenance = run_provenance.capture(_REPO_DIR)
    configuration_id, source_id = run_provenance.identity(provenance)
    if (configuration_id, source_id) != (protocol["configuration"]["configuration_sha256"], protocol["source"]["fingerprint_sha256"]):
        print("Refused: the prepared process does not match the frozen configuration and source. Nothing was run.")
        return 2

    limits = protocol["execution"]["limits"]
    guard = phase2.Guard(caps=dict(limits["max_requests_by_service_per_scenario"]),
                         llm_cap=limits["max_llm_requests_per_scenario"])
    phase1.install_guard(guard)
    phase2.install_httpx2_guard(guard)
    store_path = Path(os.environ["LOCAL_STORAGE_PATH"])
    start_date = date.fromisoformat(protocol["dates"]["trip_start_date"])
    travelers = protocol["request_defaults"]["travelers"]

    def run_canary(scenario: dict[str, Any]) -> dict[str, Any]:
        cache_hits.clear()
        return canary_city._run(
            argparse.Namespace(
                city=scenario["destination"], days=scenario["trip_days"], pace=scenario["pace"],
                interests=list(scenario["interests"]), must_visit=list(scenario["must_visit"]),
                start_date=start_date, origin=None, travelers=travelers,
            ),
            cache_hits,
        )

    audit = AuditLog(args.audit_log)
    code = run_session(
        out_dir=out_dir, audit=audit, protocol=protocol, protocol_sha256=sha256_file(PROTOCOL_PATH),
        all_scenarios=scenarios(), guard=guard, run_canary=run_canary,
        load_state=lambda destination: _latest_state(store_path, destination), provenance=provenance,
        resume=args.resume, start_date=start_date, pause_seconds=protocol["execution"]["pause_seconds_between_scenarios"],
    )
    _print_head(audit)
    return code


# -- integrity of the folders after the run ------------------------------------------------------------------


def review_dir_of(out_dir: Path) -> Path:
    return Path(f"{out_dir}_review")


def independence_dir_of(out_dir: Path) -> Path:
    return Path(f"{out_dir}_independence")


def submission_dir_of(out_dir: Path) -> Path:
    return Path(f"{out_dir}_submission")


def acceptance_path_of(out_dir: Path) -> Path:
    return Path(f"{out_dir}_acceptance_result.json")


def _verify_folder(folder: Path, entry: dict[str, Any] | None, label: str) -> list[str]:
    """A folder against its manifest, and its manifest against the digest the audit log recorded."""
    if entry is None:
        return [f"the audit log records no {label} manifest"]
    problems = [f"{label}: {problem}" for problem in verify_manifest(folder)]
    if not problems and sha256_file(folder / MANIFEST_FILE) != entry["sha256"]:
        problems.append(f"{label}: the manifest is not the one recorded in the audit log")
    return problems


def verify_run(out_dir: Path, entries: list[dict[str, Any]], all_scenarios: list[dict[str, Any]],
               protocol_sha256: str | None = None) -> list[str]:
    """The run folder, its attempts and the protocol: every later step reads the reviewer and the operator from
    the protocol, so it must be the one the run started under (an amendment after the run is never allowed)."""
    problems = _verify_folder(out_dir, _last(entries, "run_manifest"), "run")
    sessions = _events(entries, "session_started")
    if not sessions or sessions[0].get("protocol_sha256") != (protocol_sha256 or sha256_file(PROTOCOL_PATH)):
        problems.append("the protocol is not the one the run started under")
    if _last(entries, "run_void"):
        problems.append("the audit log records the run as void")
    attempts, ledger = read_ledger(out_dir, entries)
    return problems + ledger + ledger_problems(out_dir, attempts, all_scenarios)


def verify_declaration(out_dir: Path, entries: list[dict[str, Any]]) -> list[str]:
    entry = _last(entries, "declaration_locked")
    path = independence_dir_of(out_dir) / "declaration.md"
    if entry is None:
        return ["no independence declaration is locked"]
    if not path.is_file() or sha256_file(path) != entry["sha256"]:
        return ["the locked independence declaration is missing or changed"]
    return []


def verify_review(out_dir: Path, entries: list[dict[str, Any]], all_scenarios: list[dict[str, Any]], protocol: dict[str, Any]) -> list[str]:
    """The review folder against its manifest and the audit log, and the packet mapping against the one the
    frozen seed produces -- so a changed mapping is caught even if someone rewrote the manifest too."""
    entry = _last(entries, "review_manifest")
    review_dir = review_dir_of(out_dir)
    problems = _verify_folder(review_dir, entry, "review")
    mapping_path = review_dir / packets.OPERATOR_DIR / "unblinding.json"
    if not mapping_path.is_file():
        return problems + ["review: the packet mapping is missing"]
    if entry is not None and sha256_file(mapping_path) != entry.get("unblinding_sha256"):
        problems.append("review: the packet mapping is not the one recorded in the audit log")
    expected = packets.packet_codes([scenario["id"] for scenario in all_scenarios], protocol["review"]["presentation_seed"])
    if json.loads(mapping_path.read_text(encoding="utf-8")) != expected:
        problems.append("review: the packet mapping is not the one the frozen seed produces")
    return problems


def verify_submission(out_dir: Path, entries: list[dict[str, Any]]) -> list[str]:
    entry = _last(entries, "submission_locked")
    submission = submission_dir_of(out_dir)
    problems = _verify_folder(submission, None if entry is None else {"sha256": entry["manifest_sha256"]}, "submission")
    sheet = submission / "scoresheet.csv"
    if entry is not None and (not sheet.is_file() or sha256_file(sheet) != entry["scoresheet_sha256"]):
        problems.append("submission: the locked scoresheet is missing or changed")
    return problems


def _print_head(audit: AuditLog) -> None:
    try:
        print(f"Audit log head {audit.head()} -- record it outside this machine (docs/37 section 8).")
    except IntegrityError as exc:
        print(f"Audit log: {exc}.")


# -- the independence declaration -------------------------------------------------------------------------


_TICKED = re.compile(r"^\s*[-*]?\s*\[[xX]\]\s*(.+?)\s*$")


def check_declaration(text: str, protocol: dict[str, Any], *, not_before: date, today: date) -> list[str]:
    """The signed declaration against the protocol: the designated reviewer, the recorded operator (a different
    person), every statement ticked, signed by the reviewer, dated after the run and not in the future."""
    fields: dict[str, str] = {}
    ticked: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        for key in ("Reviewer", "Execution operator", "Date", "Signed"):
            if stripped.startswith(key + ":"):
                fields[key] = stripped.split(":", 1)[1].strip()
        if match := _TICKED.match(line):
            ticked.add(" ".join(match.group(1).split()))
    reviewer, operator = protocol["review"].get("designated_reviewer"), protocol["execution"].get("operator")
    problems: list[str] = []
    if not _person(reviewer) or _person(fields.get("Reviewer")) != _person(reviewer):
        problems.append("the declaration is not by the designated reviewer")
    if not _person(operator) or _person(fields.get("Execution operator")) != _person(operator):
        problems.append("the declaration does not name the recorded execution operator")
    if _person(fields.get("Reviewer")) and _person(fields.get("Reviewer")) == _person(fields.get("Execution operator")):
        problems.append("the reviewer and the execution operator are the same person")
    if not _person(fields.get("Signed")) or _person(fields.get("Signed")) != _person(fields.get("Reviewer")):
        problems.append("the declaration is not signed by the reviewer")
    try:
        signed_on = date.fromisoformat(fields.get("Date", ""))
        if not not_before <= signed_on <= today:
            problems.append("the declaration's date is not between the end of the run and today")
    except ValueError:
        problems.append("the declaration's date is not YYYY-MM-DD")
    for statement in DECLARATION_STATEMENTS:
        if " ".join(statement.split()) not in ticked:
            problems.append(f"not ticked: {statement}")
    return problems


def declare(out_dir: Path, audit: AuditLog, protocol: dict[str, Any], declaration: Path, *,
            all_scenarios: list[dict[str, Any]] | None = None, today: date | None = None,
            protocol_sha256: str | None = None) -> int:
    all_scenarios = scenarios() if all_scenarios is None else all_scenarios
    today = today or datetime.now(timezone.utc).date()
    try:
        entries = audit.entries()
    except IntegrityError as exc:
        print(f"Refused: {exc}.")
        return 2
    problems = verify_run(out_dir, entries, all_scenarios, protocol_sha256)
    if problems:
        print("Refused: the run is not complete and intact: " + "; ".join(problems[:5]))
        return 2
    if _last(entries, "declaration_locked") or independence_dir_of(out_dir).exists():
        print("Refused: an independence declaration is already locked.")
        return 2
    if _last(entries, "review_manifest") or review_dir_of(out_dir).exists():
        print("Refused: review material already exists; the declaration must come before it.")
        return 2
    text = declaration.read_text(encoding="utf-8")
    run_ended = date.fromisoformat(_last(entries, "run_manifest")["at"][:10])
    problems = check_declaration(text, protocol, not_before=run_ended, today=today)
    if problems:
        print("The declaration cannot be accepted:\n- " + "\n- ".join(problems))
        return 2
    digest = write_once(independence_dir_of(out_dir) / "declaration.md", text)
    audit.append("declaration_locked", sha256=digest)
    print(f"Independence declaration locked (sha256 {digest}), apart from the review material.")
    _print_head(audit)
    return 0


# -- review packets, submission and the acceptance calculation -----------------------------------------------


def build_packets(out_dir: Path, audit: AuditLog, protocol: dict[str, Any], *, all_scenarios: list[dict[str, Any]] | None = None,
                  protocol_sha256: str | None = None) -> int:
    all_scenarios = scenarios() if all_scenarios is None else all_scenarios
    try:
        entries = audit.entries()
    except IntegrityError as exc:
        print(f"Refused: {exc}.")
        return 2
    problems = verify_run(out_dir, entries, all_scenarios, protocol_sha256) + verify_declaration(out_dir, entries)
    if problems:
        print("Refused: " + "; ".join(problems[:5]))
        return 2
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    if not summary.get("run_valid") or summary.get("scenarios_not_run") or not_result_ids(out_dir, all_scenarios):
        print("Refused: the run is not a valid, complete run of the set; it cannot be reviewed for acceptance.")
        return 2
    records = {s["id"]: record for s in all_scenarios if (record := runner.load_record(out_dir, s)) is not None}
    projections: dict[str, dict[str, Any] | None] = {}
    for scenario in all_scenarios:
        path = out_dir / TRAVELER_VIEW_DIR / f"{runner.scenario_slug(scenario)}.json"
        projection = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        completed = records[scenario["id"]].get("status") == tuning.STATUS_COMPLETED
        if completed and (projection is None or "unavailable" in projection):
            print(f"Refused: {scenario['id']} completed but its traveller view is missing; report this, do not rerun.")
            return 2
        projections[scenario["id"]] = projection if completed else None
    review_dir = review_dir_of(out_dir)
    if review_dir.exists() or _last(entries, "review_manifest"):
        print("Refused: review material already exists for this run.")
        return 2
    codes = packets.build_review_materials(
        scenarios=all_scenarios, records=records, projections=projections,
        seed=protocol["review"]["presentation_seed"], out_dir=review_dir,
        instructions=INSTRUCTIONS_PATH.read_text(encoding="utf-8"),
    )
    digest = write_manifest(review_dir)
    audit.append("review_manifest", sha256=digest, packets=len(codes),
                 unblinding_sha256=sha256_file(review_dir / packets.OPERATOR_DIR / "unblinding.json"))
    print(f"Wrote {len(codes)} packets; review manifest sha256 {digest}.")
    print(f"Give the reviewer a COPY of {packets.BUNDLE_DIR}/ only. Keep {packets.OPERATOR_DIR}/ until `submit` has locked the scoresheet.")
    _print_head(audit)
    return 0


def parse_scoresheet(text: str, codes: list[str], protocol: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The completed scoresheet, validated against the protocol's scoring procedure."""
    problems: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != packets.SCORESHEET_COLUMNS:
        return {}, ["the scoresheet's columns are not the template's"]
    for row in reader:
        code = (row["packet"] or "").strip()
        if code in rows:
            problems.append(f"{code}: more than one row")
        rows[code] = row
    if sorted(rows) != sorted(codes):
        problems.append("the scoresheet must have exactly one row per packet")
    reviewer = protocol["review"].get("designated_reviewer")
    scored: dict[str, dict[str, Any]] = {}
    for code, row in rows.items():
        values: dict[str, Any] = {}
        for field, _, _ in packets.RUBRIC:
            value = (row.get(field) or "").strip()
            if value not in {"1", "2", "3", "4", "5"}:
                problems.append(f"{code}: {field} must be a whole number from 1 to 5")
                continue
            values[field] = int(value)
        errors = (row.get("verified_factual_errors") or "").strip()
        if not errors.isdigit():
            problems.append(f"{code}: verified_factual_errors must be a whole number (0 if none)")
        else:
            values["verified_factual_errors"] = int(errors)
        if (row.get("part_b_read_after_scoring") or "").strip().lower() != "yes":
            problems.append(f"{code}: part_b_read_after_scoring must be 'yes'")
        if not _person(reviewer) or _person(row.get("reviewer")) != _person(reviewer):
            problems.append(f"{code}: scored by someone other than the designated reviewer")
        try:
            date.fromisoformat((row.get("scored_on") or "").strip())
        except ValueError:
            problems.append(f"{code}: scored_on must be a date (YYYY-MM-DD)")
        scored[code] = values
    return scored, problems


def read_scoresheet(path: Path, codes: list[str], protocol: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    return parse_scoresheet(path.read_text(encoding="utf-8"), codes, protocol)


def submit(out_dir: Path, audit: AuditLog, protocol: dict[str, Any], scoresheet: Path, returned: Path, *,
           all_scenarios: list[dict[str, Any]] | None = None, protocol_sha256: str | None = None) -> int:
    """Locks the reviewer's final scoresheet and returned packets. Until this, editing the scoresheet is the
    reviewer's job and is never treated as corruption; from this on, nothing they hold can change the result."""
    all_scenarios = scenarios() if all_scenarios is None else all_scenarios
    try:
        entries = audit.entries()
    except IntegrityError as exc:
        print(f"Refused: {exc}.")
        return 2
    problems = verify_run(out_dir, entries, all_scenarios, protocol_sha256) + verify_declaration(out_dir, entries)
    problems += verify_review(out_dir, entries, all_scenarios, protocol)
    if problems:
        print("Refused: " + "; ".join(problems[:5]))
        return 2
    if _last(entries, "submission_locked") or submission_dir_of(out_dir).exists():
        print("Refused: a submission is already locked; it is never replaced.")
        return 2
    issued_dir = review_dir_of(out_dir) / packets.BUNDLE_DIR / "packets"
    codes = sorted(path.stem for path in issued_dir.glob("*.md"))
    text = scoresheet.read_text(encoding="utf-8")  # read ONCE: what is validated is what is locked
    _, problems = parse_scoresheet(text, codes, protocol)
    returned_texts = {path.name: path.read_text(encoding="utf-8") for path in sorted(returned.glob("*.md"))} if returned.is_dir() else {}
    if sorted(returned_texts) != [f"{code}.md" for code in codes]:
        problems.append("the returned packets must be exactly the issued ones, one file per packet code")
    for code in codes:
        issued = (issued_dir / f"{code}.md").read_text(encoding="utf-8")
        if f"{code}.md" in returned_texts and packets.issued_part(returned_texts[f"{code}.md"]) != packets.issued_part(issued):
            problems.append(f"{code}: the returned packet's plan is not the plan issued as {code}")
    if problems:
        print("Not locked -- the submission is not complete (the reviewer may correct it and submit again):\n- " + "\n- ".join(problems))
        return 2
    submission = submission_dir_of(out_dir)
    sheet_digest = write_once(submission / "scoresheet.csv", text)
    for name, packet_text in returned_texts.items():
        write_once(submission / "packets" / name, packet_text)
    manifest_digest = write_manifest(submission)
    os.chmod(submission / MANIFEST_FILE, 0o444)
    audit.append("submission_locked", scoresheet_sha256=sheet_digest, manifest_sha256=manifest_digest, packets=len(returned_texts))
    print(f"Submission locked: scoresheet sha256 {sheet_digest}. The reviewer may now receive {packets.OPERATOR_DIR}/after_scoring/.")
    _print_head(audit)
    return 0


def acceptance(
    *, records: dict[str, dict[str, Any]], run_valid: bool, scores: dict[str, dict[str, Any]], unblinding: dict[str, str]
) -> dict[str, Any]:
    """The contract's release target (docs/24 section 6), unchanged, with criterion 3 read as frozen in the
    protocol: every generation completed with the canary's baseline hard correctness passed, and the reviewer
    verified no factual error. The three criteria are reported separately; the target is met only if all are."""
    overall = [scores[code]["overall_would_use"] for code in sorted(scores)]
    median = statistics.median(overall) if overall else None
    automated_failures = sorted(
        scenario_id for scenario_id, record in records.items()
        if record.get("status") != tuning.STATUS_COMPLETED or not (record.get("baseline_hard_correctness") or {}).get("passed")
    )
    factual_errors = {unblinding[code]: values["verified_factual_errors"] for code, values in scores.items()
                      if values.get("verified_factual_errors")}
    criteria = {
        "median_overall_at_least_4": {"observed": median, "met": median is not None and median >= 4},
        "no_overall_below_3": {
            "observed": sorted(unblinding[code] for code, values in scores.items() if values["overall_would_use"] < 3),
            "met": bool(overall) and min(overall) >= 3,
        },
        "zero_hard_factual_safety_failures": {
            "observed": {"automated_part_a_failures": automated_failures, "human_verified_factual_errors": factual_errors},
            "met": run_valid and not automated_failures and not factual_errors,
        },
    }
    return {
        "run_valid": run_valid,
        "criteria": criteria,
        "release_target_met": all(criterion["met"] for criterion in criteria.values()),
        "scores_by_scenario": {unblinding[code]: values for code, values in sorted(scores.items())},
        "other_dimensions_have_no_threshold": True,
    }


def score(out_dir: Path, audit: AuditLog, protocol: dict[str, Any], *, all_scenarios: list[dict[str, Any]] | None = None,
          protocol_sha256: str | None = None) -> int:
    all_scenarios = scenarios() if all_scenarios is None else all_scenarios
    try:
        entries = audit.entries()
    except IntegrityError as exc:
        print(f"Refused: {exc}.")
        return 2
    problems = verify_run(out_dir, entries, all_scenarios, protocol_sha256) + verify_declaration(out_dir, entries)
    problems += verify_review(out_dir, entries, all_scenarios, protocol) + verify_submission(out_dir, entries)
    if problems:
        print("Refused -- nothing was scored:\n- " + "\n- ".join(problems[:8]))
        return 2
    unblinding = json.loads((review_dir_of(out_dir) / packets.OPERATOR_DIR / "unblinding.json").read_text(encoding="utf-8"))
    sheet = submission_dir_of(out_dir) / "scoresheet.csv"
    scores, problems = parse_scoresheet(sheet.read_text(encoding="utf-8"), list(unblinding), protocol)
    if problems:
        print("Refused: the locked scoresheet is not complete:\n- " + "\n- ".join(problems))
        return 2
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    records = {s["id"]: record for s in all_scenarios if (record := runner.load_record(out_dir, s)) is not None}
    result = acceptance(records=records, run_valid=bool(summary.get("run_valid")), scores=scores, unblinding=unblinding)
    result["scoresheet_sha256"] = sha256_file(sheet)
    result["protocol_sha256"] = protocol_sha256 or sha256_file(PROTOCOL_PATH)
    text = tuning._dump(result)
    result_path = acceptance_path_of(out_dir)
    if result_path.exists():
        if result_path.read_text(encoding="utf-8") != text:
            print("Refused: a different acceptance result is already recorded; it is never replaced.")
            return 2
    else:
        digest = write_once(result_path, text)
        audit.append("acceptance_recorded", sha256=digest, release_target_met=result["release_target_met"])
    for name, criterion in result["criteria"].items():
        print(f"{name}: {'MET' if criterion['met'] else 'NOT MET'} (observed: {criterion['observed']})")
    print(f"RELEASE TARGET {'MET' if result['release_target_met'] else 'NOT MET'}")
    _print_head(audit)
    return 0 if result["release_target_met"] else 1


# -- command line -------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="offline: every protocol check; sends nothing")
    run_parser = commands.add_parser("run", help="the whole set, once (live providers)")
    run_parser.add_argument("--allow-quality-holdout", action="store_true")
    run_parser.add_argument("--execute", action="store_true")
    run_parser.add_argument("--resume", action="store_true", help="continue after a guard interruption")
    declare_parser = commands.add_parser("declare", help="offline: lock the reviewer's signed independence declaration")
    declare_parser.add_argument("--declaration", type=Path, required=True)
    commands.add_parser("packets", help="offline: blinded review material")
    submit_parser = commands.add_parser("submit", help="offline: lock the completed scoresheet and returned packets")
    submit_parser.add_argument("--scoresheet", type=Path, required=True)
    submit_parser.add_argument("--packets", type=Path, required=True)
    commands.add_parser("score", help="offline: the acceptance calculation from the locked submission")
    for sub in commands.choices.values():
        sub.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the run's result directory")
        sub.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT_LOG, help="the append-only audit log")
    args = parser.parse_args(argv)
    protocol = load_protocol()

    if args.command == "preflight":
        results = preflight_checks(protocol, out_dir=args.out, audit_path=args.audit_log)
        for result in results:
            print(f"[{'PASS' if result['passed'] else 'FAIL'}] {result['check']}: {result['observed']}")
        print("Preflight " + ("PASSED" if not failed(results) else f"FAILED ({len(failed(results))} check(s))") + ". Nothing was sent.")
        return 0 if not failed(results) else 1
    if args.command == "run":
        if not (args.allow_quality_holdout and args.execute):
            print("Refusing to run the frozen set without --allow-quality-holdout --execute. Nothing was run.")
            return 2
        return execute(args, protocol)
    audit = AuditLog(args.audit_log)
    if args.command == "declare":
        return declare(args.out, audit, protocol, args.declaration)
    if args.command == "packets":
        return build_packets(args.out, audit, protocol)
    if args.command == "submit":
        return submit(args.out, audit, protocol, args.scoresheet, args.packets)
    return score(args.out, audit, protocol)


if __name__ == "__main__":
    raise SystemExit(main())
