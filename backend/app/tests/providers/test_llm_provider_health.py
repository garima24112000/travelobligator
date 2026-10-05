from __future__ import annotations

import threading

import pytest

from app.core.config import get_settings
from app.providers.ai_failure import (
    HEALTH_AUTHENTICATION,
    HEALTH_CONNECTION_ERROR,
    HEALTH_MALFORMED_RESPONSE,
    HEALTH_PROVIDER_UNAVAILABLE,
    HEALTH_RATE_LIMIT,
    HEALTH_SCHEMA_VALIDATION,
    HEALTH_SERVER_ERROR,
    HEALTH_TIMEOUT,
    HEALTH_UNKNOWN_TRANSPORT,
    LLMStructuredOutputError,
    health_failure_kind,
    parse_duration_seconds,
    provider_reset_seconds,
)
from app.providers.llm_provider_health import (
    GEMINI,
    GROQ,
    GroqQuotaSnapshot,
    LLMProviderHealthRegistry,
    LLMProviderState,
    parse_groq_quota_headers,
)
from app.tests.providers.llm_failover_support import FakeClock, StatusError

# The in-process health record of the Groq <-> Gemini pair: the four states,
# what moves them, and the only two sources of DRAINING (Groq's rate-limit
# headers; Gemini's configured limits against local counters). Fake clock,
# no network.

HEALTHY, DRAINING, OPEN, HALF_OPEN = (
    LLMProviderState.HEALTHY, LLMProviderState.DRAINING, LLMProviderState.OPEN, LLMProviderState.HALF_OPEN,
)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def health(clock: FakeClock) -> LLMProviderHealthRegistry:
    return LLMProviderHealthRegistry(clock=clock, today=lambda: "2026-10-05")


def _limits(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


# -- failure classification ------------------------------------------------------------------


class _GeminiError(Exception):
    """Shaped like google-genai's `APIError`: an int `code` and a `status` name."""

    def __init__(self, code: int, status: str, details: dict | None = None) -> None:
        super().__init__("raw body")
        self.code, self.status, self.details = code, status, details


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (StatusError(429), HEALTH_RATE_LIMIT),
        (StatusError(503), HEALTH_PROVIDER_UNAVAILABLE),
        (_GeminiError(503, "UNAVAILABLE"), HEALTH_PROVIDER_UNAVAILABLE),
        (StatusError(500), HEALTH_SERVER_ERROR),
        (StatusError(502), HEALTH_SERVER_ERROR),
        (TimeoutError("t"), HEALTH_TIMEOUT),
        (StatusError(504), HEALTH_TIMEOUT),
        (ConnectionError("c"), HEALTH_CONNECTION_ERROR),
        (StatusError(401), HEALTH_AUTHENTICATION),
        (StatusError(403), HEALTH_AUTHENTICATION),
        (StatusError(400, "json_validate_failed"), HEALTH_MALFORMED_RESPONSE),
        (LLMStructuredOutputError("x"), HEALTH_SCHEMA_VALIDATION),
        # an unrecognized 4xx is named as such -- never read as a structural answer
        (StatusError(400), HEALTH_UNKNOWN_TRANSPORT),
        (StatusError(404), HEALTH_UNKNOWN_TRANSPORT),
        (StatusError(422, "some_other_code"), HEALTH_UNKNOWN_TRANSPORT),
        # raised locally with no HTTP status: says nothing about the provider
        (ValueError("local"), None),
    ],
)
def test_a_failure_is_classified_for_provider_health(exc: Exception, kind: str | None) -> None:
    assert health_failure_kind(exc) == kind


def test_httpx_transport_errors_are_timeouts_or_connection_errors() -> None:
    import httpx

    assert health_failure_kind(httpx.ReadTimeout("t")) == HEALTH_TIMEOUT
    assert health_failure_kind(httpx.ConnectError("c")) == HEALTH_CONNECTION_ERROR


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("7.66s", 7.66), ("2m59.56s", 179.56), ("1h2m3s", 3723.0), ("250ms", 0.25), ("34", 34.0), (12, 12.0)],
)
def test_reset_durations_are_parsed(value: object, seconds: float) -> None:
    assert parse_duration_seconds(value) == pytest.approx(seconds)


@pytest.mark.parametrize("value", [None, "", "soon", "5 minutes", "-3", "3x", "s", True, "inf"])
def test_unreadable_reset_durations_are_ignored(value: object) -> None:
    assert parse_duration_seconds(value) is None


def test_the_reset_of_a_rate_limit_prefers_retry_after_then_structured_metadata() -> None:
    assert provider_reset_seconds(StatusError(429, headers={"retry-after": "120"})) == 120.0
    retry_info = {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"}]}}
    assert provider_reset_seconds(_GeminiError(429, "RESOURCE_EXHAUSTED", retry_info)) == 34.0
    # no Retry-After: the reset of the dimension the headers show as used up
    exhausted = {
        "x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "1h0m0s",
        "x-ratelimit-remaining-tokens": "5000", "x-ratelimit-reset-tokens": "7s",
    }
    assert provider_reset_seconds(StatusError(429, headers=exhausted)) == 3600.0
    assert provider_reset_seconds(StatusError(429)) is None
    assert provider_reset_seconds(StatusError(429, headers={"retry-after": "garbage"})) is None


# -- Groq quota headers ----------------------------------------------------------------------

_HEADERS = {
    "x-ratelimit-limit-requests": "14400", "x-ratelimit-remaining-requests": "7200",
    "x-ratelimit-reset-requests": "2m59.56s",
    "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "2000",
    "x-ratelimit-reset-tokens": "7.66s",
    "authorization": "Bearer SECRET", "set-cookie": "session=SECRET",
}


def test_groq_rate_limit_headers_become_plain_numbers_and_nothing_else() -> None:
    snapshot = parse_groq_quota_headers(_HEADERS)
    assert snapshot == GroqQuotaSnapshot(
        remaining_request_ratio=0.5, remaining_token_ratio=0.25,
        reset_requests_seconds=pytest.approx(179.56), reset_tokens_seconds=pytest.approx(7.66),
    )
    assert "SECRET" not in repr(snapshot)


def test_missing_and_malformed_groq_headers_are_tolerated() -> None:
    assert parse_groq_quota_headers(None) is None
    assert parse_groq_quota_headers({}) is None
    assert parse_groq_quota_headers({"x-ratelimit-limit-requests": "abc", "x-ratelimit-remaining-requests": "x"}) is None
    # one readable dimension is kept; the unreadable one is simply absent
    partial = parse_groq_quota_headers(
        {"x-ratelimit-limit-tokens": "1000", "x-ratelimit-remaining-tokens": "100", "x-ratelimit-limit-requests": "0"}
    )
    assert partial is not None
    assert (partial.remaining_token_ratio, partial.remaining_request_ratio) == (0.1, None)
    # a ratio needs BOTH numbers
    assert parse_groq_quota_headers({"x-ratelimit-remaining-requests": "5"}) is None


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (GroqQuotaSnapshot(remaining_request_ratio=0.10, reset_requests_seconds=120.0), DRAINING),  # at the reserve
        (GroqQuotaSnapshot(remaining_request_ratio=0.05, reset_requests_seconds=120.0), DRAINING),
        (GroqQuotaSnapshot(remaining_token_ratio=0.02, reset_tokens_seconds=30.0), DRAINING),
        (GroqQuotaSnapshot(remaining_request_ratio=0.11, remaining_token_ratio=0.5), HEALTHY),  # above it
        (GroqQuotaSnapshot(reset_requests_seconds=5.0), HEALTHY),  # no ratio: no evidence, no guess
    ],
)
def test_groq_drains_only_at_or_under_the_reserve_ratio(
    health: LLMProviderHealthRegistry, snapshot: GroqQuotaSnapshot, expected: LLMProviderState
) -> None:
    health.record_transport_success(GROQ, groq_quota=snapshot)
    assert health.state(GROQ) == expected


def test_groq_stops_draining_when_its_quota_window_resets_or_headroom_returns(
    health: LLMProviderHealthRegistry, clock: FakeClock
) -> None:
    health.record_transport_success(GROQ, groq_quota=GroqQuotaSnapshot(remaining_token_ratio=0.05, reset_tokens_seconds=30.0))
    assert health.state(GROQ) == DRAINING
    clock.now += 29.0
    assert health.state(GROQ) == DRAINING
    clock.now += 1.0
    assert health.state(GROQ) == HEALTHY

    health.record_transport_success(GROQ, groq_quota=GroqQuotaSnapshot(remaining_token_ratio=0.05, reset_tokens_seconds=30.0))
    health.record_transport_success(GROQ, groq_quota=GroqQuotaSnapshot(remaining_token_ratio=0.9, reset_tokens_seconds=30.0))
    assert health.state(GROQ) == HEALTHY
    report = health.quota_report(GROQ)
    assert report == {"remaining_request_ratio": None, "remaining_token_ratio": 0.9, "reset_seconds": 30.0}


def test_a_draining_dimension_without_a_reset_drains_for_the_probe_interval(
    health: LLMProviderHealthRegistry, clock: FakeClock
) -> None:
    health.record_transport_success(GROQ, groq_quota=GroqQuotaSnapshot(remaining_request_ratio=0.01))
    clock.now += 299.0
    assert health.state(GROQ) == DRAINING
    clock.now += 1.0
    assert health.state(GROQ) == HEALTHY


def test_without_quota_evidence_nothing_drains(health: LLMProviderHealthRegistry, clock: FakeClock) -> None:
    for _ in range(500):
        health.note_request(GROQ)
        health.record_transport_success(GROQ)
        clock.now += 0.01
    assert health.state(GROQ) == HEALTHY and health.quota_report(GROQ) == {}


# -- circuit ---------------------------------------------------------------------------------


def test_a_rate_limit_opens_for_the_providers_own_reset(health: LLMProviderHealthRegistry, clock: FakeClock) -> None:
    health.record_failure(GROQ, HEALTH_RATE_LIMIT, reset_seconds=900.0)
    assert health.state(GROQ) == OPEN and health.open_reason(GROQ) == HEALTH_RATE_LIMIT
    clock.now += 899.0
    assert health.state(GROQ) == OPEN
    clock.now += 1.0
    assert health.state(GROQ) == HALF_OPEN


def test_a_rate_limit_without_reset_information_opens_for_the_probe_interval(
    health: LLMProviderHealthRegistry, clock: FakeClock
) -> None:
    health.record_failure(GROQ, HEALTH_RATE_LIMIT)
    clock.now += 299.0
    assert health.state(GROQ) == OPEN
    clock.now += 1.0
    assert health.state(GROQ) == HALF_OPEN


@pytest.mark.parametrize(
    "kind", [HEALTH_PROVIDER_UNAVAILABLE, HEALTH_SERVER_ERROR, HEALTH_TIMEOUT, HEALTH_CONNECTION_ERROR, HEALTH_UNKNOWN_TRANSPORT]
)
def test_availability_failures_open_for_the_short_cooldown_never_a_quota_wait(
    health: LLMProviderHealthRegistry, clock: FakeClock, kind: str
) -> None:
    # a reset value that happened to be on the response is NOT used: this is not a quota failure
    health.record_failure(GEMINI, kind, reset_seconds=86_400.0)
    assert health.state(GEMINI) == OPEN and health.open_reason(GEMINI) == kind
    clock.now += 59.0
    assert health.state(GEMINI) == OPEN
    clock.now += 1.0
    assert health.state(GEMINI) == HALF_OPEN


def test_rejected_credentials_stay_unavailable_and_are_never_probed(
    health: LLMProviderHealthRegistry, clock: FakeClock
) -> None:
    health.record_failure(GROQ, HEALTH_AUTHENTICATION)
    clock.now += 10 * 86_400.0
    assert health.state(GROQ) == OPEN
    assert health.try_acquire_probe(GROQ) is False


@pytest.mark.parametrize("kind", [HEALTH_MALFORMED_RESPONSE, HEALTH_SCHEMA_VALIDATION])
def test_a_structural_failure_never_opens_the_circuit(health: LLMProviderHealthRegistry, kind: str) -> None:
    health.record_failure(GROQ, kind)
    assert health.state(GROQ) == HEALTHY and health.open_reason(GROQ) is None


def test_a_failure_with_no_verdict_changes_nothing(health: LLMProviderHealthRegistry) -> None:
    health.record_failure(GROQ, None)
    assert health.state(GROQ) == HEALTHY


def _half_open(health: LLMProviderHealthRegistry, clock: FakeClock, provider: str = GROQ) -> None:
    health.record_failure(provider, HEALTH_PROVIDER_UNAVAILABLE)
    clock.now += 60.0
    assert health.state(provider) == HALF_OPEN


def test_a_half_open_probe_whose_request_completes_makes_the_provider_healthy(
    health: LLMProviderHealthRegistry, clock: FakeClock
) -> None:
    _half_open(health, clock)
    assert health.try_acquire_probe(GROQ) is True
    health.record_transport_success(GROQ)
    assert health.state(GROQ) == HEALTHY


@pytest.mark.parametrize("kind", [HEALTH_MALFORMED_RESPONSE, HEALTH_SCHEMA_VALIDATION])
def test_a_probe_answered_with_unusable_output_still_proves_the_provider_healthy(
    health: LLMProviderHealthRegistry, clock: FakeClock, kind: str
) -> None:
    """`json_validate_failed` / a local schema failure: the provider received
    and processed the request. The stage handles the structural failure."""
    _half_open(health, clock)
    assert health.try_acquire_probe(GROQ) is True
    health.record_failure(GROQ, kind)
    assert health.state(GROQ) == HEALTHY


@pytest.mark.parametrize(
    ("kind", "reset", "reopened_for"),
    [
        (HEALTH_RATE_LIMIT, 600.0, 600.0),  # quota semantics
        (HEALTH_RATE_LIMIT, None, 300.0),
        (HEALTH_PROVIDER_UNAVAILABLE, None, 60.0),  # short cooldown
        (HEALTH_SERVER_ERROR, None, 60.0),
        (HEALTH_TIMEOUT, None, 60.0),
        (HEALTH_CONNECTION_ERROR, None, 60.0),
        (HEALTH_UNKNOWN_TRANSPORT, None, 60.0),  # an unknown 4xx is not proof of health
    ],
)
def test_a_failed_probe_reopens_the_circuit(
    health: LLMProviderHealthRegistry, clock: FakeClock, kind: str, reset: float | None, reopened_for: float
) -> None:
    _half_open(health, clock)
    assert health.try_acquire_probe(GROQ) is True
    health.record_failure(GROQ, kind, reset)
    assert health.state(GROQ) == OPEN
    clock.now += reopened_for - 1.0
    assert health.state(GROQ) == OPEN
    clock.now += 1.0
    assert health.state(GROQ) == HALF_OPEN
    assert health.try_acquire_probe(GROQ) is True  # the slot was given back


def test_a_probe_failing_on_credentials_makes_the_provider_unavailable(
    health: LLMProviderHealthRegistry, clock: FakeClock
) -> None:
    _half_open(health, clock)
    assert health.try_acquire_probe(GROQ) is True
    health.record_failure(GROQ, HEALTH_AUTHENTICATION)
    clock.now += 86_400.0
    assert health.state(GROQ) == OPEN


def test_only_one_probe_is_allowed_at_a_time(health: LLMProviderHealthRegistry, clock: FakeClock) -> None:
    _half_open(health, clock)
    assert health.try_acquire_probe(GROQ) is True
    assert health.try_acquire_probe(GROQ) is False
    # a probe that ended without a verdict gives the slot back; the provider is still HALF_OPEN
    health.release_probe(GROQ)
    assert health.state(GROQ) == HALF_OPEN and health.try_acquire_probe(GROQ) is True


def test_concurrent_probe_attempts_are_bounded_to_one(health: LLMProviderHealthRegistry, clock: FakeClock) -> None:
    _half_open(health, clock)
    barrier, won = threading.Barrier(16), []

    def attempt() -> None:
        barrier.wait()
        won.append(health.try_acquire_probe(GROQ))

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(won) == [False] * 15 + [True]


def test_no_probe_is_handed_out_unless_the_provider_is_half_open(health: LLMProviderHealthRegistry) -> None:
    assert health.try_acquire_probe(GROQ) is False  # HEALTHY
    health.record_failure(GROQ, HEALTH_RATE_LIMIT, 60.0)
    assert health.try_acquire_probe(GROQ) is False  # OPEN


def test_providers_are_tracked_independently(health: LLMProviderHealthRegistry) -> None:
    health.record_failure(GROQ, HEALTH_RATE_LIMIT, 60.0)
    assert health.states([GROQ, GEMINI]) == {"groq": "open", "gemini": "healthy"}


def test_force_open_is_permanent_for_the_process(health: LLMProviderHealthRegistry, clock: FakeClock) -> None:
    health.force_open(GROQ)
    clock.now += 10 * 86_400.0
    assert health.state(GROQ) == OPEN and health.try_acquire_probe(GROQ) is False


# -- Gemini advisory counters ----------------------------------------------------------------


def test_gemini_never_drains_when_no_limit_is_configured(health: LLMProviderHealthRegistry, clock: FakeClock) -> None:
    for _ in range(1_000):
        health.note_request(GEMINI)
        health.record_transport_success(GEMINI, total_tokens=50_000)
    assert health.state(GEMINI) == HEALTHY and health.quota_report(GEMINI) == {}
    # reactive handling still works without any limit
    health.record_failure(GEMINI, HEALTH_RATE_LIMIT, 30.0)
    assert health.state(GEMINI) == OPEN


def test_a_configured_rpm_limit_drains_at_the_reserve_and_recovers_with_the_window(
    health: LLMProviderHealthRegistry, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, GEMINI_RPM_LIMIT="10")
    for _ in range(8):
        health.note_request(GEMINI)
    assert health.state(GEMINI) == HEALTHY  # 2 of 10 left: 0.2 > 0.10
    health.note_request(GEMINI)
    assert health.state(GEMINI) == DRAINING  # 1 of 10 left: 0.1 <= 0.10
    assert health.quota_report(GEMINI) == {
        "configured_rpm": 10, "configured_tpm": None, "configured_rpd": None, "advisory_remaining_ratio": 0.1,
    }
    clock.now += 60.0
    assert health.state(GEMINI) == HEALTHY


def test_a_configured_tpm_limit_uses_only_reported_token_usage(
    health: LLMProviderHealthRegistry, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, GEMINI_TPM_LIMIT="1000")
    health.record_transport_success(GEMINI, total_tokens=800)
    assert health.state(GEMINI) == HEALTHY
    # usage the provider did not report is never estimated
    health.record_transport_success(GEMINI, total_tokens=None)
    assert health.state(GEMINI) == HEALTHY
    health.record_transport_success(GEMINI, total_tokens=100)
    assert health.state(GEMINI) == DRAINING  # 100 of 1000 left
    clock.now += 60.0
    assert health.state(GEMINI) == HEALTHY


def test_a_configured_rpd_limit_drains_at_the_reserve_and_resets_with_the_day(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, GEMINI_RPD_LIMIT="20")
    day = ["2026-10-05"]
    health = LLMProviderHealthRegistry(clock=clock, today=lambda: day[0])
    for _ in range(18):
        health.note_request(GEMINI)
        clock.now += 120.0  # well apart: this is the daily limit, not a per-minute one
    assert health.state(GEMINI) == DRAINING  # 2 of 20 left
    day[0] = "2026-10-06"
    assert health.state(GEMINI) == HEALTHY


def test_gemini_counters_never_go_negative_past_a_limit(
    health: LLMProviderHealthRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, GEMINI_RPM_LIMIT="5")
    for _ in range(50):
        health.note_request(GEMINI)
    assert health.quota_report(GEMINI)["advisory_remaining_ratio"] == 0.0


def test_gemini_counters_are_exact_under_concurrency(
    health: LLMProviderHealthRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _limits(monkeypatch, GEMINI_RPM_LIMIT="10000", GEMINI_TPM_LIMIT="1000000")
    barrier = threading.Barrier(8)

    def work() -> None:
        barrier.wait()
        for _ in range(250):
            health.note_request(GEMINI)
            health.record_transport_success(GEMINI, total_tokens=100)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # 2000 requests of 10000 and 200000 tokens of 1000000: both exactly 0.8 remaining
    assert health.quota_report(GEMINI)["advisory_remaining_ratio"] == pytest.approx(0.8)


def test_gemini_counters_are_deterministic(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    _limits(monkeypatch, GEMINI_RPM_LIMIT="10", GEMINI_TPM_LIMIT="1000", GEMINI_RPD_LIMIT="100")

    def run() -> list[str]:
        fake = FakeClock()
        registry = LLMProviderHealthRegistry(clock=fake, today=lambda: "2026-10-05")
        states = []
        for index in range(12):
            registry.note_request(GEMINI)
            registry.record_transport_success(GEMINI, total_tokens=70)
            fake.now += 4.0 if index % 3 else 9.0
            states.append(registry.state(GEMINI).value)
        return states

    assert run() == run()
