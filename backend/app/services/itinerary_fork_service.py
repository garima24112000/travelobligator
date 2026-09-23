from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from app.models.itinerary_lineage import ItineraryBranch
from app.repositories.factory import get_lineage_repository
from app.services.revision_lineage_service import (
    BranchActivationResult,
    revision_lineage_service,
)

logger = logging.getLogger(__name__)

_STAGE = "itinerary_branch"
_MAX_DISPLAY_NAME_LENGTH = 80

# Section 199B (Task 12): the one service boundary for creating a real
# itinerary fork -- a new `ItineraryBranch` whose lineage starts at an
# existing, immutable `ItineraryRevision`. Deliberately separate from
# `RevisionLineageService` (which owns lineage RECORDING/consistency,
# used by every generation/regeneration path) -- this service owns
# FORK CREATION specifically, the one genuinely new piece of user-facing
# behavior 199B adds. Never puts this logic directly in the API route.
#
# Forking never mutates the source revision (Core invariant 1) -- this
# service only ever reads `source_revision_id` and creates a brand new
# `ItineraryBranch` row; nothing about the source revision or its owning
# branch is touched. The new branch initially shares immutable ancestry
# with its source (`base_revision_id == head_revision_id == source.
# revision_id`, Task 9) -- it is never cloned into a fake new root
# revision just to make a `branch_id` match the source's.


class ForkCreationStatus(str, Enum):
    CREATED = "created"
    SOURCE_NOT_FOUND = "source_not_found"
    SNAPSHOT_UNAVAILABLE = "snapshot_unavailable"
    INVALID_NAME = "invalid_name"
    NAME_CONFLICT = "name_conflict"


@dataclass
class ForkCreationResult:
    status: ForkCreationStatus
    message: str
    branch: ItineraryBranch | None = None
    # Only set when `activate_after_create=True` was requested -- Task
    # 33: creation and activation are reported honestly and separately.
    # A `CREATED` fork with a non-`ACTIVATED` `activation` means exactly
    # what it says: the branch now exists, but the workspace is still on
    # whichever branch was active before (never silently rolled back --
    # neither local JSON nor this section's Postgres usage wraps the two
    # steps in one transaction, so this is never described as a full
    # rollback).
    activation: BranchActivationResult | None = None

    @property
    def created(self) -> bool:
        return self.status == ForkCreationStatus.CREATED


class ItineraryForkService:
    def create_fork(
        self,
        trip_id: str,
        source_revision_id: str,
        display_name: str,
        *,
        activate_after_create: bool = False,
    ) -> ForkCreationResult:
        repository = get_lineage_repository()

        # Task 8: only a real, same-trip, snapshot-available revision may
        # be forked -- never a metadata-only pre-199A version_history
        # entry masquerading as one via a fabricated id.
        source = repository.get_revision(source_revision_id)
        if source is None or source.trip_id != trip_id:
            # Task 49: never reveal whether a revision_id belonging to a
            # different trip exists at all.
            return ForkCreationResult(
                status=ForkCreationStatus.SOURCE_NOT_FOUND,
                message=f"Revision '{source_revision_id}' was not found for trip '{trip_id}'.",
            )

        if not source.snapshot_available:
            return ForkCreationResult(
                status=ForkCreationStatus.SNAPSHOT_UNAVAILABLE,
                message=(
                    f"Revision '{source_revision_id}' has no stored snapshot to fork "
                    "from -- it is a historical version this section found recorded "
                    "but never itself captured full state for."
                ),
            )

        # Task 8: the snapshot must also actually deserialize, not just
        # report `snapshot_available=True` -- defensive, never trusted
        # blindly.
        if revision_lineage_service.load_snapshot(source_revision_id) is None:
            return ForkCreationResult(
                status=ForkCreationStatus.SNAPSHOT_UNAVAILABLE,
                message=f"Revision '{source_revision_id}'s snapshot could not be loaded.",
            )

        normalized_name = display_name.strip()
        if not normalized_name:
            return ForkCreationResult(
                status=ForkCreationStatus.INVALID_NAME,
                message="display_name must not be empty.",
            )
        if len(normalized_name) > _MAX_DISPLAY_NAME_LENGTH:
            return ForkCreationResult(
                status=ForkCreationStatus.INVALID_NAME,
                message=f"display_name must be at most {_MAX_DISPLAY_NAME_LENGTH} characters.",
            )

        # Task 11: unique per trip, case-insensitive/trimmed -- checked
        # here for an immediate, friendly error, and enforced again at
        # the database level for Postgres (a real unique index, Task 48
        # concurrency) -- this check alone is not a race-proof guarantee
        # on its own for two simultaneous local-JSON requests, honestly
        # (see this service's own docstring / docs for the disclosed
        # residual race, matching this codebase's existing "no full
        # distributed locking system" scope for 199B).
        existing_names = {
            branch.display_name.strip().lower()
            for branch in repository.list_branches_for_trip(trip_id)
        }
        if normalized_name.lower() in existing_names:
            return ForkCreationResult(
                status=ForkCreationStatus.NAME_CONFLICT,
                message=f"A branch named '{normalized_name}' already exists for this trip.",
            )

        branch = repository.create_branch(
            ItineraryBranch(
                trip_id=trip_id,
                display_name=normalized_name,
                is_default=False,
                base_revision_id=source.revision_id,
                head_revision_id=source.revision_id,
            )
        )
        logger.info(
            "Fork branch created for trip %s.",
            trip_id,
            extra={
                "stage": _STAGE,
                "trip_id": trip_id,
                "branch_id": branch.branch_id,
                "source_revision_id": source_revision_id,
                "operation": "create_fork",
                "status": ForkCreationStatus.CREATED.value,
            },
        )

        activation: BranchActivationResult | None = None
        if activate_after_create:
            # Task 33: the exact same guarded activation path every
            # other activation goes through -- never bypassed just
            # because it follows creation.
            activation = revision_lineage_service.activate_branch(trip_id, branch.branch_id)

        return ForkCreationResult(
            status=ForkCreationStatus.CREATED,
            message=f"Branch '{normalized_name}' created from revision '{source_revision_id}'.",
            branch=branch,
            activation=activation,
        )


itinerary_fork_service = ItineraryForkService()
