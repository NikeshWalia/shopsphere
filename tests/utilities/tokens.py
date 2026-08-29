"""JWT helpers for the security suite.

These exist so token lifecycle can be tested *deterministically*. Waiting for a
real token to expire would mean sleeping for the token's lifetime, which is both
slow and the exact kind of time-dependent test that turns flaky on a loaded CI
runner. Minting a token that is already expired takes microseconds and always
behaves the same way.

The signing key is read from configuration and must match the backend's - see
TEST_JWT_SECRET in .env.example.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from tests.configuration.settings import settings


def mint_token(
    *,
    user_id: int,
    email: str = "someone@shopsphere.test",
    role: str = "customer",
    expires_in: timedelta = timedelta(minutes=60),
    token_type: str = "access",  # noqa: S107 - the JWT claim value, not a password
    secret: str | None = None,
    algorithm: str | None = None,
    extra_claims: dict[str, Any] | None = None,
    omit_claims: tuple[str, ...] = (),
) -> str:
    """Build a token with full control over its claims.

    ``omit_claims`` and a custom ``secret`` are what let the security tests
    construct the specific malformed tokens they need to prove are rejected.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if extra_claims:
        payload.update(extra_claims)
    for claim in omit_claims:
        payload.pop(claim, None)

    return jwt.encode(
        payload,
        secret if secret is not None else settings.jwt_secret,
        algorithm=algorithm or settings.jwt_algorithm,
    )


def expired_token(*, user_id: int = 1, role: str = "customer") -> str:
    """A structurally valid, correctly signed token whose `exp` has passed."""
    return mint_token(user_id=user_id, role=role, expires_in=timedelta(minutes=-5))


def wrong_signature_token(*, user_id: int = 1, role: str = "customer") -> str:
    """Correct claims, signed with the wrong key."""
    return mint_token(user_id=user_id, role=role, secret="an-entirely-different-secret")


def forged_admin_token(*, user_id: int = 1) -> str:
    """A token claiming the admin role, signed with the wrong key.

    Proves the server derives authority from the database and a verified
    signature - not from whatever the token claims.
    """
    return mint_token(
        user_id=user_id,
        role="admin",
        email="attacker@shopsphere.test",
        secret="not-the-real-secret",
    )


def self_signed_admin_token(*, user_id: int) -> str:
    """A correctly signed token whose `role` claim has been escalated to admin.

    The nastier case: the signature verifies. The server must still refuse,
    because the role is looked up from the user record rather than trusted from
    the token body.
    """
    return mint_token(user_id=user_id, role="admin")


def alg_none_token(*, user_id: int = 1, role: str = "admin") -> str:
    """An unsigned token using ``alg: none``.

    The classic JWT downgrade attack. Rejected because the decoder pins an
    explicit algorithm allow-list rather than trusting the header.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "email": "attacker@shopsphere.test",
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=60)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, key="", algorithm="none")


def decode_without_verification(token: str) -> dict[str, Any]:
    """Read a token's claims without checking its signature.

    Used only to assert on what the *server* put in a token it issued - for
    example that it does not leak a password hash into the payload.
    """
    return jwt.decode(token, options={"verify_signature": False})


MALFORMED_TOKENS: tuple[tuple[str, str], ...] = (
    ("empty", ""),
    ("not-a-jwt", "definitely-not-a-token"),
    ("two-segments", "aGVhZGVy.cGF5bG9hZA"),
    ("garbage-segments", "a.b.c"),
    ("valid-shape-bad-base64", "!!!.???.###"),
    ("sql-injection-attempt", "' OR '1'='1"),
    ("very-long", "x" * 5000),
)
