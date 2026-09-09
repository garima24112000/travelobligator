from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.itinerary_narrative import ItineraryNarrativeReport, ItineraryNarrativeRequest

# Provider boundary for the itinerary narrator (Step 182F,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
# Deliberately a separate interface from
# `app.providers.ai_candidate_proposal.base.AICandidateProposalProvider`
# -- the narrator is a different feature (read-only prose over an
# already-finished plan) with a different contract (it never proposes a
# candidate, never requires grounding, and is never fed into scheduling).
# `ItineraryNarrativeRequest`/`ItineraryNarrativeReport` (Step 182F)
# remain the only contract shape any adapter is allowed to accept/return
# -- a `narrate` implementation may never bypass those models' validation
# (no price, rating, review count, route/travel-time duration, booking
# link, availability flag, opening hour, or flight-schedule field exists
# on either model).


class ItineraryNarratorProvider(ABC):
    """Interface for a provider that turns an `ItineraryNarrativeRequest`
    (a strict allow-list of already-computed `PlanningState` fields) into
    polished traveler-facing prose. Uses `abc.ABC` so every concrete
    adapter must explicitly implement `narrate` -- there is no silent
    default behavior to fall back on.
    """

    provider_name: str = "itinerary_narrator_provider"

    @abstractmethod
    def narrate(self, request: ItineraryNarrativeRequest) -> ItineraryNarrativeReport:
        """Return an `ItineraryNarrativeReport` for `request`.

        Implementations must never invent a hotel, flight, price, rating,
        review count, route/travel-time duration, booking confirmation,
        or a clock time not already present in `request`. Output is
        presentation prose only -- it must never be treated as a new
        factual travel claim, and it never feeds back into scheduling,
        validation, or provider coverage.
        """
        raise NotImplementedError
