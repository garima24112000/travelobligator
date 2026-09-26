"""Persistence-level concurrency errors (Section 200C).

Deliberately tiny, backend-agnostic exceptions raised by repositories when the
DATABASE (or, for Local JSON, an in-process check) detects that a writer acted on
stale information. Messages are fixed and safe: they never carry SQL, versions,
row identifiers beyond the public trip/job id, or driver text. The API layer maps
them to HTTP 409 (`CONCURRENT_UPDATE` / `JOB_ALREADY_RUNNING`).
"""

from __future__ import annotations


class ConcurrentStateUpdateError(Exception):
    """A whole-`PlanningState` write was based on a stale read.

    Raised by `PostgresPlanningStateRepository.save` when
    `UPDATE ... WHERE lock_version = :expected` updates zero rows, i.e. another
    writer committed first (or the caller has no concurrency token for an
    already-existing row). The stale writer's data is NOT written -- the caller
    must reload the state and decide again.
    """

    def __init__(self, trip_id: str | None = None) -> None:
        self.trip_id = trip_id
        super().__init__(
            "The itinerary was changed by another request; reload it and try again."
        )


class BranchHeadConflictError(ConcurrentStateUpdateError):
    """A branch head compare-and-set found a different head than the caller expected."""

    def __init__(self, branch_id: str | None = None) -> None:
        self.branch_id = branch_id
        super().__init__(None)


class JobAlreadyActiveError(Exception):
    """The database refused a second active (queued/running) job for one trip
    (`uq_generation_jobs_one_active_per_trip`)."""

    def __init__(self, trip_id: str | None = None) -> None:
        self.trip_id = trip_id
        super().__init__("A generation or regeneration job is already active for this trip.")


class _Unset:
    """Sentinel: "the caller did not supply an expected value" (distinct from a real `None`,
    e.g. a branch whose head is legitimately still `None`)."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET = _Unset()
