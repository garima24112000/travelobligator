from __future__ import annotations

from fastapi.testclient import TestClient

# Section 199B Task 31: audits `POST /trips/{trip_id}/generate`'s
# behavior once a trip already has branches. `generate_full_plan`'s own
# `VersioningService.create_initial_version` call is pre-existing and
# idempotent (a no-op once `version_history` is non-empty, regardless of
# Section 199B) -- re-running `/generate` re-executes the whole pipeline
# against the CURRENTLY ACTIVE branch's live working copy but never
# creates a second "v1" label, so `RevisionLineageService.record_
# current_revision`'s own idempotency (Task 29) correctly treats it as
# "already recorded" and does not capture a second snapshot for the
# same label. This is `/generate`'s pre-existing "initial-plan-only,
# idempotent" meaning (unchanged by 199B, per this task's own "preserve
# that meaning" instruction) -- documented here, not newly introduced.
#
# The one invariant 199B genuinely must prove is Task 31's other half:
# `/generate` never resets/touches any OTHER branch's already-recorded,
# immutable revisions.


def _main_branch_id(client: TestClient, trip_id: str) -> str:
    return client.get(f"/trips/{trip_id}/branches").json()["data"]["active_branch_id"]


def test_regenerate_call_never_touches_other_branches(
    client: TestClient, generated_trip_id: str
) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    main_head_before = client.get(f"/trips/{generated_trip_id}/branches").json()["data"][
        "branches"
    ]
    main_head_before = next(b for b in main_head_before if b["branch_id"] == main_branch_id)[
        "head_revision_id"
    ]

    fork_response = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={"source_revision_id": main_head_before, "display_name": "Untouched Fork"},
    )
    assert fork_response.status_code == 201
    fork_branch_id = fork_response.json()["data"]["branch"]["branch_id"]
    fork_head_before = fork_response.json()["data"]["head_revision_id"]

    # Calling /generate again (still on Main, the active branch) must
    # never affect the fork branch created above at all.
    second_generate = client.post(f"/trips/{generated_trip_id}/generate")
    assert second_generate.status_code == 200

    branches_after = client.get(f"/trips/{generated_trip_id}/branches").json()["data"]["branches"]
    fork_after = next(b for b in branches_after if b["branch_id"] == fork_branch_id)
    assert fork_after["head_revision_id"] == fork_head_before
    assert fork_after["base_revision_id"] == main_head_before

    # Re-fetching the fork's own revision list is unaffected too.
    revisions_response = client.get(
        f"/trips/{generated_trip_id}/branches/{fork_branch_id}/revisions"
    )
    assert revisions_response.status_code == 200
    assert revisions_response.json()["data"]["revisions"] == []
