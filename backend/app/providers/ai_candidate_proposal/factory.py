from __future__ import annotations

from app.core.config import get_settings
from app.providers.ai_candidate_proposal.base import AICandidateProposalProvider
from app.providers.ai_candidate_proposal.not_connected_adapter import (
    NotConnectedAICandidateProposalProvider,
)

# Config-gated provider-selection boundary (Step 160E,
# itinerary-generator-build-spec.md Stage 5, docs/13_llm_reasoning_
# pipeline.md section 38, docs/14_backend_architecture.md section 25).
# Still no real LLM, LangGraph, LangSmith, or OpenAI/Anthropic/Gemini
# client code is added here -- this module only selects between
# `AICandidateProposalProvider` adapters that already exist.
#
# `"not_connected"` is the only supported value today, mapping to the
# Step 157B `NotConnectedAICandidateProposalProvider`. This exists so a
# future real LLM-backed adapter can be added later and config-gated in
# (e.g. `AI_CANDIDATE_PROPOSAL_PROVIDER=openai`) without changing any
# calling code -- `AICandidateDiscoveryService` already resolves its
# default provider through this factory rather than constructing
# `NotConnectedAICandidateProposalProvider` directly.

_SUPPORTED_PROVIDERS: dict[str, type[AICandidateProposalProvider]] = {
    "not_connected": NotConnectedAICandidateProposalProvider,
}


def get_ai_candidate_proposal_provider(provider_name: str | None = None) -> AICandidateProposalProvider:
    """Resolves an `AICandidateProposalProvider` from `provider_name`, or
    from `Settings.ai_candidate_proposal_provider` (default
    `"not_connected"`) when `provider_name` is omitted.

    An unsupported/unrecognized provider name can never silently create
    fake proposals: it falls back to the same honest
    `NotConnectedAICandidateProposalProvider` used when nothing is
    configured at all, rather than raising or guessing. Unknown
    configuration is functionally identical to "not connected," so it is
    treated identically.
    """
    resolved_name = provider_name if provider_name is not None else get_settings().ai_candidate_proposal_provider

    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, NotConnectedAICandidateProposalProvider)
    return provider_cls()
