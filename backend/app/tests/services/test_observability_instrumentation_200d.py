from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.core.metrics import registry
from app.models.generation_job import GenerationJobType, create_queued_job
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.repositories.errors import ConcurrentStateUpdateError
from app.repositories.factory import get_job_repository
from app.repositories.postgres_planning_state_repository import PostgresPlanningStateRepository
from app.repositories.unit_of_work import postgres_unit_of_work
from app.services import generation_job_service as svc
from app.storage.provider_cache_store import make_query_hash
from app.storage.redis_provider_cache_store import RedisProviderCacheStore

# Section 200D: the metrics/events wired into cache, transactions, jobs, providers and heartbeats.
# Every assertion uses DELTAS of the process registry, so tests never depend on global counts.

P = "travelobligator_"


def _v(name: str, **labels: str) -> float:
    return registry.value(P + name, labels)


# -- provider cache (200B outcomes, now measurable without log parsing) --------------------------------------------


class _Redis:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.fail: Exception | None = None

    def get(self, key: str):
        if self.fail:
            raise self.fail
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        if self.fail:
            raise self.fail
        self.data[key] = value.encode()
        return True

    def ping(self):
        return True


def test_cache_miss_hit_bypass_and_error_are_counted_per_provider() -> None:
    import redis

    client = _Redis()
    now = [100.0]
    store = RedisProviderCacheStore(client, key_prefix="t", clock=lambda: now[0], cooldown_seconds=15)
    digest = make_query_hash({"q": 1})
    base = {s: _v("provider_cache_total", provider="open_meteo", status=s) for s in ("MISS", "HIT", "ERROR", "BYPASS")}

    assert store.get("open_meteo", digest) is None  # first request: MISS
    store.set("open_meteo", digest, {"a": 1}, ttl_seconds=60)
    assert store.get("open_meteo", digest) is not None  # second request: HIT
    client.fail = redis.exceptions.ConnectionError("down")
    assert store.get("open_meteo", digest) is None  # outage: ERROR ...
    assert store.get("open_meteo", digest) is None  # ... then BYPASS during the cooldown

    for status, expected in (("MISS", 1), ("HIT", 1), ("ERROR", 1), ("BYPASS", 1)):
        assert _v("provider_cache_total", provider="open_meteo", status=status) - base[status] == expected, status


def test_cache_labels_are_provider_and_status_only() -> None:
    for labels in registry.label_sets(P + "provider_cache_total"):
        assert set(labels) == {"provider", "status"}
        assert len(labels["provider"]) < 64 and labels["status"] in {"HIT", "MISS", "BYPASS", "ERROR"}


# -- transactions (200C) --------------------------------------------------------------------------------------------------


class _Session:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self): ...
    def rollback(self): ...

    def execute(self, stmt):
        class R:
            def first(self_inner):
                return None  # stale writer: zero rows

        return R()


def test_commit_and_rollback_are_counted_and_a_business_conflict_is_a_conflict_not_an_error() -> None:
    c0, r0, d0 = _v("db_transactions_total", scope="unit_of_work", outcome="commit"), _v("db_transactions_total", scope="unit_of_work", outcome="rollback"), _v("db_transaction_duration_seconds", scope="unit_of_work")
    with postgres_unit_of_work(lambda: _Session()):
        pass
    with pytest.raises(RuntimeError):
        with postgres_unit_of_work(lambda: _Session()):
            raise RuntimeError("boom")
    with pytest.raises(ConcurrentStateUpdateError):
        with postgres_unit_of_work(lambda: _Session()):
            raise ConcurrentStateUpdateError("t")
    assert _v("db_transactions_total", scope="unit_of_work", outcome="commit") - c0 == 1
    assert _v("db_transactions_total", scope="unit_of_work", outcome="rollback") - r0 == 2
    assert _v("db_transaction_duration_seconds", scope="unit_of_work") - d0 == 3  # duration recorded for each


def test_an_optimistic_conflict_is_counted_by_kind() -> None:
    before = _v("db_concurrency_conflicts_total", kind="state")
    state = PlanningState(
        trip_request=TripRequest(primary_destination="X", start_date="2026-10-10", end_date="2026-10-11", travelers_count=1, travel_group_type=TravelGroupType.SOLO)
    )
    state._lock_version = 3
    with pytest.raises(ConcurrentStateUpdateError):
        PostgresPlanningStateRepository(session_factory=lambda: _Session()).save(state)
    assert _v("db_concurrency_conflicts_total", kind="state") - before == 1


class _Orig(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate


def test_retries_are_counted_per_reason_and_ordinary_errors_are_not_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import transactions

    monkeypatch.setattr(transactions.time, "sleep", lambda s: None)
    ser0, dead0 = _v("db_transaction_retries_total", reason="serialization_failure"), _v("db_transaction_retries_total", reason="deadlock")
    attempts = iter(["40001", "40P01", None])

    def op() -> str:
        code = next(attempts)
        if code:
            raise OperationalError("stmt", {}, _Orig(code))
        return "ok"

    assert transactions.run_with_transient_retry(op) == "ok"
    assert _v("db_transaction_retries_total", reason="serialization_failure") - ser0 == 1
    assert _v("db_transaction_retries_total", reason="deadlock") - dead0 == 1

    total = registry.total(P + "db_transaction_retries_total")
    with pytest.raises(ValueError):
        transactions.run_with_transient_retry(lambda: (_ for _ in ()).throw(ValueError("business")))
    assert registry.total(P + "db_transaction_retries_total") == total


# -- jobs ----------------------------------------------------------------------------------------------------------------


def _state(trip_id: str) -> PlanningState:
    return PlanningState(
        trip_id=trip_id,
        trip_request=TripRequest(primary_destination="X", start_date="2026-10-10", end_date="2026-10-11", travelers_count=1, travel_group_type=TravelGroupType.SOLO),
    )


@pytest.fixture()
def pipeline(monkeypatch: pytest.MonkeyPatch):
    def generate(trip_id: str, *a: Any, **k: Any) -> PlanningState:
        return _state(trip_id)

    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan", generate)
    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan_via_langgraph", generate)


def _jobs(job_type: str, event: str) -> float:
    return _v("jobs_total", job_type=job_type, event=event)


def test_job_created_claimed_and_succeeded_are_counted_and_the_running_gauge_returns_to_zero(pipeline: None) -> None:
    base = {e: _jobs("generate", e) for e in ("created", "claimed", "succeeded", "stale_owner_rejected")}
    job = create_queued_job(trip_id="trip_m1", owner_id="u", job_type=GenerationJobType.GENERATE)
    svc._create_job_or_conflict(job)
    svc.run_generate_job(job.job_id, lease_owner="inst-a")
    for event in ("created", "claimed", "succeeded"):
        assert _jobs("generate", event) - base[event] == 1, event
    assert _jobs("generate", "stale_owner_rejected") == base["stale_owner_rejected"]
    assert registry.value(P + "jobs_running_local") == 0


def test_claim_conflicts_and_stale_owner_rejections_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = get_job_repository()
    job = create_queued_job(trip_id="trip_m2", owner_id="u", job_type=GenerationJobType.GENERATE)
    repo.create(job)
    repo.claim(job.job_id, "inst-live", 120)
    conflict0 = _jobs("unknown", "claim_conflict")
    svc.run_generate_job(job.job_id, lease_owner="inst-b")  # a live instance owns it
    assert _jobs("unknown", "claim_conflict") - conflict0 == 1

    stale0 = _jobs("generate", "stale_owner_rejected")

    def takeover(trip_id: str, *a: Any, **k: Any) -> PlanningState:
        repo.claim(job.job_id, "inst-c", 120, now=datetime.now(timezone.utc) + timedelta(hours=1))
        return _state(trip_id)

    job2 = create_queued_job(trip_id="trip_m3", owner_id="u", job_type=GenerationJobType.GENERATE)
    repo.create(job2)
    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan", lambda t, *a, **k: (repo.claim(job2.job_id, "inst-c", 120, now=datetime.now(timezone.utc) + timedelta(hours=1)), _state(t))[1])
    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan_via_langgraph", lambda t, *a, **k: (repo.claim(job2.job_id, "inst-c", 120, now=datetime.now(timezone.utc) + timedelta(hours=1)), _state(t))[1])
    svc.run_generate_job(job2.job_id, lease_owner="inst-a")
    assert _jobs("generate", "stale_owner_rejected") - stale0 == 1


def test_a_failed_job_and_an_interrupted_recovery_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(trip_id: str, *a: Any, **k: Any):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan", boom)
    monkeypatch.setattr(svc.planning_orchestrator, "generate_full_plan_via_langgraph", boom)
    repo = get_job_repository()
    failed0, interrupted0 = _jobs("generate", "failed"), _jobs("generate", "interrupted")
    job = create_queued_job(trip_id="trip_m4", owner_id="u", job_type=GenerationJobType.GENERATE)
    repo.create(job)
    svc.run_generate_job(job.job_id, lease_owner="inst-a")
    assert _jobs("generate", "failed") - failed0 == 1

    stuck = create_queued_job(trip_id="trip_m5", owner_id="u", job_type=GenerationJobType.GENERATE)
    repo.create(stuck)
    repo.claim(stuck.job_id, "inst-dead", 120)
    assert svc.recover_interrupted_jobs() == 1  # Local JSON single-process recovery
    assert _jobs("generate", "interrupted") - interrupted0 == 1


def test_job_metric_labels_exclude_every_identifier() -> None:
    for labels in registry.label_sets(P + "jobs_total"):
        assert set(labels) == {"job_type", "event"}


def test_job_log_lines_carry_the_event_taxonomy() -> None:
    job = create_queued_job(trip_id="trip_e", owner_id="u", job_type=GenerationJobType.GENERATE)
    assert svc._job_log_fields(job)["event"] == "job.created"
    from app.models.generation_job import GenerationJobStatus

    job.status = GenerationJobStatus.RUNNING
    assert svc._job_log_fields(job)["event"] == "job.claimed"
    assert svc._job_log_fields(job, status="succeeded")["event"] == "job.succeeded"
    assert svc._job_log_fields(job, status="failed", error_code="JOB_INTERRUPTED")["event"] == "job.interrupted"
    assert svc._job_log_fields(job, status="failed", error_code="STAGE_FAILED")["event"] == "job.failed"


# -- providers ---------------------------------------------------------------------------------------------------------------


def test_provider_calls_are_counted_with_provider_stage_and_outcome_only() -> None:
    from app.providers.gateway import _log_provider_call

    base = _v("provider_calls_total", provider="osrm", stage="routing", outcome="failed")
    hist = _v("provider_call_duration_seconds", provider="osrm", stage="routing")
    _log_provider_call(provider="osrm", stage="routing", status="failed", duration_ms=40)
    _log_provider_call(provider="osrm", stage="routing", status="totally_new_status", duration_ms=1)
    assert _v("provider_calls_total", provider="osrm", stage="routing", outcome="failed") - base == 1
    assert _v("provider_calls_total", provider="osrm", stage="routing", outcome="other") >= 1  # bounded outcome set
    assert _v("provider_call_duration_seconds", provider="osrm", stage="routing") - hist == 2
    for labels in registry.label_sets(P + "provider_calls_total"):
        assert set(labels) == {"provider", "stage", "outcome"}


def test_normalised_provider_responses_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.common import DataStatus, ProviderStatus
    from app.models.providers import ProviderResponse, ProviderType
    from app.providers.gateway import ProviderGateway

    response = ProviderResponse(
        provider_name="open_meteo", provider_type=ProviderType.WEATHER, status=ProviderStatus.SUCCESS, data_status=DataStatus.LIVE
    )
    before = _v("provider_calls_total", provider="open_meteo", stage="weather", outcome="success")
    ProviderGateway.to_status_entry(response)
    assert _v("provider_calls_total", provider="open_meteo", stage="weather", outcome="success") - before == 1


# -- heartbeat lifecycle (Task 31) ---------------------------------------------------------------------------------------


class _Repo:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.beats = 0

    def heartbeat(self, job_id: str, owner: str, lease: int) -> bool:
        self.beats += 1
        outcome = self.outcomes.pop(0) if self.outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture()
def fast_leases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_uses_database_leases", lambda: True)
    monkeypatch.setattr(svc, "_heartbeat_interval_seconds", lambda lease: 0.01)


def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _heartbeat_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("job-heartbeat-")]


def test_heartbeat_thread_exits_when_the_job_body_finishes_and_leaks_no_thread(fast_leases: None) -> None:
    repo = _Repo([])
    with svc._LeaseHeartbeat(repo, "job_a", "inst", 30):
        assert _wait(lambda: repo.beats >= 2) and svc.active_heartbeat_count() == 1
    assert svc.active_heartbeat_count() == 0
    assert _wait(lambda: not [t for t in _heartbeat_threads() if "job_a" in t.name])
    beats = repo.beats
    time.sleep(0.1)
    assert repo.beats == beats  # no further beats after completion


def test_heartbeat_thread_exits_on_ownership_loss_and_counts_the_failure(fast_leases: None) -> None:
    repo = _Repo([True, False])
    failures0 = _jobs("unknown", "heartbeat_failed")
    heartbeat = svc._LeaseHeartbeat(repo, "job_b", "inst", 30)
    with heartbeat:
        assert _wait(lambda: heartbeat.lost)
        assert _wait(lambda: svc.active_heartbeat_count() == 0)  # the thread exited by itself
    assert repo.beats == 2 and _jobs("unknown", "heartbeat_failed") - failures0 == 1


def test_an_exception_inside_the_heartbeat_does_not_kill_the_thread_or_the_process(fast_leases: None) -> None:
    repo = _Repo([RuntimeError("db blip"), RuntimeError("db blip"), True])
    heartbeat = svc._LeaseHeartbeat(repo, "job_c", "inst", 30)
    with heartbeat:
        assert _wait(lambda: repo.beats >= 4)  # it kept going after the errors
        assert not heartbeat.lost
    assert svc.active_heartbeat_count() == 0


def test_heartbeat_threads_are_daemons_so_they_cannot_block_process_exit(fast_leases: None) -> None:
    repo = _Repo([])
    with svc._LeaseHeartbeat(repo, "job_d", "inst", 30):
        assert _wait(lambda: any(t.name == "job-heartbeat-job_d" for t in threading.enumerate()))
        thread = next(t for t in threading.enumerate() if t.name == "job-heartbeat-job_d")
        assert thread.daemon is True


def test_shutdown_hook_stops_every_running_heartbeat_quickly(fast_leases: None) -> None:
    repos = [_Repo([]) for _ in range(3)]
    beats = [svc._LeaseHeartbeat(r, f"job_s{i}", "inst", 30) for i, r in enumerate(repos)]
    for b in beats:
        b.__enter__()
    try:
        assert svc.active_heartbeat_count() == 3
        started = time.monotonic()
        assert svc.stop_all_heartbeats(1.0) == 3
        assert time.monotonic() - started < 1.5
        assert svc.active_heartbeat_count() == 0
        assert _wait(lambda: not _heartbeat_threads())
    finally:
        for b in beats:
            b.stop()


def test_a_finished_job_run_leaves_no_heartbeat_behind(pipeline: None, fast_leases: None) -> None:
    job = create_queued_job(trip_id="trip_hb", owner_id="u", job_type=GenerationJobType.GENERATE)
    get_job_repository().create(job)
    svc.run_generate_job(job.job_id, lease_owner="inst-a")
    assert svc.active_heartbeat_count() == 0
    assert _wait(lambda: not _heartbeat_threads())
