"""Repository factory (Step 183D; Section 200A default flip).

`get_*_repository()` are the single place production code (routes,
services, `PlanningOrchestrator`) resolves a repository from -- never import
a local-JSON or Postgres repository module directly (Section 200A found and
fixed one such direct binding in `TargetedRegenerationApplicationService`).

Persistence contract (Section 200A): `Settings.persistence_backend`
defaults to `"postgres"`; `"local_json"` must be requested explicitly and is
a development/test alternative, never a fallback. All five repositories
(users, trips, planning states, generation jobs, itinerary branches/
revisions) switch together on that ONE setting, so no mixed state is
possible. If Postgres is selected and unreachable, the Postgres repositories
raise; nothing here ever substitutes Local JSON.

Every function reads the setting on *every call* (never at import time):
`app/providers/gateway.py`'s import-time singleton once baked in a live
provider before per-test isolation existed (Step 183B-FIX). Postgres
repositories are constructed lazily (`functools.lru_cache`) on the first call
made while `"postgres"` is selected, so importing this module never opens a
database connection. `"local_json"` returns the module-level singletons that
tests monkeypatch (see `backend/app/tests/conftest.py`).
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.repositories.itinerary_lineage_repository import (
    itinerary_lineage_repository as _local_lineage_repository,
)
from app.repositories.job_repository import job_repository as _local_job_repository
from app.repositories.planning_state_repository import (
    planning_state_repository as _local_planning_state_repository,
)
from app.repositories.protocols import (
    GenerationJobRepositoryProtocol,
    ItineraryLineageRepositoryProtocol,
    PlanningStateRepositoryProtocol,
    TripRepositoryProtocol,
    UserRepositoryProtocol,
)
from app.repositories.trip_repository import trip_repository as _local_trip_repository
from app.repositories.user_repository import user_repository as _local_user_repository


@lru_cache
def _postgres_trip_repository() -> TripRepositoryProtocol:
    from app.repositories.postgres_trip_repository import PostgresTripRepository

    return PostgresTripRepository()


@lru_cache
def _postgres_planning_state_repository() -> PlanningStateRepositoryProtocol:
    from app.repositories.postgres_planning_state_repository import (
        PostgresPlanningStateRepository,
    )

    return PostgresPlanningStateRepository()


@lru_cache
def _postgres_user_repository() -> UserRepositoryProtocol:
    from app.repositories.postgres_user_repository import PostgresUserRepository

    return PostgresUserRepository()


@lru_cache
def _postgres_job_repository() -> GenerationJobRepositoryProtocol:
    from app.repositories.postgres_job_repository import PostgresJobRepository

    return PostgresJobRepository()


@lru_cache
def _postgres_lineage_repository() -> ItineraryLineageRepositoryProtocol:
    from app.repositories.postgres_itinerary_lineage_repository import (
        PostgresItineraryLineageRepository,
    )

    return PostgresItineraryLineageRepository()


def get_trip_repository() -> TripRepositoryProtocol:
    if get_settings().persistence_backend == "postgres":
        return _postgres_trip_repository()
    return _local_trip_repository


def get_planning_state_repository() -> PlanningStateRepositoryProtocol:
    if get_settings().persistence_backend == "postgres":
        return _postgres_planning_state_repository()
    return _local_planning_state_repository


def get_user_repository() -> UserRepositoryProtocol:
    if get_settings().persistence_backend == "postgres":
        return _postgres_user_repository()
    return _local_user_repository


def get_job_repository() -> GenerationJobRepositoryProtocol:
    """Async job foundation (Step 186B, wired to Postgres in Step 186F --
    docs/14_backend_architecture.md sections 116-119).

    Same per-call resolution as `get_trip_repository()`/
    `get_planning_state_repository()`/`get_user_repository()` above:
    `"local_json"` (the default) returns the existing module-level
    `JobRepository` singleton unchanged; `PERSISTENCE_BACKEND=postgres`
    lazily constructs and caches one `PostgresJobRepository` for the
    process, first connecting only on the first call made while that
    setting is active. `DATABASE_URL` alone never switches this -- only
    an explicit `PERSISTENCE_BACKEND=postgres` does.
    """
    if get_settings().persistence_backend == "postgres":
        return _postgres_job_repository()
    return _local_job_repository


def get_lineage_repository() -> ItineraryLineageRepositoryProtocol:
    """Section 199A revision-snapshot/branch-lineage foundation. Same
    per-call resolution as every other `get_*_repository()` above."""
    if get_settings().persistence_backend == "postgres":
        return _postgres_lineage_repository()
    return _local_lineage_repository
