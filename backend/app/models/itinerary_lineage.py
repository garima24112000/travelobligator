from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

# Section 199A (docs/14_backend_architecture.md, following section 61):
# the persistence/domain foundation for TRUE itinerary forks -- NOT fork
# creation itself (that is Section 199B's job). Today `PlanningState`
# only ever holds the CURRENT plan; `version_history`/`VersionHistoryItem`
# (app.models.planning_state) is metadata-only bookkeeping about which
# sections changed for each labeled version, never a snapshot of that
# version's actual content (Task 1's audit finding, recorded in
# `RevisionLineageService`'s own module docstring). This module adds two
# new, deliberately minimal models:
#
#   ItineraryBranch  -- a stable, named line of revisions for one trip.
#                        Every existing trip gets exactly one ("Main",
#                        `is_default=True`) branch once this section's
#                        lineage bookkeeping is first initialized for it.
#   ItineraryRevision -- one immutable, addressable point in that line,
#                         optionally carrying a full serialized
#                         `PlanningState` snapshot (`snapshot_available`
#                         distinguishes a revision this section actually
#                         captured from an older, pre-199A version-history
#                         entry for which no historical state was ever
#                         stored -- Task 2/18: that absence is never
#                         fabricated/reconstructed).
#
# Neither model is nested inside `PlanningState` itself, and
# `ItineraryRevision.snapshot` never contains another `ItineraryRevision`
# -- deliberately avoiding the recursive-snapshot anti-pattern Task 7
# warns against. Both follow this codebase's existing
# `<prefix>_<uuid4 hex>` stable-id convention (see
# `app.models.planning_state._new_id`, `app.models.generation_job.
# new_job_id`) -- a revision's `revision_id` is its stable branching
# identity; the human-readable `version_label` (`"v3"`, etc.) is kept
# only for display/cross-reference with `VersionHistoryItem`/
# `FeedbackEvent.applied_in_version`/`RegenerationAttempt`, and is never
# itself a stable identity (Core invariant 3).


def new_branch_id() -> str:
    return f"branch_{uuid4().hex}"


def new_revision_id() -> str:
    return f"revision_{uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Mirrors `VersionHistoryItem.created_by`'s existing two real values
# (`"system_generation"`, `"user_feedback"`) so a revision's origin is
# reported the same way the version history already does, never a
# second, divergent vocabulary.
DEFAULT_BRANCH_DISPLAY_NAME = "Main"


class ItineraryBranch(BaseModel):
    """A stable, named line of revisions for one trip (Task 19).

    Every trip gets exactly one branch once 199A's lineage bookkeeping
    has been initialized for it (`is_default=True`, `display_name=
    "Main"`) -- Section 199B is what will let a user create additional,
    non-default branches. `head_revision_id` is `None` only in the brief
    window between a branch being created and its first revision being
    recorded (Core invariant 6 still holds once that first revision
    exists); `base_revision_id` records which revision this branch was
    created from (`None` for the one default/main branch of a trip,
    since it was not forked from anything -- Section 199B will set this
    for a real fork).
    """

    branch_id: str = Field(default_factory=new_branch_id)
    trip_id: str

    display_name: str = DEFAULT_BRANCH_DISPLAY_NAME
    is_default: bool = False

    base_revision_id: str | None = None
    head_revision_id: str | None = None

    created_at: datetime = Field(default_factory=_utc_now)


class ItineraryRevision(BaseModel):
    """One immutable, addressable point in a branch's line of revisions
    (Task 20).

    `revision_id` is the stable branching identity Section 199B will
    fork from -- `version_label` is carried along purely for display and
    cross-reference with `VersionHistoryItem`/`FeedbackEvent.
    applied_in_version`/`RegenerationAttempt.current_version` (Task 14),
    never as a substitute identity (Core invariant 3). `parent_revision_id`
    is `None` only for a branch's very first revision (Task 5); every
    later one names its direct predecessor, never derived from
    `version_label` arithmetic (Task 15).

    `snapshot_available=False` with `snapshot=None` is the honest
    representation of a historical version this section found already
    recorded (as `VersionHistoryItem` metadata) but for which no full
    `PlanningState` was ever stored before 199A existed (Task 2/17) --
    this is never backfilled by reversing a diff or replaying feedback
    history (Task 18). `snapshot`, when present, is the canonical
    serialized form produced by
    `app.services.revision_snapshot_service.serialize_planning_state_snapshot`
    (Task 10) -- a plain JSON-compatible dict, never a second hand-built
    partial copy of `PlanningState`'s fields.
    """

    revision_id: str = Field(default_factory=new_revision_id)
    trip_id: str
    branch_id: str
    parent_revision_id: str | None = None

    version_label: str
    created_by: str
    created_at: datetime = Field(default_factory=_utc_now)

    # Task 20: "originating feedback/version-history linkage only if
    # existing IDs make this clean" -- both already exist as stable ids
    # on the models this revision corresponds to, so both are carried
    # here verbatim rather than re-derived.
    feedback_event_id: str | None = None
    version_history_item_id: str | None = None

    snapshot_available: bool = False
    snapshot: dict[str, Any] | None = None
