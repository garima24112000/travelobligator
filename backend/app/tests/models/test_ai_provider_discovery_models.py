from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.ai_provider_discovery import (
    AIProviderDiscoveryAttempt,
    AIProviderDiscoveryAttemptStatus,
    AIProviderDiscoveryResult,
)
from app.models.candidate_grounding import ProviderCandidateForGrounding
from app.models.common import DataStatus, GeoPoint

# Tests for the Section 192 AI-directed provider discovery contract models
# (docs/14_backend_architecture.md section 138). No provider/LLM call is
# made here -- these tests only exercise pydantic construction/validation.


def _match(**overrides: object) -> ProviderCandidateForGrounding:
    fields: dict[str, object] = {
        "provider_name": "openstreetmap_places",
        "provider_place_id": "way/123",
        "name": "Mercado da Ribeira",
        "category": "marketplace",
        "coordinates": GeoPoint(lat=38.7071, lng=-9.1456),
        "data_status": DataStatus.LIVE,
        "confidence": 0.5,
    }
    fields.update(overrides)
    return ProviderCandidateForGrounding(**fields)


def _matched_attempt(**overrides: object) -> AIProviderDiscoveryAttempt:
    fields: dict[str, object] = {
        "proposal_id": "proposal_001",
        "search_query": "historic food market",
        "status": AIProviderDiscoveryAttemptStatus.MATCHED,
        "match": _match(),
        "message": "Provider matched 'historic food market' to 'Mercado da Ribeira'.",
    }
    fields.update(overrides)
    return AIProviderDiscoveryAttempt(**fields)


def test_matched_attempt_requires_a_match() -> None:
    with pytest.raises(ValidationError):
        AIProviderDiscoveryAttempt(
            proposal_id="proposal_001",
            search_query="historic food market",
            status=AIProviderDiscoveryAttemptStatus.MATCHED,
            match=None,
            message="no match provided",
        )


@pytest.mark.parametrize(
    "status",
    [
        AIProviderDiscoveryAttemptStatus.NOT_FOUND,
        AIProviderDiscoveryAttemptStatus.PROVIDER_FAILED,
        AIProviderDiscoveryAttemptStatus.PROVIDER_NOT_CONNECTED,
        AIProviderDiscoveryAttemptStatus.NOT_SEARCHED,
    ],
)
def test_non_matched_attempt_forbids_a_match(status: AIProviderDiscoveryAttemptStatus) -> None:
    with pytest.raises(ValidationError):
        AIProviderDiscoveryAttempt(
            proposal_id="proposal_001",
            search_query="historic food market",
            status=status,
            match=_match(),
            message="should not carry a match",
        )


def test_valid_matched_attempt_is_accepted() -> None:
    attempt = _matched_attempt()
    assert attempt.match is not None
    assert attempt.match.name == "Mercado da Ribeira"


def test_attempt_rejects_blank_search_query() -> None:
    with pytest.raises(ValidationError):
        _matched_attempt(search_query="   ")


def test_result_counts_must_match_attempts() -> None:
    with pytest.raises(ValidationError):
        AIProviderDiscoveryResult(
            attempts=[_matched_attempt()],
            provider_name="openstreetmap_places",
            searched_count=0,
            matched_count=0,
        )


def test_result_with_correct_counts_is_valid() -> None:
    not_found_attempt = AIProviderDiscoveryAttempt(
        proposal_id="proposal_002",
        search_query="sunset viewpoint",
        status=AIProviderDiscoveryAttemptStatus.NOT_FOUND,
        message="No usable result.",
    )
    not_searched_attempt = AIProviderDiscoveryAttempt(
        proposal_id="proposal_003",
        search_query="Belem Tower",
        status=AIProviderDiscoveryAttemptStatus.NOT_SEARCHED,
        message="Bound reached.",
    )
    result = AIProviderDiscoveryResult(
        attempts=[_matched_attempt(), not_found_attempt, not_searched_attempt],
        provider_name="openstreetmap_places",
        searched_count=2,
        matched_count=1,
    )
    assert result.searched_count == 2
    assert result.matched_count == 1


def test_matches_by_proposal_id_only_includes_matched_attempts() -> None:
    not_found_attempt = AIProviderDiscoveryAttempt(
        proposal_id="proposal_002",
        search_query="sunset viewpoint",
        status=AIProviderDiscoveryAttemptStatus.NOT_FOUND,
        message="No usable result.",
    )
    result = AIProviderDiscoveryResult(
        attempts=[_matched_attempt(), not_found_attempt],
        provider_name="openstreetmap_places",
        searched_count=2,
        matched_count=1,
    )
    matches = result.matches_by_proposal_id()
    assert set(matches.keys()) == {"proposal_001"}
    assert matches["proposal_001"].name == "Mercado da Ribeira"


def test_empty_result_is_valid() -> None:
    result = AIProviderDiscoveryResult()
    assert result.attempts == []
    assert result.matches_by_proposal_id() == {}


def test_models_never_contain_forbidden_field_names() -> None:
    forbidden = {"price", "rating", "opening_hours", "route_time", "booking_url", "availability"}
    for model_cls in (AIProviderDiscoveryAttempt, AIProviderDiscoveryResult):
        field_names = set(model_cls.model_fields.keys())
        overlap = field_names & forbidden
        assert overlap == set(), f"{model_cls.__name__} has forbidden field(s): {overlap}"
