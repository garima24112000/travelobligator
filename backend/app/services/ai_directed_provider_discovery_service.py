from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.models.ai_candidate_proposal import (
    AICandidatePriorityHint,
    AICandidateProposal,
    AICandidateProposalType,
)
from app.models.ai_provider_discovery import (
    AIProviderDiscoveryAttempt,
    AIProviderDiscoveryAttemptStatus,
    AIProviderDiscoveryResult,
)
from app.models.candidate_grounding import ProviderCandidateForGrounding
from app.models.common import ProviderStatus
from app.models.planning_state import PlanningState
from typing import Any

from app.providers.gateway import ProviderGateway, provider_gateway
from app.core.provider_usage import GenerationProviderContext
from app.services.candidate_grounding_service import find_broad_pool_name_matches

logger = logging.getLogger(__name__)

# Section 192.1: deterministic selection order when more proposals need a
# targeted lookup than `Settings.ai_directed_provider_discovery_max_searches`
# allows in one call -- high priority first, then medium, then low, then
# unknown last (a proposal the LLM itself didn't mark confidently
# important is a conservative last pick, never a random one). Ties within
# the same priority are broken by original proposal order (stable sort),
# never randomly -- no LLM call is ever made to choose.
_PRIORITY_SEARCH_ORDER: dict[AICandidatePriorityHint, int] = {
    AICandidatePriorityHint.HIGH: 0,
    AICandidatePriorityHint.MEDIUM: 1,
    AICandidatePriorityHint.LOW: 2,
    AICandidatePriorityHint.UNKNOWN: 3,
}

# Section 202C.1C: the minimum proposal confidence for a NAMED place to draw
# on the reserve budget (see `discover`).
_RESERVE_MIN_CONFIDENCE = 0.7

# Section 192 (docs/14_backend_architecture.md section 138): the targeted,
# AI-directed provider-discovery step between AI candidate proposals
# (Sections 191A/191B) and grounding (`CandidateGroundingService`). This is
# an ADDITIONAL targeted path, not a replacement for the existing broad
# `destination_context` provider discovery -- `DestinationContextService`'s
# own `search_attractions`/`search_restaurants`/`search_accommodation_pois`
# calls are completely untouched by this module.
#
# `discover` reaches provider data only through `ProviderGateway.places`
# (`self.gateway.places.search_must_visit_place`, the same targeted-
# lookup method `DestinationContextService._append_must_visit_candidates`
# already uses for a traveler's explicit must-visit terms) -- never a
# direct OpenStreetMap/Nominatim/httpx call, matching the required
# `service -> ProviderGateway -> provider adapter` layering every other
# stage service already follows. It never calls an LLM, never calls
# `CandidateGroundingService` itself, and never mutates `PlanningState`.
#
# Core rule (unchanged from Sections 191A/191B): the LLM decides what to
# investigate; only a real provider response can decide what actually
# exists. `AIProviderDiscoveryAttempt.match` is populated exclusively from
# a `NormalizedPlace` a real `PlacesProvider` call returned -- never from
# `AICandidateProposal.candidate_name`/`search_query` text itself, and
# never guessed when the provider finds nothing or fails.


class AIDirectedProviderDiscoveryService:
    """Runs one targeted provider lookup (`search_must_visit_place`) per
    AI candidate proposal that actually needs one, bounded by
    `Settings.ai_directed_provider_discovery_max_searches`.

    A `named_place` proposal only needs a lookup when it does *not*
    already have exactly one clean match in the broad `provider_candidates`
    pool (`find_broad_pool_name_matches`, the same predicate
    `CandidateGroundingService` itself uses) -- this both avoids a wasted
    provider call for a proposal broad matching would have grounded anyway,
    and keeps the search bound meaningful (it is spent only on proposals
    that actually need it). A `discovery_query` proposal always needs a
    lookup, since it has no broad-pool name to match by at all.
    """

    def __init__(self, gateway: ProviderGateway | None = None) -> None:
        self.gateway = gateway or provider_gateway

    def discover(
        self,
        planning_state: PlanningState,
        proposals: list[AICandidateProposal],
        provider_candidates: list[ProviderCandidateForGrounding],
        *,
        max_searches: int | None = None,
        max_extra_searches: int | None = None,
        provider_context: GenerationProviderContext | None = None,
    ) -> AIProviderDiscoveryResult:
        # Section 203C.2B: every lookup below is charged to THIS generation.
        places_for = getattr(self.gateway, "places_for", None)
        places = places_for(provider_context) if callable(places_for) else self.gateway.places
        destination_name = planning_state.trip_request.primary_destination
        bound = (
            max_searches
            if max_searches is not None
            else get_settings().ai_directed_provider_discovery_max_searches
        )
        provider_name = getattr(self.gateway.places, "provider_name", None)

        # Task 5/6 (Section 192.1): every proposal that actually needs a
        # lookup -- named_place with no clean broad-pool match, or any
        # discovery_query -- draws from one shared budget, never a
        # per-type budget. `eligible_indices` preserves original proposal
        # order (used below both for `attempts`' reporting order and as
        # the stable tie-break for selection); `selected_indices` is the
        # deterministic subset (high priority first, then medium, then
        # low, then unknown, ties broken by original order -- never
        # random, never an LLM call) that actually spends a real provider
        # call when there are more eligible proposals than `bound` allows.
        eligible_indices: list[int] = []
        for index, proposal in enumerate(proposals):
            if proposal.proposal_type == AICandidateProposalType.NAMED_PLACE:
                assert proposal.candidate_name is not None
                if len(find_broad_pool_name_matches(proposal.candidate_name, provider_candidates)) == 1:
                    # Already cleanly grounded by the broad pool -- no
                    # targeted lookup needed, and this proposal never even
                    # counts against the search bound.
                    continue
            eligible_indices.append(index)

        # Section 202C.1C: bounded, ranked, de-duplicated selection.
        #   1. De-duplicate: two proposals with the same normalised search
        #      query cost one lookup, not two (the later one is `not_searched`).
        #   2. Rank: priority hint, then named places before free-text ideas,
        #      then the proposal's own confidence (higher first), then original
        #      order -- never random. Confidence
        #      only orders WHICH ideas are checked; it is never a fact and
        #      never reaches scoring.
        #   3. Base budget: the first `bound` ranked proposals are looked up.
        #   4. Reserve: up to `extra_bound` further lookups, only for NAMED
        #      proposals with confidence >= `_RESERVE_MIN_CONFIDENCE`, and only
        #      while fewer than `bound` lookups have matched (i.e. a base
        #      lookup came back empty/failed) -- then stop.
        # Real provider calls per generation never exceed bound + extra_bound.
        extra_bound = (
            0
            if max_searches is not None and max_extra_searches is None
            else (
                max_extra_searches
                if max_extra_searches is not None
                else get_settings().ai_directed_provider_discovery_max_extra_searches
            )
        )

        seen_queries: set[str] = set()
        duplicate_indices: set[int] = set()
        for index in eligible_indices:
            key = " ".join(proposals[index].search_query.lower().split())
            if key in seen_queries:
                duplicate_indices.add(index)
            else:
                seen_queries.add(key)

        ranked = sorted(
            (i for i in eligible_indices if i not in duplicate_indices),
            key=lambda i: (
                _PRIORITY_SEARCH_ORDER.get(proposals[i].priority_hint, len(_PRIORITY_SEARCH_ORDER)),
                # a NAMED place is something a geocoder can actually find; a free-text
                # idea ("historic neighbourhood walk in ...") rarely is, so within one
                # priority the named places are checked first
                0 if proposals[i].proposal_type == AICandidateProposalType.NAMED_PLACE else 1,
                -proposals[i].confidence,
                i,
            ),
        )

        attempts_by_index: dict[int, AIProviderDiscoveryAttempt] = {}
        searches_used = 0
        matched = 0

        def run_search(index: int) -> None:
            nonlocal searches_used, matched
            proposal = proposals[index]
            searches_used += 1
            started_at = time.monotonic()
            attempt = self._search_one(destination_name, proposal, places)
            duration_ms = (time.monotonic() - started_at) * 1000
            logger.info(
                "AI-directed provider discovery attempt completed.",
                extra={
                    "stage": "ai_directed_provider_discovery",
                    "proposal_type": proposal.proposal_type.value,
                    "provider": provider_name or "unknown",
                    "status": attempt.status.value,
                    "duration_ms": round(duration_ms, 3),
                },
            )
            if attempt.status == AIProviderDiscoveryAttemptStatus.MATCHED:
                matched += 1
            attempts_by_index[index] = attempt

        for index in ranked[:bound]:
            run_search(index)

        extras_used = 0
        for index in ranked[bound:]:
            if extras_used >= extra_bound or matched >= bound:
                break
            proposal = proposals[index]
            if (
                proposal.proposal_type != AICandidateProposalType.NAMED_PLACE
                or proposal.confidence < _RESERVE_MIN_CONFIDENCE
            ):
                continue
            extras_used += 1
            run_search(index)

        attempts: list[AIProviderDiscoveryAttempt] = []
        for index in eligible_indices:
            if index in attempts_by_index:
                attempts.append(attempts_by_index[index])
                continue
            proposal = proposals[index]
            attempts.append(
                AIProviderDiscoveryAttempt(
                    proposal_id=proposal.proposal_id,
                    search_query=proposal.search_query,
                    status=AIProviderDiscoveryAttemptStatus.NOT_SEARCHED,
                    message=(
                        "Provider search was not executed because an earlier proposal already "
                        "searched for the same query."
                        if index in duplicate_indices
                        else "Provider search was not executed because the targeted-search "
                        "limit for this generation was already reached."
                    ),
                )
            )

        # Defensive invariant, not a business rule: real provider calls can
        # never exceed the base bound plus the reserve.
        assert searches_used <= bound + extra_bound

        return AIProviderDiscoveryResult(
            attempts=attempts,
            provider_name=provider_name,
            searched_count=sum(
                1 for attempt in attempts if attempt.status != AIProviderDiscoveryAttemptStatus.NOT_SEARCHED
            ),
            matched_count=sum(
                1 for attempt in attempts if attempt.status == AIProviderDiscoveryAttemptStatus.MATCHED
            ),
        )

    def _search_one(
        self, destination_name: str, proposal: AICandidateProposal, places: Any = None
    ) -> AIProviderDiscoveryAttempt:
        """One real `search_must_visit_place(search_query, destination_name)`
        call -- the same targeted Nominatim lookup
        `DestinationContextService` already uses for must-visit terms,
        reused here for an AI-proposed search intent/name instead. Returns
        at most one match (the provider's own contract); never fabricates
        a place when the provider finds nothing, fails, or is not
        connected.
        """
        query = proposal.search_query
        try:
            response = (places or self.gateway.places).search_must_visit_place(query, destination_name)
        except Exception as exc:  # provider/test-double failure -> honest, non-fatal
            logger.warning(
                "AI-directed provider discovery lookup raised unexpectedly: %s", exc
            )
            return AIProviderDiscoveryAttempt(
                proposal_id=proposal.proposal_id,
                search_query=query,
                status=AIProviderDiscoveryAttemptStatus.PROVIDER_FAILED,
                message="Provider lookup raised an unexpected error.",
            )

        if response.status == ProviderStatus.NOT_CONNECTED:
            return AIProviderDiscoveryAttempt(
                proposal_id=proposal.proposal_id,
                search_query=query,
                status=AIProviderDiscoveryAttemptStatus.PROVIDER_NOT_CONNECTED,
                message=response.message or "Places provider is not connected.",
            )

        if response.status == ProviderStatus.FAILED:
            return AIProviderDiscoveryAttempt(
                proposal_id=proposal.proposal_id,
                search_query=query,
                status=AIProviderDiscoveryAttemptStatus.PROVIDER_FAILED,
                message=response.message or "Provider lookup failed.",
            )

        place = response.data[0] if response.data else None
        if place is None or place.coordinates is None:
            return AIProviderDiscoveryAttempt(
                proposal_id=proposal.proposal_id,
                search_query=query,
                status=AIProviderDiscoveryAttemptStatus.NOT_FOUND,
                message=response.message or "Provider search returned no usable result.",
            )

        match = ProviderCandidateForGrounding(
            provider_name=place.source,
            provider_place_id=place.place_id,
            name=place.name,
            category=place.category,
            coordinates=place.coordinates,
            data_status=place.data_status,
            confidence=place.confidence,
            provider_tags=place.provider_tags,
        )
        return AIProviderDiscoveryAttempt(
            proposal_id=proposal.proposal_id,
            search_query=query,
            status=AIProviderDiscoveryAttemptStatus.MATCHED,
            match=match,
            message=f"Provider matched search_query {query!r} to {place.name!r}.",
        )
