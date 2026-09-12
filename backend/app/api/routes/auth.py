"""Auth routes (Step 184C): signup, login, logout, current user.

Session cookie is signed (`itsdangerous`) and `HttpOnly` -- see
`app/auth/sessions.py`. Nothing here is applied to `/trips/*` yet; those
routes keep their current zero-authentication behavior exactly as before
(Step 184D wires ownership checks in). No test-only bypass exists
anywhere in this router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.auth.dependencies import get_current_user
from app.auth.service import login, signup
from app.auth.sessions import clear_session_cookie, create_session_token, set_session_cookie
from app.core.config import Settings, get_settings
from app.core.errors import auth_not_configured_error
from app.core.response import success_response
from app.models.user import AuthResponse, LoginRequest, PublicUser, SignupRequest
from app.schemas.api_responses import ApiResponse

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
def signup_route(payload: SignupRequest, response: Response) -> ApiResponse[AuthResponse]:
    settings = get_settings()
    _require_auth_configured(settings)

    public_user = signup(payload)

    token = create_session_token(public_user.user_id, settings)
    set_session_cookie(response, token, settings)

    return success_response(AuthResponse(user=public_user))


@router.post("/login", response_model=ApiResponse[AuthResponse])
def login_route(payload: LoginRequest, response: Response) -> ApiResponse[AuthResponse]:
    settings = get_settings()
    _require_auth_configured(settings)

    public_user = login(payload)

    token = create_session_token(public_user.user_id, settings)
    set_session_cookie(response, token, settings)

    return success_response(AuthResponse(user=public_user))


@router.post("/logout", response_model=ApiResponse[None])
def logout_route(response: Response) -> ApiResponse[None]:
    # Clearing a cookie never depends on SESSION_SECRET_KEY -- it's a
    # plain expired Set-Cookie header, nothing is signed/verified here --
    # so logout always works, even if auth is otherwise unconfigured.
    clear_session_cookie(response, get_settings())
    return success_response(None, message="Logged out.")


@router.get("/me", response_model=ApiResponse[AuthResponse])
def me_route(current_user: PublicUser = Depends(get_current_user)) -> ApiResponse[AuthResponse]:
    return success_response(AuthResponse(user=current_user))
