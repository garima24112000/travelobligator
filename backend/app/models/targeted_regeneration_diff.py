from __future__ import annotations

from pydantic import BaseModel, Field

# Section 197C (docs/14_backend_architecture.md, following section
# 149.1): the smallest real, content-level, before/after diff contract
# for a targeted regeneration -- deliberately separate from the existing
# `PlanDiffPreview` (`app.models.planning_state`), which is section/stage
# -level only and was never meant to carry item identity. Every field
# here is derived directly by comparing two real `PlanningState`
# snapshots (Task 19: stable `experience_id` identity, never a fuzzy
# name/coordinate match) -- nothing here is an LLM claim, an evaluative
# judgment ("improved"), or an invented fact.


class MovedExperienceDiff(BaseModel):
    experience_id: str
    from_day: int
    to_day: int


class ReorderedDayDiff(BaseModel):
    """A day whose scheduled experience_id SET is unchanged but whose
    relative order differs -- reported only when meaningful (the set
    itself didn't change; an add/remove/move on that day is reported via
    `added_experience_ids`/`removed_experience_ids`/`moved_experiences`
    instead, never double-counted here)."""

    day_index: int
    before_order: list[str] = Field(default_factory=list)
    after_order: list[str] = Field(default_factory=list)


class TravelerProfileDiff(BaseModel):
    """Only real, supported profile fields (Task 24) -- never an
    arbitrary serialized-object comparison dump."""

    pace_before: str | None = None
    pace_after: str | None = None
    interests_added: list[str] = Field(default_factory=list)
    interests_removed: list[str] = Field(default_factory=list)


class TargetedRegenerationDiff(BaseModel):
    """A factual before/after content diff, never an explanatory or
    evaluative claim (Task 25: "just report the factual change")."""

    trip_id: str
    source_version: str
    new_version: str

    affected_day_indices: list[int] = Field(default_factory=list)
    preserved_day_indices: list[int] = Field(default_factory=list)

    added_experience_ids: list[str] = Field(default_factory=list)
    removed_experience_ids: list[str] = Field(default_factory=list)
    moved_experiences: list[MovedExperienceDiff] = Field(default_factory=list)
    reordered_days: list[ReorderedDayDiff] = Field(default_factory=list)

    traveler_profile_diff: TravelerProfileDiff | None = None

    validation_status_before: str | None = None
    validation_status_after: str | None = None
    warning_count_before: int = 0
    warning_count_after: int = 0
    critical_issue_count_before: int = 0
    critical_issue_count_after: int = 0
