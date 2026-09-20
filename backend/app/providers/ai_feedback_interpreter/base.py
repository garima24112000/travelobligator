from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationRequest,
    AIFeedbackInterpretationResult,
)

# Provider boundary for the Section 196 AI feedback interpreter
# (docs/14_backend_architecture.md, following section 146). Mirrors
# `app.providers.ai_itinerary_reasoning.base.AIItineraryReasoningProvider`/
# `app.providers.itinerary_narrator.base.ItineraryNarratorProvider`
# exactly -- same `abc.ABC` shape, same "the contract models are the only
# thing an adapter may accept/return" rule.
#
# `AIFeedbackInterpretationRequest`/`AIFeedbackInterpretationResult`
# remain the only contract shape any adapter is allowed to accept/
# return -- an `interpret` implementation may never bypass those models'
# validation, and every `experience_id`/`day_index` referenced in a
# `completed` result must already resolve against `request.current_items`
# (enforced by
# `app.models.ai_feedback_interpretation.validate_interpretation_against_request`,
# which every adapter below calls itself before ever reporting
# `completed`).


class AIFeedbackInterpreterProvider(ABC):
    """Interface for a provider that translates natural-language
    itinerary feedback, plus the current final itinerary context, into a
    structured change request. Uses `abc.ABC` so every concrete adapter
    must explicitly implement `interpret` -- there is no silent default
    behavior to fall back on.
    """

    provider_name: str = "ai_feedback_interpreter_provider"

    @abstractmethod
    def interpret(self, request: AIFeedbackInterpretationRequest) -> AIFeedbackInterpretationResult:
        """Return an `AIFeedbackInterpretationResult` for `request`.

        Implementations must never invent a new experience_id, a new
        place's provider id/coordinates/price/rating/opening hour/
        route duration/availability, or resolve an ambiguous reference by
        guessing. Every experience_id/day_index in a `completed` result
        must resolve against `request.current_items`/
        `request.trip_duration_days` -- an implementation that receives
        an out-of-bounds reference must report `rejected`, never
        silently drop it while still reporting `completed`.
        """
        raise NotImplementedError
