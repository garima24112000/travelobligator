from __future__ import annotations

from pathlib import Path

import pytest

from app.models.user import UserRecord
from app.repositories.protocols import UserAlreadyExistsError
from app.repositories.user_repository import UserRepository
from app.storage.local_json_store import LocalJsonStore

# Tests for the Step 184C local-JSON user repository
# (backend/app/repositories/user_repository.py). Mirrors
# test_persistence.py's style for TripRepository/PlanningStateRepository.


def _user(email: str = "user@example.com", password_hash: str = "hash") -> UserRecord:
    return UserRecord(email=email, password_hash=password_hash)


def test_create_user_stores_and_returns_the_record(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    repo = UserRepository(store=store)

    created = repo.create_user(_user())

    assert created.email == "user@example.com"
    assert repo.get_by_user_id(created.user_id) == created


def test_get_by_email_uses_normalized_email(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    repo = UserRepository(store=store)
    repo.create_user(_user(email="  User@Example.com  "))

    assert repo.get_by_email("user@example.com") is not None
    assert repo.get_by_email("USER@EXAMPLE.COM") is not None
    assert repo.get_by_email("  user@example.com  ") is not None


def test_get_by_email_returns_none_for_unknown_email(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    repo = UserRepository(store=store)

    assert repo.get_by_email("nobody@example.com") is None


def test_get_by_user_id_returns_none_for_unknown_id(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    repo = UserRepository(store=store)

    assert repo.get_by_user_id("does_not_exist") is None


def test_create_user_raises_for_duplicate_email(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    repo = UserRepository(store=store)
    repo.create_user(_user(email="user@example.com", password_hash="hash1"))

    with pytest.raises(UserAlreadyExistsError) as exc_info:
        repo.create_user(_user(email="User@Example.com", password_hash="hash2"))

    assert exc_info.value.email == "user@example.com"


def test_duplicate_email_does_not_overwrite_the_existing_record(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    repo = UserRepository(store=store)
    first = repo.create_user(_user(email="user@example.com", password_hash="original-hash"))

    with pytest.raises(UserAlreadyExistsError):
        repo.create_user(_user(email="user@example.com", password_hash="attacker-hash"))

    reloaded = repo.get_by_email("user@example.com")
    assert reloaded is not None
    assert reloaded.password_hash == "original-hash"
    assert reloaded.user_id == first.user_id


def test_repository_reloads_from_disk_in_a_fresh_instance(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path / "state.json")
    first = UserRepository(store=store)
    created = first.create_user(_user())

    second = UserRepository(store=store)

    assert second.get_by_user_id(created.user_id) == created
    assert second.get_by_email("user@example.com") == created


def test_password_hash_is_never_the_plaintext(tmp_path: Path) -> None:
    """Sanity check that this repository stores exactly whatever
    `password_hash` it was given -- never the plaintext password field
    (there isn't one on `UserRecord` at all)."""
    store = LocalJsonStore(tmp_path / "state.json")
    repo = UserRepository(store=store)
    created = repo.create_user(_user(password_hash="$2b$12$fakehashvalue"))

    assert "password" not in UserRecord.model_fields
    assert created.password_hash == "$2b$12$fakehashvalue"


def test_users_collection_does_not_disturb_existing_trip_collections(tmp_path: Path) -> None:
    """Users share the same underlying JSON file as trips/planning_states
    but must live in their own collection -- writing users must never
    clobber an existing trips collection in the same file."""
    from app.repositories.trip_repository import TripRepository

    store = LocalJsonStore(tmp_path / "state.json")
    trip_repo = TripRepository(store=store)
    trip_repo.create("trip_abc")

    user_repo = UserRepository(store=store)
    user_repo.create_user(_user())

    # Re-load both from the same file to confirm neither clobbered the other.
    trip_repo_reloaded = TripRepository(store=store)
    user_repo_reloaded = UserRepository(store=store)
    assert trip_repo_reloaded.get("trip_abc") is not None
    assert user_repo_reloaded.get_by_email("user@example.com") is not None
