from __future__ import annotations

import logging

from app.models.itinerary_lineage import (
    DEFAULT_BRANCH_DISPLAY_NAME,
    ItineraryBranch,
    ItineraryRevision,
)
from app.models.planning_state import PlanningState
from app.repositories.factory import get_lineage_repository
from app.services.revision_snapshot_service import (
    deserialize_planning_state_snapshot,
    serialize_planning_state_snapshot,
)

logger = logging.getLogger(__name__)

_STAGE = "revision_lineage"

# Section 199A (docs/14_backend_architecture.md, following section 61):
# the persistence/domain FOUNDATION for true itinerary forks -- NOT fork
# creation itself. This service:
#
#   - ensures every trip lazily has one default ("Main") branch
#     (Task 6/17) -- safe to call from a read path, since it only ever
#     creates a cheap metadata row, never a snapshot.
#   - records an immutable revision (with a full `PlanningState`
#     snapshot) for whatever version a caller's generation/regeneration
#     just successfully produced (Task 12/13/14), idempotently
#     (Task 29) and best-effort (a failure here is logged and swallowed,
#     never allowed to turn an already-successful generation/
#     regeneration into a reported failure -- the real `PlanningState`
#     write already happened and already succeeded by the time this
#     runs).
#   - exposes read-only lineage listing/snapshot-loading and a
#     consistency check (Task 25/28).
#
# Task 1's own audit, recorded here rather than assumed: before this
# section, NO historical full `PlanningState` was ever stored anywhere
# -- `PlanningStateRepository`/`PostgresPlanningStateRepository` keep
# only the CURRENT state per trip_id, and `version_history`/
# `VersionHistoryItem` (app.models.planning_state) is metadata-only
# bookkeeping (which sections changed, never their content). Once a
# trip regenerates from v1 to v2, v1's actual content is gone -- there
# was and is no code path that could reconstruct it. This service never
# pretends otherwise (Task 2/18): a historical `VersionHistoryItem` this
# service finds but never itself captured a snapshot for stays
# `snapshot_available=False`, permanently.
#
# STRICT SCOPE BOUNDARY (per this section's own spec): 199A does NOT let
# a user create a fork, switch the editable branch, regenerate from a
# historical revision, merge branches, or compare branches -- every
# method below is either lazy-initialization, an immutable append, or
# read-only. Section 199B is what will add real fork-creation semantics
# on top of this foundation.


class RevisionLineageService:
    def ensure_default_branch(self, trip_id: str) -> ItineraryBranch:
        """Idempotent (Task 6/29): returns the trip's existing default
        branch if one is already recorded, otherwise creates it. Cheap
        and side-effect-free beyond that one metadata row -- safe to call
        from a read-only path, since it never touches/creates a revision
        or snapshot itself."""
        repository = get_lineage_repository()
        existing = repository.get_default_branch(trip_id)
        if existing is not None:
            return existing

        branch = repository.create_branch(
            ItineraryBranch(
                trip_id=trip_id,
                display_name=DEFAULT_BRANCH_DISPLAY_NAME,
                is_default=True,
            )
        )
        logger.info(
            "Default branch created for trip %s.",
            trip_id,
            extra={
                "stage": _STAGE,
                "trip_id": trip_id,
                "branch_id": branch.branch_id,
                "operation": "ensure_default_branch",
            },
        )
        return branch

    def record_current_revision(
        self, planning_state: PlanningState, *, created_by: str | None = None
    ) -> ItineraryRevision | None:
        """Records an immutable revision (Task 12/13/14) for whatever
        version `planning_state.metadata.current_version` names RIGHT
        NOW, on the trip's default branch, with a full snapshot (Task 8/9
        -- the entire `PlanningState`, faithfully, never trimmed).

        Idempotent (Task 29): if a revision already exists for this
        branch+version (e.g. a retried call, or a read-after-write
        verification), returns the existing one rather than creating a
        duplicate. Parent lineage (Task 5/15) is always the branch's
        CURRENT head at call time -- never derived from `version_label`
        arithmetic, and correctly `None` for a branch's first-ever
        captured revision (a brand new trip's real v1, or an old
        pre-199A trip's current version being captured for the first
        time under this section -- Task 17: the old version(s) before it
        are never fabricated as revisions, so the newly captured one
        honestly has no real recorded parent).

        Best-effort by design (Task 16's own "failure: head remains
        unchanged" + this section's overall foundation-only scope): any
        exception here is logged and swallowed, never raised -- the
        caller's real `PlanningState` write has already succeeded by the
        time every call site below reaches this, and a lineage-recording
        problem must never turn that into a reported failure.
        """
        try:
            return self._record_current_revision(planning_state, created_by=created_by)
        except Exception:
            logger.warning(
                "Failed to record a revision snapshot for trip %s; the "
                "real plan state was already saved successfully and is "
                "unaffected.",
                planning_state.trip_id,
                exc_info=True,
                extra={
                    "stage": _STAGE,
                    "trip_id": planning_state.trip_id,
                    "version_label": planning_state.metadata.current_version,
                    "operation": "record_current_revision",
                },
            )
            return None

    def _record_current_revision(
        self, planning_state: PlanningState, *, created_by: str | None
    ) -> ItineraryRevision:
        repository = get_lineage_repository()
        trip_id = planning_state.trip_id
        version_label = planning_state.metadata.current_version

        branch = self.ensure_default_branch(trip_id)

        existing = repository.get_revision_by_branch_and_version(
            branch.branch_id, version_label
        )
        if existing is not None:
            logger.info(
                "Revision for trip %s branch %s version %s already recorded; skipping.",
                trip_id,
                branch.branch_id,
                version_label,
                extra={
                    "stage": _STAGE,
                    "trip_id": trip_id,
                    "branch_id": branch.branch_id,
                    "revision_id": existing.revision_id,
                    "version_label": version_label,
                    "operation": "record_current_revision_idempotent_skip",
                },
            )
            return existing

        # Task 5/15: the direct parent is the branch's CURRENT head --
        # never searched/derived by version label.
        parent_revision_id = branch.head_revision_id

        # Task 14: cross-reference the exact `VersionHistoryItem` this
        # version_label corresponds to (the most recently appended match
        # -- `version_history` is append-only, so this is always the one
        # `create_initial_version`/`create_version_after_feedback` just
        # added), so `created_by`/`feedback_event_id` stay a real
        # restatement of already-recorded facts, never guessed.
        version_history_item = next(
            (
                item
                for item in reversed(planning_state.version_history)
                if item.version_label == version_label
            ),
            None,
        )

        snapshot = serialize_planning_state_snapshot(planning_state)

        revision = ItineraryRevision(
            trip_id=trip_id,
            branch_id=branch.branch_id,
            parent_revision_id=parent_revision_id,
            version_label=version_label,
            created_by=created_by
            or (version_history_item.created_by if version_history_item else "system_generation"),
            feedback_event_id=(
                version_history_item.feedback_event_id if version_history_item else None
            ),
            version_history_item_id=(
                version_history_item.version_id if version_history_item else None
            ),
            snapshot_available=True,
            snapshot=snapshot,
        )
        created = repository.create_revision(revision)

        # Task 16: branch head only ever advances after the revision it
        # will point to already exists -- never the other way around.
        repository.update_branch_head(branch.branch_id, created.revision_id)

        logger.info(
            "Revision recorded for trip %s.",
            trip_id,
            extra={
                "stage": _STAGE,
                "trip_id": trip_id,
                "branch_id": branch.branch_id,
                "revision_id": created.revision_id,
                "parent_revision_id": created.parent_revision_id,
                "version_label": created.version_label,
                "snapshot_available": created.snapshot_available,
                "operation": "record_current_revision",
            },
        )
        return created

    def list_branch_lineage(self, branch_id: str) -> list[ItineraryRevision]:
        """Read-only (Task 25/26): every revision recorded for `branch_id`,
        oldest first -- metadata only from the repository's own list
        method; callers building an API list response should still
        prefer a summary shape that omits each revision's `snapshot`
        (Task 20/27), even though the repository itself returns full
        objects."""
        return get_lineage_repository().list_revisions_for_branch(branch_id)

    def load_snapshot(self, revision_id: str) -> PlanningState | None:
        """Read-only (Task 25): the full historical `PlanningState` for
        `revision_id`, or `None` if the revision doesn't exist or never
        had a snapshot captured (`snapshot_available=False`) -- never
        reconstructed by any other means (Task 18)."""
        revision = get_lineage_repository().get_revision(revision_id)
        if revision is None or not revision.snapshot_available or revision.snapshot is None:
            return None
        return deserialize_planning_state_snapshot(revision.snapshot)

    def check_branch_head_consistency(self, planning_state: PlanningState) -> bool:
        """Task 28: does the trip's default branch head agree with the
        live, editable `PlanningState.metadata.current_version`? Returns
        `True` when lineage has not been initialized/no revision has
        been captured yet for this trip (nothing to disagree with) --
        never silently repairs a real mismatch, only reports it so the
        caller can log/surface it through existing conventions."""
        repository = get_lineage_repository()
        branch = repository.get_default_branch(planning_state.trip_id)
        if branch is None or branch.head_revision_id is None:
            return True

        head_revision = repository.get_revision(branch.head_revision_id)
        if head_revision is None:
            logger.warning(
                "Branch head consistency check failed for trip %s: head "
                "revision id is recorded but the revision itself is missing.",
                planning_state.trip_id,
                extra={
                    "stage": _STAGE,
                    "trip_id": planning_state.trip_id,
                    "branch_id": branch.branch_id,
                    "revision_id": branch.head_revision_id,
                    "operation": "check_branch_head_consistency",
                },
            )
            return False

        if head_revision.version_label != planning_state.metadata.current_version:
            logger.warning(
                "Branch head consistency check failed for trip %s: branch "
                "head is version %s but the live planning state is version %s.",
                planning_state.trip_id,
                head_revision.version_label,
                planning_state.metadata.current_version,
                extra={
                    "stage": _STAGE,
                    "trip_id": planning_state.trip_id,
                    "branch_id": branch.branch_id,
                    "revision_id": branch.head_revision_id,
                    "version_label": head_revision.version_label,
                    "operation": "check_branch_head_consistency",
                },
            )
            return False

        return True


revision_lineage_service = RevisionLineageService()
