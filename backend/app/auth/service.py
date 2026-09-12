"""Auth service (Step 184C): signup/login business logic.

Wraps password hashing (`app/auth/passwords.py`) and a user repository
(`app/repositories/factory.py`'s `get_user_repository()`) into the
operations `/auth/signup`/`/auth/login`/`/auth/me` need. Route handlers
(`app/api/routes/auth.py`) stay thin: this is where "normalize email,
hash password, create record, translate a duplicate into
EMAIL_ALREADY_REGISTERED" and "look up by email, verify hash, generic
INVALID_CREDENTIALS on any failure" live, independently testable without
a FastAPI `TestClient`.

Never logs a plaintext password or a password hash.
"""

from __future__ import annotations

from app.auth.passwords import hash_password, verify_password
from app.core.errors import email_already_registered_error, invalid_credentials_error
from app.models.user import LoginRequest, PublicUser, SignupRequest, UserRecord
from app.repositories.factory import get_user_repository
from app.repositories.protocols import UserAlreadyExistsError, UserRepositoryProtocol


def signup(
    request: SignupRequest, user_repository: UserRepositoryProtocol | None = None
) -> PublicUser:
    """Creates a new user account.

    Raises `email_already_registered_error()` (409) if `request.email`
    already has an account -- never overwrites the existing account,
    never reveals anything about it beyond "it exists."
    """
    repository = user_repository or get_user_repository()
    password_hash = hash_password(request.password)
    user = UserRecord(email=request.email, password_hash=password_hash)

    try:
        created = repository.create_user(user)
    except UserAlreadyExistsError:
        raise email_already_registered_error() from None

    return created.to_public()


def login(
    request: LoginRequest, user_repository: UserRepositoryProtocol | None = None
) -> PublicUser:
    """Verifies credentials and returns the matching user.

    Raises `invalid_credentials_error()` (401) for either an unknown
    email or a wrong password -- deliberately the same error either way,
    so a login attempt can never be used to enumerate registered
    accounts.
    """
    repository = user_repository or get_user_repository()
    existing = repository.get_by_email(request.email)

    if existing is None or not verify_password(request.password, existing.password_hash):
        raise invalid_credentials_error()

    return existing.to_public()


def get_current_user_by_id(
    user_id: str, user_repository: UserRepositoryProtocol | None = None
) -> PublicUser | None:
    """Returns the `PublicUser` for `user_id`, or `None` if no such user
    exists (e.g. a session cookie that outlived the account, such as
    after direct data deletion outside the API)."""
    repository = user_repository or get_user_repository()
    record = repository.get_by_user_id(user_id)
    return record.to_public() if record is not None else None
