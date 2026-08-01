from __future__ import annotations

from pydantic import BaseModel

from app.models.ai_candidate_proposal import (
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalTask,
)
from app.models.candidate_grounding import CandidateGroundingRequest, CandidateGroundingResult
from app.models.planning_state import PlanningState
from app.providers.ai_candidate_proposal import (
    AICandidateProposalProvider,
    get_ai_candidate_proposal_provider,
)
from app.services.ai_candidate_proposal_request_builder import AICandidateProposalRequestBuilder
from app.services.candidate_grounding_request_builder import CandidateGroundingRequestBuilder
from app.services.candidate_grounding_service import CandidateGroundingService

# Dry-run composition service for the candidate-discovery flow (Step 160B,
# itinerary-generator-build-spec.md Stages 5-6, docs/13_llm_reasoning_
# pipeline.md section 35, docs/14_backend_architecture.md section 25). This
# wires together four already-safe pieces built in Steps 157B/159A/159B/160A
# -- `AICandidateProposalRequestBuilder` -> a proposal provider ->
# `CandidateGroundingRequestBuilder` -> `CandidateGroundingService` -- into
# one deterministic call, without adding a real LLM.
#
# `PlanningOrchestrator` calls `dry_run` (Step 161B, docs/13_llm_reasoning_
# pipeline.md section 40) only from its disabled-by-default AI candidate
# discovery shadow stage -- `dry_run` itself is unaware of that caller and
# behaves identically either way: it still never mutates `planning_state`,
# persists anything, or schedules anything (see below). Nothing else in the
# app (API routes, scheduling, validation, or regeneration/feedback/
# versioning services) calls it.
#
# The default `proposal_provider` is resolved through
# `get_ai_candidate_proposal_provider` (Step 160E, docs/13_llm_reasoning_
# pipeline.md section 38) -- a config-gated factory whose only supported
# value today is `"not_connected"`, mapping to the Step 157B
# `NotConnectedAICandidateProposalProvider`. That adapter never calls a
# network service and always returns an honest `not_connected` result with
# an empty `proposals` list. Because `CandidateGroundingService.ground`
# returns `skipped` for an empty `proposals` list, the default `dry_run`
# call therefore produces no proposals and no grounded candidates -- this
# module never fabricates a fallback proposal or grounded candidate to
# compensate. A real LLM-backed `AICandidateProposalProvider` adapter can
# be injected via the constructor (bypassing the factory entirely), but
# nothing in this module ever constructs or calls one itself.


class AICandidateDiscoveryDryRunResult(BaseModel):
    """Bundles every step of one `AICandidateDiscoveryService.dry_run` call.

    Each field validates through its own existing contract model
    (`AICandidateProposalRequest`/`AICandidateProposalResult`/
    `CandidateGroundingRequest`/`CandidateGroundingResult`) -- this wrapper
    adds no new validation of its own.
    """

    proposal_request: AICandidateProposalRequest
    proposal_result: AICandidateProposalResult
    grounding_request: CandidateGroundingRequest
    grounding_result: CandidateGroundingResult


class AICandidateDiscoveryService:
    """Composes the candidate-discovery flow in dry-run mode.

    `dry_run` only ever reads `planning_state` (via the injected builders)
    and calls the injected proposal provider / grounding service -- it
    never mutates `planning_state`, never persists anything, and never
    schedules anything. `PlanningOrchestrator`'s AI candidate discovery
    shadow stage (Step 161B) is the only caller in the app, and only when
    explicitly enabled via config; storing the result on `planning_state`
    is that caller's responsibility, not this service's.
    """

    def __init__(
        self,
        proposal_request_builder: AICandidateProposalRequestBuilder | None = None,
        proposal_provider: AICandidateProposalProvider | None = None,
        grounding_request_builder: CandidateGroundingRequestBuilder | None = None,
        grounding_service: CandidateGroundingService | None = None,
    ) -> None:
        self.proposal_request_builder = proposal_request_builder or AICandidateProposalRequestBuilder()
        self.proposal_provider = proposal_provider or get_ai_candidate_proposal_provider()
        self.grounding_request_builder = grounding_request_builder or CandidateGroundingRequestBuilder()
        self.grounding_service = grounding_service or CandidateGroundingService()

    def dry_run(
        self,
        planning_state: PlanningState,
        task: AICandidateProposalTask = AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        max_candidates: int = 15,
    ) -> AICandidateDiscoveryDryRunResult:
        proposal_request = self.proposal_request_builder.build_request(
            planning_state, task=task, max_candidates=max_candidates
        )
        proposal_result = self.proposal_provider.propose(proposal_request)

        grounding_request = self.grounding_request_builder.build_request(
            planning_state, proposals=proposal_result.proposals
        )
        grounding_result = self.grounding_service.ground(grounding_request)

        return AICandidateDiscoveryDryRunResult(
            proposal_request=proposal_request,
            proposal_result=proposal_result,
            grounding_request=grounding_request,
            grounding_result=grounding_result,
        )
