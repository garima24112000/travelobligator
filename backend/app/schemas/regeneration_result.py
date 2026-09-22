from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.targeted_regeneration_diff import TargetedRegenerationDiff


class RegenerateResponseData(BaseModel):
    """Response payload for a successful `POST /trips/{trip_id}/regenerate`
    (Step 174C) -- the "applied" outcome of the confirm=true, feedback-
    exists, zero-active-locks MVP scope. Deliberately minimal: no plan
    content, no fabricated diff, no per-item change list -- just the same
    kind of section-name/version bookkeeping `VersionHistoryItem` and
    `PlanDiffPreview` already use elsewhere. Callers that want the plan's
    actual current content should follow up with `GET /trips/{trip_id}`.

    Section 197C extends this backward-compatibly (Task 26): every field
    below `message` is new and optional, defaulting to `None`/empty so a
    legacy (non-targeted) response is unchanged in substance -- only
    `targeted=False` and empty/null values are added to the payload. A
    targeted-mode response additionally populates `targeted=True` plus
    whichever of `interpretation_status`/`execution_status`/`diff`/
    clarification fields apply to that outcome.
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

    targeted: bool = False
    interpretation_status: str | None = None
    execution_status: str | None = None
    affected_day_indices: list[int] = Field(default_factory=list)
    preserved_day_indices: list[int] = Field(default_factory=list)
    diff: TargetedRegenerationDiff | None = None
    clarification_reason: str | None = None
    clarification_possible_experience_ids: list[str] = Field(default_factory=list)
