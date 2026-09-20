from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.ai_itinerary_reasoning import AIItineraryReasoningRequest, AIItineraryReasoningResult
from app.models.ai_itinerary_repair import AIItineraryRepairRequest, AIItineraryRepairResult

# Provider boundary for the Section 193A/193B itinerary-reasoning layer
# (LLM #2, docs/14_backend_architecture.md sections 141-142), extended in
# Section 194A with a `repair` method for the same LLM #2 job responding
# to deterministic validation findings. Mirrors
# `app.providers.ai_candidate_proposal.base.AICandidateProposalProvider`
# exactly -- same `abc.ABC` shape, same "the contract models are the only
# thing an adapter may accept/return" rule -- because this is the same
# kind of provider boundary (structured, candidate-safe LLM output), not
# `app.providers.itinerary_narrator.base.ItineraryNarratorProvider` (a
# different feature: read-only prose over an already-finished plan).
#
# `AIItineraryReasoningRequest`/`AIItineraryReasoningResult` (Section 193A)
# remain the only contract shape any adapter is allowed to accept/return
# for `reason` -- a `reason` implementation may never bypass those
# models' validation, and every candidate_id in a `completed` result must
# already be a member of `request.allowed_candidates` (enforced by
# `app.models.ai_itinerary_reasoning.validate_result_against_request`,
# which every adapter below calls itself before ever reporting
# `completed`). `AIItineraryRepairRequest`/`AIItineraryRepairResult`
# (Section 194A) are the equivalent contract for `repair` -- same rule,
# narrower scope (only the day_index values in `request.affected_days`
# may be repaired; every candidate_id still must already be a member of
# `request.allowed_candidates`).
#
# `repair` is added directly to this same ABC (Task 9), rather than a
# separate provider interface, so every existing adapter's client setup/
# API-key resolution/model resolution is reused as-is instead of
# duplicated in a second provider hierarchy -- a repair call is the same
# LLM #2 job (select/group/order already-verified candidates), just
# scoped to a subset of days in response to a validator finding.


class AIItineraryReasoningProvider(ABC):
    """Interface for a provider that selects, groups, and coarsely orders
    already-verified, provider-backed candidates into a day-by-day
    itinerary-reasoning proposal, and can repair a subset of days in an
    already-completed proposal in response to deterministic validation
    findings. Uses `abc.ABC` so every concrete adapter must explicitly
    implement both `reason` and `repair` -- there is no silent default
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

    @abstractmethod
    def repair(self, request: AIItineraryRepairRequest) -> AIItineraryRepairResult:
        """Return an `AIItineraryRepairResult` for `request`.

        Implementations must never invent a candidate_id, place,
        coordinate, price, rating, opening hour, availability claim,
        booking link, or route time/distance, and must never modify a
        day_index outside `request.affected_days`. Every candidate_id in
        a `completed` result must resolve to exactly one entry in
        `request.allowed_candidates` -- an implementation that receives a
        candidate_id outside that set, or a day outside
        `request.affected_days`, must report `rejected`, never silently
        drop/ignore it while still reporting `completed`.
        """
        raise NotImplementedError
