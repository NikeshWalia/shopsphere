"""Authentication and account endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.responses import AUTH_ERRORS, errors
from app.core.deps import CurrentUser, DbSession
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
)
from app.schemas.common import MessageResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a customer account",
    description=(
        "Creates a customer account and returns an access token so the client does not "
        "have to make a second login call. New accounts always receive the `customer` "
        "role - the admin role can only be granted by the seeder or an existing admin."
    ),
    responses=errors(409, 422),
)
def register(payload: RegisterRequest, db: DbSession) -> TokenResponse:
    return auth_service.register(db, payload)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange credentials for an access token",
    description=(
        "An unknown email and a wrong password return the identical `INVALID_CREDENTIALS` "
        "response, so this endpoint cannot be used to discover which addresses are registered."
    ),
    responses=errors(401, 403, 422),
)
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    return auth_service.authenticate(db, payload.email, payload.password)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Log out",
    description=(
        "Access tokens are stateless and self-contained, so there is no server-side session "
        "to destroy: the client discards the token. The endpoint exists so clients have a "
        "single place to signal logout, and so the event is auditable. A token that has "
        "already been issued stays valid until it expires - see 'Known limitations'."
    ),
    responses=AUTH_ERRORS,
)
def logout(user: CurrentUser) -> MessageResponse:
    return MessageResponse(message=f"Signed out. Discard the access token for {user.email}.")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Current user",
    responses=AUTH_ERRORS,
)
def me(user: CurrentUser) -> UserResponse:
    return auth_service.to_user_response(user)


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update the current user's profile",
    responses=errors(401, 403, 422),
)
def update_me(payload: UpdateProfileRequest, user: CurrentUser, db: DbSession) -> UserResponse:
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return auth_service.to_user_response(user)


@router.post(
    "/me/password",
    response_model=MessageResponse,
    summary="Change password",
    description="Requires the current password; the new password must satisfy the password policy.",
    responses=errors(401, 403, 422),
)
def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, db: DbSession
) -> MessageResponse:
    auth_service.change_password(db, user, payload.current_password, payload.new_password)
    return MessageResponse(message="Password updated. Existing tokens remain valid until expiry.")
