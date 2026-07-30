from __future__ import annotations

from app.models.candidate_grounding import (
    CandidateGroundingGuardrailReport,
    CandidateGroundingRequest,
    CandidateGroundingResult,
    CandidateGroundingStatus,
)

# Service boundary for the future candidate grounding/verification stage
# (Step 158B, itinerary-generator-build-spec.md Stage 6, docs/13_llm_
# reasoning_pipeline.md section 31, docs/14_backend_architecture.md
# section 25). No LLM, provider call, LangGraph, or LangSmith dependency
# is wired up yet -- `ground` always returns an honest `not_connected`
# result. Nothing in this module calls a network service, and nothing
# mutates `PlanningState`. Nothing elsewhere in the app currently calls
# this service, and it is not wired into `PlanningOrchestrator`.


class CandidateGroundingService:
    """`ground` is intentionally boring: it exists only so a future
    pipeline has a safe boundary before real grounding logic is
    implemented. It does not inspect `request.proposals`, does not attempt
    any name/location matching, and does not call a provider -- it always
    returns the same `not_connected` `CandidateGroundingResult` regardless
    of what `request` contains.
    """

    provider_name = "candidate_grounding_service"

    def ground(self, request: CandidateGroundingRequest) -> CandidateGroundingResult:
        return CandidateGroundingResult(
            status=CandidateGroundingStatus.NOT_CONNECTED,
            grounded_candidates=[],
            rejected_proposals=[],
            guardrail_report=CandidateGroundingGuardrailReport(
                passed=False,
                blocked_reasons=["Candidate grounding service is not connected yet."],
                checked_fields=["grounding_connection"],
            ),
            provider_name=self.provider_name,
            confidence=0.0,
        )
