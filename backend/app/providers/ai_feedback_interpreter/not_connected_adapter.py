from __future__ import annotations

from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationRequest,
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
)
from app.providers.ai_feedback_interpreter.base import AIFeedbackInterpreterProvider

# Default feedback-interpreter adapter (Section 196). No LLM provider is
# connected yet, so `interpret` always returns an honest `not_connected`
# result -- it never calls a network service, never inspects `request`
# beyond preserving nothing from it, and never produces an action.


class NotConnectedAIFeedbackInterpreterProvider(AIFeedbackInterpreterProvider):
    """`AIFeedbackInterpreterProvider` implementation used until a real
    LLM-backed adapter is configured and enabled. `interpret` is
    deterministic: given any request, it always returns the same empty,
    `not_connected` `AIFeedbackInterpretationResult`.
    """

    provider_name = "ai_feedback_interpreter_provider"

    def interpret(self, request: AIFeedbackInterpretationRequest) -> AIFeedbackInterpretationResult:
        return AIFeedbackInterpretationResult(
            status=AIFeedbackInterpretationStatus.NOT_CONNECTED,
            blocked_reasons=["AI feedback interpreter is not connected yet."],
            provider_name=self.provider_name,
            model_name=None,
            confidence=0.0,
        )
