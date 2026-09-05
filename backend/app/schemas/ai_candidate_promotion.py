from __future__ import annotations

from pydantic import BaseModel

from app.models.ai_candidate_promotion import AICandidatePromotionReport


class AICandidatePromotionResponseData(BaseModel):
    """Response shape for `POST /trips/{trip_id}/ai-candidate-promotions`
    (Step 170C). `ai_candidate_promotion_report` is always present --
    honestly empty (`status="no_candidates_reviewed"`/`"no_eligible_candidates"`)
    when no AI candidates exist or none are eligible, never fabricated.
    """

    trip_id: str
    ai_candidate_promotion_report: AICandidatePromotionReport
