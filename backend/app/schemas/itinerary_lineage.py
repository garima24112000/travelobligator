from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.planning_state import PlanningState

# Section 199A (Task 20/26/27) read-only API response shapes.
#
# `ItineraryRevisionSummary` deliberately never carries `snapshot` --
# Task 20's own instruction ("do not duplicate the entire PlanningState
# directly in API list responses") -- so `GET .../revisions` stays cheap
# regardless of how large a captured `PlanningState` snapshot is.
# `ItineraryRevisionDetailResponseData` reuses the exact same
# `PlanningState` shape `TripResponseData`/`GET /trips/{trip_id}` already
# returns (Task 27: never a second, richer, internal-only representation
# leaking through this endpoint) -- `planning_state` is `None` exactly
# when `snapshot_available` is `False` (a historical, pre-199A version
# this section found recorded but never itself captured a snapshot for),
# never a fabricated placeholder.


class ItineraryBranchResponseData(BaseModel):
    branch_id: str
    trip_id: str
    display_name: str
    is_default: bool
    # Section 199B (Task 22): whether THIS branch is the trip's current
    # active/editable one -- computed by the route from
    # `RevisionLineageService.resolve_active_branch_id`, never a stored
    # `is_active` field on `ItineraryBranch` itself (Task 2's own
    # reasoning against ambiguous multiple `is_active=true` rows still
    # applies at the model level; this is a per-response, derived flag).
    is_active: bool
    base_revision_id: str | None
    head_revision_id: str | None
    created_at: datetime


class ItineraryBranchListResponseData(BaseModel):
    trip_id: str
    # Section 199B (Task 22): the stable id `ItineraryBranchResponseData.
    # is_active` above was computed against -- included at the top level
    # too so a caller can identify the active branch without scanning
    # every entry for `is_active=True`.
    active_branch_id: str
    branches: list[ItineraryBranchResponseData] = Field(default_factory=list)


class ItineraryRevisionSummary(BaseModel):
    revision_id: str
    trip_id: str
    branch_id: str
    parent_revision_id: str | None
    version_label: str
    created_by: str
    created_at: datetime
    feedback_event_id: str | None
    snapshot_available: bool


class ItineraryRevisionListResponseData(BaseModel):
    trip_id: str
    branch_id: str
    revisions: list[ItineraryRevisionSummary] = Field(default_factory=list)


class ItineraryRevisionDetailResponseData(BaseModel):
    revision_id: str
    trip_id: str
    branch_id: str
    parent_revision_id: str | None
    version_label: str
    created_by: str
    created_at: datetime
    feedback_event_id: str | None
    snapshot_available: bool
    planning_state: PlanningState | None


# Section 199B (Task 6/7/19) fork-creation and branch-activation shapes.


class CreateItineraryForkRequest(BaseModel):
    source_revision_id: str
    display_name: str
    activate_after_create: bool = False


class CreateItineraryForkResponseData(BaseModel):
    status: str
    message: str
    branch: ItineraryBranchResponseData | None = None
    source_revision_id: str
    # Task 7: "current head revision" -- for a freshly created fork this
    # always equals `source_revision_id` (Task 9: the branch initially
    # shares immutable ancestry with its source, never a cloned new
    # root), included explicitly anyway so a caller never has to assume
    # that equality itself.
    head_revision_id: str | None = None
    snapshot_available: bool
    activated: bool
    # Only meaningful when `activate_after_create=True` was requested --
    # `None` otherwise (Task 33: creation and activation are reported
    # honestly and separately, never conflated).
    activation_status: str | None = None
    activation_message: str | None = None


class ActivateItineraryBranchResponseData(BaseModel):
    status: str
    message: str
    previous_branch_id: str | None = None
    active_branch_id: str | None = None
    head_revision_id: str | None = None
    current_version: str | None = None
