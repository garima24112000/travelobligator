from __future__ import annotations

from app.models.ai_candidate_proposal import (
    AICandidateProposalGuardrailReport,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
)
from app.providers.ai_candidate_proposal.base import AICandidateProposalProvider

# Default AI candidate proposal adapter (Step 157B). No LLM provider is
# connected yet, so `propose` always returns an honest `not_connected`
# result -- it never calls a network service, never inspects `request`
# beyond `request.task` (to preserve it on the response), and never
# produces a proposal.


class NotConnectedAICandidateProposalProvider(AICandidateProposalProvider):
    """`AICandidateProposalProvider` implementation used until a real
    LLM-backed adapter is connected. `propose` is deterministic: given the
    same `request.task`, it always returns the same
    `AICandidateProposalResult` -- empty proposals, zero confidence, and a
    failed guardrail report explaining why.
    """

    provider_name = "ai_candidate_proposal_provider"

    def propose(self, request: AICandidateProposalRequest) -> AICandidateProposalResult:
        return AICandidateProposalResult(
            task=request.task,
            status=AICandidateProposalStatus.NOT_CONNECTED,
            proposals=[],
            rejected_raw_items=[],
            guardrail_report=AICandidateProposalGuardrailReport(
                passed=False,
                blocked_reasons=["AI candidate proposal provider is not connected yet."],
                checked_fields=["provider_connection"],
            ),
            provider_name=self.provider_name,
            model_name=None,
            confidence=0.0,
        )
