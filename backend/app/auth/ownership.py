"""Trip ownership check (Step 184D).

`require_trip_owner` is a FastAPI dependency usable as
`Depends(require_trip_owner)` on any route with a `{trip_id}` path
parameter -- FastAPI resolves `trip_id` from the path and runs the nested
`get_current_user` dependency (401 for a missing/invalid/expired session,
or a valid session for a since-deleted user) *before* this function's own
body ever runs, so authentication and ownership are both checked before
any route logic executes, including every generation/regeneration/
mutation path.

Uses `TripRecord.owner_id` (`app/repositories/trip_repository.py`) as the
sole source of truth for both existence and ownership -- never
`PlanningState`, which has no `owner_id` field and never will (ownership
lives on `trips` only). `TripRecord`/`PlanningState` are always created
together by `PlanningOrchestrator.create_trip`, so in normal operation
this is equivalent to the existing `planning_state is None` checks most
routes already perform for their own business-logic reasons; those stay
in place unchanged as a defensive fallback, not because this dependency
is insufficient.
"""

from __future__ import annotations

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.core.errors import forbidden_error, trip_not_found_error
from app.models.user import PublicUser
from app.repositories.factory import get_trip_repository


def require_trip_owner(
    trip_id: str, current_user: PublicUser = Depends(get_current_user)
) -> PublicUser:
    """Raises `trip_not_found_error()` (404) if no trip with this id
    exists at all -- preserving exactly the same 404 behavior every route
    already had before this step. Raises `forbidden_error()` (403) if the
    trip exists but has no owner (created before Step 184D existed --
    deliberately never silently assigned to whichever user asks for it
    first) or is owned by a different user. Both the "unowned" and
    "owned by someone else" cases return the identical 403 -- neither the
    response nor its message ever reveals which one it was, or who the
    real owner is.

    Returns `current_user` unchanged on success, so a route that also
    wants the authenticated user (e.g. to set `owner_id` on a mutation)
    can depend on this for both the check and the value in one line.
    """
    trip_record = get_trip_repository().get(trip_id)
    if trip_record is None:
        raise trip_not_found_error(trip_id)

    if trip_record.owner_id is None or trip_record.owner_id != current_user.user_id:
        raise forbidden_error()

    return current_user


__all__ = ["require_trip_owner"]
