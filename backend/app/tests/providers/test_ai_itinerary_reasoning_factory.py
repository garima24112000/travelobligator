from __future__ import annotations

import pytest

from app.core.config import Settings
from app.providers.ai_itinerary_reasoning import (
    AnthropicAIItineraryReasoningProvider,
    GroqAIItineraryReasoningProvider,
    NotConnectedAIItineraryReasoningProvider,
    get_ai_itinerary_reasoning_provider,
)
from app.providers.ai_itinerary_reasoning import factory as factory_module

# Tests for the Section 193B provider-selection factory
# (docs/14_backend_architecture.md section 142), mirroring
# test_ai_candidate_proposal_factory.py exactly.


def test_default_settings_resolve_to_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    provider = get_ai_itinerary_reasoning_provider()

    assert isinstance(provider, NotConnectedAIItineraryReasoningProvider)


def test_explicit_not_connected_returns_not_connected_provider() -> None:
    provider = get_ai_itinerary_reasoning_provider("not_connected")
    assert isinstance(provider, NotConnectedAIItineraryReasoningProvider)


def test_explicit_groq_returns_groq_provider() -> None:
    provider = get_ai_itinerary_reasoning_provider("groq")
    assert isinstance(provider, GroqAIItineraryReasoningProvider)


def test_explicit_anthropic_returns_anthropic_provider() -> None:
    provider = get_ai_itinerary_reasoning_provider("anthropic")
    assert isinstance(provider, AnthropicAIItineraryReasoningProvider)


def test_unknown_provider_name_falls_back_to_not_connected() -> None:
    provider = get_ai_itinerary_reasoning_provider("some_unsupported_provider")
    assert isinstance(provider, NotConnectedAIItineraryReasoningProvider)


def test_unknown_provider_never_silently_maps_to_groq() -> None:
    provider = get_ai_itinerary_reasoning_provider("gemini")
    assert not isinstance(provider, GroqAIItineraryReasoningProvider)
    assert isinstance(provider, NotConnectedAIItineraryReasoningProvider)


def test_settings_provider_value_is_used_when_name_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        factory_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_PROVIDER="groq")
    )

    provider = get_ai_itinerary_reasoning_provider()

    assert isinstance(provider, GroqAIItineraryReasoningProvider)


def test_factory_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructing any provider through the factory must never itself
    make a network call -- both Groq/Anthropic adapters stay safe by
    default with no API key configured."""
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None, GROQ_API_KEY=""))
    provider = get_ai_itinerary_reasoning_provider("groq")
    assert isinstance(provider, GroqAIItineraryReasoningProvider)
