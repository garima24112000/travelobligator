from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.scraping import (
    ScrapedDataConfidence,
    ScrapedDataProvenance,
    ScrapingExtractionMethod,
    ScrapingRateLimitGuard,
    ScrapingSourcePolicy,
    ScrapingSourceRegistry,
    ScrapingSourceType,
    default_scraping_source_registry,
)

# Model/contract tests for Step 168A's scraping policy, registry, and
# scraped-data provenance foundation. Never calls a real network service,
# never parses real HTML -- these only exercise pydantic validation and
# in-memory registry logic.


def _policy(**overrides: object) -> ScrapingSourcePolicy:
    fields: dict[str, object] = {
        "source_id": "example_travel_blog",
        "source_name": "Example Travel Blog",
        "base_url": "https://example-travel-blog.test",
        "approved_for_personal_use": True,
        "rate_limit_seconds": 10,
    }
    fields.update(overrides)
    return ScrapingSourcePolicy(**fields)


def _provenance(**overrides: object) -> ScrapedDataProvenance:
    fields: dict[str, object] = {
        "source_id": "example_travel_blog",
        "source_name": "Example Travel Blog",
        "confidence": ScrapedDataConfidence.EXPERIMENTAL,
    }
    fields.update(overrides)
    return ScrapedDataProvenance(**fields)


# ---------------------------------------------------------------------------
# 2. Source policy defaults to disabled unless explicitly enabled.
# ---------------------------------------------------------------------------


def test_policy_defaults_to_disabled() -> None:
    policy = _policy()
    assert policy.enabled is False


def test_policy_defaults_approved_for_personal_use_to_false() -> None:
    policy = ScrapingSourcePolicy(
        source_id="unapproved_source",
        source_name="Unapproved Source",
        base_url="https://unapproved.test",
        rate_limit_seconds=10,
    )
    assert policy.approved_for_personal_use is False
    assert policy.is_unsafe is True


def test_policy_can_be_explicitly_enabled_when_safe() -> None:
    policy = _policy(enabled=True, approved_for_personal_use=True)
    assert policy.enabled is True
    assert policy.is_unsafe is False


# ---------------------------------------------------------------------------
# 3/4/5. Source policy rejects an unsafe enabled source (login-required,
# paywalled, captcha-expected).
# ---------------------------------------------------------------------------


def test_policy_rejects_enabled_login_required_source() -> None:
    with pytest.raises(ValidationError):
        _policy(enabled=True, requires_login=True)


def test_policy_rejects_enabled_paywalled_source() -> None:
    with pytest.raises(ValidationError):
        _policy(enabled=True, paywalled=True)


def test_policy_rejects_enabled_captcha_expected_source() -> None:
    with pytest.raises(ValidationError):
        _policy(enabled=True, captcha_expected=True)


def test_policy_marks_login_required_source_unsafe_even_when_disabled() -> None:
    """A login-required source can still be represented (e.g. to record
    that it exists and is rejected) as long as it's not `enabled` --
    `is_unsafe` still honestly flags it."""
    policy = _policy(requires_login=True)
    assert policy.enabled is False
    assert policy.is_unsafe is True


def test_policy_marks_paywalled_source_unsafe_even_when_disabled() -> None:
    policy = _policy(paywalled=True)
    assert policy.is_unsafe is True


def test_policy_marks_captcha_expected_source_unsafe_even_when_disabled() -> None:
    policy = _policy(captcha_expected=True)
    assert policy.is_unsafe is True


# ---------------------------------------------------------------------------
# 6. Source policy requires approved_for_personal_use=True before
# activation.
# ---------------------------------------------------------------------------


def test_policy_rejects_enabled_source_not_approved_for_personal_use() -> None:
    with pytest.raises(ValidationError):
        _policy(enabled=True, approved_for_personal_use=False)


def test_policy_allows_disabled_source_not_approved_for_personal_use() -> None:
    policy = _policy(enabled=False, approved_for_personal_use=False)
    assert policy.enabled is False
    assert policy.is_unsafe is True


# ---------------------------------------------------------------------------
# Rate limit must be a real, positive value -- no aggressive crawling.
# ---------------------------------------------------------------------------


def test_policy_rejects_non_positive_rate_limit() -> None:
    with pytest.raises(ValidationError):
        _policy(rate_limit_seconds=0)


def test_policy_rejects_negative_rate_limit() -> None:
    with pytest.raises(ValidationError):
        _policy(rate_limit_seconds=-5)


# ---------------------------------------------------------------------------
# 7. Registry returns no active sources by default.
# ---------------------------------------------------------------------------


def test_default_registry_starts_empty() -> None:
    assert default_scraping_source_registry.all_sources() == []
    assert default_scraping_source_registry.active_sources() == []


def test_new_registry_with_no_sources_has_no_active_sources() -> None:
    registry = ScrapingSourceRegistry()
    assert registry.active_sources() == []


def test_registry_with_disabled_source_has_no_active_sources() -> None:
    registry = ScrapingSourceRegistry([_policy(enabled=False)])
    assert registry.active_sources() == []


# ---------------------------------------------------------------------------
# 8. Registry returns only explicitly enabled and safe sources.
# ---------------------------------------------------------------------------


def test_registry_returns_enabled_safe_source() -> None:
    safe_source = _policy(
        source_id="safe_source", enabled=True, approved_for_personal_use=True
    )
    registry = ScrapingSourceRegistry([safe_source])

    active = registry.active_sources()

    assert len(active) == 1
    assert active[0].source_id == "safe_source"


def test_registry_excludes_unsafe_source_even_if_mutated_after_construction() -> None:
    """Defensive check: even if a policy object is mutated in-place after
    construction (bypassing the constructor-time validator) to look
    enabled-and-unsafe, `active_sources` still refuses to return it."""
    safe_source = _policy(
        source_id="mutated_source", enabled=True, approved_for_personal_use=True
    )
    safe_source.requires_login = True
    registry = ScrapingSourceRegistry([safe_source])

    assert registry.active_sources() == []


def test_registry_get_returns_none_for_unknown_source() -> None:
    registry = ScrapingSourceRegistry()
    assert registry.get("does_not_exist") is None


def test_registry_register_adds_a_source() -> None:
    registry = ScrapingSourceRegistry()
    registry.register(_policy(source_id="newly_registered"))

    assert registry.get("newly_registered") is not None
    assert registry.active_sources() == []


def test_registry_mixed_sources_returns_only_the_enabled_safe_one() -> None:
    registry = ScrapingSourceRegistry(
        [
            _policy(source_id="disabled_source", enabled=False),
            _policy(
                source_id="enabled_unapproved_source",
                enabled=False,
                approved_for_personal_use=False,
            ),
            _policy(
                source_id="enabled_safe_source",
                enabled=True,
                approved_for_personal_use=True,
            ),
        ]
    )

    active_ids = {source.source_id for source in registry.active_sources()}
    assert active_ids == {"enabled_safe_source"}


# ---------------------------------------------------------------------------
# 9. Provenance model labels source_type/provenance as scraped_public_page.
# ---------------------------------------------------------------------------


def test_provenance_defaults_source_type_and_provenance_to_scraped_public_page() -> None:
    provenance = _provenance()
    assert provenance.source_type == ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    assert provenance.provenance == ScrapingSourceType.SCRAPED_PUBLIC_PAGE


def test_provenance_rejects_unknown_source_type() -> None:
    with pytest.raises(ValidationError):
        _provenance(source_type="official_api")


# ---------------------------------------------------------------------------
# 10. Provenance model defaults official_provider=False.
# ---------------------------------------------------------------------------


def test_provenance_defaults_official_provider_to_false() -> None:
    provenance = _provenance()
    assert provenance.official_provider is False


def test_provenance_rejects_official_provider_true() -> None:
    """`official_provider` is typed `Literal[False]` -- scraped data can
    never be marked as official-provider data, not even explicitly."""
    with pytest.raises(ValidationError):
        _provenance(official_provider=True)


# ---------------------------------------------------------------------------
# 11. Provenance model can represent experimental/fragile scraped data.
# ---------------------------------------------------------------------------


def test_provenance_can_represent_experimental_confidence() -> None:
    provenance = _provenance(confidence=ScrapedDataConfidence.EXPERIMENTAL)
    assert provenance.confidence == ScrapedDataConfidence.EXPERIMENTAL


def test_provenance_can_represent_fragile_confidence() -> None:
    provenance = _provenance(confidence=ScrapedDataConfidence.FRAGILE)
    assert provenance.confidence == ScrapedDataConfidence.FRAGILE


def test_provenance_rejects_unknown_confidence() -> None:
    with pytest.raises(ValidationError):
        _provenance(confidence="verified")


def test_provenance_requires_confidence_explicitly() -> None:
    with pytest.raises(ValidationError):
        ScrapedDataProvenance(source_id="s", source_name="S")


def test_provenance_accepts_full_optional_fields() -> None:
    fetched_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
    provenance = _provenance(
        fetched_at=fetched_at,
        parser_version="v0.1.0",
        source_url="https://example-travel-blog.test/hotels/example",
        extraction_method=ScrapingExtractionMethod.STATIC_HTML_PARSER,
    )
    assert provenance.fetched_at == fetched_at
    assert provenance.parser_version == "v0.1.0"
    assert provenance.source_url == "https://example-travel-blog.test/hotels/example"
    assert provenance.extraction_method == ScrapingExtractionMethod.STATIC_HTML_PARSER


def test_provenance_extraction_method_defaults_to_unknown() -> None:
    provenance = _provenance()
    assert provenance.extraction_method == ScrapingExtractionMethod.UNKNOWN


def test_provenance_optional_fields_default_to_none() -> None:
    provenance = _provenance()
    assert provenance.fetched_at is None
    assert provenance.parser_version is None
    assert provenance.source_url is None


# ---------------------------------------------------------------------------
# 12. No scraped model defaults price/rating/availability/booking data --
# neither model in this module has such a field at all, so there is
# nothing to fabricate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_field_name",
    [
        "price",
        "nightly_price_amount",
        "total_price_amount",
        "rating",
        "availability",
        "availability_status",
        "booking_url",
        "amenities",
        "cancellation_policy",
        "opening_hours",
        "route_time",
        "safety_score",
    ],
)
def test_no_scraping_model_defines_a_travel_fact_field(forbidden_field_name: str) -> None:
    assert forbidden_field_name not in ScrapingSourcePolicy.model_fields
    assert forbidden_field_name not in ScrapedDataProvenance.model_fields


# ---------------------------------------------------------------------------
# Module import safety: no LangGraph, Groq, Anthropic, Kiwi/MCP, httpx,
# requests, or BeautifulSoup import in the scraping contract module.
# ---------------------------------------------------------------------------


def test_scraping_module_has_no_disallowed_imports() -> None:
    import app.models.scraping as scraping_module

    source = inspect.getsource(scraping_module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "bs4",
        "beautifulsoup",
        "selenium",
        "playwright",
        "groq",
        "anthropic",
        "openai",
        "gemini",
        "google.generativeai",
        "kiwi",
        "mcp",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"disallowed import found: {name}"


def test_provider_gateway_does_not_reference_scraping_module() -> None:
    import app.providers.gateway as gateway_module

    source = inspect.getsource(gateway_module)
    assert "app.models.scraping" not in source
    assert "ScrapingSourceRegistry" not in source
    assert "ScrapingSourcePolicy" not in source


def test_planning_orchestrator_does_not_reference_scraping_module() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "app.models.scraping" not in source
    assert "ScrapingSourceRegistry" not in source
    assert "ScrapingSourcePolicy" not in source


# ---------------------------------------------------------------------------
# Step 168D: ScrapingRateLimitGuard -- deterministic in-process guard for a
# future live scraper adapter. No live fetching exists yet; these tests
# only ever inject a fake clock/sleep, never a real `time.sleep`.
# ---------------------------------------------------------------------------


def test_rate_limit_guard_does_not_wait_on_first_attempt() -> None:
    sleep_calls: list[float] = []
    guard = ScrapingRateLimitGuard(clock=lambda: 0.0, sleep=sleep_calls.append)
    policy = _policy(rate_limit_seconds=10)

    waited = guard.wait_if_needed(policy)

    assert waited == 0.0
    assert sleep_calls == []


# ---------------------------------------------------------------------------
# 14/15. Rate-limit guard respects source_policy.rate_limit_seconds, and
# is fully testable without any real sleeping.
# ---------------------------------------------------------------------------


def test_rate_limit_guard_waits_for_the_remaining_gap() -> None:
    fake_time = {"now": 0.0}
    sleep_calls: list[float] = []

    def fake_clock() -> float:
        return fake_time["now"]

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        fake_time["now"] += seconds

    guard = ScrapingRateLimitGuard(clock=fake_clock, sleep=fake_sleep)
    policy = _policy(rate_limit_seconds=10)

    guard.wait_if_needed(policy)
    fake_time["now"] += 3.0  # only 3 of the required 10 seconds have passed
    waited = guard.wait_if_needed(policy)

    assert waited == pytest.approx(7.0)
    assert sleep_calls == [pytest.approx(7.0)]


def test_rate_limit_guard_does_not_wait_once_enough_time_has_passed() -> None:
    fake_time = {"now": 0.0}
    sleep_calls: list[float] = []
    guard = ScrapingRateLimitGuard(
        clock=lambda: fake_time["now"], sleep=lambda seconds: sleep_calls.append(seconds)
    )
    policy = _policy(rate_limit_seconds=5)

    guard.wait_if_needed(policy)
    fake_time["now"] += 10.0  # more than enough time has already passed
    waited = guard.wait_if_needed(policy)

    assert waited == 0.0
    assert sleep_calls == []


def test_rate_limit_guard_never_reaches_real_sleep_when_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirms the guard is fully testable without real sleeping -- the
    default `time.sleep` is never reached when a fake `sleep` is
    injected, even when a wait is genuinely required."""
    import time as time_module

    def _fail_if_real_sleep_called(seconds: float) -> None:
        raise AssertionError("real time.sleep must not be called when a fake sleep is injected")

    monkeypatch.setattr(time_module, "sleep", _fail_if_real_sleep_called)

    fake_sleep_calls: list[float] = []
    guard = ScrapingRateLimitGuard(clock=lambda: 0.0, sleep=fake_sleep_calls.append)
    policy = _policy(rate_limit_seconds=10)

    guard.wait_if_needed(policy)
    guard.wait_if_needed(policy)  # would need to wait 10s -- must call the fake, never real sleep

    assert fake_sleep_calls == [10.0]


def test_rate_limit_guard_tracks_timing_independently_per_source_id() -> None:
    fake_time = {"now": 0.0}
    sleep_calls: list[float] = []
    guard = ScrapingRateLimitGuard(
        clock=lambda: fake_time["now"], sleep=lambda seconds: sleep_calls.append(seconds)
    )
    first_source = _policy(source_id="first_source", rate_limit_seconds=10)
    second_source = _policy(source_id="second_source", rate_limit_seconds=10)

    guard.wait_if_needed(first_source)
    # A brand-new source_id has never been attempted, so it never waits
    # because of a different source's timing.
    waited = guard.wait_if_needed(second_source)

    assert waited == 0.0
    assert sleep_calls == []


def test_rate_limit_guard_reset_clears_recorded_timing() -> None:
    fake_time = {"now": 0.0}
    guard = ScrapingRateLimitGuard(clock=lambda: fake_time["now"], sleep=lambda seconds: None)
    policy = _policy(rate_limit_seconds=10)

    guard.wait_if_needed(policy)
    guard.reset(policy.source_id)
    waited = guard.wait_if_needed(policy)

    assert waited == 0.0


# ---------------------------------------------------------------------------
# 16. Rate-limit guard is never used to bypass unsafe source policy -- it
# has no concept of is_unsafe/enabled at all, so it grants no permission.
# ---------------------------------------------------------------------------


def test_rate_limit_guard_operates_identically_regardless_of_source_safety() -> None:
    """The guard purely tracks timing -- it never raises, refuses, or
    behaves differently based on `is_unsafe`/`enabled`. A caller must
    still perform its own safety checks before ever consulting this guard;
    the guard itself cannot grant permission to scrape anything."""
    unsafe_policy = _policy(enabled=False, requires_login=True, rate_limit_seconds=10)
    assert unsafe_policy.is_unsafe is True

    guard = ScrapingRateLimitGuard(clock=lambda: 0.0, sleep=lambda seconds: None)

    waited = guard.wait_if_needed(unsafe_policy)

    assert waited == 0.0


def test_rate_limit_guard_has_no_safety_approval_method() -> None:
    """Structural confirmation that this guard exposes no `is_allowed`/
    `approve`/similar method that could be mistaken for a safety check --
    it only ever tracks timing (`wait_if_needed`/`reset`)."""
    guard = ScrapingRateLimitGuard(clock=lambda: 0.0, sleep=lambda seconds: None)
    public_methods = {name for name in dir(guard) if not name.startswith("_")}
    assert public_methods == {"wait_if_needed", "reset"}
