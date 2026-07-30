from __future__ import annotations

from pydantic import BaseModel

from app.models.candidate_quality import CandidateQualityReport


class CandidateQualityResponseData(BaseModel):
    """Read-only response shape for `GET /trips/{trip_id}/candidate-quality`
    (Step 156B, docs/18_candidate_quality.md). `candidate_quality_report`
    stays `None` (returned honestly, never fabricated) until a destination
    context has been generated for this trip.
    """

    trip_id: str
    candidate_quality_report: CandidateQualityReport | None = None
