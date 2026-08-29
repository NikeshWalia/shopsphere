"""FastAPI dependencies: database sessions, the current user, role gates."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import InactiveAccountError, InvalidTokenError, PermissionDeniedError
from app.core.logging import user_id_ctx
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.enums import RoleName
from app.models.user import User
from app.repositories import user as user_repo

# auto_error=False so that a missing or malformed header reaches our own
# handler. FastAPI's built-in behaviour returns {"detail": "..."} with a 403,
# which would be the one response in the API not using the standard error
# envelope - and tests would have to special-case it.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[Session, Depends(get_db)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_current_user(request: Request, db: DbSession, credentials: Credentials) -> User:
    """Resolve the authenticated user, or raise 401/403.

    The user is re-read from the database on every request rather than trusted
    from the token body. That costs one indexed lookup and buys immediate
    revocation: deactivating an account takes effect on the next request instead
    of whenever the token happens to expire.
    """
    if credentials is None or not credentials.credentials:
        raise InvalidTokenError("Authorization header is missing.")

    payload = decode_access_token(credentials.credentials)

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenError("Token subject is not a valid user id.") from exc

    user = user_repo.get_user(db, user_id)
    if user is None:
        raise InvalidTokenError("The account this token belongs to no longer exists.")
    if not user.is_active:
        raise InactiveAccountError()

    # Makes user_id available to the access log and to every business event
    # emitted while handling this request.
    user_id_ctx.set(user.id)
    request.state.user_id = user.id
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_admin(user: CurrentUser) -> User:
    """Require the admin role.

    Layered on top of ``get_current_user``, so an anonymous caller gets 401 and
    an authenticated non-admin gets 403 - which is the distinction the security
    suite asserts on.
    """
    if user.role_name != RoleName.ADMIN:
        raise PermissionDeniedError(
            "This endpoint is restricted to administrators.",
            details={"required_role": RoleName.ADMIN.value, "your_role": user.role_name},
        )
    return user


CurrentAdmin = Annotated[User, Depends(get_current_admin)]


def get_optional_user(request: Request, db: DbSession, credentials: Credentials) -> User | None:
    """Resolve the user if a valid token is present, otherwise ``None``.

    Used by endpoints that behave differently for admins (for example allowing
    ``include_inactive`` on the product listing) but are public otherwise.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        return get_current_user(request, db, credentials)
    except (InvalidTokenError, InactiveAccountError):
        # A bad token on a public endpoint is treated as "not logged in" rather
        # than as an error, so browsing never breaks because of a stale token.
        return None


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def get_idempotency_key(
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description=(
                "Client-generated unique key. Repeating a checkout with the same key "
                "returns the original order instead of creating a second one."
            ),
            max_length=80,
        ),
    ] = None,
) -> str | None:
    if idempotency_key is None:
        return None
    cleaned = idempotency_key.strip()
    return cleaned or None


IdempotencyKey = Annotated[str | None, Depends(get_idempotency_key)]
