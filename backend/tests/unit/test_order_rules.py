"""Unit tests for the order state machine, payment classification and logging redaction."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import allure
import httpx
import pytest

from app.core.logging import REDACTED, redact
from app.models.enums import ORDER_STATUS_TRANSITIONS, OrderStatus, PaymentStatus
from app.repositories.order import generate_order_number
from app.services.payment import ChargeOutcome, PaymentGateway, detect_card_brand, luhn_is_valid

pytestmark = [pytest.mark.unit, allure.epic("Commerce")]


@allure.feature("Order lifecycle")
@allure.story("Status transitions")
class TestOrderStatusTransitions:
    def test_every_status_has_a_transition_rule(self) -> None:
        """A status with no entry would raise a KeyError at runtime."""
        assert set(ORDER_STATUS_TRANSITIONS) == set(OrderStatus)

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (OrderStatus.PENDING, OrderStatus.CONFIRMED),
            (OrderStatus.CONFIRMED, OrderStatus.PROCESSING),
            (OrderStatus.PROCESSING, OrderStatus.SHIPPED),
            (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
            (OrderStatus.PENDING, OrderStatus.CANCELLED),
            (OrderStatus.CONFIRMED, OrderStatus.CANCELLED),
            (OrderStatus.PROCESSING, OrderStatus.CANCELLED),
        ],
    )
    def test_permitted_transitions(self, current: OrderStatus, target: OrderStatus) -> None:
        assert target in ORDER_STATUS_TRANSITIONS[current]

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (OrderStatus.DELIVERED, OrderStatus.SHIPPED),  # no going backwards
            (OrderStatus.CANCELLED, OrderStatus.CONFIRMED),  # cancelled is final
            (OrderStatus.SHIPPED, OrderStatus.CANCELLED),  # already in transit
            (OrderStatus.DELIVERED, OrderStatus.CANCELLED),
            (OrderStatus.PENDING, OrderStatus.SHIPPED),  # no skipping ahead
            (OrderStatus.PENDING, OrderStatus.DELIVERED),
            (OrderStatus.CONFIRMED, OrderStatus.DELIVERED),
        ],
    )
    def test_forbidden_transitions(self, current: OrderStatus, target: OrderStatus) -> None:
        assert target not in ORDER_STATUS_TRANSITIONS[current]

    def test_terminal_statuses_allow_nothing(self) -> None:
        for status in OrderStatus.terminal():
            assert ORDER_STATUS_TRANSITIONS[status] == frozenset()

    def test_shipped_orders_cannot_be_cancelled(self) -> None:
        """Once it is on a van, cancellation is a returns problem, not a stock one."""
        assert OrderStatus.SHIPPED not in OrderStatus.cancellable()
        assert OrderStatus.DELIVERED not in OrderStatus.cancellable()

    def test_cancellable_statuses_are_exactly_the_pre_shipping_ones(self) -> None:
        assert OrderStatus.cancellable() == frozenset(
            {OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING}
        )

    def test_no_transition_targets_a_status_that_does_not_exist(self) -> None:
        for targets in ORDER_STATUS_TRANSITIONS.values():
            assert targets <= set(OrderStatus)


@allure.feature("Order lifecycle")
@allure.story("Order numbers")
class TestOrderNumber:
    def test_format(self) -> None:
        number = generate_order_number(datetime(2026, 8, 27, tzinfo=UTC))
        assert number.startswith("SS-20260827-")
        assert len(number) == len("SS-20260827-ABCDEF")

    def test_numbers_are_unique(self) -> None:
        moment = datetime(2026, 8, 27, tzinfo=UTC)
        generated = {generate_order_number(moment) for _ in range(2000)}
        # 32^6 possibilities; 2000 draws colliding would indicate a broken RNG.
        assert len(generated) >= 1995

    def test_suffix_avoids_look_alike_characters(self) -> None:
        """0/O and 1/I are indistinguishable when read over the phone."""
        suffix = generate_order_number()[-6:]
        assert not (set(suffix) & set("01OI"))

    def test_numbers_are_not_sequential(self) -> None:
        """A sequential counter would publish how many orders the shop takes."""
        moment = datetime(2026, 8, 27, tzinfo=UTC)
        suffixes = [generate_order_number(moment)[-6:] for _ in range(5)]
        assert len(set(suffixes)) == 5


@allure.feature("Payments")
@allure.story("Card validation")
class TestCardHelpers:
    @pytest.mark.parametrize(
        "number",
        ["4111111111111111", "5555555555554444", "378282246310005", "4000000000000002"],
    )
    def test_valid_luhn_numbers_pass(self, number: str) -> None:
        assert luhn_is_valid(number)

    @pytest.mark.parametrize(
        "number", ["4111111111111112", "1234567812345678", "0000000000000001", "411111111111111"]
    )
    def test_invalid_luhn_numbers_fail(self, number: str) -> None:
        assert not luhn_is_valid(number)

    @pytest.mark.parametrize("number", ["", "4111", "abc", "41111111111111111111111"])
    def test_nonsense_input_fails_safely(self, number: str) -> None:
        assert not luhn_is_valid(number)

    @pytest.mark.parametrize(
        ("number", "brand"),
        [
            ("4111111111111111", "visa"),
            ("5555555555554444", "mastercard"),
            ("2221000000000009", "mastercard"),  # the newer 2-series range
            ("378282246310005", "amex"),
            ("6011111111111117", "discover"),
            ("9999999999999999", "unknown"),
            ("abcd", "unknown"),
        ],
    )
    def test_brand_detection(self, number: str, brand: str) -> None:
        assert detect_card_brand(number) == brand


@allure.feature("Payments")
@allure.story("Provider response classification")
class TestChargeInterpretation:
    """The gateway must never mistake an odd response for an approval.

    `_interpret` is exercised directly with synthetic responses so every branch
    can be covered without a running provider - including the ones that are
    awkward to trigger for real, such as a 200 with an unrecognised body.
    """

    @staticmethod
    def interpret(status: int, body: dict | None, text: str | None = None):
        import json

        content = text if text is not None else json.dumps(body or {})
        response = httpx.Response(
            status_code=status,
            content=content,
            headers={"Content-Type": "application/json"},
            request=httpx.Request("POST", "http://provider.test/payments/charge"),
        )
        # A synthetic response has no `elapsed`; the gateway must tolerate that
        # rather than let a diagnostic field break charge classification.
        return PaymentGateway._interpret(
            response, card_last4="1111", card_brand="visa", reference="REF-1"
        )

    def test_approved(self) -> None:
        result = self.interpret(200, {"status": "approved", "transaction_id": "txn_1"})
        assert result.outcome is ChargeOutcome.APPROVED
        assert result.approved
        assert result.provider_reference == "txn_1"

    def test_declined_via_402(self) -> None:
        result = self.interpret(
            402,
            {"status": "declined", "decline_code": "insufficient_funds", "message": "No funds."},
        )
        assert result.outcome is ChargeOutcome.DECLINED
        assert not result.approved
        assert result.failure_code == "insufficient_funds"

    def test_provider_500_is_a_provider_error(self) -> None:
        result = self.interpret(500, {"error": "PROVIDER_ERROR", "message": "Boom"})
        assert result.outcome is ChargeOutcome.PROVIDER_ERROR
        assert not result.approved

    def test_provider_400_is_an_invalid_request(self) -> None:
        result = self.interpret(400, {"error": "INVALID_CARD_NUMBER", "message": "Bad number"})
        assert result.outcome is ChargeOutcome.INVALID_REQUEST
        assert not result.approved

    def test_a_200_with_an_unrecognised_body_is_not_an_approval(self) -> None:
        """The most dangerous failure mode.

        If the provider changed its response shape, treating "not obviously a
        decline" as success would hand out free goods. Anything unrecognised is
        classified as a failure.
        """
        result = self.interpret(200, {"status": "something-new"})
        assert result.outcome is ChargeOutcome.PROVIDER_ERROR
        assert not result.approved

    def test_a_200_with_an_empty_body_is_not_an_approval(self) -> None:
        result = self.interpret(200, {})
        assert not result.approved

    def test_unparseable_json_is_not_an_approval(self) -> None:
        result = self.interpret(200, None, text="<html>gateway offline</html>")
        assert not result.approved

    def test_the_card_last_four_survives_every_branch(self) -> None:
        for status, body in [
            (200, {"status": "approved", "transaction_id": "t"}),
            (402, {"status": "declined"}),
            (500, {}),
            (400, {}),
        ]:
            assert self.interpret(status, body).card_last4 == "1111"


@allure.feature("Observability")
@allure.story("Log redaction")
class TestLogRedaction:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "current_password",
            "new_password",
            "token",
            "access_token",
            "authorization",
            "secret_key",
            "card_number",
            "cvv",
        ],
    )
    def test_sensitive_keys_are_redacted(self, key: str) -> None:
        assert redact({key: "super-secret"})[key] == REDACTED

    def test_redaction_is_case_insensitive(self) -> None:
        assert redact({"PASSWORD": "x", "Card_Number": "y"}) == {
            "PASSWORD": REDACTED,
            "Card_Number": REDACTED,
        }

    def test_nested_structures_are_redacted(self) -> None:
        payload = {"user": {"email": "a@b.test", "password": "secret"}, "items": [{"cvv": "123"}]}
        result = redact(payload)
        assert result["user"]["password"] == REDACTED
        assert result["user"]["email"] == "a@b.test"
        assert result["items"][0]["cvv"] == REDACTED

    def test_non_sensitive_values_are_untouched(self) -> None:
        payload = {"order_id": 7, "total": Decimal("19.99"), "status": "paid"}
        assert redact(payload) == payload

    def test_deep_nesting_terminates(self) -> None:
        """Guards against unbounded recursion on a pathological structure."""
        deep: dict = {"level": 0}
        current = deep
        for level in range(1, 30):
            current["child"] = {"level": level, "password": "secret"}
            current = current["child"]
        redact(deep)  # must return rather than hit the recursion limit


@allure.feature("Order lifecycle")
@allure.story("Payment statuses")
class TestPaymentStatus:
    def test_all_documented_statuses_exist(self) -> None:
        assert {status.value for status in PaymentStatus} == {
            "pending",
            "paid",
            "failed",
            "refunded",
        }

    def test_statuses_serialise_as_their_values(self) -> None:
        """StrEnum members must render as 'paid', not 'PaymentStatus.PAID'."""
        assert f"{PaymentStatus.PAID}" == "paid"
        assert f"{OrderStatus.CONFIRMED}" == "confirmed"
