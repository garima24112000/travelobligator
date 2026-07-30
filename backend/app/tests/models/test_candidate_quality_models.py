from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.candidate_quality import (
    CandidateQualityReport,
    CandidateQualityScore,
    CandidateQualityTier,
    CandidateRejectReason,
    CandidateUseCase,
)

_FORBIDDEN_FACTUAL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "safety_score",
}


def _valid_score(**overrides: object) -> CandidateQualityScore:
    fields: dict[str, object] = {
        "candidate_id": "way/123",
        "candidate_name": "Example Museum",
        "use_case": CandidateUseCase.ATTRACTION,
        "quality_tier": CandidateQualityTier.GOOD_CANDIDATE,
        "total_score": 0.6,
        "score_components": {"category_signal": 0.85, "provider_confidence": 0.4},
        "positive_signals": ["Name/category matches a strong attraction-type keyword."],
        "negative_signals": [],
        "reject_reasons": [],
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": 0.4,
    }
    fields.update(overrides)
    return CandidateQualityScore(**fields)


# ---------------------------------------------------------------------------
# 1. CandidateQualityScore rejects blank candidate_id/name.
# ---------------------------------------------------------------------------


def test_rejects_blank_candidate_id() -> None:
    with pytest.raises(ValidationError):
        _valid_score(candidate_id="   ")


def test_rejects_blank_candidate_name() -> None:
    with pytest.raises(ValidationError):
        _valid_score(candidate_name="")


# ---------------------------------------------------------------------------
# 2. CandidateQualityScore rejects score_components outside 0..1.
# ---------------------------------------------------------------------------


def test_rejects_score_components_above_one() -> None:
    with pytest.raises(ValidationError):
        _valid_score(score_components={"category_signal": 1.5})


def test_rejects_score_components_below_zero() -> None:
    with pytest.raises(ValidationError):
        _valid_score(score_components={"category_signal": -0.1})


def test_accepts_score_components_within_bounds() -> None:
    score = _valid_score(score_components={"category_signal": 0.0, "provider_confidence": 1.0})
    assert score.score_components["category_signal"] == 0.0
    assert score.score_components["provider_confidence"] == 1.0


# ---------------------------------------------------------------------------
# 3. rejected tier requires reject reasons.
# ---------------------------------------------------------------------------


def test_rejected_tier_requires_reject_reasons() -> None:
    with pytest.raises(ValidationError):
        _valid_score(quality_tier=CandidateQualityTier.REJECTED, reject_reasons=[])


def test_rejected_tier_with_reject_reasons_is_valid() -> None:
    score = _valid_score(
        quality_tier=CandidateQualityTier.REJECTED,
        reject_reasons=[CandidateRejectReason.MISSING_COORDINATES],
    )
    assert score.quality_tier == CandidateQualityTier.REJECTED


# ---------------------------------------------------------------------------
# 4. reject reasons force low_priority/rejected.
# ---------------------------------------------------------------------------


def test_reject_reasons_force_low_priority_or_rejected() -> None:
    with pytest.raises(ValidationError):
        _valid_score(
            quality_tier=CandidateQualityTier.GOOD_CANDIDATE,
            reject_reasons=[CandidateRejectReason.WEAK_CATEGORY],
        )


def test_reject_reasons_with_low_priority_tier_is_valid() -> None:
    score = _valid_score(
        quality_tier=CandidateQualityTier.LOW_PRIORITY,
        reject_reasons=[CandidateRejectReason.WEAK_CATEGORY],
    )
    assert score.quality_tier == CandidateQualityTier.LOW_PRIORITY


def test_no_reject_reasons_allows_any_non_rejected_tier() -> None:
    score = _valid_score(quality_tier=CandidateQualityTier.PRIMARY_ANCHOR, reject_reasons=[])
    assert score.quality_tier == CandidateQualityTier.PRIMARY_ANCHOR


# ---------------------------------------------------------------------------
# total_score / confidence bounds.
# ---------------------------------------------------------------------------


def test_rejects_total_score_out_of_bounds() -> None:
    with pytest.raises(ValidationError):
        _valid_score(total_score=1.2)


def test_rejects_confidence_out_of_bounds() -> None:
    with pytest.raises(ValidationError):
        _valid_score(confidence=-0.5)


# ---------------------------------------------------------------------------
# 15. No score output contains forbidden factual fields.
# ---------------------------------------------------------------------------


def test_score_model_never_contains_forbidden_factual_fields() -> None:
    field_names = set(CandidateQualityScore.model_fields.keys())
    overlap = field_names & _FORBIDDEN_FACTUAL_FIELD_NAMES
    assert overlap == set(), f"CandidateQualityScore has forbidden field(s): {overlap}"


def test_report_model_never_contains_forbidden_factual_fields() -> None:
    field_names = set(CandidateQualityReport.model_fields.keys())
    overlap = field_names & _FORBIDDEN_FACTUAL_FIELD_NAMES
    assert overlap == set(), f"CandidateQualityReport has forbidden field(s): {overlap}"


# ---------------------------------------------------------------------------
# CandidateQualityReport validation.
# ---------------------------------------------------------------------------


def test_report_rejects_blank_destination_name() -> None:
    with pytest.raises(ValidationError):
        CandidateQualityReport(destination_name="  ", generated_at=datetime.now(timezone.utc))


def test_report_with_valid_fields_is_accepted() -> None:
    report = CandidateQualityReport(
        destination_name="New York",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[_valid_score()],
        summary={"good_candidate": 1},
    )
    assert report.destination_name == "New York"
    assert len(report.attraction_scores) == 1
