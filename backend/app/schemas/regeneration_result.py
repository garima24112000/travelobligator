from __future__ import annotations

from pydantic import BaseModel, Field


class RegenerateResponseData(BaseModel):
    """Response payload for a successful `POST /trips/{trip_id}/regenerate`
    (Step 174C) -- the "applied" outcome of the confirm=true, feedback-
    exists, zero-active-locks MVP scope. Deliberately minimal: no plan
    content, no fabricated diff, no per-item change list -- just the same
    kind of section-name/version bookkeeping `VersionHistoryItem` and
    `PlanDiffPreview` already use elsewhere. Callers that want the plan's
    actual current content should follow up with `GET /trips/{trip_id}`.
    """

    trip_id: str
    status: str = "applied"
    previous_version: str | None
    current_version: str
    changed_sections: list[str] = Field(default_factory=list)
    preserved_sections: list[str] = Field(default_factory=list)
    applied_feedback_event_ids: list[str] = Field(default_factory=list)
    active_lock_count: int
    message: str
