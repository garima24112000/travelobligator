from __future__ import annotations

import logging
import time

from pydantic import BaseModel

from app.models.ai_candidate_proposal import (
    AICandidateProposalBatch,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalTask,
)
from app.models.ai_provider_discovery import AIProviderDiscoveryResult
from app.models.candidate_grounding import (
    CandidateGroundingBatch,
    CandidateGroundingRequest,
    CandidateGroundingResult,
)
from app.models.planning_state import PlanningState
from app.providers.ai_candidate_proposal import (
    AICandidateProposalProvider,
    get_ai_candidate_proposal_provider,
)
from app.services.ai_candidate_proposal_request_builder import AICandidateProposalRequestBuilder
from app.services.ai_directed_provider_discovery_service import AIDirectedProviderDiscoveryService
from app.services.candidate_grounding_request_builder import CandidateGroundingRequestBuilder
from app.services.candidate_grounding_service import CandidateGroundingService

logger = logging.getLogger(__name__)

# Dry-run composition service for the candidate-discovery flow (Step 160B,
# itinerary-generator-build-spec.md Stages 5-6, docs/13_llm_reasoning_
# pipeline.md section 35, docs/14_backend_architecture.md section 25). This
# wires together four already-safe pieces built in Steps 157B/159A/159B/160A
# -- `AICandidateProposalRequestBuilder` -> a proposal provider ->
# `CandidateGroundingRequestBuilder` -> `CandidateGroundingService` -- into
# one deterministic call, without adding a real LLM.
#
# `dry_run` (pure/read-only) has two production callers as of Step 191A
# (docs/14_backend_architecture.md section 135):
# `PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage` (Step
# 161B, legacy-engine-only, gated by
# `Settings.ai_candidate_discovery_shadow_mode_enabled`) and the LangGraph
# `ai_candidate` node (Step 191A, gated by the separate
# `Settings.ai_candidate_discovery_enabled`) -- both reach it only through
# `apply_discovery_to_state` below, the shared fail-safe wrapper that
# actually stores the result onto `PlanningState`. With both flags at
# their default `False`, default app/generation behavior is unaffected by
# either wiring.
#
# The default `proposal_provider` is resolved through
# `get_ai_candidate_proposal_provider` (Step 160E, docs/13_llm_reasoning_
# pipeline.md section 38) -- a config-gated factory whose default value is
# `"not_connected"`, mapping to the Step 157B
# `NotConnectedAICandidateProposalProvider`. That adapter never calls a
# network service and always returns an honest `not_connected` result with
# an empty `proposals` list. Because `CandidateGroundingService.ground`
# returns `skipped` for an empty `proposals` list, the default `dry_run`
# call therefore produces no proposals and no grounded candidates -- this
# module never fabricates a fallback proposal or grounded candidate to
# compensate. A real LLM-backed `AICandidateProposalProvider` adapter
# (`"anthropic"` or `"groq"`, Step 161A/162A) can be selected via
# `AI_CANDIDATE_PROPOSAL_PROVIDER` or injected via the constructor
# (bypassing the factory entirely), but
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
    # Section 192 (docs/14_backend_architecture.md section 138): the
    # targeted provider-discovery attempts run between proposal and
    # grounding, kept as its own field so its outcome (matched/not_found/
    # provider_failed/not_searched per proposal) is inspectable separately
    # from the final grounding result it fed into.
    provider_discovery_result: AIProviderDiscoveryResult


class AICandidateDiscoveryService:
    """Composes the candidate-discovery flow in dry-run mode.

    `dry_run` only ever reads `planning_state` (via the injected builders)
    and calls the injected proposal provider / grounding service -- it
    never mutates `planning_state`, never persists anything, and never
    schedules anything. It is only called by `PlanningOrchestrator` when
    shadow mode is explicitly enabled (Step 161B); by default it is not
    called by anything in the runtime pipeline.

    Section 192 inserts one more step between proposal and grounding:
    `provider_discovery_service.discover` runs a targeted provider lookup
    for any proposal the broad `destination_context` pool alone could not
    (or, for a `discovery_query` proposal, never could) cleanly match, and
    its real matches are handed to `grounding_service.ground` as
    `CandidateGroundingRequest.ai_directed_matches` -- `ground` itself
    still decides, per proposal, whether to use them (only as a fallback
    after broad-pool matching, per `CandidateGroundingService._ground_one`).
    This never bypasses grounding and never adds a second, parallel
    candidate pool of its own.
    """

    def __init__(
        self,
        proposal_request_builder: AICandidateProposalRequestBuilder | None = None,
        proposal_provider: AICandidateProposalProvider | None = None,
        grounding_request_builder: CandidateGroundingRequestBuilder | None = None,
        grounding_service: CandidateGroundingService | None = None,
        provider_discovery_service: AIDirectedProviderDiscoveryService | None = None,
    ) -> None:
        self.proposal_request_builder = proposal_request_builder or AICandidateProposalRequestBuilder()
        self.proposal_provider = proposal_provider or get_ai_candidate_proposal_provider()
        self.grounding_request_builder = grounding_request_builder or CandidateGroundingRequestBuilder()
        self.grounding_service = grounding_service or CandidateGroundingService()
        self.provider_discovery_service = provider_discovery_service or AIDirectedProviderDiscoveryService()

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

        provider_discovery_result = self.provider_discovery_service.discover(
            planning_state, proposal_result.proposals, grounding_request.provider_candidates
        )
        if provider_discovery_result.matches_by_proposal_id():
            grounding_request = grounding_request.model_copy(
                update={"ai_directed_matches": provider_discovery_result.matches_by_proposal_id()}
            )

        grounding_result = self.grounding_service.ground(grounding_request)

        return AICandidateDiscoveryDryRunResult(
            proposal_request=proposal_request,
            proposal_result=proposal_result,
            grounding_request=grounding_request,
            grounding_result=grounding_result,
            provider_discovery_result=provider_discovery_result,
        )


# Step 191A (docs/14_backend_architecture.md section 135): shared,
# fail-safe, state-mutating composition of one `dry_run` call, extracted
# from `PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage`'s
# own pre-191A body so the legacy shadow stage and the new live LangGraph
# `ai_candidate` node call one implementation instead of two duplicated
# ones. `discovery_service` is accepted as a plain parameter (not read off
# `self`) so any object exposing a `dry_run(planning_state, task=...,
# max_candidates=...)` method works -- including the real
# `AICandidateDiscoveryService` and every existing test double built for
# the pre-191A shadow-stage tests.
def apply_discovery_to_state(
    planning_state: PlanningState,
    discovery_service: AICandidateDiscoveryService,
    *,
    task: AICandidateProposalTask = AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
    max_candidates: int = 15,
    stage_label: str = "ai_candidate_discovery",
) -> PlanningState:
    """Runs one `discovery_service.dry_run(...)` call and, on success,
    stores its result onto `planning_state.ai_candidate_proposal_batch`/
    `.candidate_grounding_batch` -- the real stage-runner equivalent of
    every other stage service's own `run(planning_state)` method, except
    this one never raises.

    A no-op (returns `planning_state` completely unchanged, no log line)
    whenever `planning_state.destination_context` is `None` -- grounding
    has nothing real to match an AI proposal against yet.

    Fails safe: an unexpected exception from `dry_run` (provider timeout,
    missing API key surfaced as an exception rather than an honest
    `not_connected` result, malformed response the provider adapter itself
    didn't catch, or any other unexpected failure) is caught here, logged
    with the same safe, secret-free structured fields Step 187F's shadow-
    stage logging already used (`provider`/`stage`/`status`/`error_code`/
    `duration_ms` -- never a prompt, a raw exception message, or an API
    key), and `planning_state` is returned completely unchanged for that
    call -- never a fabricated proposal or grounded candidate.

    `stage_label` only changes the safe `stage` log field so a caller can
    tell which integration produced a given log line (e.g.
    `"ai_candidate_proposal"` for the legacy Step 161B shadow stage vs.
    the default `"ai_candidate_discovery"` for the Step 191A live
    LangGraph stage) -- both call sites reuse this exact same
    implementation rather than duplicating it.
    """
    if planning_state.destination_context is None:
        return planning_state

    proposal_provider = getattr(discovery_service, "proposal_provider", None)
    provider_name = getattr(proposal_provider, "provider_name", "ai_candidate_proposal_provider")
    started_at = time.monotonic()
    try:
        dry_run_result = discovery_service.dry_run(planning_state, task=task, max_candidates=max_candidates)
    except Exception:
        duration_ms = (time.monotonic() - started_at) * 1000
        logger.warning(
            "AICandidateDiscoveryService.dry_run failed unexpectedly; leaving the plan "
            "otherwise unaffected.",
            exc_info=True,
            extra={
                "provider": provider_name,
                "stage": stage_label,
                "status": "failed",
                "error_code": "PROVIDER_FAILED",
                "duration_ms": round(duration_ms, 3),
            },
        )
        return planning_state

    duration_ms = (time.monotonic() - started_at) * 1000
    proposal_status = dry_run_result.proposal_result.status.value
    log_fields: dict[str, object] = {
        "provider": provider_name,
        "stage": stage_label,
        "status": proposal_status,
        "duration_ms": round(duration_ms, 3),
    }
    # Only "completed" (AICandidateProposalStatus.COMPLETED) is a real
    # success -- "not_connected"/"skipped"/"rejected" are all real, honest
    # non-success outcomes and logged at warning, mirroring
    # ProviderGateway's own info/warning split.
    if proposal_status == "completed":
        logger.info("AI candidate proposal call completed.", extra=log_fields)
    else:
        if proposal_status == "not_connected":
            log_fields["error_code"] = "PROVIDER_NOT_CONNECTED"
        logger.warning("AI candidate proposal call did not succeed.", extra=log_fields)

    planning_state.ai_candidate_proposal_batch = AICandidateProposalBatch(
        request=dry_run_result.proposal_request,
        result=dry_run_result.proposal_result,
    )
    planning_state.candidate_grounding_batch = CandidateGroundingBatch(
        request=dry_run_result.grounding_request,
        result=dry_run_result.grounding_result,
    )
    # Section 192: stored separately from candidate_grounding_batch so a
    # caller can distinguish "grounded via the broad destination_context
    # pool" from "grounded via a Section 192 targeted provider lookup" for
    # debugging/evaluation -- every `GroundedCandidate` this attempt
    # actually contributed to is still also reflected in
    # `candidate_grounding_batch.result.grounded_candidates` above (via
    # `match_type=targeted_lookup`), this field is purely additional,
    # inspectable detail, never a second candidate pool.
    planning_state.ai_provider_discovery_result = dry_run_result.provider_discovery_result
    return planning_state
