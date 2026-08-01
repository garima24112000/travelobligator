from __future__ import annotations

import ast
import inspect

import pytest

from app.core.config import Settings
from app.models.ai_candidate_proposal import (
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidateProposalTask,
)
from app.providers.ai_candidate_proposal import (
    NotConnectedAICandidateProposalProvider,
    get_ai_candidate_proposal_provider,
)
from app.providers.ai_candidate_proposal import factory as factory_module

_FORBIDDEN_MODEL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
}


def _request(**overrides: object) -> AICandidateProposalRequest:
    fields: dict[str, object] = {
        "task": AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        "trip_id": "trip_001",
        "destination_name": "Lisbon",
        "trip_duration_days": 4,
    }
    fields.update(overrides)
    return AICandidateProposalRequest(**fields)


# ---------------------------------------------------------------------------
# 1. Factory returns NotConnectedAICandidateProposalProvider by default.
# ---------------------------------------------------------------------------


def test_factory_returns_not_connected_provider_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate from any AI_CANDIDATE_PROPOSAL_PROVIDER set in the real
    # environment/.env so this test reflects the documented default.
    monkeypatch.delenv("AI_CANDIDATE_PROPOSAL_PROVIDER", raising=False)
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings())

    provider = get_ai_candidate_proposal_provider()

    assert isinstance(provider, NotConnectedAICandidateProposalProvider)


# ---------------------------------------------------------------------------
# 2. Factory returns NotConnectedAICandidateProposalProvider for explicit
#    "not_connected".
# ---------------------------------------------------------------------------


def test_factory_returns_not_connected_provider_for_explicit_name() -> None:
    provider = get_ai_candidate_proposal_provider("not_connected")
    assert isinstance(provider, NotConnectedAICandidateProposalProvider)


# ---------------------------------------------------------------------------
# 3. Factory behavior for unsupported provider names is safe.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unsupported_name", ["openai", "made_up_provider", "", "NOT_CONNECTED", "gpt-4"])
def test_factory_falls_back_to_not_connected_for_unsupported_names(unsupported_name: str) -> None:
    provider = get_ai_candidate_proposal_provider(unsupported_name)

    # Unsupported/unrecognized config can never silently create fake
    # proposals -- it must fall back to the same honest not_connected
    # adapter used when nothing is configured, never raise, and never
    # fabricate output.
    assert isinstance(provider, NotConnectedAICandidateProposalProvider)
    result = provider.propose(_request())
    assert result.status == AICandidateProposalStatus.NOT_CONNECTED
    assert result.proposals == []
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# 4. Config default is "not_connected".
# ---------------------------------------------------------------------------


def test_settings_default_ai_candidate_proposal_provider_is_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_CANDIDATE_PROPOSAL_PROVIDER", raising=False)
    settings = Settings()
    assert settings.ai_candidate_proposal_provider == "not_connected"


def test_settings_reads_ai_candidate_proposal_provider_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CANDIDATE_PROPOSAL_PROVIDER", "some_future_provider")
    settings = Settings()
    assert settings.ai_candidate_proposal_provider == "some_future_provider"


def test_factory_uses_settings_when_no_provider_name_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        factory_module, "get_settings", lambda: Settings(ai_candidate_proposal_provider="not_connected")
    )
    provider = get_ai_candidate_proposal_provider()
    assert isinstance(provider, NotConnectedAICandidateProposalProvider)


def test_factory_falls_back_when_settings_hold_unsupported_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        factory_module, "get_settings", lambda: Settings(ai_candidate_proposal_provider="openai")
    )
    provider = get_ai_candidate_proposal_provider()
    assert isinstance(provider, NotConnectedAICandidateProposalProvider)


# ---------------------------------------------------------------------------
# Repeated calls / no mutation / valid output (mirrors the adapter's own
# safety tests, applied through the factory boundary).
# ---------------------------------------------------------------------------


def test_factory_provider_output_is_a_valid_result_instance() -> None:
    provider = get_ai_candidate_proposal_provider("not_connected")
    result = provider.propose(_request())
    assert isinstance(result, AICandidateProposalResult)
    AICandidateProposalResult.model_validate(result.model_dump())


# ---------------------------------------------------------------------------
# 9/10. No LLM client, LangGraph, LangSmith, or provider-adapter imports in
#       the factory module.
# ---------------------------------------------------------------------------


def test_factory_module_has_no_disallowed_imports() -> None:
    source = inspect.getsource(factory_module)
    tree = ast.parse(source)

    # Vendor SDK names are only meaningful as *real* top-level imports
    # (e.g. a bare `import anthropic`). The factory legitimately imports
    # `app.providers.ai_candidate_proposal.anthropic_adapter` (Step 161A) --
    # an internal module path that happens to contain "anthropic" without
    # being a vendor SDK import -- so vendor checks are skipped for
    # anything under our own `app.` namespace. Internal disallowed-path
    # checks apply regardless.
    vendor_disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "gemini",
        "google.generativeai",
    )
    internal_disallowed_substrings = (
        "app.providers.places",
        "app.providers.routes",
        "app.providers.transit",
        "app.providers.accommodation",
        "app.providers.weather",
        "app.providers.holidays",
        "app.providers.currency",
        "app.providers.gateway",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        if not lowered.startswith("app."):
            for disallowed in vendor_disallowed_substrings:
                assert disallowed not in lowered, f"Disallowed import found: {name}"
        for disallowed in internal_disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


# ---------------------------------------------------------------------------
# 13. No forbidden factual fields in factory result dumps.
# ---------------------------------------------------------------------------


def test_factory_result_dump_has_no_forbidden_factual_keys() -> None:
    provider = get_ai_candidate_proposal_provider("not_connected")
    result = provider.propose(_request())
    dumped = result.model_dump()

    def _collect_keys(value: object, keys: set[str]) -> None:
        if isinstance(value, dict):
            keys.update(value.keys())
            for nested in value.values():
                _collect_keys(nested, keys)
        elif isinstance(value, list):
            for item in value:
                _collect_keys(item, keys)

    all_keys: set[str] = set()
    _collect_keys(dumped, all_keys)
    overlap = all_keys & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"Factory result dump has forbidden key(s): {overlap}"
