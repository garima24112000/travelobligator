from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from app.core import performance
from app.models.generation_performance import GenerationPerformanceReport
from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider
from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.tests.core.test_generation_performance_1a import (  # noqa: F401 - `production_like_pipeline` is a fixture
    _canary_args,
    _load_canary,
    production_like_pipeline,
)
from app.tests.providers.llm_failover_support import (
    FakeClock,
    ScriptedClient,
    StatusError,
    configure_pair,
    install_clients,
    use_clock,
)
from app.tests.providers.test_groq_ai_candidate_proposal_provider import (
    _request as _anchor_request,
    _valid_output as _anchor_output,
)
from app.tests.providers.test_groq_ai_itinerary_reasoning_provider import (
    _request as _reasoning_request,
    _valid_output as _reasoning_output,
)
from app.tests.providers.test_narrator_structural_retry_202c1d import _VALID as _NARRATIVE, _request as _narrator_request
from app.tests.scripts.test_benchmark_tuning_cities_script_3a import _fake_report, bench

# Reporting semantics of the Groq <-> Gemini pair. A model stage's attempt
# total is PROVIDER-NEUTRAL (every provider's requests for the stage); who was
# actually called comes only from the per-provider request counters. Nothing
# may describe a request Gemini served as a Groq request. Reporting only: the
# generations below run through the real stage adapters with scripted clients.

_STAGES = ("anchor", "reasoning", "repair", "narrator")
_ZERO = {stage: 0 for stage in _STAGES}


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    use_clock(monkeypatch, fake)
    return fake


def _generation(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, *, groq: dict[str, list[Any]], gemini: dict[str, list[Any]]
) -> GenerationPerformanceReport:
    """Anchor, reasoning and narrator of one generation. `groq` / `gemini`
    give, per stage, what that provider answers (a value or an exception)."""
    stages = (
        ("anchor", GroqAICandidateProposalProvider, "propose", _anchor_request),
        ("reasoning", GroqAIItineraryReasoningProvider, "reason", _reasoning_request),
        ("narrator", GroqItineraryNarratorProvider, "narrate", _narrator_request),
    )
    recorder = performance.PerformanceRecorder()
    recorder.start()
    with performance.activate(recorder):
        for name, provider_cls, method, request in stages:
            install_clients(
                monkeypatch, provider_cls,
                groq=ScriptedClient(clock, *((0.5, step) for step in groq.get(name, []))),
                gemini=ScriptedClient(clock, *((0.5, step) for step in gemini.get(name, []))),
            )
            getattr(provider_cls(), method)(request())
    return GenerationPerformanceReport.model_validate(recorder.snapshot())


_ANSWERS = {"anchor": [_anchor_output()], "reasoning": [_reasoning_output()], "narrator": [_NARRATIVE]}


def _section(canary: Any, report: GenerationPerformanceReport) -> dict[str, Any]:
    return canary._performance_section(report)


def _stage(section: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in section["llm_stages"] if item["stage"] == name)


def _groq_figures(value: Any, path: tuple[str, ...] = ()) -> list[tuple[str, Any]]:
    """Every non-zero number reported under a key or label that names Groq."""
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        label = str(value.get("label", ""))
        for key, item in value.items():
            found += _groq_figures(item, (*path, str(key), *([label] if label else [])))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found += _groq_figures(item, path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and value:
        if any("groq" in part.lower() for part in path):
            found.append((" > ".join(path), value))
    return found


# -- A: forced Groq OPEN -----------------------------------------------------------------------


def test_forced_groq_open_reports_three_gemini_requests_and_no_groq_request_anywhere(
    production_like_pipeline: dict[str, list[httpx.Request]], clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = _load_canary()
    report = canary._run(_canary_args(), {})
    acceptance_before = json.dumps(report["acceptance"], sort_keys=True, default=str)

    configure_pair(monkeypatch)
    canary._force_open_provider("groq")
    section = _section(canary, _generation(monkeypatch, clock, groq={}, gemini=_ANSWERS))

    # the actual provider counters
    assert section["llm_calls_by_provider"] == {
        "groq": _ZERO, "gemini": {"anchor": 1, "reasoning": 1, "repair": 0, "narrator": 1},
    }
    counts = section["counts"]
    assert (counts["gemini_anchor_calls"], counts["gemini_reasoning_calls"], counts["gemini_narrator_calls"]) == (1, 1, 1)
    assert (counts["groq_anchor_calls"], counts["groq_reasoning_calls"], counts["groq_narrator_calls"]) == (0, 0, 0)
    # the semantic stages: one attempt each, labelled provider-neutrally, all sent to Gemini
    for name in ("anchor", "reasoning", "narrator"):
        stage = _stage(section, name)
        assert (stage["label"], stage["attempts"]) == (f"LLM {name}", 1)
        assert stage["provider_requests"] == {"groq": 0, "gemini": 1}
        assert (stage["providers"]["attempted"], stage["providers"]["final"]) == (["gemini"], "gemini")
    # no JSON field that names Groq carries a non-zero figure
    assert _groq_figures(section) == []

    # the rendered report
    report["performance"] = section
    report["provider_usage"]["llm_calls_by_provider"] = section["llm_calls_by_provider"]
    report["provider_usage"]["llm_stage_runs"] = {
        "anchor_proposal": 1, "itinerary_reasoning": 1, "itinerary_repair_attempts": 0, "narrator": 1,
    }
    assert _groq_figures(report["provider_usage"]) == [] and not [key for key in report["provider_usage"] if "groq" in key]
    text = canary._render(report)
    lines = text.splitlines()
    assert "Groq calls" not in text
    assert "- LLM calls by provider/stage (requests actually sent):" in lines
    assert "    Groq: anchor: 0, reasoning: 0, repair: 0, narrator: 0" in lines
    assert "    Gemini: anchor: 1, reasoning: 1, repair: 0, narrator: 1" in lines
    for name in ("anchor", "reasoning", "narrator"):
        (stage_line,) = [line for line in lines if line.startswith(f"- LLM {name}: ")]
        assert stage_line.startswith(f"- LLM {name}: 1 attempt(s) in ") and "sent to: groq 0, gemini 1" in stage_line
    # no line credits Groq with a request: every Groq row says zero
    groq_rows = [line for line in lines if re.match(r"^\s*-?\s*Groq\b", line)]
    assert groq_rows and all(re.search(r"over 0 request attempt|: anchor: 0, reasoning: 0", line) for line in groq_rows)
    assert not re.search(r"Groq (anchor|reasoning|repair|narrator): [1-9]\d* attempt", text)
    for line in lines:
        if "groq_" in line and re.search(r"_calls: ", line):
            assert line.strip().endswith(": 0"), line
    # reporting only: acceptance is what it was
    assert json.dumps(canary._acceptance(report), sort_keys=True, default=str) == acceptance_before


# -- B: mixed failover -------------------------------------------------------------------------


def test_a_reasoning_failover_reports_two_semantic_attempts_one_per_provider(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_pair(monkeypatch)
    canary = _load_canary()
    # anchor is served by Groq; reasoning hits a Groq 503 and is served by Gemini; the narrator
    # then goes straight to Gemini (Groq's circuit is open)
    section = _section(
        canary,
        _generation(
            monkeypatch, clock,
            groq={"anchor": _ANSWERS["anchor"], "reasoning": [StatusError(503)]},
            gemini={"reasoning": _ANSWERS["reasoning"], "narrator": _ANSWERS["narrator"]},
        ),
    )

    reasoning = _stage(section, "reasoning")
    assert reasoning["providers"]["attempted"] == ["groq", "gemini"]
    assert reasoning["attempts"] == 2  # the semantic total
    assert reasoning["provider_requests"] == {"groq": 1, "gemini": 1}  # the provider counters
    assert sum(reasoning["provider_requests"].values()) == reasoning["attempts"]
    assert section["llm_calls_by_provider"] == {
        "groq": {"anchor": 1, "reasoning": 1, "repair": 0, "narrator": 0},
        "gemini": {"anchor": 0, "reasoning": 1, "repair": 0, "narrator": 1},
    }
    assert _stage(section, "anchor")["provider_requests"] == {"groq": 1, "gemini": 0}
    assert _stage(section, "narrator")["provider_requests"] == {"groq": 0, "gemini": 1}


# -- C / D: single provider --------------------------------------------------------------------


@pytest.mark.parametrize(("only", "other"), [("groq", "gemini"), ("gemini", "groq")])
def test_with_one_provider_configured_semantic_and_provider_attempts_coincide(
    only: str, other: str, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_pair(monkeypatch, groq=only == "groq", gemini=only == "gemini")
    canary = _load_canary()
    # the narrator needs its structural retry: two requests to the one provider
    answers = {**_ANSWERS, "narrator": [StatusError(400, "json_validate_failed"), _NARRATIVE]}
    section = _section(canary, _generation(monkeypatch, clock, **{only: answers, other: {}}))

    expected = {"anchor": 1, "reasoning": 1, "repair": 0, "narrator": 2}
    assert section["llm_calls_by_provider"] == {only: expected, other: _ZERO}
    for name in ("anchor", "reasoning", "narrator"):
        stage = _stage(section, name)
        assert stage["label"] == f"LLM {name}"
        assert stage["attempts"] == stage["provider_requests"][only] == expected[name]
        assert stage["provider_requests"][other] == 0
        assert stage["providers"]["attempted"] == [only] * expected[name]
    if only == "gemini":
        assert _groq_figures(section) == []


# -- E: the benchmark --------------------------------------------------------------------------


def _city_record(city: str, section: dict[str, Any]) -> dict[str, Any]:
    report = _fake_report()
    report["performance"]["llm_stages"] = section["llm_stages"]
    report["provider_usage"].pop("groq_stage_calls", None)
    report["provider_usage"]["llm_calls_by_provider"] = section["llm_calls_by_provider"]
    return bench.extract_metrics(report, city=city, scenario=bench.DEFAULT_SCENARIO, run_timestamp="2026-10-05T00:00:00Z")


def test_benchmark_provider_totals_come_from_the_providers_actually_called(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = _load_canary()
    configure_pair(monkeypatch)

    canary._force_open_provider("groq")
    forced = _city_record(bench.CANONICAL_CITIES[0], _section(canary, _generation(monkeypatch, clock, groq={}, gemini=_ANSWERS)))

    from app.providers.llm_provider_health import get_llm_provider_health

    get_llm_provider_health().reset()
    mixed = _city_record(
        bench.CANONICAL_CITIES[1],
        _section(
            canary,
            _generation(
                monkeypatch, clock,
                groq={"anchor": _ANSWERS["anchor"], "reasoning": [StatusError(503)]},
                gemini={"reasoning": _ANSWERS["reasoning"], "narrator": _ANSWERS["narrator"]},
            ),
        ),
    )

    # per city: semantic totals are provider-neutral; provider figures name the provider called
    assert forced["providers"]["llm_attempts_by_stage"] == {"LLM anchor": 1, "LLM reasoning": 1, "LLM narrator": 1}
    assert forced["providers"]["llm_calls_by_provider"]["groq"] == _ZERO
    assert forced["providers"]["llm_calls_by_provider"]["gemini"] == {"anchor": 1, "reasoning": 1, "repair": 0, "narrator": 1}
    assert forced["llm_providers"]["successful_stage_calls"] == {"groq": 0, "gemini": 3}
    assert forced["llm_providers"]["failed_stage_calls"] == {"groq": 0, "gemini": 0}
    assert not [key for key in forced["providers"] if "groq" in key]
    assert _groq_figures(forced["providers"]) == [] and _groq_figures(forced["llm_providers"]) == []

    assert mixed["providers"]["llm_attempts_by_stage"] == {"LLM anchor": 1, "LLM reasoning": 2, "LLM narrator": 1}
    assert mixed["llm_providers"]["successful_stage_calls"] == {"groq": 1, "gemini": 2}
    assert mixed["llm_providers"]["failed_stage_calls"] == {"groq": 1, "gemini": 0}
    assert mixed["llm_providers"]["failover_count"] == 2  # reasoning (switched) and narrator (Groq open)

    figures = bench.aggregate([forced, mixed])["llm_providers"]
    assert figures == {
        "groq_successful_stage_calls": 1, "gemini_successful_stage_calls": 5,
        "groq_failed_stage_calls": 1, "gemini_failed_stage_calls": 0,
        "provider_failover_count": 5, "generations_using_provider_failover": 2,
        "deterministic_llm_fallback_count": 0, "generations_with_all_llm_providers_unavailable": 0,
        "provider_circuit_open_events": 1, "provider_draining_events": 0,
    }
    # each provider total equals the requests the provider counters saw, split into outcome
    for provider in ("groq", "gemini"):
        sent = sum(sum(record["providers"]["llm_calls_by_provider"][provider].values()) for record in (forced, mixed))
        assert figures[f"{provider}_successful_stage_calls"] + figures[f"{provider}_failed_stage_calls"] == sent

    # acceptance and flags are untouched by any of it
    plain = bench.extract_metrics(
        _fake_report(), city=bench.CANONICAL_CITIES[0], scenario=bench.DEFAULT_SCENARIO, run_timestamp="t"
    )
    assert forced["acceptance"] == plain["acceptance"] == mixed["acceptance"]
    row = bench.csv_row(forced)
    assert row["llm_final_providers"] == "anchor=gemini; reasoning=gemini; narrator=gemini"
    # Groq is only named as the REASON it was not called -- never as having served anything
    assert set(row["llm_failover_reasons"].split("; ")) == {
        "anchor=groq_circuit_open", "reasoning=groq_circuit_open", "narrator=groq_circuit_open",
    }


def test_a_stage_is_credited_only_to_a_provider_whose_own_request_succeeded() -> None:
    # a record claiming `final: groq` while only Gemini's request succeeded credits nobody with Groq
    stage = {
        "label": "LLM reasoning", "attempts": 2, "result": "success",
        "providers": {
            "preferred": "groq", "attempted": ["groq", "gemini"], "final": "groq", "failover": True, "reason": "groq_timeout",
            "attempts": [{"provider": "groq", "result": "transport_failure"}, {"provider": "gemini", "result": "success"}],
        },
    }
    figures = bench.llm_provider_metrics([stage])
    assert figures["successful_stage_calls"] == {"groq": 0, "gemini": 0}
    assert figures["failed_stage_calls"] == {"groq": 1, "gemini": 1}


def test_reports_written_with_the_old_groq_labels_are_read_as_provider_neutral() -> None:
    old = _fake_report()  # labels "Groq anchor" / "Groq reasoning", usage key `groq_stage_calls`
    record = bench.extract_metrics(old, city=bench.CANONICAL_CITIES[0], scenario=bench.DEFAULT_SCENARIO, run_timestamp="t")
    assert record["providers"]["llm_attempts_by_stage"] == {"LLM anchor": 1, "LLM reasoning": 2}
    assert record["providers"]["llm_calls_by_provider"] is None  # not recorded then: never inferred

    canary = _load_canary()
    assert [canary._stage_name(label) for label in ("Groq anchor", "LLM anchor", "anchor")] == ["anchor"] * 3
