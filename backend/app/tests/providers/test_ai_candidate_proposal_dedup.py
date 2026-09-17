from __future__ import annotations

from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateProposalType,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.providers.ai_candidate_proposal.proposal_dedup import deduplicate_proposals

# Tests for the Step 191B shared proposal-deduplication helper used by both
# the Groq and Anthropic AICandidateProposalProvider adapters. Deliberately
# deterministic-only: no semantic/fuzzy matching against provider facts is
# exercised or expected here (that remains CandidateGroundingService's job
# and is out of scope for this helper).


def _named_place(proposal_id: str, candidate_name: str, **overrides: object) -> AICandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": proposal_id,
        "candidate_name": candidate_name,
        "candidate_type": AICandidateType.ATTRACTION,
        "why_consider": "A landmark worth checking.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


def _discovery_query(proposal_id: str, search_query: str, **overrides: object) -> AICandidateProposal:
    fields: dict[str, object] = {
        "proposal_id": proposal_id,
        "proposal_type": AICandidateProposalType.DISCOVERY_QUERY,
        "candidate_name": None,
        "search_query": search_query,
        "candidate_type": AICandidateType.FOOD_AREA,
        "why_consider": "Matches traveler's stated interest in food.",
        "verification_requirements": [
            AICandidateVerificationRequirement.MUST_NOT_USE_WITHOUT_PROVIDER_MATCH
        ],
        "confidence": 0.5,
    }
    fields.update(overrides)
    return AICandidateProposal(**fields)


def test_exact_duplicate_names_collapse_to_first_occurrence() -> None:
    proposals = [
        _named_place("proposal_001", "Belem Tower"),
        _named_place("proposal_002", "Belem Tower"),
    ]
    deduped = deduplicate_proposals(proposals)
    assert len(deduped) == 1
    assert deduped[0].proposal_id == "proposal_001"


def test_accent_and_punctuation_variants_collapse() -> None:
    proposals = [
        _named_place("proposal_001", "Belém Tower"),
        _named_place("proposal_002", "Belem Tower!"),
        _named_place("proposal_003", "  belem   tower "),
    ]
    deduped = deduplicate_proposals(proposals)
    assert len(deduped) == 1
    assert deduped[0].proposal_id == "proposal_001"


def test_leading_article_variant_collapses() -> None:
    proposals = [
        _named_place("proposal_001", "Old Town Waterfront"),
        _named_place("proposal_002", "The Old Town Waterfront"),
    ]
    deduped = deduplicate_proposals(proposals)
    assert len(deduped) == 1


def test_translation_is_not_collapsed() -> None:
    """A genuine translation ('Torre de Belem' vs 'Belem Tower') is a
    semantic judgment, not a deterministic normalization -- it must stay
    distinct rather than being fuzzy-matched away.
    """
    proposals = [
        _named_place("proposal_001", "Belem Tower"),
        _named_place("proposal_002", "Torre de Belem"),
    ]
    deduped = deduplicate_proposals(proposals)
    assert len(deduped) == 2


def test_distinct_named_places_are_preserved() -> None:
    proposals = [
        _named_place("proposal_001", "Belem Tower"),
        _named_place("proposal_002", "Alfama District"),
    ]
    deduped = deduplicate_proposals(proposals)
    assert len(deduped) == 2


def test_discovery_query_duplicates_collapse_by_search_query() -> None:
    proposals = [
        _discovery_query("proposal_001", "historic food market"),
        _discovery_query("proposal_002", "Historic Food Market!"),
    ]
    deduped = deduplicate_proposals(proposals)
    assert len(deduped) == 1
    assert deduped[0].proposal_id == "proposal_001"


def test_named_place_and_discovery_query_with_same_text_do_not_collapse() -> None:
    """A named place and a category search sharing the same words mean
    different things to a future provider search -- they must never
    collapse into each other.
    """
    proposals = [
        _named_place("proposal_001", "Food Market"),
        _discovery_query("proposal_002", "Food Market"),
    ]
    deduped = deduplicate_proposals(proposals)
    assert len(deduped) == 2


def test_empty_list_returns_empty_list() -> None:
    assert deduplicate_proposals([]) == []


def test_dedup_preserves_original_order() -> None:
    proposals = [
        _named_place("proposal_001", "Alfama District"),
        _named_place("proposal_002", "Belem Tower"),
        _named_place("proposal_003", "Alfama District"),
        _named_place("proposal_004", "Chiado"),
    ]
    deduped = deduplicate_proposals(proposals)
    assert [proposal.proposal_id for proposal in deduped] == [
        "proposal_001",
        "proposal_002",
        "proposal_004",
    ]
