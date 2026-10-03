"""Bounded concurrency for independent provider requests (Section 1B).

The provider clients are synchronous, so a batch of INDEPENDENT requests
is run on a small, short-lived thread pool -- never an unbounded one, and
never a redesign of the provider layer. Concurrency may only change how
long a generation takes:

  * `run_bounded` returns one `Outcome` per task IN THE ORDER THE TASKS
    WERE GIVEN, whatever order they finished in. The caller applies them
    in that order, on its own thread.
  * A task only fetches. Anything that touches shared generation state
    (allowances, the place pool, merge counters, the route memo) is done
    by the caller before the batch is dispatched or after it is collected.
  * A task that fails or times out yields an `Outcome` holding its error;
    the other tasks of the batch are neither cancelled nor affected.
  * Every task runs in a copy of the caller's context, so the generation's
    performance recorder and request id follow it.

`PROVIDER_IO_CONCURRENCY_ENABLED=false` (or a batch of one, or a limit of
one) runs the same tasks serially on the calling thread, in order.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Generic, Sequence, TypeVar

from app.core import performance
from app.core.config import get_settings

T = TypeVar("T")

# Per-batch bounds (Section 1B). The number of Geoapify requests actually in
# flight is additionally capped per generation by
# `GEOAPIFY_MAX_CONCURRENT_REQUESTS` (`core/provider_usage.RequestLimiter`).
PLACES_BATCH_LIMIT = 4
NAMED_LOOKUP_BATCH_LIMIT = 4
PLACE_DETAILS_BATCH_LIMIT = 4
WALK_ROUTE_BATCH_LIMIT = 3
DRIVE_ROUTE_BATCH_LIMIT = 3
CONTEXT_PROVIDER_BATCH_LIMIT = 3


@dataclass(frozen=True)
class Outcome(Generic[T]):
    """What one task of a batch produced: its value, or the exception it raised."""

    value: T | None = None
    error: Exception | None = None

    def unwrap(self) -> T:
        if self.error is not None:
            raise self.error
        return self.value  # type: ignore[return-value]


def _run_one(task: Callable[[], T]) -> Outcome[T]:
    try:
        return Outcome(value=task())
    except Exception as exc:  # noqa: BLE001 - reported to the caller, in order
        return Outcome(error=exc)


def _run_in_worker(task: Callable[[], T]) -> Outcome[T]:
    performance.mark_concurrent_task()
    return _run_one(task)


def run_bounded(operation: str, tasks: Sequence[Callable[[], T]], limit: int) -> list[Outcome[T]]:
    """Runs `tasks` with at most `limit` at a time; outcomes in task order."""
    workers = min(max(1, int(limit)), len(tasks))
    if workers <= 1 or not get_settings().provider_io_concurrency_enabled:
        return [_run_one(task) for task in tasks]

    performance.note_batch(operation, len(tasks))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"generation-{operation}") as pool:
        futures = [pool.submit(contextvars.copy_context().run, _run_in_worker, task) for task in tasks]
        return [future.result() for future in futures]
