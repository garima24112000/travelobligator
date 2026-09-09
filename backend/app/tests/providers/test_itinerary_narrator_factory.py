from __future__ import annotations

import ast
import inspect

import pytest

from app.core.config import Settings
from app.providers.itinerary_narrator import (
    AnthropicItineraryNarratorProvider,
    GroqItineraryNarratorProvider,
    NotConnectedItineraryNarratorProvider,
    get_itinerary_narrator_provider,
)
from app.providers.itinerary_narrator import factory as factory_module

# Safety tests for the Step 182F itinerary narrator provider factory.
# Mirrors test_ai_candidate_proposal_factory.py, but for a completely
# separate feature/config surface. Never calls a real network service.


def test_factory_returns_not_connected_provider_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ITINERARY_NARRATOR_PROVIDER", raising=False)
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    provider = get_itinerary_narrator_provider()

    assert isinstance(provider, NotConnectedItineraryNarratorProvider)


def test_factory_returns_not_connected_provider_for_explicit_name() -> None:
    provider = get_itinerary_narrator_provider("not_connected")
    assert isinstance(provider, NotConnectedItineraryNarratorProvider)


def test_factory_returns_anthropic_provider_for_explicit_name() -> None:
    provider = get_itinerary_narrator_provider("anthropic")
    assert isinstance(provider, AnthropicItineraryNarratorProvider)


def test_factory_returns_groq_provider_for_explicit_name() -> None:
    provider = get_itinerary_narrator_provider("groq")
    assert isinstance(provider, GroqItineraryNarratorProvider)


@pytest.mark.parametrize(
    "unsupported_name",
    ["openai", "gemini", "made_up_provider", "", "NOT_CONNECTED", "kiwi_mcp"],
)
def test_factory_falls_back_to_not_connected_for_unsupported_names(
    unsupported_name: str,
) -> None:
    provider = get_itinerary_narrator_provider(unsupported_name)

    assert isinstance(provider, NotConnectedItineraryNarratorProvider)


def test_factory_uses_settings_when_no_provider_name_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module, "get_settings", lambda: Settings(itinerary_narrator_provider="groq")
    )
    provider = get_itinerary_narrator_provider()
    assert isinstance(provider, GroqItineraryNarratorProvider)


def test_factory_falls_back_when_settings_hold_unsupported_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module, "get_settings", lambda: Settings(itinerary_narrator_provider="openai")
    )
    provider = get_itinerary_narrator_provider()
    assert isinstance(provider, NotConnectedItineraryNarratorProvider)


def test_factory_module_has_no_disallowed_imports() -> None:
    source = inspect.getsource(factory_module)
    tree = ast.parse(source)

    vendor_disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "selenium",
        "playwright",
    )
    internal_disallowed_substrings = (
        "app.providers.places",
        "app.providers.weather",
        "app.providers.holidays",
        "app.providers.currency",
        "app.providers.gateway",
        "app.providers.routing",
        "app.providers.accommodation",
        "app.providers.flights",
        "app.providers.hotel_ratings",
        "app.providers.ai_candidate_proposal",
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


def test_not_connected_adapter_module_has_no_disallowed_imports() -> None:
    import app.providers.itinerary_narrator.not_connected_adapter as not_connected_module

    source = inspect.getsource(not_connected_module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "groq",
        "anthropic",
        "openai",
        "selenium",
        "playwright",
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
            assert disallowed not in lowered, f"Disallowed import found: {name}"


def test_narrator_is_a_separate_provider_interface_from_ai_candidate_proposal() -> None:
    """The narrator must not be mixed into
    AICandidateProposalProvider -- it is its own ABC with its own
    request/report contract."""
    from app.providers.ai_candidate_proposal.base import AICandidateProposalProvider
    from app.providers.itinerary_narrator.base import ItineraryNarratorProvider

    assert ItineraryNarratorProvider is not AICandidateProposalProvider
    assert not issubclass(ItineraryNarratorProvider, AICandidateProposalProvider)
    assert not issubclass(AICandidateProposalProvider, ItineraryNarratorProvider)
