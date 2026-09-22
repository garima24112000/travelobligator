from __future__ import annotations

from typing import Any

from app.models.planning_state import PlanningState

# Section 199A (Task 10): the ONE canonical boundary between a live
# `PlanningState` and its immutable, storable revision-snapshot payload
# -- every other 199A call site (the repository, `RevisionLineageService`,
# tests) goes through these two functions instead of scattering
# `model_dump()`/manual dict surgery across several places.
#
# `serialize_planning_state_snapshot` deliberately does not remove,
# rename, or reshape any field -- Task 9's own instruction is to preserve
# historical state faithfully in the stored snapshot; any "reset this
# counter/field for a fresh working copy" semantic belongs to Section
# 199B's future fork-creation step, never to this read-only foundation.
# `model_dump(mode="json")` is the same serialization every other
# repository in this codebase already uses for a `PlanningState` (see
# `PlanningStateRepository.save`, `PlanningStateRow.state`) -- reusing it
# here means a revision snapshot round-trips through JSON exactly like
# the live planning-state document already does, with no second,
# divergent serialization format to keep in sync.


def serialize_planning_state_snapshot(planning_state: PlanningState) -> dict[str, Any]:
    """`PlanningState` -> a plain JSON-compatible dict, safe to store as
    `ItineraryRevision.snapshot` (local-JSON collection or Postgres
    JSONB). A deep copy by construction -- `model_dump` never returns
    objects shared with `planning_state`'s own live, still-mutable
    fields, so storing this dict can never let a later in-place mutation
    of the working `PlanningState` reach back into an already-persisted
    revision (Task 11's immutability requirement)."""
    return planning_state.model_dump(mode="json")


def deserialize_planning_state_snapshot(snapshot: dict[str, Any]) -> PlanningState:
    """The inverse of `serialize_planning_state_snapshot` -- a snapshot
    payload -> a fresh, independent `PlanningState` clone. Read-only
    verification/loading only in 199A (Task 10); Section 199B is what
    will actually use a clone like this as the seed for a new fork's
    working copy."""
    return PlanningState.model_validate(snapshot)
