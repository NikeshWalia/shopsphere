# Test strategy

## Contents

- [What this suite is for](#what-this-suite-is-for)
- [The pyramid, and where each rule is tested](#the-pyramid-and-where-each-rule-is-tested)
- [Choosing a layer](#choosing-a-layer)
- [Test data and isolation](#test-data-and-isolation)
- [Flaky test prevention](#flaky-test-prevention)
- [Business rules and their tests](#business-rules-and-their-tests)
- [Parallel execution](#parallel-execution)
- [What is deliberately not tested](#what-is-deliberately-not-tested)

---

## What this suite is for

Not "coverage". The suite exists to make a specific list of failures impossible
to ship unnoticed:

1. A customer buys more units than exist.
2. A customer sees or modifies another customer's data.
3. A customer reaches an administrator's endpoint.
4. The price the customer pays is not the price the shop set.
5. A failed payment produces a paid order.
6. Stock is not returned when an order fails or is cancelled.
7. A double-clicked button creates two orders.
8. A response's shape changes without anyone noticing.

Every one of those has a named test. When one breaks, the failure message says
which rule broke.

---

## The pyramid, and where each rule is tested

```
                       ┌──────────────────┐
                       │   E2E journeys   │   ~7 browser journeys
                       │   (Playwright)   │   slowest · highest confidence
                       └──────────────────┘
                  ┌────────────────────────────┐
                  │   UI component behaviour   │   catalog, auth, cart
                  │        (Playwright)        │
                  └────────────────────────────┘
             ┌──────────────────────────────────────┐
             │  API · contract · security · database │  the bulk of the suite
             │       (HTTPX · jsonschema · psycopg)   │  fast, precise, parallel
             └──────────────────────────────────────┘
        ┌────────────────────────────────────────────────┐
        │              Unit tests (pure logic)            │  235 tests · ~4s
        │  pricing · inventory · security · state machine │  no DB · no HTTP
        └────────────────────────────────────────────────┘
```

The shape is the point. Pricing arithmetic has **66 unit tests** and runs in
under a second; the same coverage through the browser would take twenty minutes
and tell you less about *which* rule broke.

| Layer | Runs in | Needs | Answers |
| --- | --- | --- | --- |
| Unit | ~4s | nothing | "Is this rule correct?" |
| API | seconds | API + DB | "Does the endpoint enforce it?" |
| Database | seconds | DB | "Was it actually persisted correctly?" |
| Contract | seconds | API | "Does the response still match its declared shape?" |
| Security | seconds | API | "Can this be abused?" |
| Integration | seconds–minutes | everything | "Do the components agree?" |
| UI | minutes | everything + browser | "Can a person actually do this?" |

---

## Choosing a layer

The rule applied throughout: **test a behaviour at the lowest layer that can
observe it, and exactly once.**

Worked example — *"a customer cannot buy more than is in stock"*:

| Layer | What it tests here | Why it belongs at this layer |
| --- | --- | --- |
| Unit | `validate_availability` rejects `requested > available`, handles zero, negatives, booleans, and the exact boundary | Pure logic. 20+ cases in milliseconds, no fixtures. |
| API | `POST /cart/items` returns 409 `INSUFFICIENT_INVENTORY` with `details.available` | Proves the endpoint actually *calls* the rule and shapes the error correctly. |
| Database | `CHECK (quantity >= 0)` exists; no negative row is ever present | The backstop that holds even if the application logic were bypassed. |
| Integration | six concurrent checkouts for three units → exactly three succeed | Only observable with real concurrency and real row locks. |
| UI | the stepper cannot exceed available stock; a short cart disables checkout | What a person actually experiences. |

Five layers, five *different* questions — not the same assertion repeated.

---

## Test data and isolation

**Rule 10 of the project brief is "tests must be independent". Here is
mechanically how that is guaranteed.**

### Identity comes from a UUID, not from cleanup

```python
def unique_email(prefix: str = "user") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}@shopsphere.test"
```

A counter would collide across xdist workers. Relying on teardown would break
the moment a test failed before its teardown ran. Uniqueness that comes from the
value itself cannot fail either way.

### Every test that mutates, creates

| Fixture | Scope | What it gives you |
| --- | --- | --- |
| `customer` | function | A brand-new registered account. One HTTP call. |
| `second_customer` | function | An unrelated account — the other half of every IDOR test. |
| `product_factory(**kw)` | function | Products owned by this test, deactivated at teardown. |
| `product_with_stock(n)` | function | A product with **exactly** n units. |
| `admin_token` | session | The seeded admin, treated as read-only infrastructure. |

The one shared fixture is `admin_token`, and it is shared precisely because no
test mutates the admin account. Anything that would has to create its own.

### Data is created through the API, not inserted

A user inserted directly into `users` would never have gone through password
hashing or role assignment — a test using it would prove nothing about the real
signup path. The database layer is used to *observe*, never to manufacture.

### Cleanup is best-effort, not load-bearing

`product_factory` deactivates its products at teardown, wrapped so a failure
there can never mask the real failure. Correctness does not depend on it: unique
SKUs mean leftover rows are inert.

---

## Flaky test prevention

**`time.sleep` appears nowhere in the suite.** That is enforced by habit and
visible on inspection — `grep -rn "time.sleep" tests/` returns nothing.

| Instead of | The suite does |
| --- | --- |
| `sleep(2)` after a click | Playwright auto-waits for visible + stable + enabled |
| `sleep(1)` for an API effect | `page.expect_response(...)` around the action |
| `sleep(5)` for a token to expire | mints a token with a past `exp` — microseconds |
| `sleep(10)` for the stack to boot | polls `/health/ready` until the DB is genuinely reachable |
| `sleep` then assert a count | `expect(locator).to_have_count(n)` — retries |

### Two flakiness sources found and fixed while building this

**Unstable pagination.** Sorting by price with no tiebreaker let PostgreSQL
return equal-priced rows in any order, so page 2 could repeat a row from page 1.
Fixed by appending `Product.id` to every sort. Guarded by a test that fetches
both pages and asserts no id appears twice.

**`Locator.count()` does not auto-wait.** A test calling `count()` straight
after navigation read `0` while the request was still in flight. Fixed in the
page object: `open()` establishes the page's *ready* state, so every test built
on it starts from a settled page.

### Retries

`pytest-rerunfailures` is installed but **not enabled by default**. A retry that
hides a real intermittent product defect is worse than a red build. It is
available for genuine infrastructure flakiness (`--reruns 2`), and any use in CI
would be a deliberate, reviewed decision.

---

## Business rules and their tests

| # | Rule | Where it is proved |
| --- | --- | --- |
| 1 | Cannot buy more than available | unit → API → DB constraint → concurrency |
| 2 | Cannot read another user's orders | security (IDOR, parametrised) + API |
| 3 | Customers cannot reach admin APIs | security (every admin endpoint) + integration |
| 4 | The client cannot dictate a price | API cart tests send `unit_price`/`price` and assert they are ignored |
| 5 | A failed payment never produces a paid order | API + integration + database |
| 6 | Order total matches the backend calculation | unit + API (quote total == order total) + contract |
| 7 | Inventory updates after a successful order | database (stock before/after) |
| 8 | Cancellation restores inventory | API + database |
| 9 | Duplicate checkout creates one order | API (same key twice) + concurrency (5 simultaneous) |
| 10 | Tests are independent | fixture design; provable by running `-p no:randomly` in any order and under `-n auto` |

---

## Parallel execution

The suite is parallel-safe by construction, so `-n auto` needs no special
handling:

```bash
pytest -n auto              # everything, across all cores
pytest -m "not slow" -n auto  # skip the deliberately slow timeout/concurrency tests
```

Two categories are excluded from parallelism by marker:

- `@pytest.mark.slow` — the payment-timeout test genuinely waits 8 seconds for a
  real socket timeout. That is the behaviour under test, not a lazy sleep.
- `@pytest.mark.serial` — the concurrency tests *are* the concurrency. Running
  them under xdist would add a second, uncontrolled source of it and make the
  result meaningless.

Measured speedup is reported in the README from `make benchmark`, which prints
the machine, core count and exact command alongside the number. It measures how
much of the suite is I/O wait that can be overlapped — not application
performance.

---

## What is deliberately not tested

Being explicit about the gaps is more useful than implying there are none.

- **Visual regression.** No screenshot diffing. It needs a baseline store and a
  human to adjudicate diffs; without that it produces noise.
- **Accessibility beyond the basics.** Labels, roles and focus states are used
  correctly and are locatable, but there is no axe-core audit.
- **Load beyond a single machine.** Locust runs against one local instance. Any
  number from it describes this laptop, not a production capacity.
- **Real payment integration.** By design — the brief calls for a mock, and a
  mock is what makes the failure paths reliably reproducible.
- **Browser matrix on every commit.** Chromium on every push; Firefox and WebKit
  on demand. A three-browser matrix on every pull request costs more time than
  it catches bugs.
- **Mutation testing.** The most honest way to measure whether these tests would
  actually catch a regression. It is in *Future improvements*, not claimed here.
