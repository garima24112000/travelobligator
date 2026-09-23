from __future__ import annotations

from pathlib import Path

from app.models.itinerary_lineage import ItineraryBranch, ItineraryRevision
from app.repositories.itinerary_lineage_repository import ItineraryLineageRepository
from app.storage.local_json_store import LocalJsonStore

# Section 199B Task 45: local persistence restart for a forked trip --
# create a fork, advance it (a second revision), recreate the repository
# against the same file, and assert both branches, their independent
# heads, and every snapshot all reload correctly.


def test_fork_and_alternate_branch_regeneration_survive_a_repository_restart(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "state.json"
    first_repo = ItineraryLineageRepository(LocalJsonStore(store_path))

    main = first_repo.create_branch(
        ItineraryBranch(trip_id="trip_restart_fork", is_default=True)
    )
    r1 = first_repo.create_revision(
        ItineraryRevision(
            trip_id="trip_restart_fork",
            branch_id=main.branch_id,
            version_label="v1",
            created_by="system_generation",
            snapshot_available=True,
            snapshot={"trip_id": "trip_restart_fork", "metadata": {"current_version": "v1"}},
        )
    )
    first_repo.update_branch_head(main.branch_id, r1.revision_id)

    alternate = first_repo.create_branch(
        ItineraryBranch(
            trip_id="trip_restart_fork",
            display_name="Alternate",
            base_revision_id=r1.revision_id,
            head_revision_id=r1.revision_id,
        )
    )
    r2_alt = first_repo.create_revision(
        ItineraryRevision(
            trip_id="trip_restart_fork",
            branch_id=alternate.branch_id,
            parent_revision_id=r1.revision_id,
            version_label="v2",
            created_by="user_feedback",
            snapshot_available=True,
            snapshot={"trip_id": "trip_restart_fork", "metadata": {"current_version": "v2"}},
        )
    )
    first_repo.update_branch_head(alternate.branch_id, r2_alt.revision_id)

    # A brand new repository instance, backed by a brand new
    # `LocalJsonStore` pointed at the SAME file -- simulates a real
    # backend process restart.
    second_repo = ItineraryLineageRepository(LocalJsonStore(store_path))

    reloaded_branches = second_repo.list_branches_for_trip("trip_restart_fork")
    assert {b.branch_id for b in reloaded_branches} == {main.branch_id, alternate.branch_id}

    reloaded_main = second_repo.get_branch(main.branch_id)
    reloaded_alternate = second_repo.get_branch(alternate.branch_id)
    assert reloaded_main is not None and reloaded_alternate is not None
    assert reloaded_main.head_revision_id == r1.revision_id
    assert reloaded_alternate.head_revision_id == r2_alt.revision_id
    assert reloaded_alternate.base_revision_id == r1.revision_id

    reloaded_r2_alt = second_repo.get_revision(r2_alt.revision_id)
    assert reloaded_r2_alt is not None
    assert reloaded_r2_alt.snapshot == {
        "trip_id": "trip_restart_fork",
        "metadata": {"current_version": "v2"},
    }
    assert reloaded_r2_alt.parent_revision_id == r1.revision_id

    # Main's own lineage (just R1) is unaffected/undivided by Alternate's.
    assert [r.revision_id for r in second_repo.list_revisions_for_branch(main.branch_id)] == [
        r1.revision_id
    ]
    assert [r.revision_id for r in second_repo.list_revisions_for_branch(alternate.branch_id)] == [
        r2_alt.revision_id
    ]
