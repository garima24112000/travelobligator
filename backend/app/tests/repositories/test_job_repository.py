from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models.generation_job import (
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
    mark_job_failed,
    mark_job_running,
    mark_job_succeeded,
)
from app.repositories.job_repository import JobRepository
from app.storage.local_json_store import LocalJsonStore

# Tests for the local_json job repository foundation (Step 186B,
# docs/14_backend_architecture.md section 116). No route, background
# task, or Redis/live database is involved anywhere in this file -- every
# test here uses only a temporary local JSON file (tmp_path), matching
# app.tests.repositories.test_local_json_store.py's own pattern.


def _job(**overrides: object) -> GenerationJob:
    defaults: dict[str, object] = {
        "trip_id": "trip_1",
        "owner_id": "user_1",
        "job_type": GenerationJobType.GENERATE,
    }
    defaults.update(overrides)
    return GenerationJob(**defaults)  # type: ignore[arg-type]


def test_create_then_get_by_job_id(tmp_path: Path) -> None:
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))
    job = _job()

    repo.create(job)

    assert repo.get_by_job_id(job.job_id) == job


def test_get_by_job_id_returns_none_for_unknown_job(tmp_path: Path) -> None:
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))
    assert repo.get_by_job_id("job_does_not_exist") is None


def test_save_replaces_by_job_id(tmp_path: Path) -> None:
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))
    job = _job()
    repo.create(job)

    mark_job_running(job, progress_stage="traveler_profile")
    repo.save(job)

    reloaded = repo.get_by_job_id(job.job_id)
    assert reloaded is not None
    assert reloaded.status == GenerationJobStatus.RUNNING
    assert reloaded.progress_stage == "traveler_profile"


def test_list_by_trip_id_filters_correctly(tmp_path: Path) -> None:
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))
    trip_one_job = _job(trip_id="trip_1")
    trip_two_job = _job(trip_id="trip_2")
    repo.create(trip_one_job)
    repo.create(trip_two_job)

    assert repo.list_by_trip_id("trip_1") == [trip_one_job]
    assert repo.list_by_trip_id("trip_2") == [trip_two_job]
    assert repo.list_by_trip_id("trip_does_not_exist") == []


def test_list_by_trip_id_and_status_filters_by_status(tmp_path: Path) -> None:
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))
    queued_job = _job()
    succeeded_job = _job()
    mark_job_running(succeeded_job)
    mark_job_succeeded(succeeded_job)
    repo.create(queued_job)
    repo.create(succeeded_job)

    result = repo.list_by_trip_id_and_status("trip_1", {"succeeded"})

    assert result == [succeeded_job]


def test_list_running_by_trip_id_returns_only_queued_and_running(tmp_path: Path) -> None:
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))

    queued_job = _job()
    running_job = _job()
    mark_job_running(running_job)
    succeeded_job = _job()
    mark_job_running(succeeded_job)
    mark_job_succeeded(succeeded_job)
    failed_job = _job()
    mark_job_running(failed_job)
    mark_job_failed(failed_job, error_code="STAGE_FAILED", error_message="It failed.")

    for job in (queued_job, running_job, succeeded_job, failed_job):
        repo.create(job)

    running = repo.list_running_by_trip_id("trip_1")

    assert {job.job_id for job in running} == {queued_job.job_id, running_job.job_id}


def test_list_running_by_trip_id_is_empty_when_nothing_is_running(tmp_path: Path) -> None:
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))
    succeeded_job = _job()
    mark_job_running(succeeded_job)
    mark_job_succeeded(succeeded_job)
    repo.create(succeeded_job)

    assert repo.list_running_by_trip_id("trip_1") == []


# ---------------------------------------------------------------------------
# list_non_terminal (Step 186E, docs/14_backend_architecture.md section 118)
# -- app-startup recovery's only caller, since it must scan every trip's
# jobs at once, not one trip at a time.
# ---------------------------------------------------------------------------


def test_list_non_terminal_returns_queued_and_running_across_every_trip(
    tmp_path: Path,
) -> None:
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))

    queued_job = _job(trip_id="trip_1")
    running_job = _job(trip_id="trip_2")
    mark_job_running(running_job)
    succeeded_job = _job(trip_id="trip_3")
    mark_job_running(succeeded_job)
    mark_job_succeeded(succeeded_job)
    failed_job = _job(trip_id="trip_4")
    mark_job_running(failed_job)
    mark_job_failed(failed_job, error_code="STAGE_FAILED", error_message="It failed.")

    for job in (queued_job, running_job, succeeded_job, failed_job):
        repo.create(job)

    non_terminal = repo.list_non_terminal()

    assert {job.job_id for job in non_terminal} == {queued_job.job_id, running_job.job_id}


def test_list_non_terminal_is_empty_when_nothing_is_queued_or_running(
    tmp_path: Path,
) -> None:
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))
    succeeded_job = _job()
    mark_job_running(succeeded_job)
    mark_job_succeeded(succeeded_job)
    repo.create(succeeded_job)

    assert repo.list_non_terminal() == []


def test_list_non_terminal_is_empty_on_a_fresh_store(tmp_path: Path) -> None:
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))
    assert repo.list_non_terminal() == []


def test_jobs_collection_does_not_clobber_trips_planning_states_or_users(
    tmp_path: Path,
) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    store.write_collection("trips", {"trip_1": {"trip_id": "trip_1"}})
    store.write_collection("planning_states", {"trip_1": {"trip_id": "trip_1"}})
    store.write_collection("users", {"user_1": {"user_id": "user_1"}})

    repo = JobRepository(store)
    repo.create(_job())

    assert store.read_collection("trips") == {"trip_1": {"trip_id": "trip_1"}}
    assert store.read_collection("planning_states") == {"trip_1": {"trip_id": "trip_1"}}
    assert store.read_collection("users") == {"user_1": {"user_id": "user_1"}}
    assert len(store.read_collection("jobs")) == 1


def test_list_by_trip_id_is_ordered_by_created_at_ascending(tmp_path: Path) -> None:
    """Documents the deterministic ordering choice this repository makes:
    oldest first (stored/chronological order), matching this codebase's
    existing audit-trail convention (e.g.
    `PlanningState.regeneration_attempts`/`version_history`, both read
    back in append order) rather than newest-first."""
    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))
    now = datetime.now(timezone.utc)

    newer_job = _job(created_at=now)
    older_job = _job(created_at=now - timedelta(minutes=5))
    # Inserted out of chronological order on purpose, to prove the
    # repository -- not insertion order -- determines the returned order.
    repo.create(newer_job)
    repo.create(older_job)

    result = repo.list_by_trip_id("trip_1")

    assert [job.job_id for job in result] == [older_job.job_id, newer_job.job_id]


def test_repository_reloads_jobs_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    first = JobRepository(LocalJsonStore(path))
    job = _job()
    first.create(job)

    second = JobRepository(LocalJsonStore(path))

    assert second.get_by_job_id(job.job_id) == job
    assert second.list_by_trip_id("trip_1") == [job]


def test_generation_job_repository_does_not_require_redis(tmp_path: Path) -> None:
    """Sanity check that the local_json job repository never imports or
    touches anything Redis-related -- REDIS_URL/a live Redis server are
    never required for normal pytest (see
    test_async_generation_config.test_redis_url_does_not_imply_async_generation
    for the matching config-level proof)."""
    import app.repositories.job_repository as job_repository_module

    source = job_repository_module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        contents = handle.read()
    assert "redis" not in contents.lower()

    repo = JobRepository(LocalJsonStore(tmp_path / "state.json"))
    repo.create(_job())
    assert len(repo.list_by_trip_id("trip_1")) == 1
