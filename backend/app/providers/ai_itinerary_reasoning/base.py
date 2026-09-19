from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.ai_itinerary_reasoning import AIItineraryReasoningRequest, AIItineraryReasoningResult

# Provider boundary for the Section 193A/193B itinerary-reasoning layer
# (LLM #2, docs/14_backend_architecture.md sections 141-142). Mirrors
# `app.providers.ai_candidate_proposal.base.AICandidateProposalProvider`
# exactly -- same `abc.ABC` shape, same "the contract models are the only
# thing an adapter may accept/return" rule -- because this is the same
# kind of provider boundary (structured, candidate-safe LLM output), not
# `app.providers.itinerary_narrator.base.ItineraryNarratorProvider` (a
# different feature: read-only prose over an already-finished plan).
#
# `AIItineraryReasoningRequest`/`AIItineraryReasoningResult` (Section 193A)
# remain the only contract shape any adapter is allowed to accept/return --
# a `reason` implementation may never bypass those models' validation, and
# every candidate_id in a `completed` result must already be a member of
# `request.allowed_candidates` (enforced by
# `app.models.ai_itinerary_reasoning.validate_result_against_request`,
# which every adapter below calls itself before ever reporting `completed`).


class AIItineraryReasoningProvider(ABC):
    """Interface for a provider that selects, groups, and coarsely orders
    already-verified, provider-backed candidates into a day-by-day
    itinerary-reasoning proposal. Uses `abc.ABC` so every concrete adapter
    must explicitly implement `reason` -- there is no silent default
    behavior to fall back on.
    """

    provider_name: str = "ai_itinerary_reasoning_provider"

    @abstractmethod
    def reason(self, request: AIItineraryReasoningRequest) -> AIItineraryReasoningResult:
        """Return an `AIItineraryReasoningResult` for `request`.

        Implementations must never invent a candidate_id, place,
        coordinate, price, rating, opening hour, availability claim,
        booking link, or route time/distance. Every candidate_id in a
        `completed` result must resolve to exactly one entry in
        `request.allowed_candidates` -- an implementation that receives a
        candidate_id outside that set must report `rejected`, never
        silently drop it while still reporting `completed`.
        """
        raise NotImplementedError
