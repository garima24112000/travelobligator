"""Local-file-backed user repository (Step 184C).

Mirrors `app.repositories.trip_repository.TripRepository`/
`app.repositories.planning_state_repository.PlanningStateRepository`
exactly: cached in memory for the lifetime of this instance (loaded from
disk at construction time), persisted to the same local JSON file (see
`app.storage.local_json_store`) under its own `"users"` collection, so
existing `"trips"`/`"planning_states"` collections in that file are
completely unaffected. Not for production use: no multi-worker/
multi-process coordination, no migrations -- see ARCHITECTURE.md.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.models.user import UserRecord
from app.repositories.protocols import UserAlreadyExistsError
from app.storage.local_json_store import LocalJsonStore, get_local_json_store

_COLLECTION = "users"


class UserRepository:
    def __init__(self, store: LocalJsonStore | None = None) -> None:
        self._store = store or get_local_json_store(
            get_settings().resolved_local_storage_path()
        )
        self._users: dict[str, UserRecord] = {
            user_id: UserRecord.model_validate(record)
            for user_id, record in self._store.read_collection(_COLLECTION).items()
        }

    def _persist(self) -> None:
        self._store.write_collection(
            _COLLECTION,
            {
                user_id: record.model_dump(mode="json")
                for user_id, record in self._users.items()
            },
        )

    def create_user(self, user: UserRecord) -> UserRecord:
        """Raises `UserAlreadyExistsError` if `user.email` (already
        normalized by `UserRecord`'s own validator) already has an
        account -- never silently overwrites an existing user, and never
        stores two records for the same email."""
        if self.get_by_email(user.email) is not None:
            raise UserAlreadyExistsError(user.email)

        self._users[user.user_id] = user
        self._persist()
        return user

    def get_by_user_id(self, user_id: str) -> UserRecord | None:
        return self._users.get(user_id)

    def get_by_email(self, email: str) -> UserRecord | None:
        normalized = email.strip().lower()
        for record in self._users.values():
            if record.email == normalized:
                return record
        return None


user_repository = UserRepository()
