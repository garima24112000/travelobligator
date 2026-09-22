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
    base_revision_id: str | None
    head_revision_id: str | None
    created_at: datetime


class ItineraryBranchListResponseData(BaseModel):
    trip_id: str
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
