from __future__ import annotations

import re
import unicodedata

from app.models.ai_candidate_proposal import AICandidateProposal

# Step 191B (docs/13_llm_reasoning_pipeline.md section 42): deterministic,
# proposal-level deduplication shared by every AICandidateProposalProvider
# adapter (Groq, Anthropic) so "Belem Tower" / "Belém Tower" / "BELEM
# TOWER!" don't consume three separate proposal slots. This deliberately
# does NOT attempt semantic/fuzzy matching against provider facts (that is
# CandidateGroundingService's job, and it is explicitly out of scope to
# weaken) -- it only collapses proposals whose normalized lookup key
# (accent-stripped, lowercased, punctuation-stripped, leading-article-
# stripped name/search_query) is identical. A translation like "Torre de
# Belem" is intentionally left distinct: that is a semantic judgment, not a
# deterministic normalization.

_LEADING_ARTICLES = ("the ", "a ", "an ")


def _normalize_name(name: str) -> str:
    stripped_accents = "".join(
        char
        for char in unicodedata.normalize("NFKD", name)
        if not unicodedata.combining(char)
    )
    lowered = stripped_accents.strip().lower()
    no_punctuation = re.sub(r"[^\w\s]", "", lowered)
    collapsed = re.sub(r"\s+", " ", no_punctuation).strip()
    for article in _LEADING_ARTICLES:
        if collapsed.startswith(article):
            collapsed = collapsed[len(article) :]
            break
    return collapsed


def deduplicate_proposals(proposals: list[AICandidateProposal]) -> list[AICandidateProposal]:
    """Collapses proposals that normalize to the same lookup key, keeping
    the first occurrence in `proposals`' original order. The lookup key is
    `candidate_name` when present (named_place proposals), otherwise
    `search_query` (discovery_query proposals) -- proposals of different
    `proposal_type` never collapse into each other, since a named place
    and a category search for the same words mean different things to a
    future provider search.
    """
    seen_keys: set[tuple[str, str]] = set()
    deduped: list[AICandidateProposal] = []
    for proposal in proposals:
        lookup_text = proposal.candidate_name or proposal.search_query
        key = (proposal.proposal_type.value, _normalize_name(lookup_text or ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(proposal)
    return deduped
