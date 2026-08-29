"""Unit tests for password hashing, the password policy and JWT handling."""

from __future__ import annotations

from datetime import timedelta

import allure
import jwt
import pytest

from app.core.config import settings
from app.core.errors import InvalidTokenError, TokenExpiredError
from app.core.security import (
    BCRYPT_MAX_BYTES,
    PasswordPolicyError,
    create_access_token,
    decode_access_token,
    extract_bearer_token,
    hash_password,
    is_valid_email,
    normalise_email,
    validate_password_strength,
    verify_password,
)

pytestmark = [pytest.mark.unit, allure.epic("Security"), allure.feature("Authentication helpers")]


@allure.story("Password hashing")
class TestPasswordHashing:
    def test_hash_then_verify_round_trips(self) -> None:
        assert verify_password("Str0ngPass!", hash_password("Str0ngPass!"))

    def test_wrong_password_does_not_verify(self) -> None:
        assert not verify_password("WrongPass1!", hash_password("Str0ngPass!"))

    def test_the_hash_is_not_the_password(self) -> None:
        password = "Str0ngPass!"
        digest = hash_password(password)
        assert password not in digest
        assert digest.startswith("$2b$")

    def test_the_same_password_hashes_differently_each_time(self) -> None:
        """Per-hash salting.

        Identical hashes for identical passwords would let anyone who saw the
        table find every account sharing a password.
        """
        assert hash_password("Str0ngPass!") != hash_password("Str0ngPass!")

    def test_verification_is_case_sensitive(self) -> None:
        digest = hash_password("Str0ngPass!")
        assert not verify_password("str0ngpass!", digest)

    @pytest.mark.parametrize("bad_hash", ["", "not-a-hash", "$2b$12$tooshort", "null"])
    def test_a_corrupt_hash_returns_false_rather_than_raising(self, bad_hash: str) -> None:
        """A damaged row must produce a failed login, not a 500."""
        assert verify_password("anything", bad_hash) is False

    def test_passwords_longer_than_bcrypts_limit_are_rejected(self) -> None:
        """bcrypt silently truncates at 72 bytes.

        Accepting a longer password would mean everything past byte 72 is
        ignored - two different passwords could then unlock the same account.
        """
        with pytest.raises(ValueError, match="72"):
            hash_password("a" * (BCRYPT_MAX_BYTES + 1))

    def test_unicode_passwords_work(self) -> None:
        password = "Pa55w0rd-日本語-ñ"
        assert verify_password(password, hash_password(password))


@allure.story("Password policy")
class TestPasswordPolicy:
    @pytest.mark.parametrize(
        "password", ["Str0ngPass", "Abcdefg1", "P@ssw0rd123", "aA1aaaaa", "Test1234"]
    )
    def test_accepts_compliant_passwords(self, password: str) -> None:
        validate_password_strength(password)

    @pytest.mark.parametrize(
        ("password", "expected_hint"),
        [
            ("Ab1", "at least 8"),
            ("alllowercase1", "uppercase"),
            ("ALLUPPERCASE1", "lowercase"),
            ("NoDigitsHere", "digit"),
            ("", "at least 8"),
        ],
    )
    def test_rejects_and_explains(self, password: str, expected_hint: str) -> None:
        """The message must say what is wrong, so the UI can act on it."""
        with pytest.raises(PasswordPolicyError) as exc:
            validate_password_strength(password)
        assert expected_hint in str(exc.value)

    def test_lists_every_problem_at_once(self) -> None:
        """Reporting one failure at a time turns signup into a guessing game."""
        with pytest.raises(PasswordPolicyError) as exc:
            validate_password_strength("abc")
        message = str(exc.value)
        assert "at least 8" in message
        assert "uppercase" in message
        assert "digit" in message


@allure.story("Email normalisation")
class TestEmail:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Ada@Example.COM", "ada@example.com"),
            ("  ada@example.com  ", "ada@example.com"),
            ("ADA@EXAMPLE.COM", "ada@example.com"),
            ("ada@example.com", "ada@example.com"),
        ],
    )
    def test_normalisation_is_case_and_whitespace_insensitive(
        self, raw: str, expected: str
    ) -> None:
        """Otherwise Ada@x.com and ada@x.com become two accounts."""
        assert normalise_email(raw) == expected

    @pytest.mark.parametrize(
        "email", ["ada@example.com", "a.b+tag@sub.example.co.uk", "user@shopsphere.test"]
    )
    def test_accepts_valid_addresses(self, email: str) -> None:
        assert is_valid_email(email)

    @pytest.mark.parametrize(
        "email", ["", "plain", "no-at.test", "@nolocal.test", "a@b", "spaces in@x.test", "a@@b.com"]
    )
    def test_rejects_invalid_addresses(self, email: str) -> None:
        assert not is_valid_email(email)


@allure.story("Access tokens")
class TestAccessTokens:
    def test_a_minted_token_decodes_to_its_claims(self) -> None:
        token = create_access_token(user_id=42, email="ada@example.com", role="customer")
        payload = decode_access_token(token)
        assert payload["sub"] == "42"
        assert payload["email"] == "ada@example.com"
        assert payload["role"] == "customer"
        assert payload["type"] == "access"

    def test_subject_is_a_string(self) -> None:
        """The JWT spec requires `sub` to be a string.

        Some libraries reject an integer subject outright, so emitting one would
        make tokens unusable by other clients.
        """
        payload = decode_access_token(
            create_access_token(user_id=7, email="a@b.test", role="customer")
        )
        assert isinstance(payload["sub"], str)

    def test_every_token_has_a_unique_id(self) -> None:
        """`jti` makes a token identifiable in logs without logging the token."""
        first = decode_access_token(
            create_access_token(user_id=1, email="a@b.test", role="customer")
        )
        second = decode_access_token(
            create_access_token(user_id=1, email="a@b.test", role="customer")
        )
        assert first["jti"] != second["jti"]

    def test_no_secret_or_password_leaks_into_the_payload(self) -> None:
        token = create_access_token(user_id=1, email="ada@example.com", role="admin")
        payload = jwt.decode(token, options={"verify_signature": False})
        assert settings.secret_key not in str(payload)
        assert not {"password", "password_hash"} & set(payload)

    def test_an_expired_token_is_rejected(self) -> None:
        """Minted already-expired, so this takes microseconds rather than an hour."""
        token = create_access_token(
            user_id=1, email="a@b.test", role="customer", expires_delta=timedelta(seconds=-1)
        )
        with pytest.raises(TokenExpiredError):
            decode_access_token(token)

    def test_a_token_signed_with_another_key_is_rejected(self) -> None:
        forged = jwt.encode(
            {"sub": "1", "role": "admin", "type": "access", "exp": 9_999_999_999},
            "a-completely-different-secret",
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError):
            decode_access_token(forged)

    def test_an_unsigned_alg_none_token_is_rejected(self) -> None:
        """The classic JWT downgrade attack.

        Blocked because the decoder pins an explicit algorithm allow-list
        instead of trusting the token's own header.
        """
        unsigned = jwt.encode(
            {"sub": "1", "role": "admin", "type": "access", "exp": 9_999_999_999},
            key="",
            algorithm="none",
        )
        with pytest.raises(InvalidTokenError):
            decode_access_token(unsigned)

    @pytest.mark.parametrize(
        "token", ["", "not-a-token", "a.b.c", "header.payload", "!!!.???.###", "x" * 2000]
    )
    def test_malformed_tokens_are_rejected(self, token: str) -> None:
        with pytest.raises(InvalidTokenError):
            decode_access_token(token)

    def test_a_token_missing_required_claims_is_rejected(self) -> None:
        """Absent must not mean valid-by-default."""
        incomplete = jwt.encode(
            {"email": "a@b.test", "exp": 9_999_999_999},
            settings.secret_key,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(InvalidTokenError):
            decode_access_token(incomplete)

    def test_a_token_of_the_wrong_type_is_rejected(self) -> None:
        wrong_type = jwt.encode(
            {"sub": "1", "role": "customer", "type": "refresh", "exp": 9_999_999_999},
            settings.secret_key,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(InvalidTokenError):
            decode_access_token(wrong_type)


@allure.story("Authorization header parsing")
class TestBearerExtraction:
    def test_extracts_the_token(self) -> None:
        assert extract_bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"

    @pytest.mark.parametrize("scheme", ["bearer", "BEARER", "BeArEr"])
    def test_the_scheme_is_case_insensitive(self, scheme: str) -> None:
        """RFC 7235 defines the scheme as case-insensitive."""
        assert extract_bearer_token(f"{scheme} tok") == "tok"

    @pytest.mark.parametrize(
        "header",
        [None, "", "Bearer", "Basic abc", "Token abc", "abc.def.ghi", "Bearer a b c", "Bearer "],
    )
    def test_rejects_malformed_headers(self, header: str | None) -> None:
        with pytest.raises(InvalidTokenError):
            extract_bearer_token(header)
