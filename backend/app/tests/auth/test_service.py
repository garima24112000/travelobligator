from __future__ import annotations

import pytest

from app.auth.service import get_current_user_by_id, login, signup
from app.core.errors import AppError
from app.models.user import LoginRequest, SignupRequest
from app.repositories.user_repository import UserRepository
from app.schemas.errors import ErrorCode
from app.storage.local_json_store import LocalJsonStore

# Tests for the Step 184C auth service (backend/app/auth/service.py),
# using an injected, throwaway UserRepository -- never the real
# module-level singleton, never a live Postgres.


@pytest.fixture()
def repository(tmp_path) -> UserRepository:
    return UserRepository(store=LocalJsonStore(tmp_path / "state.json"))


def test_signup_creates_a_user_and_returns_public_user(repository: UserRepository) -> None:
    request = SignupRequest(email="User@Example.com", password="longenough1")

    public_user = signup(request, user_repository=repository)

    assert public_user.email == "user@example.com"
    assert repository.get_by_email("user@example.com") is not None


def test_signup_hashes_the_password_not_stores_plaintext(repository: UserRepository) -> None:
    request = SignupRequest(email="a@b.com", password="longenough1")

    signup(request, user_repository=repository)

    stored = repository.get_by_email("a@b.com")
    assert stored is not None
    assert stored.password_hash != "longenough1"
    assert stored.password_hash.startswith("$2")


def test_signup_returns_a_public_user_with_no_password_hash_field(
    repository: UserRepository,
) -> None:
    request = SignupRequest(email="a@b.com", password="longenough1")

    public_user = signup(request, user_repository=repository)

    assert "password_hash" not in type(public_user).model_fields


def test_signup_duplicate_email_raises_email_already_registered(
    repository: UserRepository,
) -> None:
    signup(SignupRequest(email="a@b.com", password="longenough1"), user_repository=repository)

    with pytest.raises(AppError) as exc_info:
        signup(
            SignupRequest(email="A@B.com", password="anotherpassword"),
            user_repository=repository,
        )

    assert exc_info.value.code == ErrorCode.EMAIL_ALREADY_REGISTERED
    assert exc_info.value.status_code == 409


def test_login_succeeds_with_correct_credentials(repository: UserRepository) -> None:
    signup(SignupRequest(email="a@b.com", password="longenough1"), user_repository=repository)

    public_user = login(
        LoginRequest(email="a@b.com", password="longenough1"), user_repository=repository
    )

    assert public_user.email == "a@b.com"


def test_login_wrong_password_raises_generic_invalid_credentials(
    repository: UserRepository,
) -> None:
    signup(SignupRequest(email="a@b.com", password="longenough1"), user_repository=repository)

    with pytest.raises(AppError) as exc_info:
        login(LoginRequest(email="a@b.com", password="wrongpassword"), user_repository=repository)

    assert exc_info.value.code == ErrorCode.INVALID_CREDENTIALS
    assert exc_info.value.status_code == 401


def test_login_unknown_email_raises_the_same_generic_error(repository: UserRepository) -> None:
    with pytest.raises(AppError) as exc_info:
        login(
            LoginRequest(email="nobody@example.com", password="anything"),
            user_repository=repository,
        )

    assert exc_info.value.code == ErrorCode.INVALID_CREDENTIALS
    assert exc_info.value.status_code == 401


def test_login_unknown_email_and_wrong_password_produce_identical_error_messages(
    repository: UserRepository,
) -> None:
    """Confirms unknown-email and wrong-password are truly indistinguishable
    to the caller -- both routes to the same error path."""
    signup(SignupRequest(email="a@b.com", password="longenough1"), user_repository=repository)

    try:
        login(LoginRequest(email="a@b.com", password="wrongpassword"), user_repository=repository)
        message_for_wrong_password = None
    except AppError as exc:
        message_for_wrong_password = exc.message

    try:
        login(
            LoginRequest(email="nobody@example.com", password="anything"),
            user_repository=repository,
        )
        message_for_unknown_email = None
    except AppError as exc:
        message_for_unknown_email = exc.message

    assert message_for_wrong_password == message_for_unknown_email


def test_get_current_user_by_id_returns_the_user(repository: UserRepository) -> None:
    created = signup(
        SignupRequest(email="a@b.com", password="longenough1"), user_repository=repository
    )

    found = get_current_user_by_id(created.user_id, user_repository=repository)

    assert found is not None
    assert found.email == "a@b.com"


def test_get_current_user_by_id_returns_none_for_unknown_id(repository: UserRepository) -> None:
    assert get_current_user_by_id("does_not_exist", user_repository=repository) is None
