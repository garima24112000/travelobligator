"""Repository contracts (Step 183D, extended in Step 184C).

Structural (`typing.Protocol`) contracts documenting the exact public
surface every persistence backend must satisfy -- both the existing
`LocalJsonStore`-backed repositories and the opt-in Postgres-backed ones
(`postgres_trip_repository.py`, `postgres_planning_state_repository.py`,
`postgres_user_repository.py`). `TripRepository`/`PlanningStateRepository`/
`UserRepository` already satisfy these structurally without any change;
this file adds no behavior, only a type-checkable description of the
contract for `app/repositories/factory.py`'s return types.
"""

from __future__ import annotations

from typing import Protocol

from app.models.generation_job import GenerationJob
from app.models.planning_state import PlanningState
from app.models.user import UserRecord
from app.repositories.trip_repository import TripRecord


class TripRepositoryProtocol(Protocol):
    def create(self, trip_id: str, owner_id: str | None = None) -> TripRecord: ...

    def get(self, trip_id: str) -> TripRecord | None: ...

    def update_status(self, trip_id: str, status: str) -> TripRecord | None: ...

    def list_by_owner_id(self, owner_id: str) -> list[TripRecord]: ...


class PlanningStateRepositoryProtocol(Protocol):
    def save(self, planning_state: PlanningState) -> PlanningState: ...

    def get_by_trip_id(self, trip_id: str) -> PlanningState | None: ...


class UserAlreadyExistsError(Exception):
    """Raised by any user repository's `create_user` when the given
    (already-normalized) email already has an account.

    A shared, backend-agnostic signal -- `UserRepository` (local_json)
    raises it after a `get_by_email` check, `PostgresUserRepository`
    raises it in response to the database's own UNIQUE constraint
    violation (`IntegrityError`) -- so `app/auth/service.py`'s `signup`
    can catch exactly one exception type regardless of
    `Settings.persistence_backend`, and never needs to know which backend
    is active.
    """

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"A user with email '{email}' already exists.")


class UserRepositoryProtocol(Protocol):
    def create_user(self, user: UserRecord) -> UserRecord: ...

    def get_by_user_id(self, user_id: str) -> UserRecord | None: ...

    def get_by_email(self, email: str) -> UserRecord | None: ...


class GenerationJobRepositoryProtocol(Protocol):
    """Async job foundation (Step 186B, wired into real orchestration in
    Step 186C, duplicate/restart hardening in Step 186E --
    docs/14_backend_architecture.md sections 116-118). Structural
    contract for `JobRepository` (`app/repositories/job_repository.py`);
    see `app/repositories/factory.py`'s `get_job_repository()` docstring
    for why this currently resolves to the local_json implementation
    regardless of `Settings.persistence_backend`.
    """

    def create(self, job: GenerationJob) -> GenerationJob: ...

    def save(self, job: GenerationJob) -> GenerationJob: ...

    def get_by_job_id(self, job_id: str) -> GenerationJob | None: ...

    def list_by_trip_id(self, trip_id: str) -> list[GenerationJob]: ...

    def list_by_trip_id_and_status(
        self, trip_id: str, statuses: set[str]
    ) -> list[GenerationJob]: ...

    def list_running_by_trip_id(self, trip_id: str) -> list[GenerationJob]: ...

    def list_non_terminal(self) -> list[GenerationJob]: ...
