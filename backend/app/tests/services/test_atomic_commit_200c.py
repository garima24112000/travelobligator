from __future__ import annotations

import copy
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from test_revision_lineage_branch_activation import (  # type: ignore[import-not-found]
    _fork,
    _planning_state_and_save,
)

from app.models.itinerary_lineage import ItineraryBranch
from app.models.planning_state import PlanningState
from app.repositories import unit_of_work as uow_module
from app.repositories.errors import BranchHeadConflictError, ConcurrentStateUpdateError
from app.repositories.factory import (
    get_job_repository,
    get_lineage_repository,
    get_planning_state_repository,
    get_trip_repository,
)
from app.repositories.unit_of_work import UnitOfWork
from app.services import planning_orchestrator as orchestrator_module
from app.services import revision_lineage_service as lineage_module
from app.services.revision_lineage_service import BranchActivationStatus, revision_lineage_service
from app.services.versioning_service import versioning_service

# Section 200C: ordering + rollback semantics of every atomic operation, with a TRANSACTIONAL FAKE
# unit of work (it snapshots the in-memory repositories and restores them if the block raises).
# It proves the SERVICES only ever write inside the unit of work and that a fault at any point
# leaves the pre-operation state. The same faults are injected against a real PostgreSQL
# transaction in the gated suite (test_postgres_concurrency_200c_integration.py).


class _FaultyStop(Exception):
    """Injected fault."""


@pytest.fixture()
def tx(monkeypatch: pytest.MonkeyPatch):
    @contextmanager
    def transactional():
        states = get_planning_state_repository()
        lineage = get_lineage_repository()
        trips = get_trip_repository()
        snapshot = (
            copy.deepcopy(states._states),
            copy.deepcopy(lineage._branches),
            copy.deepcopy(lineage._revisions),
            copy.deepcopy(trips._trips) if hasattr(trips, "_trips") else None,
        )
        try:
            yield UnitOfWork(
                planning_states=states, trips=trips, lineage=lineage, jobs=get_job_repository(), session=object()
            )
        except BaseException:
            states._states, lineage._branches, lineage._revisions = snapshot[0], snapshot[1], snapshot[2]
            if snapshot[3] is not None:
                trips._trips = snapshot[3]
            raise

    postgres_mode = lambda: SimpleNamespace(persistence_backend="postgres")  # noqa: E731
    monkeypatch.setattr(uow_module, "unit_of_work", transactional)
    monkeypatch.setattr(lineage_module, "get_settings", postgres_mode)
    monkeypatch.setattr(orchestrator_module, "get_settings", postgres_mode)
    return transactional


def _next_version(state: PlanningState) -> PlanningState:
    """A NEW-version working copy. Local JSON hands out the stored object itself, so mutate a
    private deep copy -- exactly what a PostgreSQL load gives (a separate object per read)."""
    working = state.model_copy(deep=True)
    return versioning_service.create_version_after_feedback(
        working, feedback_event_id="fb_1", changed_sections=["experience_plan"], preserved_sections=[], summary="s"
    )


def _seed(trip_id: str):
    state = _planning_state_and_save(trip_id)
    first = revision_lineage_service.record_current_revision(state)
    assert first is not None
    return state, first


def _snapshot_of_persisted(trip_id: str) -> dict[str, Any]:
    return get_planning_state_repository().get_by_trip_id(trip_id).model_dump(mode="json")


# -- atomic state + revision + head ------------------------------------------------------------------


def test_successful_commit_writes_state_revision_and_head_together(tx) -> None:
    state, r1 = _seed("trip_atomic_ok")
    state = _next_version(state)

    revision = revision_lineage_service.commit_state_with_revision(state)

    assert revision is not None and revision.version_label == "v2" and revision.parent_revision_id == r1.revision_id
    persisted = get_planning_state_repository().get_by_trip_id("trip_atomic_ok")
    assert persisted.metadata.current_version == "v2"
    branch = get_lineage_repository().get_default_branch("trip_atomic_ok")
    assert branch.head_revision_id == revision.revision_id
    assert revision_lineage_service.check_branch_head_consistency(persisted)


def test_fault_after_the_state_write_but_before_the_revision_rolls_everything_back(
    tx, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, r1 = _seed("trip_atomic_f1")
    before = _snapshot_of_persisted("trip_atomic_f1")
    state = _next_version(state)
    lineage = get_lineage_repository()
    monkeypatch.setattr(lineage, "create_revision", lambda rev: (_ for _ in ()).throw(_FaultyStop()))

    with pytest.raises(_FaultyStop):
        revision_lineage_service.commit_state_with_revision(state)

    assert _snapshot_of_persisted("trip_atomic_f1") == before  # no state advanced without its revision
    assert get_lineage_repository().get_default_branch("trip_atomic_f1").head_revision_id == r1.revision_id
    assert [r.version_label for r in lineage.list_revisions_for_branch(r1.branch_id)] == ["v1"]


def test_fault_after_the_revision_but_before_the_head_update_rolls_back_the_revision_too(
    tx, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, r1 = _seed("trip_atomic_f2")
    before = _snapshot_of_persisted("trip_atomic_f2")
    state = _next_version(state)
    lineage = get_lineage_repository()
    monkeypatch.setattr(lineage, "update_branch_head", lambda *a, **k: (_ for _ in ()).throw(_FaultyStop()))

    with pytest.raises(_FaultyStop):
        revision_lineage_service.commit_state_with_revision(state)

    assert _snapshot_of_persisted("trip_atomic_f2") == before
    assert [r.version_label for r in lineage.list_revisions_for_branch(r1.branch_id)] == ["v1"]  # no orphan v2
    assert lineage.get_default_branch("trip_atomic_f2").head_revision_id == r1.revision_id


def test_a_stale_state_writer_is_rejected_before_any_revision_or_head_is_written(
    tx, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, r1 = _seed("trip_atomic_stale")
    state = _next_version(state)
    states = get_planning_state_repository()
    monkeypatch.setattr(states, "save", lambda s: (_ for _ in ()).throw(ConcurrentStateUpdateError(s.trip_id)))

    with pytest.raises(ConcurrentStateUpdateError):
        revision_lineage_service.commit_state_with_revision(state)

    lineage = get_lineage_repository()
    assert [r.version_label for r in lineage.list_revisions_for_branch(r1.branch_id)] == ["v1"]
    assert lineage.get_default_branch("trip_atomic_stale").head_revision_id == r1.revision_id


def test_a_stale_branch_head_writer_cannot_overwrite_the_newer_head(tx, monkeypatch: pytest.MonkeyPatch) -> None:
    state, r1 = _seed("trip_atomic_head")
    state = _next_version(state)
    lineage = get_lineage_repository()
    real_create = lineage.create_revision

    def create_then_someone_else_moves_the_head(revision):
        created = real_create(revision)
        lineage._branches[r1.branch_id] = lineage._branches[r1.branch_id].model_copy(
            update={"head_revision_id": "rev_from_a_concurrent_writer"}
        )
        return created

    monkeypatch.setattr(lineage, "create_revision", create_then_someone_else_moves_the_head)
    with pytest.raises(BranchHeadConflictError):
        revision_lineage_service.commit_state_with_revision(state)
    assert "v2" not in [r.version_label for r in lineage.list_revisions_for_branch(r1.branch_id)]


def test_replaying_the_same_completion_never_creates_a_second_revision(tx) -> None:
    state, r1 = _seed("trip_atomic_replay")
    state = _next_version(state)

    first = revision_lineage_service.commit_state_with_revision(state)
    second = revision_lineage_service.commit_state_with_revision(state)

    assert first is not None and second is not None and second.revision_id == first.revision_id
    labels = [r.version_label for r in get_lineage_repository().list_revisions_for_branch(r1.branch_id)]
    assert labels == ["v1", "v2"]


def test_local_json_keeps_the_best_effort_revision_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    # No `tx` fixture: the ordinary (Local JSON) backend. Production-grade atomicity needs PostgreSQL.
    state = _planning_state_and_save("trip_atomic_local")
    lineage = get_lineage_repository()
    monkeypatch.setattr(lineage, "create_revision", lambda rev: (_ for _ in ()).throw(_FaultyStop()))
    state = _next_version(state)

    assert revision_lineage_service.commit_state_with_revision(state) is None  # swallowed, as before

    assert get_planning_state_repository().get_by_trip_id("trip_atomic_local").metadata.current_version == "v2"


# -- atomic branch activation ---------------------------------------------------------------------------


def test_activation_writes_state_and_active_branch_together_or_not_at_all(
    tx, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, source = _seed("trip_atomic_act")
    branch_id = _fork("trip_atomic_act", source.revision_id)
    before = _snapshot_of_persisted("trip_atomic_act")
    states = get_planning_state_repository()
    monkeypatch.setattr(states, "save", lambda s: (_ for _ in ()).throw(_FaultyStop()))

    with pytest.raises(_FaultyStop):
        revision_lineage_service.activate_branch("trip_atomic_act", branch_id)

    monkeypatch.undo()
    after = _snapshot_of_persisted("trip_atomic_act")
    assert after == before and after["metadata"].get("active_branch_id") is None  # NOT "branch B + state Main"


def test_activation_succeeds_atomically_and_the_result_corresponds_to_exactly_one_branch(tx) -> None:
    state, source = _seed("trip_atomic_act_ok")
    branch_id = _fork("trip_atomic_act_ok", source.revision_id)

    result = revision_lineage_service.activate_branch("trip_atomic_act_ok", branch_id)

    assert result.status == BranchActivationStatus.ACTIVATED
    live = get_planning_state_repository().get_by_trip_id("trip_atomic_act_ok")
    assert live.metadata.active_branch_id == branch_id
    assert revision_lineage_service.check_branch_head_consistency(live)


def test_activation_snapshot_inherits_the_token_of_the_row_it_replaces(tx, monkeypatch: pytest.MonkeyPatch) -> None:
    state, source = _seed("trip_atomic_token")
    branch_id = _fork("trip_atomic_token", source.revision_id)
    states = get_planning_state_repository()
    live = states.get_by_trip_id("trip_atomic_token")
    live._lock_version = 41
    seen: list[Any] = []
    real_save = states.save
    monkeypatch.setattr(states, "save", lambda s: (seen.append(s._lock_version), real_save(s))[1])

    revision_lineage_service.activate_branch("trip_atomic_token", branch_id)

    assert seen == [41]  # the deserialized snapshot carries the loaded row's token, so the write is a CAS


def test_a_stale_activation_write_is_reported_as_a_conflict_not_raised(tx, monkeypatch: pytest.MonkeyPatch) -> None:
    state, source = _seed("trip_atomic_act_stale")
    branch_id = _fork("trip_atomic_act_stale", source.revision_id)
    states = get_planning_state_repository()
    monkeypatch.setattr(states, "save", lambda s: (_ for _ in ()).throw(ConcurrentStateUpdateError(s.trip_id)))

    result = revision_lineage_service.activate_branch("trip_atomic_act_stale", branch_id)

    assert result.status == BranchActivationStatus.BLOCKED_STATE_CONFLICT and not result.activated


# -- fork ---------------------------------------------------------------------------------------------------------


def test_a_fork_is_a_single_row_with_a_valid_base_and_head_and_the_source_stays_immutable() -> None:
    state, source = _seed("trip_atomic_fork")
    revision_before = get_lineage_repository().get_revision(source.revision_id).model_dump(mode="json")

    branch_id = _fork("trip_atomic_fork", source.revision_id)

    branch = get_lineage_repository().get_branch(branch_id)
    assert branch.base_revision_id == branch.head_revision_id == source.revision_id
    assert get_lineage_repository().get_revision(source.revision_id).model_dump(mode="json") == revision_before


def test_a_concurrent_same_name_fork_loses_with_name_conflict_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.services.itinerary_fork_service import ForkCreationStatus, itinerary_fork_service

    state, source = _seed("trip_atomic_fork_race")
    lineage = get_lineage_repository()
    monkeypatch.setattr(
        lineage, "create_branch", lambda b: (_ for _ in ()).throw(IntegrityError("stmt", {}, Exception("dup")))
    )
    result = itinerary_fork_service.create_fork("trip_atomic_fork_race", source.revision_id, "Option B")
    assert result.status == ForkCreationStatus.NAME_CONFLICT
    assert [b.display_name for b in lineage.list_branches_for_trip("trip_atomic_fork_race")] == ["Main"]


# -- atomic trip creation ---------------------------------------------------------------------------------------------


def test_trip_creation_leaves_no_orphan_trip_when_the_state_write_fails(tx, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.planning_state import TravelGroupType, TripRequest
    from app.services.planning_orchestrator import planning_orchestrator

    states = get_planning_state_repository()
    trips = get_trip_repository()
    trips_before = copy.deepcopy(trips._trips)
    monkeypatch.setattr(states, "save", lambda s: (_ for _ in ()).throw(_FaultyStop()))
    request = TripRequest(
        primary_destination="Porto, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-11",
        travelers_count=1,
        travel_group_type=TravelGroupType.SOLO,
    )

    with pytest.raises(_FaultyStop):
        planning_orchestrator.create_trip(request, owner_id="user_1")

    assert trips._trips == trips_before  # the trip row written first was rolled back with it


def test_trip_creation_writes_trip_and_state_inside_one_unit_of_work(tx, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.planning_state import TravelGroupType, TripRequest
    from app.services.planning_orchestrator import planning_orchestrator

    entered: list[str] = []
    real = uow_module.unit_of_work

    @contextmanager
    def spying():
        entered.append("open")
        with real() as uow:
            yield uow
        entered.append("closed")

    monkeypatch.setattr(uow_module, "unit_of_work", spying)
    state = planning_orchestrator.create_trip(
        TripRequest(
            primary_destination="Porto, Portugal",
            start_date="2026-10-10",
            end_date="2026-10-11",
            travelers_count=1,
            travel_group_type=TravelGroupType.SOLO,
        ),
        owner_id="user_1",
    )
    assert entered == ["open", "closed"]
    assert get_trip_repository().get(state.trip_id) is not None
    assert get_planning_state_repository().get_by_trip_id(state.trip_id) is not None


def test_branch_object_is_unused_import_guard() -> None:
    assert ItineraryBranch is not None
