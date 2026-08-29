"""Application error taxonomy and the HTTP shape every failure is rendered in.

A single response envelope is used for *every* non-2xx response, including
FastAPI's own validation failures::

    {"error": "INSUFFICIENT_INVENTORY",
     "message": "Only 2 units of 'Aurora 14 Ultrabook' are available",
     "details": {"product_id": 7, "requested": 5, "available": 2}}

``error`` is a stable machine-readable code that clients and tests assert on.
``message`` is human-readable and safe to display. ``details`` carries
structured context and never contains internal identifiers such as stack
frames, SQL or connection strings.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every deliberate, expected failure in the application."""

    status_code: int = 400
    error_code: str = "BAD_REQUEST"
    default_message: str = "The request could not be processed."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details: dict[str, Any] = details or {}
        self.headers = headers
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        return {"error": self.error_code, "message": self.message, "details": self.details}


# --------------------------------------------------------------------------
# 400 / 409 - client and business-rule failures
# --------------------------------------------------------------------------
class ValidationFailedError(AppError):
    status_code = 422
    error_code = "VALIDATION_ERROR"
    default_message = "The request payload failed validation."


class InvalidQuantityError(AppError):
    status_code = 422
    error_code = "INVALID_QUANTITY"
    default_message = "Quantity must be a positive whole number."


class EmptyCartError(AppError):
    status_code = 409
    error_code = "EMPTY_CART"
    default_message = "Your cart is empty."


class InsufficientInventoryError(AppError):
    status_code = 409
    error_code = "INSUFFICIENT_INVENTORY"
    default_message = "There is not enough stock to fulfil this request."


class ProductUnavailableError(AppError):
    status_code = 409
    error_code = "PRODUCT_UNAVAILABLE"
    default_message = "This product is no longer available for purchase."


class DuplicateEmailError(AppError):
    status_code = 409
    error_code = "EMAIL_ALREADY_REGISTERED"
    default_message = "An account with this email address already exists."


class DuplicateSkuError(AppError):
    status_code = 409
    error_code = "SKU_ALREADY_EXISTS"
    default_message = "A product with this SKU already exists."


class InvalidOrderStateError(AppError):
    status_code = 409
    error_code = "INVALID_ORDER_STATE"
    default_message = "The order is not in a state that allows this operation."


class PromotionInvalidError(AppError):
    status_code = 422
    error_code = "PROMOTION_INVALID"
    default_message = "This promotion code cannot be applied."


class IdempotencyConflictError(AppError):
    status_code = 409
    error_code = "IDEMPOTENCY_KEY_REUSED"
    default_message = "This idempotency key was already used for a different request payload."


# --------------------------------------------------------------------------
# 401 / 403 - authentication and authorisation
# --------------------------------------------------------------------------
class AuthenticationError(AppError):
    status_code = 401
    error_code = "AUTHENTICATION_FAILED"
    default_message = "Authentication is required to access this resource."

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        headers = kwargs.pop("headers", None) or {"WWW-Authenticate": "Bearer"}
        super().__init__(message, headers=headers, **kwargs)


class InvalidCredentialsError(AuthenticationError):
    error_code = "INVALID_CREDENTIALS"
    # Deliberately identical for "no such user" and "wrong password" so the
    # endpoint cannot be used to enumerate registered email addresses.
    default_message = "Incorrect email or password."


class TokenExpiredError(AuthenticationError):
    error_code = "TOKEN_EXPIRED"
    default_message = "The access token has expired."


class InvalidTokenError(AuthenticationError):
    error_code = "INVALID_TOKEN"
    default_message = "The access token is missing or malformed."


class InactiveAccountError(AuthenticationError):
    status_code = 403
    error_code = "ACCOUNT_INACTIVE"
    default_message = "This account has been deactivated."


class PermissionDeniedError(AppError):
    status_code = 403
    error_code = "PERMISSION_DENIED"
    default_message = "You do not have permission to perform this action."


# --------------------------------------------------------------------------
# 404
# --------------------------------------------------------------------------
class NotFoundError(AppError):
    status_code = 404
    error_code = "NOT_FOUND"
    default_message = "The requested resource was not found."


class ProductNotFoundError(NotFoundError):
    error_code = "PRODUCT_NOT_FOUND"
    default_message = "Product not found."


class OrderNotFoundError(NotFoundError):
    error_code = "ORDER_NOT_FOUND"
    # A user asking for someone else's order gets exactly this response, so the
    # endpoint cannot be used to probe which order IDs exist.
    default_message = "Order not found."


class AddressNotFoundError(NotFoundError):
    error_code = "ADDRESS_NOT_FOUND"
    default_message = "Address not found."


class UserNotFoundError(NotFoundError):
    error_code = "USER_NOT_FOUND"
    default_message = "User not found."


class CartItemNotFoundError(NotFoundError):
    error_code = "CART_ITEM_NOT_FOUND"
    default_message = "That item is not in your cart."


# --------------------------------------------------------------------------
# 5xx / dependency failures
# --------------------------------------------------------------------------
class PaymentDeclinedError(AppError):
    status_code = 402
    error_code = "PAYMENT_DECLINED"
    default_message = "The payment was declined."


class PaymentProviderTimeoutError(AppError):
    status_code = 504
    error_code = "PAYMENT_PROVIDER_TIMEOUT"
    default_message = "The payment provider did not respond in time."


class PaymentProviderError(AppError):
    status_code = 502
    error_code = "PAYMENT_PROVIDER_ERROR"
    default_message = "The payment provider returned an unexpected error."


class InternalError(AppError):
    status_code = 500
    error_code = "INTERNAL_ERROR"
    default_message = "An unexpected error occurred."
