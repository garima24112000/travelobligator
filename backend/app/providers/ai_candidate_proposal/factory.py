from __future__ import annotations

from app.core.config import get_settings
from app.providers.ai_candidate_proposal.anthropic_adapter import (
    AnthropicAICandidateProposalProvider,
)
from app.providers.ai_candidate_proposal.base import AICandidateProposalProvider
from app.providers.ai_candidate_proposal.not_connected_adapter import (
    NotConnectedAICandidateProposalProvider,
)

# Config-gated provider-selection boundary (Step 160E, extended in Step
# 161A, itinerary-generator-build-spec.md Stage 5, docs/13_llm_reasoning_
# pipeline.md section 38, docs/14_backend_architecture.md section 25).
# No LangGraph, LangSmith, or OpenAI/Gemini client code is added here --
# this module only selects between `AICandidateProposalProvider` adapters
# that already exist. The default remains `"not_connected"`.
#
# `"not_connected"` maps to the Step 157B `NotConnectedAICandidateProposalProvider`.
# `"anthropic"` (Step 161A) maps to `AnthropicAICandidateProposalProvider`,
# which is itself safe by default: with no `ANTHROPIC_API_KEY` configured
# it returns an honest `not_connected` result instead of calling the real
# Anthropic API. `AICandidateDiscoveryService` resolves its default
# provider through this factory rather than constructing an adapter
# directly.

_SUPPORTED_PROVIDERS: dict[str, type[AICandidateProposalProvider]] = {
    "not_connected": NotConnectedAICandidateProposalProvider,
    "anthropic": AnthropicAICandidateProposalProvider,
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
