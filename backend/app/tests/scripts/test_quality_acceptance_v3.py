"""Final Batch 3: the third unseen acceptance set, its frozen protocol and its review tooling.

Nothing here runs a scenario, sends a request or reads a stored result."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_REPO_DIR = _BACKEND_DIR.parent
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
_V3_PATH = _SCRIPTS_DIR / "benchmark" / "quality_v3.json"
_PROTOCOL_PATH = _REPO_DIR / "evaluation" / "quality_v3" / "protocol.json"

# The frozen set (canonical JSON of its scenario list) and the frozen protocol (file bytes). A change
# to either after the freeze must be recorded in docs/37_final_quality_acceptance_v3.md section 10
# (protocol amendments) BEFORE the run; after the run neither may change at all.
_QUALITY_HOLDOUT_V3_SHA256 = "ff521eb3e851a55073a8989cbf84e3dafa7a2f9cd3b02450853b4b2823047384"
_PROTOCOL_SHA256 = "23a9204663522bbd5a0420fa7affe493086ad0e2ae6148e1cc8572900c3a693a"  # version 2
_PROTOCOL_V1_SHA256 = "19a39ecdc2ce5bf215b7e7166af18b539f10e170343af1b43f2de9b22e48650b"
_PROTOCOL_V1_FINGERPRINT = "b493b1c1bcae9fa16adfbbc5dcc9360a2620d42d9e3f2781cebecf628b0e8da2"
_PROTOCOL_DOCUMENT_SHA256 = "d3e8049c602b7ba3008fa53f76f20023260b97e13edcf9355b772ad620ca1a89"
_REVIEWER_INSTRUCTIONS_SHA256 = "64efbf796908fbce3dac5c7f170f6868f41b9c2e2588ce75bb4f97c0d5aa44ac"
_INDEPENDENCE_DECLARATION_SHA256 = "75c29d5e76190baa94b28b9980e6fd932ab8049916e5127016032df6e3fac71b"
_AMENDMENT_V2_SHA256 = "0ba0b2ae5f50bf2dd5adefccccb644b296f18f6d59c5655bbb49def4843ff3aa"


def _load(name: str) -> Any:
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(f"{name}_v3_under_test", _SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _city(destination: str) -> str:
    text = unicodedata.normalize("NFKD", destination.split(",")[0])
    return "".join(c for c in text if not unicodedata.combining(c)).strip().casefold()


def _scenarios() -> list[dict[str, Any]]:
    return json.loads(_V3_PATH.read_text(encoding="utf-8"))["quality_holdout_v3"]


def _protocol() -> dict[str, Any]:
    return json.loads(_PROTOCOL_PATH.read_text(encoding="utf-8"))


# -- the set ------------------------------------------------------------------------------------------


def test_the_set_is_frozen() -> None:
    canonical = json.dumps(_scenarios(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() == _QUALITY_HOLDOUT_V3_SHA256


def test_the_protocol_is_frozen_and_names_this_set() -> None:
    assert hashlib.sha256(_PROTOCOL_PATH.read_bytes()).hexdigest() == _PROTOCOL_SHA256
    protocol = _protocol()
    assert protocol["set"]["sha256"] == _QUALITY_HOLDOUT_V3_SHA256
    assert protocol["set"]["scenario_count"] == len(_scenarios()) == 10


def test_the_review_procedure_documents_are_frozen() -> None:
    """The protocol document and the reviewer's instructions are part of the frozen scoring procedure."""
    pins = {
        _REPO_DIR / "docs" / "37_final_quality_acceptance_v3.md": _PROTOCOL_DOCUMENT_SHA256,
        _REPO_DIR / "evaluation" / "quality_v3" / "REVIEWER_INSTRUCTIONS.md": _REVIEWER_INSTRUCTIONS_SHA256,
        _REPO_DIR / "evaluation" / "quality_v3" / "INDEPENDENCE_DECLARATION.md": _INDEPENDENCE_DECLARATION_SHA256,
        _REPO_DIR / "evaluation" / "quality_v3" / "AMENDMENT_v2.md": _AMENDMENT_V2_SHA256,
    }
    for path, pinned in pins.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == pinned, path.name


def test_amendment_v2_records_both_versions_and_changes_only_what_it_says() -> None:
    folder = _REPO_DIR / "evaluation" / "quality_v3"
    v1_path = folder / "protocol_v1.json"
    assert hashlib.sha256(v1_path.read_bytes()).hexdigest() == _PROTOCOL_V1_SHA256  # kept byte-identical
    v1, v2 = json.loads(v1_path.read_text(encoding="utf-8")), _protocol()
    assert (v1["protocol_version"], v2["protocol_version"]) == (1, 2)
    record = (folder / "AMENDMENT_v2.md").read_text(encoding="utf-8")
    for digest in (_PROTOCOL_V1_SHA256, _PROTOCOL_SHA256, _PROTOCOL_V1_FINGERPRINT, v2["source"]["fingerprint_sha256"],
                   v2["configuration"]["configuration_sha256"], _QUALITY_HOLDOUT_V3_SHA256):
        assert digest in record, digest
    assert v1["source"]["fingerprint_sha256"] == _PROTOCOL_V1_FINGERPRINT != v2["source"]["fingerprint_sha256"]
    # what an amendment may never change
    for key in ("set", "request_defaults", "dates", "configuration", "runtime", "acceptance"):
        assert v1[key] == v2[key], key
    assert v1["source"]["base_commit"] == v2["source"]["base_commit"]
    for key in ("presentation_seed", "packet_codes", "scale", "dimensions", "verified_factual_error", "second_reviewer"):
        assert v1["review"][key] == v2["review"][key], key
    assert v1["execution"]["max_resumes"] == v2["execution"]["max_resumes"]
    assert v1["execution"]["pause_seconds_between_scenarios"] == v2["execution"]["pause_seconds_between_scenarios"]
    # the two evaluation scripts carry the digests the record states
    for name in ("quality_acceptance_v3.py", "quality_review_packets.py"):
        assert hashlib.sha256((_SCRIPTS_DIR / name).read_bytes()).hexdigest() in record, name


def test_every_city_is_new_and_none_was_a_development_destination() -> None:
    history = json.loads((_SCRIPTS_DIR / "benchmark" / "cities.json").read_text(encoding="utf-8"))
    quality = json.loads((_SCRIPTS_DIR / "benchmark" / "quality_v1.json").read_text(encoding="utf-8"))
    quality_v2 = json.loads((_SCRIPTS_DIR / "benchmark" / "quality_v2.json").read_text(encoding="utf-8"))
    earlier = {_city(c) for c in (*history["tuning"], *history["holdout"])}
    earlier |= {_city(s["destination"]) for s in history["holdout_stress"]}
    earlier |= {_city(s["destination"]) for key in ("quality_tuning", "quality_holdout") for s in quality[key]}
    earlier |= {_city(s["destination"]) for s in quality_v2["quality_holdout_v2"]}
    # development destinations of the M2 and Final Batch 1 live runs
    earlier |= {"gothenburg", "porto", "london", "jersey city", "orlando", "vienna", "budapest", "edinburgh", "montreal"}
    cities = [_city(s["destination"]) for s in _scenarios()]
    assert len(set(cities)) == len(cities) == 10
    assert not set(cities) & earlier


def _names() -> set[str]:
    return {s["destination"].split(",")[0] for s in _scenarios()} | {m for s in _scenarios() for m in s["must_visit"]}


def test_no_name_of_the_set_is_in_production_code() -> None:
    sources = [p.read_text(encoding="utf-8") for p in (_BACKEND_DIR / "app").rglob("*.py") if "tests" not in p.parts]
    assert not [name for name in _names() if any(name in text for text in sources)]


def test_no_name_of_the_set_is_used_by_any_test_fixture_script_or_earlier_evaluation() -> None:
    """Unseen means unseen: no development script, earlier benchmark file, evaluation case or test of
    either service names a destination or must-visit of this set (only the set and its own tooling)."""
    own = {_V3_PATH.resolve(), Path(__file__).resolve()}
    roots = [
        (_BACKEND_DIR / "app", ("*.py", "*.json")),
        (_SCRIPTS_DIR, ("*.py", "*.json")),
        (_REPO_DIR / "evaluation", ("*.py", "*.json", "*.md")),
        (_REPO_DIR / "frontend" / "tests", ("*.mjs", "*.ts")),
        (_REPO_DIR / "frontend" / "e2e", ("*.ts", "*.json")),
    ]
    hits = []
    for root, patterns in roots:
        for pattern in patterns:
            for path in root.rglob(pattern):
                if path.resolve() in own or "quality_v3" in path.parts or "node_modules" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                hits += [(str(path.relative_to(_REPO_DIR)), name) for name in _names() if name in text]
    assert hits == []


def test_every_scenario_is_a_valid_request_with_distinct_tracked_interests() -> None:
    from app.services.place_taxonomy import canonical_interests

    for scenario in _scenarios():
        assert 1 <= scenario["trip_days"] <= 7, scenario["id"]  # MAX_TRIP_DAYS, inclusive
        assert scenario["pace"] in ("relaxed", "balanced", "packed"), scenario["id"]
        canonical = []
        for term in scenario["interests"]:
            assert canonical_interests([term]), (scenario["id"], term)
            canonical += canonical_interests([term])
        # two terms for one canonical interest would test vocabulary, not the plan
        assert len(canonical) == len(set(canonical)), scenario["id"]
        assert scenario["covers"], scenario["id"]


def test_the_set_covers_the_contract_categories_and_a_spread_of_requests() -> None:
    from app.services.place_taxonomy import canonical_interests

    interests = Counter(i for s in _scenarios() for term in s["interests"] for i in canonical_interests([term]))
    assert {"food", "waterfront", "history", "museum", "shopping", "outdoors", "architecture"} <= set(interests)
    assert set(Counter(s["pace"] for s in _scenarios())) == {"relaxed", "balanced", "packed"}
    assert {len(s["must_visit"]) for s in _scenarios()} >= {0, 1, 2}
    assert min(s["trip_days"] for s in _scenarios()) <= 2 and max(s["trip_days"] for s in _scenarios()) >= 5


# -- the protocol -------------------------------------------------------------------------------------


def test_the_frozen_configuration_is_render_yaml() -> None:
    render = yaml.safe_load((_REPO_DIR / "render.yaml").read_text(encoding="utf-8"))
    literal = {v["key"]: str(v["value"]) for v in render["services"][0]["envVars"] if "value" in v}
    assert _protocol()["configuration"]["environment"] == literal


def test_the_frozen_dates_are_consistent() -> None:
    dates = _protocol()["dates"]
    start = date.fromisoformat(dates["trip_start_date"])
    first, last = (date.fromisoformat(dates["execution_window"][key]) for key in ("first_day", "last_day"))
    assert start.weekday() == 0 and dates["trip_start_weekday"] == "Monday"
    assert date.fromisoformat(_protocol()["frozen_on"]) < first <= last <= start - timedelta(days=7)


def test_the_request_limits_are_the_release_caps() -> None:
    limits = _protocol()["execution"]["limits"]
    phase1 = _load("verify_m2_integrations")
    environment = _protocol()["configuration"]["environment"]
    assert limits["max_requests_by_service_per_scenario"]["geoapify"] == int(environment["GEOAPIFY_MAX_CREDITS_PER_GENERATION"])
    assert limits["max_llm_requests_per_scenario"] == phase1.LLM_REQUESTS_PER_GENERATION
    assert limits["max_requests_by_service_per_scenario"]["kiwi"] == 0
    assert limits["geoapify_credits_per_generation"] == int(environment["GEOAPIFY_MAX_CREDITS_PER_GENERATION"])
    # the per-attempt limits are version 1's, unchanged
    v1 = json.loads((_REPO_DIR / "evaluation" / "quality_v3" / "protocol_v1.json").read_text(encoding="utf-8"))
    for key in ("max_requests_by_service_per_scenario", "max_llm_requests_per_scenario", "geoapify_credits_per_generation", "allowed_hosts"):
        assert limits[key] == v1["execution"]["limits"][key], key


def test_the_cumulative_totals_are_the_worst_case_of_the_attempt_bound() -> None:
    """Ten completed attempts, plus at most one attempt that is not a result per session (1 + max_resumes
    sessions; three interruptions leave at most nine results): twelve attempts, each within its caps."""
    execution = _protocol()["execution"]
    limits, attempts = execution["limits"], execution["attempts"]
    scenarios, resumes = len(_scenarios()), execution["max_resumes"]
    assert resumes == 2 and attempts["max_attempts_per_scenario"] == 1 + resumes
    assert attempts["max_attempts_total"] == max(scenarios + resumes, scenarios - 1 + 1 + resumes) == 12
    for name, multiplier in (("totals_without_interruption", scenarios), ("cumulative_totals_at_most", attempts["max_attempts_total"])):
        totals = execution[name]
        for service, cap in limits["max_requests_by_service_per_scenario"].items():
            if service not in ("groq", "gemini"):
                assert totals[service] == cap * multiplier, (name, service)
        assert totals["llm_combined"] == limits["max_llm_requests_per_scenario"] * multiplier
        assert totals["geoapify_credits"] == limits["geoapify_credits_per_generation"] * multiplier
    assert {k: v for k, v in execution["cumulative_totals_at_most"].items() if k != "note"} == {
        "geoapify": 1200, "geoapify_credits": 1200, "llm_combined": 96, "open_meteo": 24, "nager_date": 36,
        "frankfurter": 24, "kiwi": 0}


def test_the_frozen_acceptance_rule_is_the_contracts_release_target() -> None:
    criteria = _protocol()["acceptance"]["criteria"]
    assert [c["id"] for c in criteria] == [
        "median_overall_at_least_4", "no_overall_below_3", "zero_hard_factual_safety_failures",
    ]
    assert _protocol()["acceptance"]["all_three_required"] is True


# -- every other way in refuses ------------------------------------------------------------------------


def test_the_quality_benchmark_cli_and_the_development_scripts_refuse_the_set() -> None:
    runner = _load("benchmark_quality")
    assert runner.SET_HOLDOUT_V3 in runner.FROZEN_SETS
    assert len(runner.scenarios_of(runner.load_data(), runner.SET_HOLDOUT_V3)) == 10
    assert runner.holdout_refusal(runner.SET_HOLDOUT_V3, allow_quality_holdout=True, scenario_ids=[], start=1, limit=None)
    with pytest.raises(SystemExit):  # not even a choice of its command line
        runner.main(["--set", runner.SET_HOLDOUT_V3, "--allow-quality-holdout", "--start-date", "2026-11-23"])
    canary = _load("canary_city")
    phase1 = _load("verify_m2_integrations")
    for scenario in _scenarios():
        assert canary._is_quality_holdout_city(scenario["destination"]), scenario["id"]
        assert phase1.normalize_city(scenario["destination"]) in phase1.frozen_destinations()


def test_the_protocol_runner_refuses_without_both_flags() -> None:
    acceptance = _load("quality_acceptance_v3")
    assert acceptance.main(["run"]) == 2
    assert acceptance.main(["run", "--allow-quality-holdout"]) == 2
    assert acceptance.main(["run", "--execute"]) == 2


def test_every_post_run_command_refuses_without_a_recorded_run(tmp_path: Path) -> None:
    acceptance = _load("quality_acceptance_v3")
    where = ["--out", str(tmp_path / "run"), "--audit-log", str(tmp_path / "audit.jsonl")]
    (tmp_path / "d.md").write_text("Reviewer: x\n", encoding="utf-8")
    (tmp_path / "returned").mkdir()
    (tmp_path / "s.csv").write_text("packet\n", encoding="utf-8")
    assert acceptance.main(["declare", "--declaration", str(tmp_path / "d.md"), *where]) == 2
    assert acceptance.main(["packets", *where]) == 2
    assert acceptance.main(["submit", "--scoresheet", str(tmp_path / "s.csv"), "--packets", str(tmp_path / "returned"), *where]) == 2
    assert acceptance.main(["score", *where]) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["d.md", "returned", "s.csv"]  # nothing was created


def test_preflight_fails_on_any_drift_and_sends_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    acceptance = _load("quality_acceptance_v3")
    import httpx

    def no_request(*_: Any, **__: Any) -> Any:
        raise AssertionError("preflight sent a request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_request)
    drifted = copy.deepcopy(_protocol())
    drifted["source"]["fingerprint_sha256"] = "0" * 64
    drifted["configuration"]["configuration_sha256"] = "0" * 64
    drifted["review"]["designated_reviewer"] = None
    (tmp_path / "audit.jsonl").write_text("not a record\n", encoding="utf-8")
    results = acceptance.preflight_checks(drifted, today=date(2027, 1, 1), out_dir=tmp_path,
                                          audit_path=tmp_path / "audit.jsonl", for_run=True, environ={})
    failed = set(acceptance.failed(results))
    assert {
        "today (UTC) is inside the execution window",
        "trip start date is the frozen one and still in the future",
        "source fingerprint equals the frozen one",
        "effective planner configuration equals the frozen one",
        "designated reviewer recorded in the protocol",
        "execution operator recorded in the protocol",
        "designated reviewer is not the execution operator",
        "provider credentials present (names only)",
        "result directory is new",
        "audit log is new",
    } <= failed
    resumed = acceptance.preflight_checks(drifted, today=date(2027, 1, 1), out_dir=tmp_path / "absent",
                                          audit_path=tmp_path / "audit.jsonl", resume=True, environ={})
    assert {"--resume continues an existing result directory", "audit log present and intact"} <= set(acceptance.failed(resumed))
    # a check reports a label, a count, a date or a digest -- never a credential
    assert all(not isinstance(result["observed"], str) or "KEY=" not in result["observed"] for result in results)


def test_the_frozen_configuration_is_what_the_frozen_environment_produces() -> None:
    """The configuration digest in the protocol is reproducible offline from the protocol's own environment."""
    acceptance = _load("quality_acceptance_v3")
    results = {r["check"]: r for r in acceptance.preflight_checks(_protocol(), today=date(2026, 10, 30))}
    assert results["effective planner configuration equals the frozen one"]["passed"]
    assert results["scenario set unchanged since the freeze"]["passed"]


# -- the interruption rule ------------------------------------------------------------------------------


class _Guard:
    """The request guard's interface (verify_m2_phase2.Guard), with no transport behind it."""

    def __init__(self) -> None:
        self.caps: dict[str, int] = {}
        self.llm_cap = 0
        self.sent: Counter = Counter()
        self.refused: Counter = Counter()
        self.statuses: Counter = Counter()
        self.stopped_by: str | None = None


_PROVENANCE: dict[str, Any] = {}


def _provenance() -> dict[str, Any]:
    if not _PROVENANCE:
        _PROVENANCE.update(_load("run_provenance").capture(_REPO_DIR))
    return _PROVENANCE


def _synthetic_scenarios(count: int = 4) -> list[dict[str, Any]]:
    return [{"id": f"S{i}", "destination": f"Testville {i}, Nowhere", "trip_days": 1, "pace": "balanced",
             "interests": ["history"], "must_visit": [], "covers": []} for i in range(1, count + 1)]


def _named_protocol(**totals: int) -> dict[str, Any]:
    protocol = copy.deepcopy(_protocol())
    protocol["review"]["designated_reviewer"] = "Reviewer A"
    protocol["execution"]["operator"] = "Operator B"
    protocol["execution"]["cumulative_totals_at_most"].update(totals)
    return protocol


class _Canary:
    """A stand-in generation. `script[id]` is the action of each successive attempt of that scenario:
    'ok' (a plan), 'stop' (the guard stops during it), 'fail' (a technical failure), 'die' (the process dies)."""

    def __init__(self, script: dict[str, list[str]] | None = None, geoapify: int = 5, llm: int = 2) -> None:
        self.script, self.geoapify, self.llm = script or {}, geoapify, llm
        self.calls: Counter = Counter()

    def bind(self, guard: _Guard) -> Any:
        def run(scenario: dict[str, Any]) -> dict[str, Any]:
            self.calls[scenario["id"]] += 1
            actions = self.script.get(scenario["id"], [])
            action = actions[self.calls[scenario["id"]] - 1] if self.calls[scenario["id"]] <= len(actions) else "ok"
            guard.sent["geoapify"] += self.geoapify
            guard.sent["groq"] += self.llm
            guard.statuses["geoapify:200"] += self.geoapify
            if action == "die":
                raise KeyboardInterrupt  # not an Exception: nothing below the session catches it
            if action == "stop":
                guard.statuses["geoapify:429"] += 1
                guard.stopped_by = "geoapify:429"
            if action == "fail":
                raise ValueError("a generation error")
            return {"acceptance": {"outcome": "PASS", "checks": []}, "itinerary_quality": {"schema_version": 6},
                    "provider_usage": {"total_geoapify_credits": self.geoapify + 2}}

        return run


def _session(acceptance: Any, tmp_path: Path, protocol: dict[str, Any], canary: _Canary, *, resume: bool,
             all_scenarios: list[dict[str, Any]] | None = None) -> int:
    guard = _Guard()  # a new process: a new guard
    return acceptance.run_session(
        out_dir=tmp_path / "run", audit=acceptance.AuditLog(tmp_path / "audit.jsonl"), protocol=protocol,
        protocol_sha256=acceptance.sha256_file(acceptance.PROTOCOL_PATH),
        all_scenarios=all_scenarios or _synthetic_scenarios(), guard=guard,
        run_canary=canary.bind(guard), load_state=lambda _: _state(), provenance=_provenance(), resume=resume,
        start_date=date(2026, 11, 23),
    )


def _attempt(tmp_path: Path, scenario_id: str, number: int, kind: str = "") -> Path:
    runner = sys.modules["benchmark_quality"]
    scenario = next(s for s in _synthetic_scenarios() if s["id"] == scenario_id)
    return tmp_path / "run" / f"attempts/{runner.scenario_slug(scenario)}/attempt-{number:02d}{'.' + kind if kind else ''}.json"


def _events(acceptance: Any, tmp_path: Path) -> list[str]:
    return [entry["event"] for entry in acceptance.AuditLog(tmp_path / "audit.jsonl").entries()]


def test_every_attempt_keeps_its_own_evidence_across_resumes(tmp_path: Path) -> None:
    acceptance = _load("quality_acceptance_v3")
    runner = sys.modules["benchmark_quality"]
    protocol = _named_protocol()
    canary = _Canary({"S1": ["fail"], "S2": ["stop", "ok"], "S3": ["stop", "ok"]})

    # session 1: S1 is a technical failure (a RESULT), S2 is interrupted, S3/S4 are never started
    assert _session(acceptance, tmp_path, protocol, canary, resume=False) == 1
    failures = {s["id"]: runner.load_record(tmp_path / "run", s)["technical_failure"] for s in _synthetic_scenarios()}
    assert failures == {"S1": "ValueError", "S2": acceptance.INTERRUPTED, "S3": acceptance.NOT_STARTED, "S4": acceptance.NOT_STARTED}
    assert canary.calls == Counter({"S1": 1, "S2": 1})  # nothing was sent for S3 or S4
    interrupted = json.loads(_attempt(tmp_path, "S2", 1).read_text(encoding="utf-8"))
    assert interrupted["outcome"] == "interrupted" and interrupted["interruption_reason"] == "geoapify:429"
    assert interrupted["requests_by_service"] == {"geoapify": 5, "groq": 2} and interrupted["llm_requests_combined"] == 2
    assert interrupted["geoapify_credits_reported"] == 7 and interrupted["provider_http_statuses"]["geoapify:429"] == 1
    assert interrupted["partial_output"]["provider_usage"] == {"total_geoapify_credits": 7}
    assert interrupted["started_at"] and interrupted["completed_at"] and interrupted["attempt"] == 1
    assert _attempt(tmp_path, "S2", 1, "started").is_file()
    assert not _attempt(tmp_path, "S3", 1).exists()
    first_evidence = _attempt(tmp_path, "S2", 1).read_bytes()
    with pytest.raises(FileExistsError):  # write-once
        acceptance.write_once(_attempt(tmp_path, "S2", 1), "{}")
    assert not os.access(_attempt(tmp_path, "S2", 1), os.W_OK)
    assert not (tmp_path / "run" / "MANIFEST.sha256").exists()

    # session 2 (resume 1): S2 now completes as attempt 2; S3 is interrupted; S4 not started; S1 is never rerun
    assert _session(acceptance, tmp_path, protocol, canary, resume=True) == 1
    assert canary.calls == Counter({"S1": 1, "S2": 2, "S3": 1})
    assert _attempt(tmp_path, "S2", 1).read_bytes() == first_evidence
    assert json.loads(_attempt(tmp_path, "S2", 2).read_text(encoding="utf-8"))["outcome"] == "result"

    # session 3 (resume 2): the rest completes; the manifest is written and entered in the audit log
    assert _session(acceptance, tmp_path, protocol, canary, resume=True) == 0
    assert canary.calls == Counter({"S1": 1, "S2": 2, "S3": 2, "S4": 1})
    assert acceptance.verify_manifest(tmp_path / "run") == []
    entries = acceptance.AuditLog(tmp_path / "audit.jsonl").entries()
    manifest = [e for e in entries if e["event"] == "run_manifest"][-1]
    assert manifest["sha256"] == acceptance.sha256_file(tmp_path / "run" / "MANIFEST.sha256")
    # cumulative accounting covers all six attempts, interrupted ones included; S1's technical failure
    # reported no credit count, so it is counted at the per-generation cap (100), never at zero
    assert manifest["cumulative_usage"] == {"attempts": 6, "requests_by_service": {"geoapify": 30},
                                           "llm_requests_combined": 12, "geoapify_credits": 5 * 7 + 100}
    assert [e["outcome"] for e in entries if e["event"] == "attempt_recorded"] == [
        "technical_failure", "interrupted", "result", "interrupted", "result", "result"]

    # a complete run is never resumed
    assert _session(acceptance, tmp_path, protocol, canary, resume=True) == 2
    assert canary.calls["S4"] == 1


def test_the_resume_limit_makes_the_run_void(tmp_path: Path) -> None:
    acceptance = _load("quality_acceptance_v3")
    protocol = _named_protocol()
    canary = _Canary({"S1": ["stop", "stop", "stop"]})
    assert _session(acceptance, tmp_path, protocol, canary, resume=False) == 1
    assert _session(acceptance, tmp_path, protocol, canary, resume=True) == 1
    assert _session(acceptance, tmp_path, protocol, canary, resume=True) == 1  # the third session: void
    assert _events(acceptance, tmp_path)[-1] == "run_void"
    assert _session(acceptance, tmp_path, protocol, canary, resume=True) == 2
    assert canary.calls == Counter({"S1": 3})  # never a fourth attempt
    assert all(_attempt(tmp_path, "S1", n).is_file() for n in (1, 2, 3))


def test_an_attempt_that_may_not_fit_the_cumulative_limits_is_never_started(tmp_path: Path) -> None:
    acceptance = _load("quality_acceptance_v3")
    # 80 requests per attempt against a 250-request total and the protocol's 100-request cap per attempt:
    # S1 (0+100) and S2 (80+100) fit; S3 (160+100 > 250) is refused before its first request
    protocol = _named_protocol(geoapify=250)
    canary = _Canary(geoapify=80)
    assert _session(acceptance, tmp_path, protocol, canary, resume=False) == 1
    assert canary.calls == Counter({"S1": 1, "S2": 1})
    runner = sys.modules["benchmark_quality"]
    s3 = runner.load_record(tmp_path / "run", _synthetic_scenarios()[2])
    assert s3["technical_failure"] == acceptance.BUDGET_EXHAUSTED
    events = _events(acceptance, tmp_path)
    assert "budget_exhausted" in events and events[-1] == "run_void"
    assert not _attempt(tmp_path, "S3", 1, "started").exists()
    assert _session(acceptance, tmp_path, protocol, canary, resume=True) == 2  # void: never resumed


def test_the_protocol_attempt_bound_holds_with_no_slack(tmp_path: Path) -> None:
    """With the real totals (12 x the caps), a pending scenario is refused once its full attempt would not fit."""
    acceptance = _load("quality_acceptance_v3")
    protocol = _named_protocol()
    caps = protocol["execution"]["limits"]
    full = {"attempts": 11, "requests_by_service": {"geoapify": 1100, "open_meteo": 22, "nager_date": 33, "frankfurter": 22},
            "llm_requests_combined": 88, "geoapify_credits": 1100}
    assert acceptance.budget_shortfall(full, 0, protocol) == []  # the twelfth full attempt fits exactly
    full["attempts"] = 12
    assert acceptance.budget_shortfall(full, 0, protocol) == ["attempts_total"]
    assert acceptance.budget_shortfall(acceptance._empty_usage(), 3, protocol) == ["attempts_per_scenario"]
    over = dict(full, attempts=0, geoapify_credits=1101)
    assert acceptance.budget_shortfall(over, 0, protocol) == ["geoapify_credits"]
    assert caps["geoapify_credits_per_generation"] == 100


def test_an_abandoned_attempt_is_counted_at_the_full_caps(tmp_path: Path) -> None:
    acceptance = _load("quality_acceptance_v3")
    protocol = _named_protocol()
    canary = _Canary({"S2": ["die"]})
    with pytest.raises(KeyboardInterrupt):  # the process dies during S2
        _session(acceptance, tmp_path, protocol, canary, resume=False)
    assert _attempt(tmp_path, "S2", 1, "started").is_file() and not _attempt(tmp_path, "S2", 1).exists()
    assert _session(acceptance, tmp_path, protocol, canary, resume=True) == 0
    note = json.loads(_attempt(tmp_path, "S2", 1, "abandoned").read_text(encoding="utf-8"))
    assert note["outcome"] == "abandoned" and note["counted_at_full_caps"] is True
    usage = [e for e in acceptance.AuditLog(tmp_path / "audit.jsonl").entries() if e["event"] == "run_manifest"][-1]["cumulative_usage"]
    caps = protocol["execution"]["limits"]
    assert usage["attempts"] == 5
    assert usage["requests_by_service"]["geoapify"] == 4 * 5 + caps["max_requests_by_service_per_scenario"]["geoapify"]
    assert usage["llm_requests_combined"] == 4 * 2 + caps["max_llm_requests_per_scenario"]
    assert usage["geoapify_credits"] == 4 * 7 + caps["geoapify_credits_per_generation"]


@pytest.mark.parametrize("tamper", ["edit_attempt", "delete_attempt", "add_attempt_file", "edit_audit", "truncate_audit_middle", "edit_record"])
def test_a_resume_refuses_any_tampered_evidence(tmp_path: Path, tamper: str) -> None:
    acceptance = _load("quality_acceptance_v3")
    protocol = _named_protocol()
    canary = _Canary({"S2": ["stop"]})
    assert _session(acceptance, tmp_path, protocol, canary, resume=False) == 1
    audit = tmp_path / "audit.jsonl"
    if tamper == "edit_attempt":
        path = _attempt(tmp_path, "S2", 1)
        os.chmod(path, 0o644)
        path.write_text(path.read_text(encoding="utf-8").replace('"geoapify": 5', '"geoapify": 0'), encoding="utf-8")
    elif tamper == "delete_attempt":
        _attempt(tmp_path, "S2", 1).unlink()
    elif tamper == "add_attempt_file":
        _attempt(tmp_path, "S2", 2).write_text("{}", encoding="utf-8")
    elif tamper == "edit_audit":
        lines = audit.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"session":1', '"session":9')
        audit.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif tamper == "truncate_audit_middle":
        lines = audit.read_text(encoding="utf-8").splitlines()
        audit.write_text("\n".join(lines[:2] + lines[3:]) + "\n", encoding="utf-8")
    elif tamper == "edit_record":  # S1's stored result, with no attempt behind the change
        runner = sys.modules["benchmark_quality"]
        path = tmp_path / "run" / "scenarios" / f"{runner.scenario_slug(_synthetic_scenarios()[1])}.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["record"]["technical_failure"] = None  # pretend the interrupted S2 had produced a result
        path.write_text(json.dumps(stored), encoding="utf-8")
    calls = Counter(canary.calls)
    assert _session(acceptance, tmp_path, protocol, canary, resume=True) == 2
    assert canary.calls == calls  # refused before anything was sent


def test_the_manifest_detects_any_change(tmp_path: Path) -> None:
    acceptance = _load("quality_acceptance_v3")
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    acceptance.write_manifest(tmp_path)
    assert acceptance.verify_manifest(tmp_path) == []
    (tmp_path / "scenarios" / "a.json").write_text('{"edited": true}', encoding="utf-8")
    (tmp_path / "late.txt").write_text("x", encoding="utf-8")
    assert set(acceptance.verify_manifest(tmp_path)) == {"scenarios/a.json", "late.txt"}


# -- blinded packets --------------------------------------------------------------------------------------


def _state() -> Any:
    from app.models.common import GeoPoint, ReadinessStatus
    from app.models.itinerary_narrative import ItineraryNarrativeDayOutput, ItineraryNarrativeReport
    from app.models.planning_state import (
        DailyPlan,
        DayVisitSchedule,
        DestinationContext,
        ExperienceItem,
        ExperiencePlan,
        InterestCoverageEntry,
        MustVisitStatusEntry,
        PlanningState,
        RestaurantSuggestion,
        TravelGroupType,
        TripRequest,
        ValidationIssue,
        ValidationReport,
        VenueEvidenceEntry,
    )
    from app.models.routing import RouteFeasibilityReport, RouteLegFeasibility

    day = date(2026, 11, 23)
    state = PlanningState(trip_request=TripRequest(
        primary_destination="Testville, Nowhere", start_date=day, end_date=day, travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE, interests=["history"], must_visit=["Old Fort"],
    ))
    state.destination_context = DestinationContext(destination_name="Testville")
    stops = [
        ExperienceItem(experience_id="e1", name="Vieux Fort", display_name="Old Fort", category="castle",
                       coordinates=GeoPoint(lat=10.123456, lng=20.654321), provider_place_id="secret-provider-id-1",
                       provider_source="geoapify", quality_tier="primary", start_time="09:30", end_time="10:15",
                       estimated_duration_minutes=45, visit_time_status="fits_listed_hours"),
        ExperienceItem(experience_id="e2", name="Town Museum", category="museum", provider_place_id="secret-provider-id-2",
                       provider_source="geoapify", quality_tier="good", visit_time_status="not_timed"),
    ]
    state.experience_plan = ExperiencePlan(daily_plans=[DailyPlan(
        day_number=1, date=day, experiences=stops, warnings=["A day note for the traveller."],
        restaurant_suggestions=[RestaurantSuggestion(name="Cafe One", data_status="live", why_suggested="near")],
        visit_schedule=DayVisitSchedule(window_start="09:30", window_end="18:00", status="partly_timed"),
    )])
    state.route_feasibility_report = RouteFeasibilityReport(
        status="success", provider="geoapify", route_data_source="geoapify_routing",
        legs=[RouteLegFeasibility(from_experience_id="e1", from_experience_name="Vieux Fort", to_experience_id="e2",
                                  to_experience_name="Town Museum", provider="geoapify", status="success",
                                  feasibility_status="feasible", distance_meters=1500.0, duration_seconds=1260.0,
                                  mode="walk", includes_ferry=True)],
    )
    state.validation_report = ValidationReport(
        readiness_status=ReadinessStatus.NEEDS_REVIEW,
        warnings=[ValidationIssue(severity="warning", category="visit_outside_listed_hours", message="Check the museum hours.")],
        review_codes=["VISIT_OUTSIDE_LISTED_HOURS"],
        review_code_classification={"visit_outside_listed_hours": "material"},
        interest_coverage=[InterestCoverageEntry(interest="history", requested_as=["history"], status="covered")],
        must_visit_status=[MustVisitStatusEntry(term="Old Fort", status="scheduled", place_name="Vieux Fort", day_number=1)],
        venue_evidence=[VenueEvidenceEntry(experience_id="e1", day_number=1, status="listed_open_on_day", hours_on_day=["09:00-18:00"])],
    )
    state.itinerary_narrative_report = ItineraryNarrativeReport(
        status="success", provider="groq", model="secret-model-name", summary="A short day in town.",
        daily_narratives=[ItineraryNarrativeDayOutput(day_number=1, date=day, title="Fort and museum", narrative="Start at the fort.")],
    )
    return state


def test_the_projection_keeps_only_what_the_traveller_is_shown() -> None:
    packets = _load("quality_review_packets")
    projection = packets.traveler_projection(_state())
    text = json.dumps(projection)
    for hidden in ("secret-provider-id", "geoapify", "groq", "secret-model-name", "primary", "quality_tier"):
        assert hidden not in text, hidden
    day = projection["days"][0]
    assert day["weekday"] == "Monday"
    assert day["stops"][0]["display_name"] == "Old Fort" and day["stops"][0]["hours_on_day"] == ["09:00-18:00"]
    assert day["legs"] == [{"from": "Vieux Fort", "to": "Town Museum", "verified": True, "mode": "walk",
                            "minutes": 21, "km": 1.5, "includes_ferry": True}]
    assert projection["findings"][0]["classification"] == "material"


def test_a_packet_is_blinded_and_its_order_is_fixed() -> None:
    packets = _load("quality_review_packets")
    scenario = {"id": "S1", "destination": "Testville, Nowhere", "trip_days": 1, "pace": "balanced",
                "interests": ["history"], "must_visit": ["Old Fort"], "covers": ["a hidden label"]}
    text = packets.render_packet("P01", scenario, packets.traveler_projection(_state()))
    # (the internal tier is checked on the projection; "primary" also occurs in the packet's own instructions)
    for hidden in ("S1", "a hidden label", "secret-provider-id", "groq", "secret-model-name", "PASS", "baseline",
                   "itinerary_quality", "quality_tier"):
        assert hidden not in text, hidden
    for shown in ("Old Fort (Vieux Fort)", "about 09:30–10:15 (45 min)", "no time estimated", "ferry and walk",
                  "21 min", "Check the museum hours.", "Some stops have no time", "Monday 2026-11-23", "Cafe One"):
        assert shown in text, shown
    assert text.count("|   |   |") >= 6  # six empty score cells, filled by a person
    order = packets.presentation_order([f"Q3-H{i}" for i in range(1, 11)], "seed")
    assert order == packets.presentation_order([f"Q3-H{i}" for i in range(10, 0, -1)], "seed")
    assert order != [f"Q3-H{i}" for i in range(1, 11)]


def test_a_request_with_no_plan_is_reviewed_as_no_plan() -> None:
    packets = _load("quality_review_packets")
    scenario = {"id": "S1", "destination": "Testville, Nowhere", "trip_days": 2, "pace": "relaxed",
                "interests": ["food"], "must_visit": [], "covers": []}
    assert "No itinerary was produced" in packets.render_packet("P02", scenario, None)


# -- the acceptance calculation ----------------------------------------------------------------------------


def _scoresheet(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    packets = _load("quality_review_packets")
    path = tmp_path / "scoresheet.csv"
    lines = [",".join(packets.SCORESHEET_COLUMNS)]
    lines += [",".join(row.get(column, "") for column in packets.SCORESHEET_COLUMNS) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _row(code: str, overall: str, **extra: str) -> dict[str, str]:
    row = {"packet": code, "attraction_usefulness": "4", "geographic_day_coherence": "4", "personalization": "4",
           "pace_realism": "4", "redundancy_avoidance": "4", "overall_would_use": overall,
           "verified_factual_errors": "0", "part_b_read_after_scoring": "yes", "reviewer": "Reviewer A",
           "scored_on": "2026-11-20"}
    row.update(extra)
    return row


def test_the_scoresheet_is_validated_against_the_scoring_procedure(tmp_path: Path) -> None:
    acceptance = _load("quality_acceptance_v3")
    protocol = {"review": {"designated_reviewer": "Reviewer A"}}
    path = _scoresheet(tmp_path, [_row("P01", "3.5"), _row("P02", "4", reviewer="Someone Else"),
                                  _row("P03", "4", part_b_read_after_scoring="no")])
    _, problems = acceptance.read_scoresheet(path, ["P01", "P02", "P03", "P04"], protocol)
    joined = " ".join(problems)
    assert "P01: overall_would_use must be a whole number from 1 to 5" in joined
    assert "P02: scored by someone other than the designated reviewer" in joined
    assert "P03: part_b_read_after_scoring must be 'yes'" in joined
    assert "exactly one row per packet" in joined


def test_the_release_target_needs_all_three_criteria() -> None:
    acceptance = _load("quality_acceptance_v3")
    unblinding = {f"P{i:02d}": f"Q3-H{i}" for i in range(1, 11)}
    passed = {"status": "completed", "baseline_hard_correctness": {"passed": True}}

    def scores(overall: list[int], errors: int = 0) -> dict[str, dict[str, Any]]:
        return {code: {"overall_would_use": value, "verified_factual_errors": errors if code == "P01" else 0}
                for code, value in zip(unblinding, overall)}

    records = {scenario_id: dict(passed) for scenario_id in unblinding.values()}
    met = acceptance.acceptance(records=records, run_valid=True, scores=scores([4] * 5 + [5] * 5), unblinding=unblinding)
    assert met["release_target_met"]
    # median 3.5 (5th and 6th of ten) does not meet >= 4
    low = acceptance.acceptance(records=records, run_valid=True, scores=scores([3] * 5 + [4] * 5), unblinding=unblinding)
    assert not low["criteria"]["median_overall_at_least_4"]["met"] and low["criteria"]["median_overall_at_least_4"]["observed"] == 3.5
    below = acceptance.acceptance(records=records, run_valid=True, scores=scores([5] * 9 + [2]), unblinding=unblinding)
    assert below["criteria"]["no_overall_below_3"]["observed"] == ["Q3-H10"] and not below["release_target_met"]
    human = acceptance.acceptance(records=records, run_valid=True, scores=scores([5] * 10, errors=1), unblinding=unblinding)
    assert not human["criteria"]["zero_hard_factual_safety_failures"]["met"]
    automated = dict(records, **{"Q3-H4": {"status": "completed", "baseline_hard_correctness": {"passed": False}}})
    auto = acceptance.acceptance(records=automated, run_valid=True, scores=scores([5] * 10), unblinding=unblinding)
    assert auto["criteria"]["zero_hard_factual_safety_failures"]["observed"]["automated_part_a_failures"] == ["Q3-H4"]
    invalid = acceptance.acceptance(records=records, run_valid=False, scores=scores([5] * 10), unblinding=unblinding)
    assert not invalid["release_target_met"]


def test_the_reviewer_bundle_holds_no_verdict_figure_or_scenario_identity(tmp_path: Path) -> None:
    packets = _load("quality_review_packets")
    scenarios = [{"id": "Q3-H1", "destination": "Testville, Nowhere", "trip_days": 1, "pace": "balanced",
                  "interests": ["history"], "must_visit": [], "covers": ["a hidden label"]}]
    record = {"id": "Q3-H1", "scenario": scenarios[0], "status": "completed",
              "baseline_hard_correctness": {"outcome": "FAIL", "failed_checks": ["zero duplicates"]},
              "quality_metrics": {"status": "measured"}, "itinerary_quality": {"schema_version": 6, "score_like": 0.73}}
    codes = packets.build_review_materials(scenarios=scenarios, records={"Q3-H1": record},
                                           projections={"Q3-H1": packets.traveler_projection(_state())},
                                           seed="s", out_dir=tmp_path / "review", instructions="the instructions\n")
    assert codes == {"P01": "Q3-H1"}
    bundle = tmp_path / "review" / packets.BUNDLE_DIR
    assert sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()) == [
        "REVIEWER_INSTRUCTIONS.md", "packets/P01.md", "scoresheet.csv"]
    for path in bundle.rglob("*.*"):
        text = path.read_text(encoding="utf-8")
        for hidden in ("Q3-H1", "a hidden label", "FAIL", "zero duplicates", "baseline", "itinerary_quality",
                       "score_like", "0.73", "measured", "unblinding"):
            assert hidden not in text, (path.name, hidden)
    operator = tmp_path / "review" / packets.OPERATOR_DIR
    after = (operator / "after_scoring" / "P01.md").read_text(encoding="utf-8")
    assert "FAIL" in after and "zero duplicates" in after
    assert json.loads((operator / "unblinding.json").read_text(encoding="utf-8")) == {"P01": "Q3-H1"}
    assert (bundle / "scoresheet.csv").read_text(encoding="utf-8").splitlines()[1].startswith("P01,")


# -- independence, review material, submission and scoring, end to end (offline) ---------------------------


def _declaration(reviewer: str = "Reviewer A", operator: str = "Operator B", signed: str | None = None,
                 on: str | None = None, untick: int | None = None) -> str:
    statements = _load("quality_acceptance_v3").DECLARATION_STATEMENTS
    lines = [f"Reviewer: {reviewer}", f"Execution operator: {operator}", f"Date: {on or _utc_today().isoformat()}", ""]
    lines += [f"- [{' ' if index == untick else 'x'}] {statement}" for index, statement in enumerate(statements)]
    lines += ["", f"Signed: {signed if signed is not None else reviewer}"]
    return "\n".join(lines) + "\n"


def _utc_today() -> date:
    """The runner dates the end of the run in UTC."""
    return datetime.now(timezone.utc).date()


def _completed_run(tmp_path: Path) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    acceptance = _load("quality_acceptance_v3")
    protocol = _named_protocol()
    assert _session(acceptance, tmp_path, protocol, _Canary(), resume=False) == 0
    return acceptance, protocol, _synthetic_scenarios()


def _audit(acceptance: Any, tmp_path: Path) -> Any:
    return acceptance.AuditLog(tmp_path / "audit.jsonl")


def _declare_and_build(acceptance: Any, tmp_path: Path, protocol: dict[str, Any], scenarios: list[dict[str, Any]]) -> Path:
    (tmp_path / "declaration.md").write_text(_declaration(), encoding="utf-8")
    assert acceptance.declare(tmp_path / "run", _audit(acceptance, tmp_path), protocol, tmp_path / "declaration.md",
                              all_scenarios=scenarios) == 0
    assert acceptance.build_packets(tmp_path / "run", _audit(acceptance, tmp_path), protocol, all_scenarios=scenarios) == 0
    return tmp_path / "run_review"


def _reviewer_returns(tmp_path: Path, review_dir: Path, overall: str = "4") -> tuple[Path, Path]:
    """The reviewer's copy of the bundle, filled in: a completed scoresheet and annotated packets."""
    packets = sys.modules["quality_review_packets"]
    returned = tmp_path / "returned"
    returned.mkdir()
    codes = []
    for issued in sorted((review_dir / packets.BUNDLE_DIR / "packets").glob("*.md")):
        codes.append(issued.stem)
        text = issued.read_text(encoding="utf-8").replace("| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? |   |   |",
                                                         "| 1 | Attraction usefulness | Are these places a visitor would be glad to have spent a slot on? | 4 | good places |")
        (returned / issued.name).write_text(text + "\nOther comments: fine.\n", encoding="utf-8")
    sheet = _scoresheet(tmp_path, [_row(code, overall) for code in codes])
    return sheet, returned


def test_the_whole_review_path_locks_and_scores_a_legitimate_submission(tmp_path: Path) -> None:
    acceptance, protocol, scenarios = _completed_run(tmp_path)
    review_dir = _declare_and_build(acceptance, tmp_path, protocol, scenarios)
    sheet, returned = _reviewer_returns(tmp_path, review_dir, overall="3")
    # the reviewer edits the scoresheet before submitting: expected, never corruption
    sheet = _scoresheet(tmp_path, [_row(f"P{i:02d}", "4") for i in range(1, 5)])
    audit = _audit(acceptance, tmp_path)
    assert acceptance.submit(tmp_path / "run", audit, protocol, sheet, returned, all_scenarios=scenarios) == 0
    locked = tmp_path / "run_submission" / "scoresheet.csv"
    assert locked.read_bytes() == sheet.read_bytes() and not os.access(locked, os.W_OK)
    sheet.write_text("edited after submission\n", encoding="utf-8")  # the reviewer's own copy no longer matters
    assert acceptance.score(tmp_path / "run", audit, protocol, all_scenarios=scenarios) == 0
    result = json.loads((tmp_path / "run_acceptance_result.json").read_text(encoding="utf-8"))
    assert result["release_target_met"] is True and result["scoresheet_sha256"] == acceptance.sha256_file(locked)
    # every digest is in the audit log, in order, and the chain is intact
    assert [e for e in _events(acceptance, tmp_path) if e not in ("attempt_started", "attempt_recorded", "session_started")] == [
        "run_manifest", "declaration_locked", "review_manifest", "submission_locked", "acceptance_recorded"]
    # a second submission or a different second result is refused
    assert acceptance.submit(tmp_path / "run", audit, protocol, sheet, returned, all_scenarios=scenarios) == 2
    assert acceptance.score(tmp_path / "run", audit, protocol, all_scenarios=scenarios) == 0  # same result: idempotent


@pytest.mark.parametrize("tamper", ["mapping", "mapping_and_manifest", "missing_packet", "modified_result",
                                    "locked_scoresheet", "after_scoring_file", "declaration"])
def test_scoring_refuses_any_altered_material(tmp_path: Path, tamper: str) -> None:
    acceptance, protocol, scenarios = _completed_run(tmp_path)
    review_dir = _declare_and_build(acceptance, tmp_path, protocol, scenarios)
    sheet, returned = _reviewer_returns(tmp_path, review_dir)
    audit = _audit(acceptance, tmp_path)
    assert acceptance.submit(tmp_path / "run", audit, protocol, sheet, returned, all_scenarios=scenarios) == 0
    packets = sys.modules["quality_review_packets"]
    mapping = review_dir / packets.OPERATOR_DIR / "unblinding.json"

    def writable(path: Path) -> Path:
        os.chmod(path, 0o644)
        return path

    if tamper.startswith("mapping"):
        codes = json.loads(mapping.read_text(encoding="utf-8"))
        first, second = list(codes)[:2]
        codes[first], codes[second] = codes[second], codes[first]  # two packets swapped
        mapping.write_text(json.dumps(codes, indent=1) + "\n", encoding="utf-8")
        if tamper == "mapping_and_manifest":
            acceptance.write_manifest(review_dir)  # someone rewrote the manifest too
    elif tamper == "missing_packet":
        (review_dir / packets.BUNDLE_DIR / "packets" / "P02.md").unlink()
    elif tamper == "modified_result":
        runner = sys.modules["benchmark_quality"]
        path = tmp_path / "run" / "scenarios" / f"{runner.scenario_slug(scenarios[0])}.json"
        path.write_text(path.read_text(encoding="utf-8").replace('"PASS"', '"PASS (edited)"'), encoding="utf-8")
    elif tamper == "locked_scoresheet":
        locked = writable(tmp_path / "run_submission" / "scoresheet.csv")
        locked.write_text(locked.read_text(encoding="utf-8").replace(",4,0,yes,", ",5,0,yes,"), encoding="utf-8")
    elif tamper == "after_scoring_file":
        (review_dir / packets.OPERATOR_DIR / "after_scoring" / "P01.md").write_text("edited\n", encoding="utf-8")
    elif tamper == "declaration":
        writable(tmp_path / "run_independence" / "declaration.md").write_text(_declaration(reviewer="Someone"), encoding="utf-8")
    assert acceptance.score(tmp_path / "run", audit, protocol, all_scenarios=scenarios) == 2
    assert not (tmp_path / "run_acceptance_result.json").exists()


def test_scoring_refuses_a_protocol_changed_after_the_run(tmp_path: Path) -> None:
    """The reviewer and operator are read from the protocol, so it must be the one the run started under."""
    acceptance, protocol, scenarios = _completed_run(tmp_path)
    review_dir = _declare_and_build(acceptance, tmp_path, protocol, scenarios)
    sheet, returned = _reviewer_returns(tmp_path, review_dir)
    audit = _audit(acceptance, tmp_path)
    assert acceptance.submit(tmp_path / "run", audit, protocol, sheet, returned, all_scenarios=scenarios,
                             protocol_sha256="e" * 64) == 2
    assert acceptance.submit(tmp_path / "run", audit, protocol, sheet, returned, all_scenarios=scenarios) == 0
    assert acceptance.score(tmp_path / "run", audit, protocol, all_scenarios=scenarios, protocol_sha256="e" * 64) == 2


def test_a_submission_is_locked_only_when_complete_and_matching_the_issued_packets(tmp_path: Path) -> None:
    acceptance, protocol, scenarios = _completed_run(tmp_path)
    review_dir = _declare_and_build(acceptance, tmp_path, protocol, scenarios)
    sheet, returned = _reviewer_returns(tmp_path, review_dir)
    audit = _audit(acceptance, tmp_path)
    packet = returned / "P03.md"
    original = packet.read_text(encoding="utf-8")
    packet.write_text(original.replace("## Request", "## Request\n\n- an edited line in the plan"), encoding="utf-8")
    assert acceptance.submit(tmp_path / "run", audit, protocol, sheet, returned, all_scenarios=scenarios) == 2
    (returned / "P04.md").unlink()
    packet.write_text(original, encoding="utf-8")
    assert acceptance.submit(tmp_path / "run", audit, protocol, sheet, returned, all_scenarios=scenarios) == 2
    assert not (tmp_path / "run_submission").exists()  # nothing locked; the reviewer may correct and resubmit
    again = tmp_path / "again"
    again.mkdir()
    sheet2, returned2 = _reviewer_returns(again, review_dir)
    incomplete = _scoresheet(tmp_path, [_row(f"P{i:02d}", "4") for i in range(1, 4)])
    assert acceptance.submit(tmp_path / "run", audit, protocol, incomplete, returned2, all_scenarios=scenarios) == 2
    assert acceptance.submit(tmp_path / "run", audit, protocol, sheet2, returned2, all_scenarios=scenarios) == 0


def test_packets_need_a_locked_declaration_by_an_independent_reviewer(tmp_path: Path) -> None:
    acceptance, protocol, scenarios = _completed_run(tmp_path)
    run, audit = tmp_path / "run", _audit(acceptance, tmp_path)
    assert acceptance.build_packets(run, audit, protocol, all_scenarios=scenarios) == 2  # no declaration yet
    bad = {
        "wrong reviewer": _declaration(reviewer="Someone Else"),
        "operator as reviewer": _declaration(reviewer="Operator B", operator="Operator B"),
        "not signed by the reviewer": _declaration(signed="Operator B"),
        "a statement not ticked": _declaration(untick=2),
        "dated in the future": _declaration(on=(_utc_today() + timedelta(days=2)).isoformat()),
        "dated before the run": _declaration(on="2020-01-01"),
    }
    for label, text in bad.items():
        (tmp_path / "declaration.md").write_text(text, encoding="utf-8")
        assert acceptance.declare(run, audit, protocol, tmp_path / "declaration.md", all_scenarios=scenarios) == 2, label
    assert not (tmp_path / "run_independence").exists()
    assert not (tmp_path / "run_review").exists()
    # an operator named as the reviewer in the protocol is refused already at preflight
    same = _named_protocol()
    same["execution"]["operator"] = "reviewer  a"
    failed = acceptance.failed(acceptance.preflight_checks(same, today=date(2026, 10, 30)))
    assert "designated reviewer is not the execution operator" in failed


def test_a_declaration_and_packets_need_a_complete_intact_run(tmp_path: Path) -> None:
    acceptance = _load("quality_acceptance_v3")
    protocol = _named_protocol()
    assert _session(acceptance, tmp_path, protocol, _Canary({"S1": ["stop"]}), resume=False) == 1
    (tmp_path / "declaration.md").write_text(_declaration(), encoding="utf-8")
    assert acceptance.declare(tmp_path / "run", _audit(acceptance, tmp_path), protocol, tmp_path / "declaration.md",
                              all_scenarios=_synthetic_scenarios()) == 2


def test_the_declaration_template_carries_exactly_the_statements_checked() -> None:
    acceptance = _load("quality_acceptance_v3")
    template = (_REPO_DIR / "evaluation" / "quality_v3" / "INDEPENDENCE_DECLARATION.md").read_text(encoding="utf-8")
    boxes = [line.split("] ", 1)[1].strip() for line in template.splitlines() if line.startswith("- [ ] ")]
    assert boxes == list(acceptance.DECLARATION_STATEMENTS)
    for field in ("Reviewer:", "Execution operator:", "Date:", "Signed:"):
        assert field in template
    # the blank template is not a declaration
    assert acceptance.check_declaration(template, _named_protocol(), not_before=date(2026, 1, 1), today=date(2026, 12, 1))
