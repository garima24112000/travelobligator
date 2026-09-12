"""Repository factory (Step 183D, extended in Step 184C with
`get_user_repository()`).

`get_trip_repository()`/`get_planning_state_repository()` are the single
place production code (routes, `PlanningOrchestrator`) should resolve a
repository from -- never import the local-JSON or Postgres repository
module directly. Both functions read `Settings.persistence_backend` on
*every call*, not once at import time: Step 183B-FIX found that
`app/providers/gateway.py`'s module-level `provider_gateway` singleton
baked in a live provider at import time (before any per-test isolation
existed), permanently contaminating the whole test session. Resolving
per-call here, and never constructing a Postgres repository until
`persistence_backend == "postgres"` is actually read, avoids repeating
that mistake -- importing this module never opens a database connection,
and neither does calling either function while `persistence_backend`
stays at its default `"local_json"`.

`"local_json"` (the default) returns the exact same module-level
singleton objects (`app.repositories.trip_repository.trip_repository`,
`app.repositories.planning_state_repository.planning_state_repository`)
that existed before this step -- not a fresh instance per call -- so
existing behavior, performance, and every test that monkeypatches those
singletons' `_store`/`_trips`/`_states` attributes directly (see
`backend/app/tests/conftest.py`'s `_reset_in_memory_repositories`
fixture) all keep working completely unchanged.

`"postgres"` lazily constructs (via `functools.lru_cache`, matching
`app/db/session.py`'s `get_engine()` pattern) and caches one
`PostgresTripRepository`/`PostgresPlanningStateRepository` for the
process -- constructed, and so first connecting, only on the first call
made while `persistence_backend == "postgres"`.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.repositories.planning_state_repository import (
    planning_state_repository as _local_planning_state_repository,
)
from app.repositories.protocols import (
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
