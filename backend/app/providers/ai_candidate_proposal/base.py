from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.ai_candidate_proposal import AICandidateProposalRequest, AICandidateProposalResult

# Provider boundary for a future AI-assisted candidate discovery layer
# (Step 157B, itinerary-generator-build-spec.md Stage 5,
# docs/13_llm_reasoning_pipeline.md section 29,
# docs/14_backend_architecture.md section 25). Still no LLM, LangGraph, or
# LangSmith dependency -- this module only defines the interface a future
# LLM-backed adapter must implement. `AICandidateProposalRequest`/
# `AICandidateProposalResult` (Step 157A) remain the only contract-shape
# any adapter is allowed to accept/return; a `propose` implementation may
# never bypass those models' validation (no coordinate, provider source,
# rating, price, opening-hours, booking/availability, or route/timing
# field on a proposal, and every proposal must still be grounded/verified
# against provider/open data before it can be scheduled).


class AICandidateProposalProvider(ABC):
    """Interface for a provider that proposes candidate place/area ideas
    for later grounding (build spec Stage 5). Unlike the other provider
    interfaces in `app.providers.base` (which default to an honest
    `not_connected` `ProviderResponse`), this uses `abc.ABC` so every
    concrete adapter must explicitly implement `propose` -- there is no
    silent default behavior to fall back on.
    """

    provider_name: str = "ai_candidate_proposal_provider"

    @abstractmethod
    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        """Return an `AICandidateProposalResult` for `request`.

        Implementations must never invent an attraction, restaurant,
        accommodation, coordinate, price, rating, opening hour, route
        time, review count, ticket price, availability, booking link, or
        safety score. Every returned proposal still requires independent
        grounding/verification (build spec Stage 6) before it can be
        scheduled.
        """
        raise NotImplementedError
