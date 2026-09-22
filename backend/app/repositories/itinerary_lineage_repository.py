from __future__ import annotations

from app.core.config import get_settings
from app.models.itinerary_lineage import ItineraryBranch, ItineraryRevision
from app.storage.local_json_store import LocalJsonStore, get_local_json_store

_BRANCHES_COLLECTION = "itinerary_branches"
_REVISIONS_COLLECTION = "itinerary_revisions"


class ItineraryLineageRepository:
    """Local-file-backed `ItineraryBranch`/`ItineraryRevision` repository
    (Section 199A, Task 21/22). Mirrors every other repository in this
    codebase (`PlanningStateRepository`, `JobRepository`): every branch
    and revision is cached in memory for the lifetime of this instance
    (loaded from disk at construction time) and persisted to the same
    local JSON file, under two new collections of their own, on every
    write -- so recorded lineage survives a backend process restart
    during local development.

    Branches and revisions are kept in one repository (rather than two
    separate ones) because they are never meaningfully used apart from
    each other -- advancing a branch's head always happens alongside
    recording the revision it now points to, and every revision read
    needs its owning branch to be just as reachable.
    """

    def __init__(self, store: LocalJsonStore | None = None) -> None:
        self._store = store or get_local_json_store(
            get_settings().resolved_local_storage_path()
        )
        self._branches: dict[str, ItineraryBranch] = {
            branch_id: ItineraryBranch.model_validate(record)
            for branch_id, record in self._store.read_collection(_BRANCHES_COLLECTION).items()
        }
        self._revisions: dict[str, ItineraryRevision] = {
            revision_id: ItineraryRevision.model_validate(record)
            for revision_id, record in self._store.read_collection(
                _REVISIONS_COLLECTION
            ).items()
        }

    def _persist_branches(self) -> None:
        self._store.write_collection(
            _BRANCHES_COLLECTION,
            {
                branch_id: branch.model_dump(mode="json")
                for branch_id, branch in self._branches.items()
            },
        )

    def _persist_revisions(self) -> None:
        self._store.write_collection(
            _REVISIONS_COLLECTION,
            {
                revision_id: revision.model_dump(mode="json")
                for revision_id, revision in self._revisions.items()
            },
        )

    # -- branches -------------------------------------------------------

    def create_branch(self, branch: ItineraryBranch) -> ItineraryBranch:
        self._branches[branch.branch_id] = branch
        self._persist_branches()
        return branch

    def get_branch(self, branch_id: str) -> ItineraryBranch | None:
        return self._branches.get(branch_id)

    def get_default_branch(self, trip_id: str) -> ItineraryBranch | None:
        for branch in self._branches.values():
            if branch.trip_id == trip_id and branch.is_default:
                return branch
        return None

    def list_branches_for_trip(self, trip_id: str) -> list[ItineraryBranch]:
        matches = [branch for branch in self._branches.values() if branch.trip_id == trip_id]
        return sorted(matches, key=lambda branch: (branch.created_at, branch.branch_id))

    def update_branch_head(
        self, branch_id: str, head_revision_id: str
    ) -> ItineraryBranch | None:
        branch = self._branches.get(branch_id)
        if branch is None:
            return None
        updated = branch.model_copy(update={"head_revision_id": head_revision_id})
        self._branches[branch_id] = updated
        self._persist_branches()
        return updated

    # -- revisions --------------------------------------------------------

    def create_revision(self, revision: ItineraryRevision) -> ItineraryRevision:
        self._revisions[revision.revision_id] = revision
        self._persist_revisions()
        return revision

    def get_revision(self, revision_id: str) -> ItineraryRevision | None:
        return self._revisions.get(revision_id)

    def list_revisions_for_branch(self, branch_id: str) -> list[ItineraryRevision]:
        matches = [
            revision for revision in self._revisions.values() if revision.branch_id == branch_id
        ]
        return sorted(matches, key=lambda revision: (revision.created_at, revision.revision_id))

    def get_revision_by_branch_and_version(
        self, branch_id: str, version_label: str
    ) -> ItineraryRevision | None:
        """Task 29 idempotency lookup: is a revision for this exact
        branch + version label already recorded? Used by
        `RevisionLineageService.record_current_revision` before creating
        a new one, so a retried/duplicate call never creates a second
        historical revision for the same real version."""
        for revision in self._revisions.values():
            if revision.branch_id == branch_id and revision.version_label == version_label:
                return revision
        return None


itinerary_lineage_repository = ItineraryLineageRepository()
