# Troubleshooting

Things that go wrong, why, and what to do. Ordered roughly by how often they
come up.

---

## Starting the stack

### `docker compose up` hangs on `backend`

The backend waits for PostgreSQL before migrating. If it never gets there, the
database is the problem:

```bash
docker compose logs postgres
docker compose ps            # is postgres "healthy" or just "running"?
```

A `running` but not `healthy` PostgreSQL usually means the volume holds data
from an incompatible version. The fix is destructive but appropriate for a demo:

```bash
docker compose down -v       # -v deletes the volume
docker compose up --build
```

### Port already in use

```
Error: bind: address already in use
```

ShopSphere wants 3000, 5432, 8000 and 9100. Either free them, or override:

```bash
FRONTEND_PORT=3001 BACKEND_PORT=8001 POSTGRES_PORT=5433 docker compose up
```

The variables are listed in `docker-compose.yml` and `.env.example`.

### The frontend loads but every request fails

Check what the browser is actually calling. In Docker, nginx proxies `/api` to
the backend, so the SPA only ever talks to its own origin. If requests are going
to `localhost:8000` directly, something is overriding that — check
`VITE_API_PROXY_TARGET` and the browser's network tab.

```bash
curl -fsS http://localhost:3000/api/v1/products?page_size=1   # via nginx
curl -fsS http://localhost:8000/api/v1/products?page_size=1   # direct
```

---

## Running the tests

### `The ShopSphere API is not reachable at http://127.0.0.1:8000`

Working as intended. The suite refuses to run rather than producing sixty
identical connection errors. Start the stack, or run only the tests that need
nothing:

```bash
pytest backend/tests          # 235 unit tests, no stack required
pytest --skip-health-check    # bypass the gate entirely
```

### `The API is running but its database is unavailable`

The readiness probe is dependency-aware, so this means the API is up but cannot
reach PostgreSQL. Usually migrations have not been applied:

```bash
make migrate && make reseed
curl -fsS http://localhost:8000/health/ready
```

### Tests fail with `ADDRESS_NOT_FOUND` or `EMPTY_CART` in setup

Almost always a stale database — for example seed data that a previous run drew
down to zero stock.

```bash
make reseed        # wipes and reseeds; safe and idempotent
```

### The suite is much slower than the README says

Two common causes.

**Accumulated test data.** Every run creates products. After several hundred
runs the catalogue has thousands of rows and every listing query does more work.
`make reseed` truncates and restores the 63-product baseline.

**bcrypt cost.** The default is 12 rounds, which is right for production and
dominates a suite where hundreds of tests register a user. CI uses 4. For local
runs:

```bash
BCRYPT_ROUNDS=4 make test
```

Same code path, without the deliberate CPU burn.

### A test passes alone and fails in the full run

That is an isolation defect, and it is worth finding rather than retrying. Bisect
it:

```bash
pytest tests/api -p no:randomly -x          # first failure, in order
pytest tests/api/tests/test_orders.py -v    # one file
pytest "tests/api/tests/test_orders.py::TestIdempotency::test_x" -v
```

The usual cause is a test asserting on shared state — a whole-catalogue count, a
seeded product's stock — that another test changed. The fix is to create what
you mutate. See [test-strategy.md](test-strategy.md#test-data-and-isolation).

### Tests fail under `-n auto` but pass sequentially

Same class of problem, made visible. Six of these were found and fixed while
building this project; the pattern was always the same — two whole-catalogue
reads with a window between them.

Rewrite the test to own its data (create products with a unique brand and filter
to it), or mark it `serial` if it genuinely asserts a global invariant:

```python
@pytest.mark.serial   # this compares two whole-catalogue reads
def test_category_counts_match_the_listing(...):
```

`make test-parallel` runs serial tests separately, afterwards.

### UI tests time out under parallelism

The Vite dev server is single-worker and transforms modules on demand. Past
about four browsers it saturates, and the symptoms look like product bugs:
spinners that never clear, elements that never appear.

`make test-parallel` caps UI workers at 4. If you are running pytest directly:

```bash
pytest tests/ui -n 4          # not -n auto
```

---

## Playwright

### `Executable doesn't exist at .../chromium-xxxx`

Browsers are a separate download from the Python package:

```bash
python -m playwright install --with-deps chromium firefox webkit
```

`--with-deps` installs the system libraries on Linux; harmless elsewhere.

### `greenlet==3.1.1 is incompatible`

Playwright before 1.55 pins greenlet exactly, and Locust's gevent needs a newer
one — genuinely mutually exclusive. `requirements-ui.txt` pins Playwright to
`>=1.55` precisely so both can share one virtualenv. If you see this, something
downgraded Playwright:

```bash
pip install -r requirements-ui.txt
pip check
```

### A UI test fails and I cannot tell why

Everything needed is already captured. Look in `artifacts/ui/`:

```
artifacts/ui/screenshots/   full-page PNG at the moment of failure
artifacts/ui/videos/        recording of the whole test
artifacts/ui/traces/        Playwright trace - the most useful of the three
```

Open a trace with:

```bash
playwright show-trace artifacts/ui/traces/<test-name>.zip
```

It gives a DOM snapshot at every step, the network log, and the console. Nothing
is kept for passing tests, so the directory only ever holds failures.

To watch a test run:

```bash
HEADLESS=false SLOW_MO_MS=300 pytest tests/ui/tests/test_e2e_journeys.py -k register
```

### `strict mode violation: resolved to 2 elements`

Playwright refuses to guess which element you meant. This is a good error — it
means the locator is ambiguous, not that Playwright is being difficult. Scope it
in the Page Object, not in the test:

```python
# The order header AND every payment row carry this test id.
return self.page.get_by_test_id("payment-status").first
```

---

## The database

### `psql: FATAL: database "shopsphere" does not exist`

```bash
createdb -U shopsphere shopsphere
make migrate && make reseed
```

### Alembic says the database is not up to date

```bash
cd backend && PYTHONPATH=. alembic current      # where are we?
cd backend && PYTHONPATH=. alembic history      # what exists?
make migrate
```

If a migration was written by hand and conflicts, the demo answer is to reset:

```bash
make clean && make up         # deletes the volume, rebuilds from scratch
```

### Autogenerate produces an empty migration

`alembic revision --autogenerate` compares `Base.metadata` with the live
database. An empty migration means either there is genuinely no drift, or the
model was not imported. Every model must be reachable from
`backend/app/models/__init__.py` — that file exists precisely so a single
`import app.models` populates the metadata.

---

## Payments

### Every checkout returns 502

The backend cannot reach the payment provider.

```bash
curl -fsS http://localhost:9100/health
curl -fsS http://localhost:8000/health/ready   # reports the provider's state
```

Note that readiness reports the provider as `degraded` rather than failing
outright — the shop can still be browsed without it, and a probe that fails on
*any* dependency takes the whole site down because one non-critical service
blipped.

### Every checkout is declined, whatever card I use

`PAYMENT_MODE` is forcing it. Check the provider:

```bash
curl -fsS http://localhost:9100/health     # reports the active mode
```

Set `PAYMENT_MODE=card` to let the card number decide again. See
[failure-simulation.md](failure-simulation.md).

### A checkout hangs for eight seconds

Expected, if the card is `4000 0000 0000 0259`. That card makes the provider
stall so the backend's client-side timeout fires — it is the behaviour under
test, not a bug. Tests using it are marked `slow`.

```bash
pytest -m "not slow"      # skip them
```

---

## CI

### It passes locally and fails in CI

The differences are deliberate and few:

| | Local | CI |
| --- | --- | --- |
| `BCRYPT_ROUNDS` | 12 | 4 |
| Database | yours, with accumulated data | fresh, seeded per job |
| Browsers | whatever you installed | pinned by the cache key |
| Parallelism | your choice | sequential per suite |

A fresh database is the usual culprit: a test that depends on data a previous
local run left behind passes for you and fails there. Reproduce with
`make reseed` first.

### The UI job fails and I cannot reproduce it

Download the `ui-artifacts-chromium` artifact from the run. It contains the
screenshots, videos, traces and the service logs from the moment of failure.

### The Allure report has no history or trend

History is restored from the `gh-pages` branch before generating. On the first
ever run there is nothing to restore, so trends appear from the second run
onwards. Pull requests deliberately do not publish, so they never overwrite it.

---

## Still stuck

```bash
make ps                      # what is running
make logs                    # everything, following
docker compose logs backend  # one service
curl -fsS http://localhost:8000/health/ready | jq

make clean && make up        # nuclear option: fresh volume, rebuilt images
```

Every backend log line is structured JSON carrying a `request_id`, and every API
response echoes it in `X-Request-ID`. To trace a specific failing request:

```bash
docker compose logs backend | grep <request-id>
```
