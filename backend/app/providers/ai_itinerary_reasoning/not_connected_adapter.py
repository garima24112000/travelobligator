from __future__ import annotations

from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningRequest,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
)
from app.models.ai_itinerary_repair import AIItineraryRepairRequest, AIItineraryRepairResult, AIItineraryRepairStatus
from app.providers.ai_itinerary_reasoning.base import AIItineraryReasoningProvider

# Default itinerary-reasoning adapter (Section 193B; `repair` added in
# Section 194A). No LLM provider is connected yet, so both `reason` and
# `repair` always return an honest `not_connected` result -- neither ever
# calls a network service, and neither inspects `request` beyond
# preserving nothing from it.


class NotConnectedAIItineraryReasoningProvider(AIItineraryReasoningProvider):
    """`AIItineraryReasoningProvider` implementation used until a real
    LLM-backed adapter is configured and enabled. `reason`/`repair` are
    both deterministic: given any request, they always return the same
    empty, `not_connected` result.
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

    def repair(self, request: AIItineraryRepairRequest) -> AIItineraryRepairResult:
        return AIItineraryRepairResult(
            status=AIItineraryRepairStatus.NOT_CONNECTED,
            repaired_days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport(
                passed=False,
                blocked_reasons=["AI itinerary-repair provider is not connected yet."],
                checked_fields=["provider_connection"],
            ),
            provider_name=self.provider_name,
            model_name=None,
            confidence=0.0,
        )
