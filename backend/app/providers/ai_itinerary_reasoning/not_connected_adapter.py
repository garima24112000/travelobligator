from __future__ import annotations

from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningRequest,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
)
from app.providers.ai_itinerary_reasoning.base import AIItineraryReasoningProvider

# Default itinerary-reasoning adapter (Section 193B). No LLM provider is
# connected yet, so `reason` always returns an honest `not_connected`
# result -- it never calls a network service, never inspects `request`
# beyond preserving nothing from it, and never produces a day plan.


class NotConnectedAIItineraryReasoningProvider(AIItineraryReasoningProvider):
    """`AIItineraryReasoningProvider` implementation used until a real
    LLM-backed adapter is configured and enabled. `reason` is
    deterministic: given any request, it always returns the same empty,
    `not_connected` `AIItineraryReasoningResult`.
    """

    provider_name = "ai_itinerary_reasoning_provider"

    def reason(self, request: AIItineraryReasoningRequest) -> AIItineraryReasoningResult:
        return AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.NOT_CONNECTED,
            days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport(
                passed=False,
                blocked_reasons=["AI itinerary-reasoning provider is not connected yet."],
                checked_fields=["provider_connection"],
            ),
            provider_name=self.provider_name,
            model_name=None,
            confidence=0.0,
        )
