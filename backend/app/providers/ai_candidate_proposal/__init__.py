from app.providers.ai_candidate_proposal.base import AICandidateProposalProvider
from app.providers.ai_candidate_proposal.not_connected_adapter import (
    NotConnectedAICandidateProposalProvider,
)

__all__ = [
    "AICandidateProposalProvider",
    "NotConnectedAICandidateProposalProvider",
]
