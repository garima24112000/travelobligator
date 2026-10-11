"""Auth routes (Step 184C): signup, login, logout, current user.

Session cookie is signed (`itsdangerous`) and `HttpOnly` -- see
`app/auth/sessions.py`. Nothing here is applied to `/trips/*` yet; those
routes keep their current zero-authentication behavior exactly as before
(Step 184D wires ownership checks in). No test-only bypass exists
anywhere in this router.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response, status

from app.auth import abuse
from app.auth.dependencies import get_current_user
from app.auth.passwords import verify_password
from app.auth.service import login, signup
from app.auth.sessions import clear_session_cookie, create_session_token, set_session_cookie
from app.core.config import Settings, get_settings
from app.core.errors import (
    AppError,
    account_deletion_blocked_error,
    auth_not_configured_error,
    authentication_required_error,
    invalid_credentials_error,
)
from app.core.response import success_response
from app.models.user import (
    AccountDeletedResponseData,
    AuthResponse,
    DeleteAccountRequest,
    LoginRequest,
    PublicUser,
    SignupRequest,
)
from app.repositories.errors import GenerationInProgressError, NothingToDeleteError
from app.repositories.factory import get_user_repository
from app.schemas.api_responses import ApiResponse
from app.schemas.errors import ErrorCode
from app.services import data_deletion_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_auth_configured(settings: Settings) -> None:
    """Raises `auth_not_configured_error()` (503) before any account is
    created/looked up -- checked up front, not caught after the fact, so
    a misconfigured server never creates a user it then can't issue a
    session for."""
    if not settings.session_secret_key:
        raise auth_not_configured_error()


@router.post(
    "/signup",
    response_model=ApiResponse[AuthResponse],
    status_code=status.HTTP_201_CREATED,
)
def signup_route(
    payload: SignupRequest, request: Request, response: Response
) -> ApiResponse[AuthResponse]:
    settings = get_settings()
    _require_auth_configured(settings)

    # P0: the signup limits are checked before the email is looked at (429 `SIGNUP_LIMIT_REACHED`
    # says nothing about the address), and only a CREATED account is counted.
    client_address = abuse.trusted_client_address(request, settings)
    abuse.ensure_signup_allowed(client_address)

    public_user = signup(payload)
    abuse.record_signup(client_address)

    token = create_session_token(public_user.user_id, settings)
    set_session_cookie(response, token, settings)

    return success_response(AuthResponse(user=public_user))


@router.post("/login", response_model=ApiResponse[AuthResponse])
def login_route(
    payload: LoginRequest, request: Request, response: Response
) -> ApiResponse[AuthResponse]:
    settings = get_settings()
    _require_auth_configured(settings)

    # P0: too many recent failures for this email (registered or not) is the same 429 whatever the
    # password, decided before any password work. A failure is counted; a success ends the streak.
    client_address = abuse.trusted_client_address(request, settings)
    abuse.ensure_login_allowed(payload.email, client_address)
    try:
        public_user = login(payload)
    except AppError as error:
        if error.code == ErrorCode.INVALID_CREDENTIALS:
            abuse.record_login_failure(payload.email, client_address)
        raise
    abuse.clear_login_failures(payload.email)

    token = create_session_token(public_user.user_id, settings)
    set_session_cookie(response, token, settings)

    return success_response(AuthResponse(user=public_user))


@router.post("/logout", response_model=ApiResponse[None])
def logout_route(response: Response) -> ApiResponse[None]:
    # Clearing a cookie never depends on SESSION_SECRET_KEY -- it's a
    # plain expired Set-Cookie header, nothing is signed/verified here --
    # so logout always works, even if auth is otherwise unconfigured.
    clear_session_cookie(response, get_settings())
    # Step 187E: deliberately no `user_id` here -- this route takes no
    # `Depends(get_current_user)` by design (see the comment above: logout
    # must always succeed even with an invalid/expired/absent cookie or an
    # unconfigured secret), so there is no already-verified identity to
    # attach without adding a session-verification step this route has
    # never had and must not gain now.
    logger.info(
        "Logout succeeded.",
        extra={"auth_event": "logout", "status": "succeeded"},
    )
    return success_response(None, message="Logged out.")


@router.post("/delete-account", response_model=ApiResponse[AccountDeletedResponseData])
def delete_account_route(
    payload: DeleteAccountRequest,
    request: Request,
    response: Response,
    current_user: PublicUser = Depends(get_current_user),
) -> ApiResponse[AccountDeletedResponseData]:
    """P1-A: permanently delete the signed-in account and everything it owns.

    A valid session is not enough: the request must carry the account's CURRENT password (there is no
    other proof of identity in this app -- no email verification, no second factor) and `confirm: true`.
    A wrong password is the ordinary 401 `INVALID_CREDENTIALS`, counted by the same sign-in attempt limit
    as a failed login (429 `TOO_MANY_ATTEMPTS`), and deletes nothing.

    Then, in one transaction (`services.data_deletion_service`): every trip the account owns with its
    plan, feedback, locks, versions, branches, revisions and jobs; the account's failed sign-in
    counters; the account itself. If any of its trips has a queued or running job the request is refused
    with 409 `ACCOUNT_DELETION_BLOCKED` and nothing is deleted. On success the session cookie is cleared;
    any other copy of the session stops working because its user no longer exists. POST, not DELETE:
    the password travels in the body.
    """
    settings = get_settings()
    client_address = abuse.trusted_client_address(request, settings)
    abuse.ensure_login_allowed(current_user.email, client_address)

    stored = get_user_repository().get_by_user_id(current_user.user_id)
    if stored is None or not verify_password(payload.password, stored.password_hash):
        abuse.record_login_failure(current_user.email, client_address)
        logger.warning(
            "Account deletion rejected: the password did not match.",
            extra={
                "auth_event": "account_delete",
                "status": "rejected",
                "error_code": "INVALID_CREDENTIALS",
                "user_id": current_user.user_id,
            },
        )
        raise invalid_credentials_error()

    try:
        summary = data_deletion_service.delete_account(
            current_user.user_id, email_digest=abuse.login_failure_key(current_user.email)
        )
    except GenerationInProgressError:
        raise account_deletion_blocked_error() from None
    except NothingToDeleteError:
        # Deleted by a simultaneous request: the session no longer belongs to anyone (every later
        # request with it is the same 401).
        raise authentication_required_error() from None

    clear_session_cookie(response, settings)
    return success_response(
        AccountDeletedResponseData(trips_deleted=summary.trips),
        message="Your account and trips were deleted.",
    )


@router.get("/me", response_model=ApiResponse[AuthResponse])
def me_route(current_user: PublicUser = Depends(get_current_user)) -> ApiResponse[AuthResponse]:
    return success_response(AuthResponse(user=current_user))
