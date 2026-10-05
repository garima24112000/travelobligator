from __future__ import annotations

from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider
from app.providers.llm_provider_health import GEMINI

# `AI_CANDIDATE_PROPOSAL_PROVIDER=gemini`. Not a second implementation: the
# stage adapter is the same one (same prompt, same wire schema, same
# per-proposal validation and dedup) -- only which member of the Groq <->
# Gemini pair it stands for differs. With `LLM_FAILOVER_ENABLED=false` this
# stage calls Gemini only; with failover on it is routed over the configured
# pair exactly like the "groq" selector
# (`app/providers/llm_provider_router.py`). Gemini proposes; it is never a
# factual source and no search / maps grounding tool is ever sent.


class GeminiAICandidateProposalProvider(GroqAICandidateProposalProvider):
    provider_name = "gemini_ai_candidate_proposal_provider"
    _selected_provider = GEMINI
