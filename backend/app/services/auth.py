"""Registration and authentication."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    AuthenticationError,
    DuplicateEmailError,
    InactiveAccountError,
    InvalidCredentialsError,
    UserNotFoundError,
)
from app.core.logging import get_logger, log_business_event
from app.core.security import (
    create_access_token,
    hash_password,
    normalise_email,
    verify_password,
)
from app.models.enums import RoleName
from app.models.user import User
from app.repositories import user as user_repo
from app.schemas.auth import RegisterRequest, TokenResponse, UserResponse

logger = get_logger(__name__)


def _to_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        role=user.role_name,
        is_active=user.is_active,
        created_at=user.created_at,
    )


def issue_token(user: User) -> TokenResponse:
    token = create_access_token(user_id=user.id, email=user.email, role=user.role_name)
    return TokenResponse(
        access_token=token,
        token_type="bearer",  # noqa: S106 - an OAuth token type, not a credential
        expires_in=settings.access_token_expire_minutes * 60,
        user=_to_user_response(user),
    )


def register(db: Session, payload: RegisterRequest) -> TokenResponse:
    """Create a customer account and return a token for it.

    New accounts are always customers. Admin is granted only by the seeder or by
    an existing admin, so registration cannot be used to escalate privilege.
    """
    email = normalise_email(payload.email)

    if user_repo.email_exists(db, email):
        raise DuplicateEmailError(details={"email": email})

    role = user_repo.get_role(db, RoleName.CUSTOMER)
    if role is None:  # pragma: no cover - guaranteed by migration + seed
        raise UserNotFoundError("The customer role is missing; run the database seed.")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        phone=payload.phone,
        role_id=role.id,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two registrations for the same address raced past the existence check.
        # The unique index is the real guarantee; this turns it into a clean 409.
        db.rollback()
        raise DuplicateEmailError(details={"email": email}) from exc

    db.refresh(user)
    log_business_event("user.registered", user_id=user.id, role=user.role_name)
    return issue_token(user)


def authenticate(db: Session, email: str, password: str) -> TokenResponse:
    """Verify credentials and issue a token.

    A missing account and a wrong password produce the identical
    :class:`InvalidCredentialsError`, and the password is verified even when no
    user was found, so response timing does not reveal which addresses are
    registered.
    """
    user = user_repo.get_user_by_email(db, email)

    if user is None:
        # Constant-ish work against a dummy hash so a missing account is not
        # measurably faster than a wrong password.
        verify_password(password, "$2b$12$" + "x" * 53)
        logger.info("Login failed", extra={"reason": "unknown_account"})
        raise InvalidCredentialsError()

    if not verify_password(password, user.password_hash):
        logger.info("Login failed", extra={"reason": "bad_password", "user_id": user.id})
        raise InvalidCredentialsError()

    if not user.is_active:
        logger.info("Login blocked", extra={"reason": "inactive", "user_id": user.id})
        raise InactiveAccountError()

    log_business_event("user.logged_in", user_id=user.id, role=user.role_name)
    return issue_token(user)


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    """Rotate a user's password after re-verifying the current one."""
    if not verify_password(current_password, user.password_hash):
        raise AuthenticationError("Current password is incorrect.")

    user.password_hash = hash_password(new_password)
    db.commit()
    log_business_event("user.password_changed", user_id=user.id)


def to_user_response(user: User) -> UserResponse:
    """Public alias used by the API layer."""
    return _to_user_response(user)
