from __future__ import annotations

from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider
from app.providers.llm_provider_health import GEMINI

# `AI_ITINERARY_REASONING_PROVIDER=gemini` (reasoning and repair). Not a
# second implementation: the stage adapter is the same one (same prompts,
# same wire schemas, same candidate-reference resolution and validation
# against the allowed candidates) -- only which member of the Groq <-> Gemini
# pair it stands for differs. With `LLM_FAILOVER_ENABLED=false` these stages
# call Gemini only; with failover on they are routed over the configured pair
# exactly like the "groq" selector (`app/providers/llm_provider_router.py`).


class GeminiAIItineraryReasoningProvider(GroqAIItineraryReasoningProvider):
    provider_name = "gemini_ai_itinerary_reasoning_provider"
    _selected_provider = GEMINI
