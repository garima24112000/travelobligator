from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_targeted_regeneration_application_service import (  # type: ignore[import-not-found]
    _completed_execution,
    _completed_interpretation,
    _FakeExecutor,
    _FakeInterpreter,
    _FakePlanBuilder,
    _pending_event,
    _planning_state,
    _ready_removal_plan,
)

from app.core.config import get_settings
from app.db.session import get_engine, get_session_factory
from app.main import app
from app.models.generation_job import GenerationJobStatus, GenerationJobType, create_queued_job
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.models.targeted_regeneration_runtime import TargetedRegenerationRuntimeStatus
from app.models.user import UserRecord
from app.repositories.errors import BranchHeadConflictError, ConcurrentStateUpdateError
from app.repositories.factory import get_job_repository, get_lineage_repository, get_planning_state_repository
from app.repositories.postgres_itinerary_lineage_repository import PostgresItineraryLineageRepository
from app.repositories.postgres_planning_state_repository import PostgresPlanningStateRepository
from app.repositories.postgres_trip_repository import PostgresTripRepository
from app.repositories.postgres_user_repository import PostgresUserRepository
from app.repositories.unit_of_work import run_atomic
from app.services import generation_job_service as job_service
from app.services.feedback_service import FeedbackService
from app.services.itinerary_fork_service import ForkCreationStatus, itinerary_fork_service
from app.services.planning_orchestrator import planning_orchestrator
from app.services.revision_lineage_service import BranchActivationStatus, revision_lineage_service
from app.services.targeted_regeneration_application_service import TargetedRegenerationApplicationService
from app.services.versioning_service import versioning_service
from app.tests.conftest import create_trip_payload

# Section 200C: REAL-PostgreSQL verification of every atomic operation + the multi-instance job
# behaviour. Gated: TRAVELOB_RUN_POSTGRES_TESTS=1 + an alembic-upgraded DATABASE_URL.

pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.skipif(
        os.environ.get("TRAVELOB_RUN_POSTGRES_TESTS") != "1" or not os.environ.get("DATABASE_URL"),
        reason="Optional live-Postgres test: set TRAVELOB_RUN_POSTGRES_TESTS=1 and a real DATABASE_URL.",
    ),
]

_BACKEND_DIR = Path(__file__).resolve().parents[3]


class _Fault(Exception):
    """Injected fault."""


def _race(worker: Callable[[int], object], workers: int) -> list[object]:
    barrier = threading.Barrier(workers)

    def run(i: int) -> object:
        barrier.wait(timeout=20)
        try:
            return worker(i)
        except Exception as exc:  # noqa: BLE001 - the exception IS the observed outcome
            return exc

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, range(workers)))


def _seed_owner() -> str:
    now = datetime.now(timezone.utc)
    owner_id = f"user_200c_{uuid.uuid4().hex}"
    PostgresUserRepository(session_factory=get_session_factory()).create_user(
        UserRecord(user_id=owner_id, email=f"{owner_id}@example.com", password_hash="x", created_at=now, updated_at=now)
    )
    return owner_id


def _seed_trip_with_lineage(*, pending: int = 0) -> tuple[str, str, Any]:
    """A real trip whose state is at v1 with its default branch + v1 revision, all in PostgreSQL."""
    owner_id = _seed_owner()
    trip_id = f"trip_200c_{uuid.uuid4().hex}"
    PostgresTripRepository(session_factory=get_session_factory()).create(trip_id, owner_id=owner_id)
    state = _planning_state(trip_id)
    now = datetime.now(timezone.utc)
    events = [_pending_event(f"Feedback {n}") for n in range(pending)]
    for n, event in enumerate(events):
        event.created_at = now + timedelta(minutes=n)
    state.feedback_history = events
    FeedbackService().recompute_pending_feedback_summary(state)
    PostgresPlanningStateRepository().save(state)
    loaded = get_planning_state_repository().get_by_trip_id(trip_id)
    r1 = revision_lineage_service.record_current_revision(loaded)
    assert r1 is not None
    return trip_id, owner_id, r1


def _row(trip_id: str) -> dict[str, Any]:
    with get_session_factory()() as session:
        row = session.execute(
            text("select lock_version, current_version from planning_states where trip_id=:t"), {"t": trip_id}
        ).one()
        return {"lock_version": row[0], "version": row[1]}


def _revision_labels(trip_id: str) -> list[str]:
    with get_session_factory()() as session:
        return [
            r[0]
            for r in session.execute(
                text("select version_label from itinerary_revisions where trip_id=:t order by created_at, version_label"),
                {"t": trip_id},
            )
        ]


def _next_version(state: PlanningState) -> PlanningState:
    return versioning_service.create_version_after_feedback(
        state, feedback_event_id="fb_1", changed_sections=["experience_plan"], preserved_sections=[], summary="s"
    )


# -- atomic revision commit (Tasks 6 / 31) -------------------------------------------------------------------------


def test_commit_writes_state_revision_and_head_in_one_transaction() -> None:
    trip_id, _, r1 = _seed_trip_with_lineage()
    state = _next_version(get_planning_state_repository().get_by_trip_id(trip_id))

    revision = revision_lineage_service.commit_state_with_revision(state)

    assert revision is not None and revision.parent_revision_id == r1.revision_id
    assert _row(trip_id) == {"lock_version": 1, "version": "v2"}
    assert _revision_labels(trip_id) == ["v1", "v2"]
    branch = get_lineage_repository().get_default_branch(trip_id)
    assert branch.head_revision_id == revision.revision_id
    assert revision_lineage_service.check_branch_head_consistency(get_planning_state_repository().get_by_trip_id(trip_id))


@pytest.mark.parametrize(
    "target,method",
    [
        (PostgresItineraryLineageRepository, "create_revision"),  # after the state UPDATE, before the revision
        (PostgresItineraryLineageRepository, "update_branch_head"),  # after the revision, before the head
    ],
)
def test_a_fault_at_any_write_point_rolls_the_whole_transition_back(
    target: type, method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id, _, r1 = _seed_trip_with_lineage()
    before = _row(trip_id)
    state = _next_version(get_planning_state_repository().get_by_trip_id(trip_id))
    token_before = state._lock_version
    monkeypatch.setattr(target, method, lambda *a, **k: (_ for _ in ()).throw(_Fault()))

    with pytest.raises(_Fault):
        revision_lineage_service.commit_state_with_revision(state)

    monkeypatch.undo()
    assert _row(trip_id) == before  # no state advanced without its revision ...
    assert _revision_labels(trip_id) == ["v1"]  # ... and no revision without its state
    assert get_lineage_repository().get_default_branch(trip_id).head_revision_id == r1.revision_id
    assert state._lock_version == token_before  # the in-memory token was restored too, so a retry works
    assert revision_lineage_service.commit_state_with_revision(state) is not None
    assert _row(trip_id)["version"] == "v2"


def test_a_stale_state_writer_leaves_no_revision_and_no_head_change() -> None:
    trip_id, _, r1 = _seed_trip_with_lineage()
    stale = _next_version(get_planning_state_repository().get_by_trip_id(trip_id))
    winner = _next_version(get_planning_state_repository().get_by_trip_id(trip_id))
    assert revision_lineage_service.commit_state_with_revision(winner) is not None

    with pytest.raises(ConcurrentStateUpdateError):
        revision_lineage_service.commit_state_with_revision(stale)

    assert _row(trip_id)["lock_version"] == 1 and _revision_labels(trip_id) == ["v1", "v2"]


def test_a_stale_branch_head_inside_the_transaction_rolls_back_the_state_and_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip_id, _, r1 = _seed_trip_with_lineage()
    state = _next_version(get_planning_state_repository().get_by_trip_id(trip_id))
    real = PostgresItineraryLineageRepository.create_revision

    def create_then_a_competitor_moves_the_head(self, revision):
        created = real(self, revision)
        with get_session_factory()() as other:  # a different connection commits a head change
            other.execute(
                text("update itinerary_branches set head_revision_id='rev_competitor' where branch_id=:b"),
                {"b": revision.branch_id},
            )
            other.commit()
        return created

    monkeypatch.setattr(PostgresItineraryLineageRepository, "create_revision", create_then_a_competitor_moves_the_head)
    with pytest.raises(BranchHeadConflictError):
        revision_lineage_service.commit_state_with_revision(state)
    monkeypatch.undo()

    assert _row(trip_id)["version"] == "v1" and _revision_labels(trip_id) == ["v1"]


def test_replaying_a_completed_commit_creates_no_second_revision() -> None:
    trip_id, _, _ = _seed_trip_with_lineage()
    state = _next_version(get_planning_state_repository().get_by_trip_id(trip_id))
    first = revision_lineage_service.commit_state_with_revision(state)
    stale_replay = _next_version(get_planning_state_repository().get_by_trip_id(trip_id))
    stale_replay.metadata.current_version = "v2"  # the SAME version again
    with pytest.raises(ConcurrentStateUpdateError):  # a replay from an old read is refused outright
        replay = _next_version(PostgresPlanningStateRepository().get_by_trip_id(trip_id))
        replay._lock_version = 0
        revision_lineage_service.commit_state_with_revision(replay)
    # and even the DB-level idempotent revision insert converges on one row
    again = PostgresItineraryLineageRepository().create_revision(first.model_copy(update={"revision_id": "rev_dup"}))
    assert again.revision_id == first.revision_id and _revision_labels(trip_id) == ["v1", "v2"]


# -- feedback / regeneration concurrency (Tasks 7 / 20) --------------------------------------------------------


def _service(revision_service: Any = None, source_version: str = "v1") -> TargetedRegenerationApplicationService:
    kwargs: dict[str, Any] = {}
    if revision_service is not None:
        kwargs["revision_lineage_service"] = revision_service
    return TargetedRegenerationApplicationService(
        interpreter_service=_FakeInterpreter(_completed_interpretation()),
        plan_builder=_FakePlanBuilder(_ready_removal_plan(source_version)),
        executor=_FakeExecutor(builder=_completed_execution),
        **kwargs,
    )


class _RacingLineage:
    """Wraps the real lineage service: just before THIS regeneration commits, a competing worker
    commits the same feedback first -- the exact stale-worker window."""

    def __init__(self, competitor: Callable[[], object]) -> None:
        self._competitor = competitor

    def __getattr__(self, name: str) -> Any:
        return getattr(revision_lineage_service, name)

    def commit_state_with_revision(self, *args: Any, **kwargs: Any) -> Any:
        self._competitor()
        return revision_lineage_service.commit_state_with_revision(*args, **kwargs)


def test_C_two_workers_cannot_both_consume_the_same_pending_feedback() -> None:
    trip_id, _, _ = _seed_trip_with_lineage(pending=2)
    winner_result: list[Any] = []

    def winner() -> None:
        winner_result.append(_service().regenerate(trip_id))

    stale_service = _service(_RacingLineage(winner))
    stale_result = stale_service.regenerate(trip_id)  # loaded A, then lost the race at commit

    assert winner_result[0].status == TargetedRegenerationRuntimeStatus.COMPLETED
    assert stale_result.status == TargetedRegenerationRuntimeStatus.CONFLICT  # detected, never silently applied twice
    state = get_planning_state_repository().get_by_trip_id(trip_id)
    applied = [e for e in state.feedback_history if e.handling_status == "applied"]
    assert len(applied) == 1 and applied[0].applied_in_version == "v2"  # A consumed exactly once
    assert state.metadata.current_version == "v2" and _revision_labels(trip_id) == ["v1", "v2"]
    assert state.pending_feedback_summary.queue_event_ids == [
        e.feedback_event_id for e in state.feedback_history if e.handling_status != "applied"
    ]
    # the loser reloads: the queue now starts at B, so A can never be consumed a second time
    pending_ids = [e.feedback_event_id for e in state.feedback_history if e.handling_status != "applied"]
    retry = _service(source_version="v2").regenerate(trip_id)
    assert retry.feedback_event_id == pending_ids[0] and retry.feedback_event_id != applied[0].feedback_event_id


def test_C_real_threads_racing_one_pending_feedback_apply_it_at_most_once() -> None:
    trip_id, _, _ = _seed_trip_with_lineage(pending=1)
    outcomes = _race(lambda i: _service().regenerate(trip_id), workers=2)

    completed = [o for o in outcomes if getattr(o, "status", None) == TargetedRegenerationRuntimeStatus.COMPLETED]
    assert len(completed) == 1, outcomes  # never two applications of the same feedback
    state = get_planning_state_repository().get_by_trip_id(trip_id)
    assert state.metadata.current_version == "v2" and _revision_labels(trip_id) == ["v1", "v2"]
    assert sum(1 for e in state.feedback_history if e.handling_status == "applied") == 1
    for other in outcomes:
        if other not in completed:  # the loser either got a conflict result or a detected stale write
            assert isinstance(other, ConcurrentStateUpdateError) or other.status != TargetedRegenerationRuntimeStatus.COMPLETED


def test_a_failed_regeneration_commit_leaves_feedback_pending_and_the_version_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip_id, _, r1 = _seed_trip_with_lineage(pending=1)
    before = _row(trip_id)
    monkeypatch.setattr(PostgresItineraryLineageRepository, "update_branch_head", lambda *a, **k: (_ for _ in ()).throw(_Fault()))

    result = _service().regenerate(trip_id)

    monkeypatch.undo()
    assert result.status == TargetedRegenerationRuntimeStatus.FAILED  # honest failure, no success claimed
    after = get_planning_state_repository().get_by_trip_id(trip_id)
    assert after.metadata.current_version == "v1" and _revision_labels(trip_id) == ["v1"]
    assert [e.handling_status for e in after.feedback_history] != ["applied"]
    assert get_lineage_repository().get_default_branch(trip_id).head_revision_id == r1.revision_id
    assert _row(trip_id)["version"] == before["version"]


# -- branch operations (Tasks 8 / 9 / 21) --------------------------------------------------------------------------------


def test_activation_is_all_or_nothing_when_the_state_write_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    trip_id, _, r1 = _seed_trip_with_lineage()
    fork = itinerary_fork_service.create_fork(trip_id, r1.revision_id, "Option B")
    before = _row(trip_id)
    monkeypatch.setattr(PostgresPlanningStateRepository, "save", lambda self, s: (_ for _ in ()).throw(_Fault()))

    with pytest.raises(_Fault):
        revision_lineage_service.activate_branch(trip_id, fork.branch.branch_id)
    monkeypatch.undo()

    live = get_planning_state_repository().get_by_trip_id(trip_id)
    assert _row(trip_id) == before and live.metadata.active_branch_id is None  # not "branch B + state Main"


def test_21_competing_activations_end_in_a_state_that_belongs_completely_to_one_branch() -> None:
    trip_id, _, r1 = _seed_trip_with_lineage()
    main_id = get_lineage_repository().get_default_branch(trip_id).branch_id
    fork_b = itinerary_fork_service.create_fork(trip_id, r1.revision_id, "B", activate_after_create=True)
    assert fork_b.activation.status == BranchActivationStatus.ACTIVATED
    v2_state = _next_version(get_planning_state_repository().get_by_trip_id(trip_id))
    r2 = revision_lineage_service.commit_state_with_revision(v2_state)  # branch B head = v2 (content differs from Main v1)
    fork_b2 = itinerary_fork_service.create_fork(trip_id, r2.revision_id, "B2")
    targets = {main_id: "v1", fork_b2.branch.branch_id: "v2"}
    ids = list(targets)

    outcomes = _race(lambda i: revision_lineage_service.activate_branch(trip_id, ids[i]), workers=2)

    for outcome in outcomes:
        assert not isinstance(outcome, Exception), outcome
        assert outcome.status in (BranchActivationStatus.ACTIVATED, BranchActivationStatus.BLOCKED_STATE_CONFLICT)
    live = get_planning_state_repository().get_by_trip_id(trip_id)
    assert live.metadata.active_branch_id in targets
    assert live.metadata.current_version == targets[live.metadata.active_branch_id]  # never mixed
    assert revision_lineage_service.check_branch_head_consistency(live)


def test_a_concurrent_same_name_fork_creates_exactly_one_branch() -> None:
    trip_id, _, r1 = _seed_trip_with_lineage()

    outcomes = _race(lambda i: itinerary_fork_service.create_fork(trip_id, r1.revision_id, "Option Z"), workers=4)

    statuses = sorted(o.status.value for o in outcomes if not isinstance(o, Exception))
    assert statuses.count("created") == 1 and statuses.count("name_conflict") == 3, outcomes
    with get_session_factory()() as s:
        assert s.execute(text("select count(*) from itinerary_branches where trip_id=:t"), {"t": trip_id}).scalar_one() == 2
    source = get_lineage_repository().get_revision(r1.revision_id)
    assert source.version_label == "v1" and source.snapshot_available  # the source revision stays untouched


# -- trip creation (Task 3) --------------------------------------------------------------------------------------------------


def _trip_request() -> TripRequest:
    return TripRequest(
        primary_destination="Porto, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-11",
        travelers_count=1,
        travel_group_type=TravelGroupType.SOLO,
    )


def test_trip_creation_writes_the_trip_row_and_initial_state_together() -> None:
    owner_id = _seed_owner()
    state = planning_orchestrator.create_trip(_trip_request(), owner_id=owner_id)
    assert get_planning_state_repository().get_by_trip_id(state.trip_id) is not None
    assert PostgresTripRepository(session_factory=get_session_factory()).get(state.trip_id).owner_id == owner_id
    assert state._lock_version == 0


def test_trip_creation_failure_after_the_first_write_leaves_no_orphan_trip_or_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = _seed_owner()
    with get_session_factory()() as s:
        trips_before = s.execute(text("select count(*) from trips where owner_id=:o"), {"o": owner_id}).scalar_one()
    monkeypatch.setattr(PostgresPlanningStateRepository, "save", lambda self, st: (_ for _ in ()).throw(_Fault()))

    with pytest.raises(_Fault):
        planning_orchestrator.create_trip(_trip_request(), owner_id=owner_id)
    monkeypatch.undo()

    with get_session_factory()() as s:
        assert s.execute(text("select count(*) from trips where owner_id=:o"), {"o": owner_id}).scalar_one() == trips_before == 0
        assert s.execute(text("select count(*) from planning_states ps left join trips t using (trip_id) where t.trip_id is null")).scalar_one() == 0


# -- transactions/pool hygiene (Tasks 25 / 35) -------------------------------------------------------------------


def test_a_failed_transaction_returns_its_connection_and_does_not_poison_later_ones() -> None:
    def boom(uow):
        uow.trips.create(f"trip_{uuid.uuid4().hex}", owner_id=None)
        raise _Fault()

    for _ in range(5):
        with pytest.raises(_Fault):
            run_atomic(boom)
    assert get_engine().pool.checkedout() == 0
    trip_id, _, _ = _seed_trip_with_lineage()  # the pool still works normally
    assert _row(trip_id)["version"] == "v1"


def test_the_commit_transaction_is_short() -> None:
    trip_id, _, _ = _seed_trip_with_lineage()
    durations: list[float] = []
    for _ in range(5):
        state = _next_version(get_planning_state_repository().get_by_trip_id(trip_id))
        started = time.perf_counter()
        revision_lineage_service.commit_state_with_revision(state)
        durations.append(time.perf_counter() - started)
    durations.sort()
    print(f"\ncommit_state_with_revision durations (s): min={durations[0]:.4f} median={durations[2]:.4f} max={durations[-1]:.4f}")
    assert durations[2] < 1.0  # a handful of statements, never a provider call


def test_lock_ordering_keeps_contending_transitions_deadlock_free() -> None:
    trip_id, _, _ = _seed_trip_with_lineage()
    states = [_next_version(get_planning_state_repository().get_by_trip_id(trip_id)) for _ in range(6)]

    outcomes = _race(lambda i: revision_lineage_service.commit_state_with_revision(states[i]), workers=6)

    assert sum(1 for o in outcomes if not isinstance(o, Exception)) == 1
    assert all(isinstance(o, ConcurrentStateUpdateError) for o in outcomes if isinstance(o, Exception)), outcomes
    assert _revision_labels(trip_id) == ["v1", "v2"]


# -- async job API concurrency (Task 12) -----------------------------------------------------------------------------------


@pytest.mark.parametrize("in_process_lock", [True, False], ids=["with-process-lock", "database-arbitrates"])
def test_12_two_concurrent_generate_requests_create_at_most_one_active_job(
    monkeypatch: pytest.MonkeyPatch, in_process_lock: bool
) -> None:
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    if not in_process_lock:  # remove the in-process guard: only PostgreSQL can arbitrate now
        monkeypatch.setattr(job_service, "_lock_for_trip", lambda trip_id: contextlib.nullcontext())
    monkeypatch.setattr(job_service, "check_no_duplicate_running_job", lambda *a, **k: None)  # force both past the pre-check

    def slow_generate(trip_id: str, *a: Any, **k: Any) -> PlanningState:
        time.sleep(1.5)
        state = PlanningState(trip_id=trip_id, trip_request=_trip_request())
        return state

    monkeypatch.setattr(job_service.planning_orchestrator, "generate_full_plan", slow_generate)
    monkeypatch.setattr(job_service.planning_orchestrator, "generate_full_plan_via_langgraph", slow_generate)

    first = TestClient(app)
    signup = first.post("/auth/signup", json={"email": f"test-200c-{uuid.uuid4().hex}@example.com", "password": "testpassword123"})
    assert signup.status_code == 201, signup.text
    trip_id = first.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    second = TestClient(app)
    second.cookies.update(first.cookies)
    clients = [first, second]

    outcomes = _race(lambda i: clients[i].post(f"/trips/{trip_id}/generate"), workers=2)

    codes = sorted(o.status_code for o in outcomes)
    assert codes == [202, 409], [getattr(o, "text", o) for o in outcomes]
    loser = next(o for o in outcomes if o.status_code == 409)
    assert loser.json()["errors"][0]["code"] == "JOB_ALREADY_RUNNING"
    with get_session_factory()() as s:
        assert s.execute(text("select count(*) from generation_jobs where trip_id=:t"), {"t": trip_id}).scalar_one() == 1
        assert s.execute(text("select status from generation_jobs where trip_id=:t"), {"t": trip_id}).scalar_one() == "succeeded"


# -- multi-process / restart (Task 32) -----------------------------------------------------------------------------------------


_PROCESS_A = """
import os, sys
from app.repositories.factory import get_job_repository
job = get_job_repository().claim(sys.argv[1], "inst-process-A", int(sys.argv[2]))
print("claimed" if job else "not-claimed", flush=True)
os._exit(1)  # crash: no cleanup, no terminal transition -- only the lease remains
"""

_PROCESS_B = """
from app.services import generation_job_service
print(generation_job_service.recover_interrupted_jobs(), flush=True)
"""


def _run_python(code: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=_BACKEND_DIR,
        env={**os.environ, "PYTHONPATH": str(_BACKEND_DIR), "PERSISTENCE_BACKEND": "postgres"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "postgres" not in result.stderr.lower() or "password" not in result.stderr.lower(), "credentials leaked"
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr[-300:]


def test_32_process_b_does_not_steal_a_live_lease_and_recovers_the_job_after_it_expires() -> None:
    owner_id = _seed_owner()
    trip_id = f"trip_200c_{uuid.uuid4().hex}"
    PostgresTripRepository(session_factory=get_session_factory()).create(trip_id, owner_id=owner_id)
    PostgresPlanningStateRepository().save(_planning_state(trip_id))  # state written by "this" process ...
    job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    get_job_repository().create(job)

    assert _run_python(_PROCESS_A, job.job_id, "4") == "claimed"  # process A claims, then crashes

    # ... and is read back by a real new process (state survives an actual restart)
    stored = get_planning_state_repository().get_by_trip_id(trip_id)
    assert stored is not None and stored._lock_version == 0

    _run_python(_PROCESS_B)  # process B starts while A's lease is still valid
    still = get_job_repository().get_by_job_id(job.job_id)
    assert still.status == GenerationJobStatus.RUNNING and still.lease_owner == "inst-process-A"
    assert get_job_repository().claim(job.job_id, "inst-process-B", 60) is None  # cannot be stolen

    time.sleep(4.5)  # process A is dead; its lease expires without a heartbeat
    _run_python(_PROCESS_B)  # process B restarts: only NOW is the abandoned job recovered
    closed = get_job_repository().get_by_job_id(job.job_id)
    assert closed.status == GenerationJobStatus.FAILED and closed.error_code == "JOB_INTERRUPTED"
    fresh = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.GENERATE)
    get_job_repository().create(fresh)  # and the trip is no longer blocked
