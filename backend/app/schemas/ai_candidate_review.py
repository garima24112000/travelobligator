from __future__ import annotations

from pydantic import BaseModel

from app.models.ai_candidate_review import AICandidateReviewReport


class AICandidateReviewResponseData(BaseModel):
    """Read-only response shape for `GET /trips/{trip_id}/ai-candidate-review`
    (Step 170A). `ai_candidate_review_report` is always present -- honestly
    empty (`status="no_candidate_data"`) when no AI candidate discovery has
    run for this trip yet, never fabricated.
    """

    trip_id: str
    ai_candidate_review_report: AICandidateReviewReport
    # Final Batch 2: whether `POST /trips/{trip_id}/ai-candidate-promotions` exists here. It is a
    # development-only inspection tool (404 in production), so a client never offers it there.
    promotion_refresh_available: bool = True
