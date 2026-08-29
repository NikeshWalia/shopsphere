"""Password hashing and JWT issuing/verification.

Kept free of database and framework imports so the whole module is unit
testable without any I/O.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import InvalidTokenError, TokenExpiredError

# Not a credential - the value of the JWT "type" claim.
TOKEN_TYPE_ACCESS: Final = "access"  # noqa: S105

# bcrypt silently truncates input at 72 bytes. Rejecting longer passwords is
# preferable to accepting a password whose tail is ignored.
BCRYPT_MAX_BYTES: Final = 72

_EMAIL_RE: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# A complete bcrypt hash: $2<variant>$<cost>$<22-char salt><31-char digest>.
_BCRYPT_HASH_RE: Final = re.compile(r"^\$2[abxy]\$\d{2}\$[./A-Za-z0-9]{53}$")


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Password must be at most {BCRYPT_MAX_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash.

    A malformed hash returns ``False`` rather than raising, so a single
    corrupted row cannot turn every login attempt into a 500.

    The structural check is not belt-and-braces: passing a truncated hash to
    ``bcrypt.checkpw`` makes the underlying Rust extension *panic*, and
    ``pyo3_runtime.PanicException`` inherits from ``BaseException`` rather than
    ``Exception``. It would therefore sail past both the ``except Exception``
    below and the application's global exception handler, failing the request at
    the ASGI layer with no structured error at all. Validating the shape first
    means that call is never reached with input that could panic.
    """
    if not _BCRYPT_HASH_RE.match(password_hash or ""):
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


class PasswordPolicyError(ValueError):
    """Raised when a password does not meet the configured complexity rules."""


def validate_password_strength(password: str) -> None:
    """Enforce the password policy, raising :class:`PasswordPolicyError`.

    The rules are deliberately modest and, crucially, *stated in the error* so
    the UI can show something actionable.
    """
    problems: list[str] = []
    if len(password) < settings.password_min_length:
        problems.append(f"be at least {settings.password_min_length} characters long")
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        problems.append(f"be at most {BCRYPT_MAX_BYTES} bytes long")
    if not any(c.isupper() for c in password):
        problems.append("contain an uppercase letter")
    if not any(c.islower() for c in password):
        problems.append("contain a lowercase letter")
    if not any(c.isdigit() for c in password):
        problems.append("contain a digit")
    if problems:
        raise PasswordPolicyError("Password must " + ", ".join(problems) + ".")


def is_valid_email(email: str) -> bool:
    """Cheap structural email check used by seed/CLI paths.

    API request bodies are validated by Pydantic's ``EmailStr``; this exists so
    non-HTTP entry points share the same expectation.
    """
    return bool(_EMAIL_RE.match(email.strip()))


def normalise_email(email: str) -> str:
    """Canonical form used for storage and uniqueness.

    Addresses are case-insensitive in practice, so ``Ada@Example.com`` and
    ``ada@example.com`` must not become two accounts.
    """
    return email.strip().lower()


# --------------------------------------------------------------------------
# JWT
# --------------------------------------------------------------------------
def create_access_token(
    *,
    user_id: int,
    email: str,
    role: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Mint a signed access token.

    ``sub`` is the user id as a string (the JWT spec requires a string subject).
    ``jti`` gives every token a unique id, which makes individual tokens
    identifiable in logs without logging the token itself.
    """
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": TOKEN_TYPE_ACCESS,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify and decode an access token.

    The algorithm allow-list is pinned to the single configured algorithm, which
    is what blocks the classic ``alg: none`` and RS256-to-HS256 confusion
    attacks. ``require`` forces the claims to actually be present rather than
    defaulting to absent-and-therefore-valid.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError() from exc

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise InvalidTokenError("Token is not an access token.")
    return payload


def extract_bearer_token(authorization_header: str | None) -> str:
    """Pull the credential out of an ``Authorization: Bearer <token>`` header."""
    if not authorization_header:
        raise InvalidTokenError("Authorization header is missing.")
    parts = authorization_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise InvalidTokenError("Authorization header must be in the form 'Bearer <token>'.")
    if not parts[1]:
        raise InvalidTokenError("Bearer token is empty.")
    return parts[1]
