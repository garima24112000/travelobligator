from __future__ import annotations

from app.core.config import get_settings
from app.providers.itinerary_narrator.anthropic_adapter import AnthropicItineraryNarratorProvider
from app.providers.itinerary_narrator.base import ItineraryNarratorProvider
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.providers.itinerary_narrator.not_connected_adapter import (
    NotConnectedItineraryNarratorProvider,
)

# Config-gated provider-selection boundary (Step 182F), mirroring
# app.providers.ai_candidate_proposal.factory but for the completely
# separate itinerary-narrator feature. No LangGraph/LangSmith dependency
# is added here -- this module only selects between
# `ItineraryNarratorProvider` adapters that already exist. The default
# remains `"not_connected"`, and even when `itinerary_narrator_enabled`
# is `True`, `itinerary_narrator_provider` itself still defaults to
# `"not_connected"` -- both adapters below are themselves safe by
# default: with no `ANTHROPIC_API_KEY`/`GROQ_API_KEY` configured they
# return an honest `not_connected` result instead of calling the real
# API. `ItineraryNarrativeService` resolves its default provider through
# this factory rather than constructing an adapter directly, and also
# checks `Settings.itinerary_narrator_enabled` itself before ever calling
# it (see that service's own docstring).

_SUPPORTED_PROVIDERS: dict[str, type[ItineraryNarratorProvider]] = {
    "not_connected": NotConnectedItineraryNarratorProvider,
    "anthropic": AnthropicItineraryNarratorProvider,
    "groq": GroqItineraryNarratorProvider,
}


def get_itinerary_narrator_provider(
    provider_name: str | None = None,
) -> ItineraryNarratorProvider:
    """Resolves an `ItineraryNarratorProvider` from `provider_name`, or
    from `Settings.itinerary_narrator_provider` (default
    `"not_connected"`) when `provider_name` is omitted.

    An unsupported/unrecognized provider name can never silently create
    fake narrative text: it falls back to the same honest
    `NotConnectedItineraryNarratorProvider` used when nothing is
    configured at all, rather than raising or guessing. Unknown
    configuration is functionally identical to "not connected," so it is
    treated identically.
    """
    resolved_name = (
        provider_name if provider_name is not None else get_settings().itinerary_narrator_provider
    )

    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, NotConnectedItineraryNarratorProvider)
    return provider_cls()
