from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from app.models.itinerary_lineage import (
    DEFAULT_BRANCH_DISPLAY_NAME,
    ItineraryBranch,
    ItineraryRevision,
)
from app.models.planning_state import PlanningState
from app.repositories.factory import (
    get_job_repository,
    get_lineage_repository,
    get_planning_state_repository,
)
from app.services.feedback_service import pending_feedback_events
from app.services.revision_snapshot_service import (
    build_workspace_consistency_projection,
    deserialize_planning_state_snapshot,
    serialize_planning_state_snapshot,
)

logger = logging.getLogger(__name__)

_STAGE = "revision_lineage"


class BranchActivationStatus(str, Enum):
    """Section 199B (Task 13/14/15/16/17/18): every outcome
    `RevisionLineageService.activate_branch` can reach. Mirrors this
    codebase's existing "one enum, one dataclass result" convention
    (`TargetedRegenerationRuntimeStatus`/`TargetedRegenerationRuntimeResult`)
    rather than raising/catching several different exception types."""

    ACTIVATED = "activated"
    BRANCH_NOT_FOUND = "branch_not_found"
    SNAPSHOT_UNAVAILABLE = "snapshot_unavailable"
    BLOCKED_PENDING_FEEDBACK = "blocked_pending_feedback"
    BLOCKED_RUNNING_JOB = "blocked_running_job"
    BLOCKED_ACTIVE_LOCK = "blocked_active_lock"
    BLOCKED_STATE_CONFLICT = "blocked_state_conflict"


@dataclass
class BranchActivationResult:
    status: BranchActivationStatus
    message: str
    previous_branch_id: str | None = None
    active_branch_id: str | None = None
    head_revision_id: str | None = None
    current_version: str | None = None

    @property
    def activated(self) -> bool:
        return self.status == BranchActivationStatus.ACTIVATED

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

    def resolve_active_branch_id(self, planning_state: PlanningState) -> str:
        """Section 199B (Task 3/24): the branch a generation/regeneration
        call operating on `planning_state` right now should record its
        result onto. `planning_state.metadata.active_branch_id` is
        authoritative once set (by a real `activate_branch` call, Task
        13); `None` -- every trip that has never had a branch explicitly
        activated, including every trip from before this section shipped
        -- always resolves to the trip's lazily-ensured default branch,
        never requiring a manual migration (Task 3)."""
        if planning_state.metadata.active_branch_id is not None:
            return planning_state.metadata.active_branch_id
        return self.ensure_default_branch(planning_state.trip_id).branch_id

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

        # Task 24/25: records onto whichever branch is CURRENTLY active
        # for this planning_state, never unconditionally the default
        # branch -- a fresh trip (or any trip that has never activated a
        # fork) still resolves to "Main," exactly as 199A already
        # behaved; a trip with an active fork records onto that fork
        # instead, and its parent is that fork's own current head
        # (Task 25) even when that head revision was inherited from
        # Main at fork time (Task 9/10).
        branch = repository.get_branch(
            self.resolve_active_branch_id(planning_state)
        ) or self.ensure_default_branch(trip_id)

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
        """Task 28 (199A), generalized to the ACTIVE branch in 199B
        (Task 4), and strengthened to real semantic content equality in
        199B.1 (Task 5) -- `active_branch_id` + a matching
        `version_label` alone is NOT treated as proof the live workspace
        equals its branch head (199B.1's own root-issue finding: a
        same-label in-place content mutation would have slipped past
        the label-only check). Returns `True` when lineage has not been
        initialized/no revision has been captured yet for this trip
        (nothing to disagree with) -- never silently repairs a real
        mismatch, only reports it so the caller can log/surface it
        through existing conventions.

        Steps (Task 5): resolve the active branch -> resolve its exact
        `head_revision_id` (stable identity, never a trip-wide label
        search -- Task 4's own explicit warning: "two branches can both
        contain v3") -> require a real snapshot for it -> verify the
        version label agrees -> compare canonical REVISION-CONTENT
        projections (`revision_snapshot_service.
        build_workspace_consistency_projection`, Task 2) -- which
        already excludes pending feedback/locks/audit-only state, so
        those legitimately differing never produces a false conflict
        here (Task 12/3).
        """
        repository = get_lineage_repository()
        branch = repository.get_branch(self.resolve_active_branch_id(planning_state))
        if branch is None or branch.head_revision_id is None:
            return True

        head_revision = repository.get_revision(branch.head_revision_id)
        if head_revision is None:
            self._log_consistency_failure(
                planning_state, branch, reason="head_revision_missing"
            )
            return False

        if head_revision.version_label != planning_state.metadata.current_version:
            self._log_consistency_failure(
                planning_state,
                branch,
                reason="version_label_mismatch",
                head_version_label=head_revision.version_label,
            )
            return False

        if not head_revision.snapshot_available or head_revision.snapshot is None:
            # Task 5 step 3: a real branch head should always carry a
            # snapshot by construction (record_current_revision/
            # create_fork both require one) -- if it somehow doesn't,
            # content equality is simply unverifiable, which is treated
            # as inconsistent rather than silently trusted.
            self._log_consistency_failure(
                planning_state, branch, reason="head_snapshot_unavailable"
            )
            return False

        head_snapshot = deserialize_planning_state_snapshot(head_revision.snapshot)
        live_projection = build_workspace_consistency_projection(planning_state)
        head_projection = build_workspace_consistency_projection(head_snapshot)
        if live_projection != head_projection:
            self._log_consistency_failure(
                planning_state,
                branch,
                reason="content_drift",
                head_version_label=head_revision.version_label,
            )
            return False

        return True

    def _log_consistency_failure(
        self,
        planning_state: PlanningState,
        branch: ItineraryBranch,
        *,
        reason: str,
        head_version_label: str | None = None,
    ) -> None:
        # Task 19: safe, structural fields only -- never the full
        # (differing) PlanningState/snapshot, never a prompt/provider
        # payload.
        logger.warning(
            "Branch head consistency check failed for trip %s: %s.",
            planning_state.trip_id,
            reason,
            extra={
                "stage": "itinerary_branch",
                "operation": "workspace_consistency",
                "trip_id": planning_state.trip_id,
                "branch_id": branch.branch_id,
                "head_revision_id": branch.head_revision_id,
                "current_version": planning_state.metadata.current_version,
                "head_version": head_version_label,
                "status": "conflict",
                "reason": reason,
            },
        )

    # -- Section 199B: branch activation (Tasks 13-18) ----------------------

    def activate_branch(self, trip_id: str, branch_id: str) -> BranchActivationResult:
        """Switches the trip's editable working copy onto `branch_id`'s
        head snapshot (Task 13). Never creates a new revision or
        `VersionHistoryItem` merely by switching, never auto-applies
        feedback, and applies every workspace-cleanliness guard (Task
        14-17) before touching anything -- a blocked activation makes
        NO change to the persisted `PlanningState`, matching the
        "failure: do not report a successful switch" requirement (Task
        18).

        Atomicity (Task 18): exactly one persisted write occurs on
        success -- `planning_state_repository.save(...)` of the deep
        snapshot clone, which already carries the new
        `active_branch_id` set on it (see the `PlanningMetadata.
        active_branch_id` docstring for why keeping this on
        `PlanningState` rather than a separate `TripRecord` field makes
        this a single-record write both backends already make atomic --
        a single JSON-file rewrite locally, a single row `UPDATE` in
        Postgres -- rather than two separate records that could
        disagree if a process died between them).
        """
        # Task 50: every outcome of this method -- success or refusal --
        # is logged through this one helper with the same allowlisted,
        # safe field set (`stage="itinerary_branch"`, matching this
        # task's own spec verbatim; never a snapshot, never a prompt).
        def _log_and_return(result: BranchActivationResult) -> BranchActivationResult:
            logger.info(
                "Branch activation outcome for trip %s: %s.",
                trip_id,
                result.status.value,
                extra={
                    "stage": "itinerary_branch",
                    "operation": "activate",
                    "trip_id": trip_id,
                    "branch_id": branch_id,
                    "previous_branch_id": result.previous_branch_id,
                    "head_revision_id": result.head_revision_id,
                    "status": result.status.value,
                },
            )
            return result

        planning_state_repository = get_planning_state_repository()
        current_state = planning_state_repository.get_by_trip_id(trip_id)
        if current_state is None:
            return _log_and_return(
                BranchActivationResult(
                    status=BranchActivationStatus.BRANCH_NOT_FOUND,
                    message=f"Trip '{trip_id}' was not found.",
                )
            )

        lineage_repository = get_lineage_repository()
        target_branch = lineage_repository.get_branch(branch_id)
        if target_branch is None or target_branch.trip_id != trip_id:
            # Task 49: never reveal whether a branch_id belonging to a
            # different trip exists at all.
            return _log_and_return(
                BranchActivationResult(
                    status=BranchActivationStatus.BRANCH_NOT_FOUND,
                    message=f"Branch '{branch_id}' was not found for trip '{trip_id}'.",
                )
            )

        previous_branch_id = self.resolve_active_branch_id(current_state)

        # Task 14 (workspace-cleanliness guards) -- checked against the
        # CURRENT branch's workspace, since that is what would be left
        # behind/orphaned by switching away from it. Order: cheapest/
        # most-common-first, but every guard is checked independently of
        # the others (no short-circuiting past a real problem).
        if pending_feedback_events(current_state.feedback_history):
            return _log_and_return(
                BranchActivationResult(
                    status=BranchActivationStatus.BLOCKED_PENDING_FEEDBACK,
                    message=(
                        "The current branch has feedback that has not been applied yet. "
                        "Regenerate (or otherwise resolve) pending feedback before "
                        "switching branches -- it is not part of any immutable "
                        "revision snapshot and would not carry over."
                    ),
                    previous_branch_id=previous_branch_id,
                )
            )

        active_lock_count = sum(1 for lock in current_state.user_locks if lock.is_active)
        if active_lock_count > 0:
            # Task 17: locks block branch activation outright, the same
            # "never worked around" policy this codebase already applies
            # to locks blocking regeneration (see
            # `regeneration_blocked_by_locks_error`) -- extended here
            # rather than inventing a separate, weaker rule for
            # branches.
            return _log_and_return(
                BranchActivationResult(
                    status=BranchActivationStatus.BLOCKED_ACTIVE_LOCK,
                    message=(
                        f"{active_lock_count} active lock(s) exist on the current "
                        "branch. Unlock every locked item before switching branches."
                    ),
                    previous_branch_id=previous_branch_id,
                )
            )

        running_jobs = get_job_repository().list_running_by_trip_id(trip_id)
        if running_jobs:
            # Task 15: a running async generate/regenerate job must
            # finish (or be observed as terminal) against the branch it
            # started on -- never cancelled automatically, never allowed
            # to complete against a branch the user has since switched
            # away from.
            return _log_and_return(
                BranchActivationResult(
                    status=BranchActivationStatus.BLOCKED_RUNNING_JOB,
                    message=(
                        "A generation or regeneration job is currently running for "
                        "this trip. Wait for it to finish before switching branches."
                    ),
                    previous_branch_id=previous_branch_id,
                )
            )

        if not self.check_branch_head_consistency(current_state):
            # Task 39: never guess which state should win -- refuse the
            # switch and surface the disagreement instead.
            return _log_and_return(
                BranchActivationResult(
                    status=BranchActivationStatus.BLOCKED_STATE_CONFLICT,
                    message=(
                        "The current branch's recorded head revision does not agree "
                        "with the live plan state. Refusing to switch branches until "
                        "this is resolved."
                    ),
                    previous_branch_id=previous_branch_id,
                )
            )

        if target_branch.head_revision_id is None:
            return _log_and_return(
                BranchActivationResult(
                    status=BranchActivationStatus.SNAPSHOT_UNAVAILABLE,
                    message=f"Branch '{branch_id}' has no revision recorded yet.",
                    previous_branch_id=previous_branch_id,
                )
            )

        target_snapshot = self.load_snapshot(target_branch.head_revision_id)
        if target_snapshot is None:
            # Task 2/8/18: never reconstructed -- a metadata-only
            # historical head (should not normally happen for a real
            # branch head, but checked defensively) is an honest refusal,
            # not a fabricated activation.
            return _log_and_return(
                BranchActivationResult(
                    status=BranchActivationStatus.SNAPSHOT_UNAVAILABLE,
                    message=(
                        f"Branch '{branch_id}'s head revision has no stored snapshot "
                        "to activate."
                    ),
                    previous_branch_id=previous_branch_id,
                )
            )

        target_snapshot.metadata.active_branch_id = target_branch.branch_id
        planning_state_repository.save(target_snapshot)

        return _log_and_return(
            BranchActivationResult(
                status=BranchActivationStatus.ACTIVATED,
                message=f"Branch '{branch_id}' activated.",
                previous_branch_id=previous_branch_id,
                active_branch_id=target_branch.branch_id,
                head_revision_id=target_branch.head_revision_id,
                current_version=target_snapshot.metadata.current_version,
            )
        )


revision_lineage_service = RevisionLineageService()
