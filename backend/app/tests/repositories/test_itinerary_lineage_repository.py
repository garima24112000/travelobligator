from __future__ import annotations

from pathlib import Path

from app.models.itinerary_lineage import ItineraryBranch, ItineraryRevision
from app.repositories.itinerary_lineage_repository import ItineraryLineageRepository
from app.storage.local_json_store import LocalJsonStore

# Section 199A: repository-level tests for `ItineraryLineageRepository`,
# using only a temporary local JSON file (`tmp_path`) -- matching
# `test_job_repository.py`'s own convention -- never the module-level
# singleton/conftest-reset instance the service-level tests use.


def test_create_and_get_branch(tmp_path: Path) -> None:
    repo = ItineraryLineageRepository(LocalJsonStore(tmp_path / "state.json"))
    branch = repo.create_branch(ItineraryBranch(trip_id="trip_a", is_default=True))

    assert repo.get_branch(branch.branch_id) == branch
    assert repo.get_default_branch("trip_a") == branch
    assert repo.get_default_branch("trip_unknown") is None


def test_list_branches_for_trip_is_scoped_and_ordered(tmp_path: Path) -> None:
    repo = ItineraryLineageRepository(LocalJsonStore(tmp_path / "state.json"))
    b1 = repo.create_branch(ItineraryBranch(trip_id="trip_a", is_default=True))
    repo.create_branch(ItineraryBranch(trip_id="trip_b", is_default=True))
    b3 = repo.create_branch(ItineraryBranch(trip_id="trip_a", display_name="Other"))

    branches = repo.list_branches_for_trip("trip_a")
    assert [b.branch_id for b in branches] == [b1.branch_id, b3.branch_id]


def test_update_branch_head(tmp_path: Path) -> None:
    repo = ItineraryLineageRepository(LocalJsonStore(tmp_path / "state.json"))
    branch = repo.create_branch(ItineraryBranch(trip_id="trip_a", is_default=True))
    assert branch.head_revision_id is None

    updated = repo.update_branch_head(branch.branch_id, "revision_xyz")
    assert updated is not None
    assert updated.head_revision_id == "revision_xyz"
    assert repo.get_branch(branch.branch_id).head_revision_id == "revision_xyz"


def test_update_branch_head_returns_none_for_unknown_branch(tmp_path: Path) -> None:
    repo = ItineraryLineageRepository(LocalJsonStore(tmp_path / "state.json"))
    assert repo.update_branch_head("branch_does_not_exist", "revision_xyz") is None


def test_create_and_get_revision(tmp_path: Path) -> None:
    repo = ItineraryLineageRepository(LocalJsonStore(tmp_path / "state.json"))
    branch = repo.create_branch(ItineraryBranch(trip_id="trip_a", is_default=True))
    revision = repo.create_revision(
        ItineraryRevision(
            trip_id="trip_a",
            branch_id=branch.branch_id,
            version_label="v1",
            created_by="system_generation",
            snapshot_available=True,
            snapshot={"trip_id": "trip_a"},
        )
    )

    assert repo.get_revision(revision.revision_id) == revision
    assert repo.get_revision_by_branch_and_version(branch.branch_id, "v1") == revision
    assert repo.get_revision_by_branch_and_version(branch.branch_id, "v2") is None


def test_list_revisions_for_branch_is_scoped_and_ordered(tmp_path: Path) -> None:
    repo = ItineraryLineageRepository(LocalJsonStore(tmp_path / "state.json"))
    branch_a = repo.create_branch(ItineraryBranch(trip_id="trip_a", is_default=True))
    branch_b = repo.create_branch(ItineraryBranch(trip_id="trip_b", is_default=True))

    r1 = repo.create_revision(
        ItineraryRevision(trip_id="trip_a", branch_id=branch_a.branch_id, version_label="v1", created_by="system_generation")
    )
    repo.create_revision(
        ItineraryRevision(trip_id="trip_b", branch_id=branch_b.branch_id, version_label="v1", created_by="system_generation")
    )
    r3 = repo.create_revision(
        ItineraryRevision(
            trip_id="trip_a",
            branch_id=branch_a.branch_id,
            parent_revision_id=r1.revision_id,
            version_label="v2",
            created_by="user_feedback",
        )
    )

    revisions = repo.list_revisions_for_branch(branch_a.branch_id)
    assert [r.revision_id for r in revisions] == [r1.revision_id, r3.revision_id]


# ---------------------------------------------------------------------------
# Task 40: local persistence restart
# ---------------------------------------------------------------------------


def test_lineage_survives_a_repository_restart(tmp_path: Path) -> None:
    store_path = tmp_path / "state.json"

    first_repo = ItineraryLineageRepository(LocalJsonStore(store_path))
    branch = first_repo.create_branch(ItineraryBranch(trip_id="trip_restart", is_default=True))
    revision = first_repo.create_revision(
        ItineraryRevision(
            trip_id="trip_restart",
            branch_id=branch.branch_id,
            version_label="v1",
            created_by="system_generation",
            snapshot_available=True,
            snapshot={"trip_id": "trip_restart", "metadata": {"current_version": "v1"}},
        )
    )
    first_repo.update_branch_head(branch.branch_id, revision.revision_id)

    # A brand new repository instance, backed by a brand new
    # `LocalJsonStore` pointed at the SAME file -- simulates a real
    # backend process restart, never reusing the first instance's
    # in-memory dicts.
    second_repo = ItineraryLineageRepository(LocalJsonStore(store_path))

    reloaded_branch = second_repo.get_branch(branch.branch_id)
    assert reloaded_branch is not None
    assert reloaded_branch.head_revision_id == revision.revision_id
    assert reloaded_branch.is_default is True

    reloaded_revision = second_repo.get_revision(revision.revision_id)
    assert reloaded_revision is not None
    assert reloaded_revision.version_label == "v1"
    assert reloaded_revision.snapshot_available is True
    assert reloaded_revision.snapshot == {
        "trip_id": "trip_restart",
        "metadata": {"current_version": "v1"},
    }


def test_lineage_collections_do_not_clobber_other_collections_in_the_same_file(
    tmp_path: Path,
) -> None:
    """`LocalJsonStore.write_collection` only ever replaces the ONE
    named collection it's given -- writing branches/revisions must never
    wipe out jobs/trips/planning_states/users already in the same file."""
    from app.models.generation_job import GenerationJobType, create_queued_job
    from app.repositories.job_repository import JobRepository

    store = LocalJsonStore(tmp_path / "state.json")
    job_repo = JobRepository(store)
    job_repo.create(
        create_queued_job(trip_id="trip_shared", owner_id="user_shared", job_type=GenerationJobType.GENERATE)
    )

    lineage_repo = ItineraryLineageRepository(store)
    lineage_repo.create_branch(ItineraryBranch(trip_id="trip_shared", is_default=True))

    # Re-load both from a fresh instance pair to confirm both survived.
    reloaded_job_repo = JobRepository(store)
    reloaded_lineage_repo = ItineraryLineageRepository(store)
    assert len(reloaded_job_repo.list_by_trip_id("trip_shared")) == 1
    assert len(reloaded_lineage_repo.list_branches_for_trip("trip_shared")) == 1
