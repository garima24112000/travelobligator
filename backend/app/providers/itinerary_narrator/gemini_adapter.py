from __future__ import annotations

from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.providers.llm_provider_health import GEMINI

# `ITINERARY_NARRATOR_PROVIDER=gemini`. Not a second implementation: the
# stage adapter is the same one (same grounded prompt, same wire schema, same
# place-identity check; the sanitizer and grounding checks live in the
# service and apply to every provider) -- only which member of the Groq <->
# Gemini pair it stands for differs. With `LLM_FAILOVER_ENABLED=false` this
# stage calls Gemini only; with failover on it is routed over the configured
# pair exactly like the "groq" selector
# (`app/providers/llm_provider_router.py`).


class GeminiItineraryNarratorProvider(GroqItineraryNarratorProvider):
    provider_name = "gemini_itinerary_narrator_provider"
    _selected_provider = GEMINI
