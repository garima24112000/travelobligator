from __future__ import annotations

from app.core.config import get_settings
from app.providers.ai_itinerary_reasoning.anthropic_adapter import (
    AnthropicAIItineraryReasoningProvider,
)
from app.providers.ai_itinerary_reasoning.base import AIItineraryReasoningProvider
from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider
from app.providers.ai_itinerary_reasoning.not_connected_adapter import (
    NotConnectedAIItineraryReasoningProvider,
)

# Config-gated provider-selection boundary (Section 193B,
# docs/14_backend_architecture.md section 142), mirroring
# `app.providers.ai_candidate_proposal.factory`/
# `app.providers.itinerary_narrator.factory` exactly. No LangGraph,
# LangSmith, or OpenAI/Gemini client code is added here -- this module
# only selects between `AIItineraryReasoningProvider` adapters that
# already exist. The default remains `"not_connected"`.

_SUPPORTED_PROVIDERS: dict[str, type[AIItineraryReasoningProvider]] = {
    "not_connected": NotConnectedAIItineraryReasoningProvider,
    "anthropic": AnthropicAIItineraryReasoningProvider,
    "groq": GroqAIItineraryReasoningProvider,
}


def get_ai_itinerary_reasoning_provider(
    provider_name: str | None = None,
) -> AIItineraryReasoningProvider:
    """Resolves an `AIItineraryReasoningProvider` from `provider_name`, or
    from `Settings.ai_itinerary_reasoning_provider` (default
    `"not_connected"`) when `provider_name` is omitted.

    An unsupported/unrecognized provider name can never silently create
    fake reasoning output: it falls back to the same honest
    `NotConnectedAIItineraryReasoningProvider` used when nothing is
    configured at all, rather than raising or guessing -- matching
    `get_ai_candidate_proposal_provider`/`get_itinerary_narrator_provider`
    exactly. Unknown configuration is functionally identical to "not
    connected," so it is treated identically; it is never silently mapped
    to Groq or any other real provider.
    """
    resolved_name = (
        provider_name if provider_name is not None else get_settings().ai_itinerary_reasoning_provider
    )

    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, NotConnectedAIItineraryReasoningProvider)
    return provider_cls()
