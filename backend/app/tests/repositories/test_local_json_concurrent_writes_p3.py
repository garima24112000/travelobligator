from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.models.planning_state import PlanningState, TravelGroupType, TripPace, TripRequest
from app.repositories.job_repository import JobRepository
from app.repositories.planning_state_repository import PlanningStateRepository
from app.repositories.trip_repository import TripRepository
from app.storage.local_json_store import LocalJsonStore

# Phase 3 (E2E reliability): the Local JSON development backend -- the one
# the browser tests run on -- serialised each collection by iterating its LIVE
# in-memory dict. When another request thread added a record at that moment
# (parallel test workers creating trips while a background job saved its
# plan) the write raised "dictionary changed size during iteration", and a
# generation job failed for no reason of its own. Writes now serialise a
# snapshot. The race needs a thread switch inside the iteration, so these
# tests force very frequent switches; they assert that NO write fails.


@pytest.fixture
def frequent_thread_switches() -> Iterator[None]:
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def _state(trip_id: str) -> PlanningState:
    return PlanningState(
        trip_id=trip_id,
        trip_request=TripRequest(
            primary_destination="Fixtureville", start_date=date(2026, 11, 10), end_date=date(2026, 11, 11),
            travelers_count=1, travel_group_type=TravelGroupType.SOLO, pace=TripPace.BALANCED,
        ),
    )


def _race(rewrite: Callable[[], Any], add: Callable[[int], Any], rounds: int = 15, adds: int = 120) -> list[str]:
    """Three threads rewrite the collection while one keeps adding records;
    returns every error raised."""
    errors: list[str] = []

    def guarded(action: Callable[[], Any]) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - the failure under test
            errors.append(f"{type(exc).__name__}: {exc}")

    def rewriter() -> None:
        for _ in range(rounds):
            guarded(rewrite)

    def adder() -> None:
        for index in range(adds):
            guarded(lambda: add(index))

    threads = [threading.Thread(target=rewriter) for _ in range(3)] + [threading.Thread(target=adder)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_planning_state_saves_survive_concurrent_inserts(tmp_path: Path, frequent_thread_switches: None) -> None:
    path = tmp_path / "state.json"
    repository = PlanningStateRepository(store=LocalJsonStore(path))
    for index in range(40):
        repository.save(_state(f"existing-{index}"))

    errors = _race(lambda: repository.save(repository.get_by_trip_id("existing-0")),
                   lambda index: repository.save(_state(f"new-{index}")))

    assert errors == []
    # nothing was lost: every state is in memory and on disk
    reloaded = PlanningStateRepository(store=LocalJsonStore(path))
    assert len([trip for trip in (f"new-{i}" for i in range(120)) if reloaded.get_by_trip_id(trip)]) == 120
    assert reloaded.get_by_trip_id("existing-0") is not None


def test_trip_writes_survive_concurrent_creates(tmp_path: Path, frequent_thread_switches: None) -> None:
    repository = TripRepository(store=LocalJsonStore(tmp_path / "state.json"))
    for index in range(40):
        repository.create(f"existing-{index}", owner_id="owner")

    errors = _race(lambda: repository.update_status("existing-0", "generated"),
                   lambda index: repository.create(f"new-{index}", owner_id="owner"))
    assert errors == []


def test_planning_state_deletion_survives_concurrent_inserts(tmp_path: Path, frequent_thread_switches: None) -> None:
    repository = PlanningStateRepository(store=LocalJsonStore(tmp_path / "state.json"))
    for index in range(60):
        repository.save(_state(f"existing-{index}"))
    victims = iter(range(60))

    errors = _race(lambda: repository.delete_for_trips({f"existing-{next(victims, 0)}"}),
                   lambda index: repository.save(_state(f"new-{index}")))
    assert errors == []


def test_reads_survive_concurrent_creates(tmp_path: Path, frequent_thread_switches: None) -> None:
    """Reads scanned the live dict too ("My trips" while another visitor
    creates a trip): they iterate a snapshot as well."""
    repository = TripRepository(store=LocalJsonStore(tmp_path / "state.json"))
    for index in range(100):
        repository.create(f"existing-{index}", owner_id="owner")

    errors = _race(lambda: repository.list_by_owner_id("owner"),
                   lambda index: repository.create(f"new-{index}", owner_id="owner"), rounds=60)
    assert errors == []
    assert len(repository.list_by_owner_id("owner")) == 220


def test_the_job_repository_serialises_a_snapshot() -> None:
    """Jobs are written under the same rule (exercised end to end by the
    browser tests; here the source is checked so the rule is not undone)."""
    source = Path(JobRepository.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).resolve().parents[3] / source).read_text()
    assert "for job_id, job in list(self._jobs.items())" in text
