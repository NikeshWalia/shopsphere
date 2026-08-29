# Failure simulation

Most test suites prove the happy path works. This one is built to prove the
application behaves correctly **when its dependencies do not**.

Two independent mechanisms drive that, chosen for different jobs.

---

## 1. Test cards — per-request, parallel-safe

The default. The card number decides the outcome, exactly as a real payment
provider's test cards do.

| Card number | Outcome | Backend response | What happens to the order |
| --- | --- | --- | --- |
| `4111 1111 1111 1111` | approved (Visa) | `201` | confirmed / paid |
| `5555 5555 5555 4444` | approved (Mastercard) | `201` | confirmed / paid |
| `3782 822463 10005` | approved (Amex) | `201` | confirmed / paid |
| `4000 0000 0000 0002` | declined — insufficient funds | `402 PAYMENT_DECLINED` | cancelled / failed, **stock restored** |
| `4000 0000 0000 0069` | declined — expired card | `402 PAYMENT_DECLINED` | cancelled / failed, stock restored |
| `4000 0000 0000 0127` | declined — incorrect CVC | `402 PAYMENT_DECLINED` | cancelled / failed, stock restored |
| `4000 0000 0000 9995` | declined — do not honour | `402 PAYMENT_DECLINED` | cancelled / failed, stock restored |
| `4000 0000 0000 0119` | provider returns HTTP 500 | `502 PAYMENT_PROVIDER_ERROR` | cancelled / failed, stock restored |
| `4000 0000 0000 0259` | provider never responds | `504 PAYMENT_PROVIDER_TIMEOUT` | **stays pending / pending, stock stays reserved** |
| any number failing Luhn | provider returns HTTP 400 | `502` | cancelled / failed, stock restored |

The provider documents its own cards at `GET /test-cards`, and the checkout UI
lists them under *"Test cards for simulating failures"* — so the scenarios are
discoverable without reading any source.

**Why the card number and not a config flag?** Because it is per-request
state. Twelve tests can each choose a different outcome and run simultaneously
under `pytest -n auto` without interfering. A global switch would force them to
run one at a time.

### Trying it

```bash
# A successful order
curl -sX POST http://localhost:8000/api/v1/orders \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"address_id":1,"payment":{"card_number":"4111111111111111",
       "card_holder":"Ada Lovelace","expiry_month":12,"expiry_year":2032,"cvv":"123"}}'

# The same request with a declined card
#   -> 402, order cancelled, and the stock you just reserved is back
curl -sX POST http://localhost:8000/api/v1/orders \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"address_id":1,"payment":{"card_number":"4000000000000002",
       "card_holder":"Ada Lovelace","expiry_month":12,"expiry_year":2032,"cvv":"123"}}'
```

---

## 2. `PAYMENT_MODE` — a global chaos switch

Forces one outcome for **every** charge, whatever card is presented. Intended
for chaos runs and demonstrations, not for the automated suite.

```bash
PAYMENT_MODE=success       # everything is approved
PAYMENT_MODE=declined      # everything is declined
PAYMENT_MODE=server_error  # the provider 500s on every request
PAYMENT_MODE=timeout       # the provider never responds
PAYMENT_MODE=card          # (default) the card number decides
```

Set it in `.env`, in `docker-compose.yml`, or per-request via the
`X-Payment-Mode` header:

```bash
# Restart the provider in "everything fails" mode
PAYMENT_MODE=declined docker compose up -d payment-mock

# Or, without restarting anything, for one request:
curl -sX POST http://localhost:9100/payments/charge \
  -H "X-Payment-Mode: server_error" -H "Content-Type: application/json" \
  -d '{"amount":"99.99","currency":"USD","card_number":"4111111111111111",
       "card_holder":"Ada","expiry_month":12,"expiry_year":2032,
       "cvv":"123","reference":"MANUAL-TEST"}'
```

**Why the automated suite does not use it.** It is shared process state.
Setting it would make every concurrently-running test see the forced outcome,
which is precisely the coupling the card-number mechanism avoids.

---

## The timeout case, and why it is different

Declined and errored charges share one property: we **know** no money moved. So
the order is cancelled and stock is returned.

A timeout does not have that property. The provider may have taken the money and
simply failed to tell us. Both possible responses are wrong:

- Marking it **paid** could charge nothing and ship goods.
- Marking it **failed** and releasing stock could take money and cancel the
  order.

So ShopSphere does neither. The order stays `pending`/`pending` with stock still
reserved, and the customer receives `504` with a message saying the order is
awaiting confirmation. The response body carries the order number so it can be
followed up.

```json
{
  "error": "PAYMENT_PROVIDER_TIMEOUT",
  "message": "The payment provider did not respond in time. Your order is pending confirmation.",
  "details": {
    "order_id": 42,
    "order_number": "SS-20260827-K7M3QP",
    "order_status": "pending",
    "payment_status": "pending"
  }
}
```

**What is missing, stated plainly:** a production system would run a
reconciliation job that queries the provider for pending charges and settles
them. ShopSphere does not have one — an administrator resolves such orders by
hand. This is listed in the README's *Known limitations* rather than glossed
over.

The knob that makes this reproducible:

```bash
PAYMENT_TIMEOUT_SECONDS=8              # how long the backend waits (client side)
PAYMENT_MOCK_TIMEOUT_DELAY_SECONDS=30  # how long the provider stalls
```

The delay must exceed the timeout for the caller to actually time out. The tests
that exercise this are marked `@pytest.mark.slow`, because the eight seconds is
a genuine socket timeout — the behaviour under test, not a lazy sleep.

---

## Inventory failure simulation

Stock shortages need no special mechanism — the admin API is the injection
point:

```bash
# Create a product with exactly 3 units
curl -sX POST http://localhost:8000/api/v1/admin/products \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"sku":"CHAOS-001","name":"Scarce Widget","price":25.00,
       "category_id":1,"brand":"ChaosCo","stock_quantity":3}'

# Drain it to zero while a customer has it in their cart
curl -sX PUT http://localhost:8000/api/v1/admin/products/<id>/stock \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"quantity":0}'
```

The customer's cart then reports the shortfall in `issues`, sets
`is_checkout_ready: false`, and checkout returns `409`. The UI disables the
checkout button and shows the warning.

`tests/integration/tests/test_concurrency.py` uses the same mechanism to prove
the row locking works: a product with three units, six simultaneous checkouts,
exactly three winners.

---

## Backend and database failure

Not simulated in code — induced directly, because the real thing is more
faithful than any flag:

```bash
docker compose stop postgres    # /health/ready reports 503; the suite refuses to run
docker compose stop payment-mock # readiness reports the provider degraded
docker compose stop backend      # the UI surfaces its error states
```

`/health/ready` is deliberately dependency-aware. The database being down makes
it `503`; the payment provider being down makes it `degraded` but still ready,
because the shop can be browsed without it. The distinction is the point — a
readiness probe that fails on *any* dependency takes the whole shop offline
because one non-critical service blipped.

---

## Why this section exists

A test suite that only ever runs against a healthy system tells you nothing
about the day the system is not healthy. These are the scenarios that produce
the interesting failures:

| Scenario | The bug it catches |
| --- | --- |
| Declined payment | An order marked paid when no money moved. |
| Provider 500 | Stock permanently lost to an order that never completed. |
| Provider timeout | An optimistic "paid" that charges nobody. |
| Stock drained mid-session | Overselling between the cart page and checkout. |
| Six concurrent buyers, three units | A race that only appears under real load. |
| Double-clicked "Place order" | Two orders, one intent. |
