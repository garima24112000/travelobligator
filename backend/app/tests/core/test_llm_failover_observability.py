from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from app.core import performance
from app.core.config import get_settings
from app.core.performance import sanitize_llm_providers
from app.models.generation_performance import GenerationPerformanceReport, LLMStagePerformance
from app.providers import llm_structured_clients
from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.providers.llm_provider_health import GROQ, LLMProviderState, get_llm_provider_health
from app.tests.core.test_generation_performance_1a import (  # noqa: F401 - `production_like_pipeline` is a fixture
    _canary_args,
    _load_canary,
    production_like_pipeline,
)
from app.tests.providers.llm_failover_support import (
    GEMINI_KEY,
    GEMINI_MODEL,
    GROQ_KEY,
    FakeClock,
    ScriptedClient,
    StatusError,
    configure_pair,
    gemini_body,
    install_clients,
    json_response,
    real_gemini_client,
    use_clock,
)
from app.tests.providers.test_groq_ai_itinerary_reasoning_provider import (
    _request as _reasoning_request,
    _valid_output as _reasoning_output,
)
from app.tests.providers.test_narrator_structural_retry_202c1d import _VALID, _request as _narrator_request
from app.tests.scripts.test_benchmark_tuning_cities_script_3a import _fake_report, bench

# What the Groq <-> Gemini pair leaves behind: the per-stage provider record,
# the log lines, and the benchmark reports. Fixed labels and numbers only --
# never a key, an Authorization value, a raw header set, a prompt or a model
# answer.

_NARRATIVE = {**_VALID, "getting_around_profile": "", "getting_around_advisory": ""}
_RAW_HEADER_VALUE = "SENTINEL_RAW_HEADER_VALUE_0003"
_MODEL_TEXT = "Start the day at Belem Tower."  # part of the model's own answer
_PROMPT_TEXT = "Lisbon, Portugal"  # part of the prompt
_FORBIDDEN = (
    GROQ_KEY, GEMINI_KEY, _RAW_HEADER_VALUE, _MODEL_TEXT, _PROMPT_TEXT, "Bearer", "authorization", "Authorization",
    "x-ratelimit", "retry-after", "x-goog-api-key", "RAW PROVIDER BODY", "org_SECRET", "https://", "http://",
)


def _assert_clean(value: Any, *, allow: tuple[str, ...] = ()) -> None:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    for forbidden in _FORBIDDEN:
        if forbidden not in allow:
            assert forbidden not in text, forbidden


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    use_clock(monkeypatch, fake)
    configure_pair(monkeypatch)
    return fake


def _failover_generation(monkeypatch: pytest.MonkeyPatch, clock: FakeClock, *, real_gemini: bool = True) -> dict[str, Any]:
    """Two stages of one generation: the narrator hits a Groq 429 carrying
    rate-limit headers and is served by Gemini (the REAL client over a mock
    transport unless `real_gemini` is off); reasoning then goes straight to
    Gemini. Returns the generation's report."""
    headers = {
        "retry-after": "900", "authorization": f"Bearer {GROQ_KEY}", "x-request-id": _RAW_HEADER_VALUE,
        "x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "15m0s", "set-cookie": _RAW_HEADER_VALUE,
    }
    groq = ScriptedClient(clock, (0.1, StatusError(429, headers=headers)))
    install_clients(
        monkeypatch, GroqItineraryNarratorProvider, groq=groq,
        gemini=ScriptedClient(clock, (0.3, _NARRATIVE), total_tokens=420),
    )
    if real_gemini:
        monkeypatch.setattr(
            GroqItineraryNarratorProvider, "_build_gemini_client",
            lambda self, timeout: real_gemini_client(
                llm_structured_clients_schema(), lambda request: json_response(gemini_body(json.dumps(_NARRATIVE), 420))
            ),
        )
    install_clients(
        monkeypatch, GroqAIItineraryReasoningProvider,
        groq=ScriptedClient(clock), gemini=ScriptedClient(clock, (0.2, _reasoning_output()), total_tokens=900),
    )
    recorder = performance.PerformanceRecorder()
    recorder.start()
    with performance.activate(recorder):
        narrative = GroqItineraryNarratorProvider().narrate(_narrator_request())
        reasoning = GroqAIItineraryReasoningProvider().reason(_reasoning_request())
    assert narrative.summary and reasoning.days
    return recorder.snapshot()


def llm_structured_clients_schema() -> Any:
    from app.providers.itinerary_narrator.groq_adapter import _NarratorBatchSchema

    return _NarratorBatchSchema


# -- the per-stage record ----------------------------------------------------------------------


def test_the_stage_record_has_every_required_diagnostic_and_nothing_sensitive(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_RPM_LIMIT", "60")
    monkeypatch.setenv("GEMINI_TPM_LIMIT", "100000")
    get_settings.cache_clear()
    snapshot = _failover_generation(monkeypatch, clock)
    report = GenerationPerformanceReport.model_validate(snapshot)

    narrator = report.llm_stages["groq_narrator"]
    assert (narrator.preferred_provider, narrator.selected_provider, narrator.final_provider) == ("groq", "groq", "gemini")
    assert narrator.attempted_providers == ["groq", "gemini"]
    assert (narrator.failover_used, narrator.failover_reason) == (True, "groq_rate_limit")
    assert narrator.provider_health_before == {"groq": "healthy", "gemini": "healthy"}
    assert narrator.provider_health_after == {"groq": "open", "gemini": "healthy"}
    assert narrator.total_provider_requests == narrator.attempts == 2
    first, second = narrator.provider_attempts
    assert (first.provider, first.result, first.transport_failure_kind, first.structural_validation_result) == (
        "groq", "transport_failure", "rate_limit", None,
    )
    assert (second.provider, second.result, second.transport_failure_kind, second.structural_validation_result) == (
        "gemini", "success", None, "valid",
    )
    assert first.duration_ms == pytest.approx(100.0) and second.duration_ms is not None
    # Gemini's advisory quota: the configured limits and a ratio from real local counters
    assert narrator.gemini_quota["configured_rpm"] == 60 and narrator.gemini_quota["configured_tpm"] == 100_000
    assert narrator.gemini_quota["configured_rpd"] is None
    assert 0.0 < narrator.gemini_quota["advisory_remaining_ratio"] < 1.0

    reasoning = report.llm_stages["groq_reasoning"]
    assert (reasoning.selected_provider, reasoning.attempted_providers, reasoning.final_provider) == (
        "gemini", ["gemini"], "gemini",
    )
    assert (reasoning.failover_used, reasoning.failover_reason) == (True, "groq_circuit_open")

    # a Gemini request is timed and counted under its own key; Groq's stay where they were
    assert snapshot["provider_attempts"] == {"groq_narrator": 1, "gemini_narrator": 1, "gemini_reasoning": 1}
    assert snapshot["counts"]["gemini_narrator_calls"] == 1 and snapshot["counts"]["groq_narrator_calls"] == 1

    _assert_clean(snapshot)
    _assert_clean(report.model_dump(mode="json"))


def test_groq_quota_figures_are_ratios_and_seconds_only(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = {
        "x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "640",
        "x-ratelimit-reset-requests": "2m0s", "x-ratelimit-limit-tokens": "8000",
        "x-ratelimit-remaining-tokens": "6000", "x-ratelimit-reset-tokens": "12s",
        "x-request-id": _RAW_HEADER_VALUE, "set-cookie": _RAW_HEADER_VALUE,
    }
    body = {
        "id": "x", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(_NARRATIVE)}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    monkeypatch.setattr(
        llm_structured_clients, "_new_http_client",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(lambda request: json_response(body, headers=headers)), **kwargs),
    )
    recorder = performance.PerformanceRecorder()
    with performance.activate(recorder):
        GroqItineraryNarratorProvider().narrate(_narrator_request())

    stage = recorder.snapshot()["llm_stages"]["groq_narrator"]
    assert stage["groq_quota"] == {"remaining_request_ratio": 0.64, "remaining_token_ratio": 0.75, "reset_seconds": 12.0}
    assert "gemini_quota" not in stage  # no Gemini limit configured: nothing is reported, nothing is guessed
    _assert_clean(stage)


def test_circuit_events_are_logged_with_allowlisted_fields_only(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        _failover_generation(monkeypatch, clock)

    opened = [record for record in caplog.records if record.getMessage() == "LLM provider circuit opened."]
    assert len(opened) == 1  # once when it opens -- not once per skipped stage
    record = opened[0]
    assert (record.provider, record.stage, record.status) == ("groq", "groq_narrator", "rate_limit")
    for entry in caplog.records:
        # no logger at all (the HTTP library's own included) ever saw a key or the model's answer
        for secret in (GROQ_KEY, GEMINI_KEY, _RAW_HEADER_VALUE, _MODEL_TEXT):
            assert secret not in entry.getMessage()
        if entry.name.startswith("app."):
            _assert_clean(entry.getMessage())
            _assert_clean({key: value for key, value in vars(entry).items() if key not in ("msg", "args", "exc_info")})
            assert entry.exc_info is None  # no traceback carrying the provider's raw error text


def test_only_fixed_labels_and_numbers_survive_into_the_report() -> None:
    hostile = {
        "preferred_provider": "groq", "selected_provider": f"Bearer {GROQ_KEY}", "final_provider": "Gemini Pro!",
        "attempted_providers": ["groq", "https://example.test/?key=abc", 7],
        "failover_used": 1, "failover_reason": "the model said: " + _MODEL_TEXT,
        "provider_health_before": {"groq": "healthy", "x-ratelimit-remaining": "open", "gemini": "Very Unhealthy"},
        "provider_health_after": "open",
        "total_provider_requests": "2",
        "provider_attempts": [
            {"provider": "groq", "result": "transport_failure", "transport_failure_kind": "rate_limit",
             "duration_ms": 12.5, "structural_validation_result": None, "prompt": _PROMPT_TEXT, "headers": {"a": "b"}},
            "not an attempt",
            {"provider": GEMINI_KEY, "result": "raw: " + _MODEL_TEXT, "duration_ms": "fast"},
        ],
        "groq_quota": {"remaining_request_ratio": 0.4, "authorization": GROQ_KEY, "reset_seconds": float("inf")},
        "gemini_quota": "none",
        "raw_headers": {"authorization": f"Bearer {GROQ_KEY}"},
        "prompt": _PROMPT_TEXT,
    }
    clean = sanitize_llm_providers(hostile)

    assert clean == {
        "preferred_provider": "groq", "selected_provider": None, "attempted_providers": ["groq"],
        "final_provider": None, "failover_used": True, "failover_reason": None,
        "provider_health_before": {"groq": "healthy"}, "provider_health_after": {},
        "total_provider_requests": 0,
        "provider_attempts": [
            {"provider": "groq", "result": "transport_failure", "transport_failure_kind": "rate_limit",
             "duration_ms": 12.5, "structural_validation_result": None},
            {"provider": None, "result": None, "transport_failure_kind": None, "duration_ms": None,
             "structural_validation_result": None},
        ],
        "groq_quota": {"remaining_request_ratio": 0.4, "reset_seconds": None},
    }
    _assert_clean(clean)
    assert sanitize_llm_providers(None) == {} and sanitize_llm_providers("text") == {}


def test_a_report_stored_before_the_provider_record_existed_still_loads() -> None:
    old = GenerationPerformanceReport.model_validate(
        {"llm_stages": {"groq_anchor": {"attempts": 2, "structural_retries": 1, "result": "success"}}}
    )
    stage = old.llm_stages["groq_anchor"]
    assert (stage.attempts, stage.preferred_provider, stage.attempted_providers, stage.failover_used) == (2, None, [], False)
    assert LLMStagePerformance().provider_attempts == [] and LLMStagePerformance().groq_quota == {}


# -- the canary report -------------------------------------------------------------------------


def test_the_canary_reports_the_provider_of_each_stage_without_judging_it(
    production_like_pipeline: dict[str, list[httpx.Request]], monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = _load_canary()
    report = canary._run(_canary_args(), {})
    acceptance_before = json.dumps(report["acceptance"], sort_keys=True, default=str)

    fake = FakeClock()
    use_clock(monkeypatch, fake)
    configure_pair(monkeypatch)
    # (this fixture owns the HTTP layer, so Gemini is a scripted client here)
    performance_report = GenerationPerformanceReport.model_validate(
        _failover_generation(monkeypatch, fake, real_gemini=False)
    )
    performance_report.provider_ms.update({"groq_narrator": 1200.0, "gemini_narrator": 800.0})
    section = canary._performance_section(performance_report)
    by_label = {item["label"]: item for item in section["llm_stages"]}

    narrator = by_label["LLM narrator"]["providers"]
    assert (narrator["preferred"], narrator["attempted"], narrator["final"]) == ("groq", ["groq", "gemini"], "gemini")
    assert (narrator["failover"], narrator["reason"]) == (True, "groq_rate_limit")
    assert by_label["LLM narrator"]["seconds"] == 2.0  # Groq's and Gemini's request time together
    assert by_label["LLM reasoning"]["providers"]["reason"] == "groq_circuit_open"

    report["performance"]["llm_stages"] = section["llm_stages"]
    text = canary._render(report)
    assert "LLM PROVIDERS" in text
    for line in (
        "narrator:", "  preferred: groq", "  attempted: [groq, gemini]", "  final: gemini", "  failover: yes",
        "  reason: groq_rate_limit", "reasoning:", "  attempted: [gemini]", "  reason: groq_circuit_open",
    ):
        assert line in text.splitlines(), line
    _assert_clean(section)
    _assert_clean(text.split("LLM PROVIDERS")[1].split("CONCURRENCY")[0])
    # reporting the provider changed nothing about acceptance
    assert json.dumps(canary._acceptance(report), sort_keys=True, default=str) == acceptance_before


def test_the_canary_says_so_when_a_generation_has_no_provider_record(
    production_like_pipeline: dict[str, list[httpx.Request]]
) -> None:
    canary = _load_canary()
    report = canary._run(_canary_args(), {})
    assert report["performance"]["llm_stages"] == []  # no model provider is connected in this fixture
    assert "- no provider record for this generation" in canary._render(report)


def test_the_live_scripts_need_geoapify_and_at_least_one_llm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    canary = _load_canary()
    for name in ("GEOAPIFY_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert canary._missing_environment() == ["GEOAPIFY_API_KEY", "GROQ_API_KEY (or GEMINI_API_KEY with GEMINI_MODEL)"]

    monkeypatch.setenv("GEOAPIFY_API_KEY", "g")
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    assert canary._missing_environment() == ["GROQ_API_KEY (or GEMINI_API_KEY with GEMINI_MODEL)"]  # no model name
    monkeypatch.setenv("GEMINI_MODEL", GEMINI_MODEL)
    assert canary._missing_environment() == []  # Gemini alone is enough
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    assert canary._missing_environment() == []  # so is Groq alone
    _assert_clean(" ".join(canary._missing_environment()))


def test_force_open_is_a_script_only_switch_with_no_setting_behind_it() -> None:
    from app.core.config import Settings

    canary = _load_canary()
    assert get_llm_provider_health().state(GROQ) == LLMProviderState.HEALTHY
    canary._force_open_provider("groq")
    assert get_llm_provider_health().state(GROQ) == LLMProviderState.OPEN
    assert get_llm_provider_health().open_reason(GROQ) == "forced_dev"
    # nothing in the application's configuration can do this
    assert not [name for name in Settings.model_fields if "force" in name and ("llm" in name or "provider" in name)]


def test_a_forced_open_groq_makes_gemini_serve_every_stage(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    _load_canary()._force_open_provider("groq")
    clock.now += 30 * 86_400.0  # no cooldown ends it within the process

    narrator = ScriptedClient(clock, (0.1, _NARRATIVE))
    built = install_clients(monkeypatch, GroqItineraryNarratorProvider, groq=ScriptedClient(clock), gemini=narrator)
    report = GroqItineraryNarratorProvider().narrate(_narrator_request())
    assert report.provider == "gemini_itinerary_narrator_provider" and [name for name, _ in built] == ["gemini"]


# -- the tuning benchmark ----------------------------------------------------------------------


def _llm_stage(label: str, result: str, **providers: Any) -> dict[str, Any]:
    record = {
        "preferred": "groq", "selected": "groq", "attempted": ["groq"], "final": "groq", "failover": False,
        "reason": None, "health_before": {"groq": "healthy", "gemini": "healthy"},
        "health_after": {"groq": "healthy", "gemini": "healthy"},
        "attempts": [{"provider": "groq", "result": "success"}],
    }
    record.update(providers)
    return {"label": label, "attempts": len(record["attempts"]), "deadline_exceeded": False, "result": result, "providers": record}


def _city_report(*stages: dict[str, Any]) -> dict[str, Any]:
    report = _fake_report()
    report["performance"]["llm_stages"] = list(stages)
    return report


_HEALTHY_CITY = (
    _llm_stage("LLM anchor", "success"),
    _llm_stage("LLM reasoning", "success"),
    _llm_stage("LLM narrator", "success"),
)
_FAILOVER_CITY = (
    _llm_stage(
        "LLM anchor", "success", attempted=["groq", "gemini"], final="gemini", failover=True, reason="groq_rate_limit",
        health_after={"groq": "open", "gemini": "healthy"},
        attempts=[{"provider": "groq", "result": "transport_failure"}, {"provider": "gemini", "result": "success"}],
    ),
    _llm_stage(
        "LLM reasoning", "success", selected="gemini", attempted=["gemini"], final="gemini", failover=True,
        reason="groq_circuit_open", health_before={"groq": "open", "gemini": "healthy"},
        health_after={"groq": "open", "gemini": "draining"}, attempts=[{"provider": "gemini", "result": "success"}],
    ),
    # Gemini answered, but the narrative was rejected by grounding: the deterministic fallback was used
    _llm_stage(
        "LLM narrator", "fallback", selected="gemini", attempted=["gemini"], final="gemini", failover=True,
        reason="groq_circuit_open", health_before={"groq": "open", "gemini": "draining"},
        health_after={"groq": "open", "gemini": "draining"}, attempts=[{"provider": "gemini", "result": "success"}],
    ),
)
_UNAVAILABLE_CITY = (
    _llm_stage(
        "LLM anchor", "failed", selected=None, attempted=[], final=None, failover=False,
        reason="all_providers_unavailable", health_before={"groq": "open", "gemini": "open"},
        health_after={"groq": "open", "gemini": "open"}, attempts=[],
    ),
)


def _record(city: str, *stages: dict[str, Any]) -> dict[str, Any]:
    return bench.extract_metrics(
        _city_report(*stages), city=city, scenario=bench.DEFAULT_SCENARIO, run_timestamp="2026-10-05T00:00:00Z"
    )


def test_each_city_records_which_provider_served_each_stage() -> None:
    record = _record(bench.CANONICAL_CITIES[0], *_FAILOVER_CITY)["llm_providers"]

    assert record["stages"] == {
        "anchor": {"preferred": "groq", "attempted": ["groq", "gemini"], "final": "gemini", "failover": True,
                   "reason": "groq_rate_limit"},
        "reasoning": {"preferred": "groq", "attempted": ["gemini"], "final": "gemini", "failover": True,
                      "reason": "groq_circuit_open"},
        # not completed: no provider is credited with it
        "narrator": {"preferred": "groq", "attempted": ["gemini"], "final": None, "failover": True,
                     "reason": "groq_circuit_open"},
    }
    assert record["successful_stage_calls"] == {"groq": 0, "gemini": 2}
    assert record["failed_stage_calls"] == {"groq": 1, "gemini": 1}
    assert (record["failover_count"], record["deterministic_fallback_count"]) == (3, 1)
    assert record["all_providers_unavailable"] is False
    assert (record["circuit_open_events"], record["draining_events"]) == (1, 1)


def test_the_aggregate_adds_the_provider_counts_up() -> None:
    cities = bench.CANONICAL_CITIES
    records = [
        _record(cities[0], *_HEALTHY_CITY), _record(cities[1], *_FAILOVER_CITY), _record(cities[2], *_UNAVAILABLE_CITY),
    ]
    figures = bench.aggregate(records)["llm_providers"]

    assert figures == {
        "groq_successful_stage_calls": 3, "gemini_successful_stage_calls": 2,
        "groq_failed_stage_calls": 1, "gemini_failed_stage_calls": 1,
        "provider_failover_count": 3, "generations_using_provider_failover": 1,
        "deterministic_llm_fallback_count": 2, "generations_with_all_llm_providers_unavailable": 1,
        "provider_circuit_open_events": 1, "provider_draining_events": 1,
    }


def test_a_stage_completed_by_gemini_counts_exactly_like_one_completed_by_groq() -> None:
    city = bench.CANONICAL_CITIES[0]
    by_groq = _record(city, *_HEALTHY_CITY)
    by_gemini = _record(
        city,
        *(
            _llm_stage(stage["label"], "success", selected="gemini", attempted=["gemini"], final="gemini", failover=True,
                       reason="groq_circuit_open", attempts=[{"provider": "gemini", "result": "success"}])
            for stage in _HEALTHY_CITY
        ),
    )

    # provider identity is metadata: acceptance, flags and every other figure are identical
    for record in (by_groq, by_gemini):
        record.pop("llm_providers")
    assert by_gemini == by_groq
    assert bench.manual_review_flags(by_gemini) == bench.manual_review_flags(by_groq)


def test_acceptance_and_flags_never_read_the_provider_record() -> None:
    city = bench.CANONICAL_CITIES[0]
    plain = bench.extract_metrics(_fake_report(), city=city, scenario=bench.DEFAULT_SCENARIO, run_timestamp="t")
    failed_over = _record(city, *_FAILOVER_CITY[:2])
    assert failed_over["acceptance"] == plain["acceptance"]
    assert set(bench.manual_review_flags(failed_over)) == set(bench.manual_review_flags(plain))


def test_a_city_recorded_before_these_figures_existed_adds_nothing() -> None:
    record = _record(bench.CANONICAL_CITIES[0], *_HEALTHY_CITY)
    record.pop("llm_providers")
    figures = bench.aggregate([record])["llm_providers"]
    assert set(figures.values()) == {0}
    assert bench.csv_row(record)["llm_failover_count"] is None


def test_the_summary_and_the_csv_report_the_providers_and_stay_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    cities = bench.CANONICAL_CITIES
    records = [_record(cities[0], *_HEALTHY_CITY), _record(cities[1], *_FAILOVER_CITY)]
    for record in records:
        record["manual_review_flags"] = bench.manual_review_flags(record)

    row = bench.csv_row(records[1])
    assert row["llm_final_providers"] == "anchor=gemini; reasoning=gemini; narrator=fallback"
    assert (row["llm_failover_count"], row["llm_deterministic_fallback_count"]) == (3, 1)
    assert "anchor=groq_rate_limit" in row["llm_failover_reasons"]
    # appended after the existing columns: earlier positions are unchanged
    assert bench._CSV_COLUMNS[-4:] == (
        "llm_final_providers", "llm_failover_count", "llm_failover_reasons", "llm_deterministic_fallback_count",
    )
    assert bench._CSV_COLUMNS.index("manual_review_flags") == len(bench._CSV_COLUMNS) - 5

    summary = {
        "schema_version": bench.SCHEMA_VERSION, "generated_at": "t", "scenario": dict(bench.DEFAULT_SCENARIO),
        "start_date": "2026-10-10", "cities": records, "aggregate": bench.aggregate(records),
    }
    markdown = bench.render_markdown(summary)
    assert "## LLM providers" in markdown
    for line in (
        "- Groq successful stage calls: 3", "- Gemini successful stage calls: 2", "- Groq failed stage calls: 1",
        "- Gemini failed stage calls: 1", "- provider failover count: 3", "- generations using provider failover: 1",
        "- deterministic LLM fallback count: 1",
        "- generations where all configured LLM providers were unavailable: 0",
        "- provider circuit-open events: 1", "- provider draining events: 1",
    ):
        assert line in markdown.splitlines(), line

    for output in (markdown, bench.render_csv(records), json.dumps(bench.scrub(summary))):
        _assert_clean(output, allow=(_PROMPT_TEXT,))  # "Lisbon, Portugal" is also a canonical benchmark city
    # a key that slipped into a label would still be removed before anything is written
    leaked = bench.scrub({"llm_providers": {"stages": {"anchor": {"reason": f"groq_{GEMINI_KEY}"}}}})
    assert GEMINI_KEY not in json.dumps(leaked)
