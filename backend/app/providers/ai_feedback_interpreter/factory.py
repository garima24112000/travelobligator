from __future__ import annotations

from app.core.config import get_settings
from app.providers.ai_feedback_interpreter.anthropic_adapter import (
    AnthropicAIFeedbackInterpreterProvider,
)
from app.providers.ai_feedback_interpreter.base import AIFeedbackInterpreterProvider
from app.providers.ai_feedback_interpreter.groq_adapter import GroqAIFeedbackInterpreterProvider
from app.providers.ai_feedback_interpreter.not_connected_adapter import (
    NotConnectedAIFeedbackInterpreterProvider,
)

# Config-gated provider-selection boundary (Section 196), mirroring
# `app.providers.ai_itinerary_reasoning.factory`/
# `app.providers.itinerary_narrator.factory` exactly. No LangGraph/
# LangSmith dependency is added here -- this module only selects between
# `AIFeedbackInterpreterProvider` adapters that already exist. The
# default remains `"not_connected"`.

_SUPPORTED_PROVIDERS: dict[str, type[AIFeedbackInterpreterProvider]] = {
    "not_connected": NotConnectedAIFeedbackInterpreterProvider,
    "anthropic": AnthropicAIFeedbackInterpreterProvider,
    "groq": GroqAIFeedbackInterpreterProvider,
}


def get_ai_feedback_interpreter_provider(
    provider_name: str | None = None,
) -> AIFeedbackInterpreterProvider:
    """Resolves an `AIFeedbackInterpreterProvider` from `provider_name`,
    or from `Settings.ai_feedback_interpreter_provider` (default
    `"not_connected"`) when `provider_name` is omitted.

    An unsupported/unrecognized provider name can never silently create
    a fake interpretation: it falls back to the same honest
    `NotConnectedAIFeedbackInterpreterProvider` used when nothing is
    configured at all, rather than raising or guessing -- matching
    `get_ai_itinerary_reasoning_provider`/`get_itinerary_narrator_provider`
    exactly.
    """
    resolved_name = (
        provider_name
        if provider_name is not None
        else get_settings().ai_feedback_interpreter_provider
    )

    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, NotConnectedAIFeedbackInterpreterProvider)
    return provider_cls()


# The credential each connected interpreter provider needs (presence only).
_PROVIDER_KEY_FIELDS = {"anthropic": "anthropic_api_key", "groq": "groq_api_key"}


def feedback_interpreter_is_configured() -> bool:
    """M2 Phase 1 (D3): True when a change request could reach a connected
    interpreter -- the stage is switched on, its provider is a connected one,
    and that provider's credential is present. Configuration only: no
    provider is constructed or called, and a present key is not proof it is
    valid (that is only known when the provider answers)."""
    settings = get_settings()
    if not settings.ai_feedback_interpreter_enabled:
        return False
    key_field = _PROVIDER_KEY_FIELDS.get(settings.ai_feedback_interpreter_provider)
    return key_field is not None and bool((getattr(settings, key_field, None) or "").strip())
