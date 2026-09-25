from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.targeted_regeneration_diff import TravelerProfileDiff

# Section 199C (Task 20-22): the factual, deterministic comparison of two
# real, snapshot-backed `ItineraryRevision`s. Deliberately carries no
# "winner"/"better"/"recommended" field of any kind -- every value here is
# a restatement of something already present in the left/right snapshots
# themselves (stable `experience_id` identity, Task 21), never an
# evaluative judgement, an LLM claim, or a provider lookup.


class ComparedRevisionSide(BaseModel):
    """Identity of ONE side of a comparison. `branch_id`/
    `branch_display_name` describe the branch that OWNS this revision --
    a freshly forked branch's head can legitimately be a revision owned
    by its source branch (Section 199B, Task 9/10), so this is never
    assumed to be "the branch the caller selected"."""

    revision_id: str
    branch_id: str
    branch_display_name: str | None = None
    version_label: str


class ComparedExperience(BaseModel):
    """A stable `experience_id` resolved against the snapshot that
    actually contains it (added -> right, removed -> left; moved/
    reordered -> right, falling back to left) -- never a fuzzy name/
    coordinate lookup (Task 25). `name` is that snapshot's own recorded
    name for exactly this id."""

    experience_id: str
    name: str


class ComparedMovedExperience(BaseModel):
    experience_id: str
    name: str
    from_day: int
    to_day: int


class ComparedReorderedDay(BaseModel):
    day_index: int
    before_order: list[ComparedExperience] = Field(default_factory=list)
    after_order: list[ComparedExperience] = Field(default_factory=list)


class ItineraryRevisionComparison(BaseModel):
    trip_id: str
    left: ComparedRevisionSide
    right: ComparedRevisionSide

    added_experiences: list[ComparedExperience] = Field(default_factory=list)
    removed_experiences: list[ComparedExperience] = Field(default_factory=list)
    moved_experiences: list[ComparedMovedExperience] = Field(default_factory=list)
    reordered_days: list[ComparedReorderedDay] = Field(default_factory=list)
    traveler_profile_diff: TravelerProfileDiff | None = None

    validation_status_left: str | None = None
    validation_status_right: str | None = None
    warning_count_left: int = 0
    warning_count_right: int = 0
    critical_issue_count_left: int = 0
    critical_issue_count_right: int = 0

    # True only when EVERY compared category above is empty/equal --
    # never a claim that the two revisions are identical in every
    # stored respect (audit/transient/other stored fields are
    # deliberately not part of this comparison).
    no_compared_differences: bool = False
