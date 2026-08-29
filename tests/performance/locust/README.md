# Performance testing

Locust load model for ShopSphere.

> **Read this first.** Every number below was produced by a laptop running the
> entire stack — API, PostgreSQL, payment provider and load generator — on one
> machine. They are **baselines for detecting regressions**, not statements
> about capacity. Any figure quoted here comes with the exact command that
> produced it, so it can be reproduced or disputed.

---

## Contents

- [Running it](#running-it)
- [The load model](#the-load-model)
- [The staged shape](#the-staged-shape)
- [Metrics that matter](#metrics-that-matter)
- [Baseline targets](#baseline-targets)
- [Measured results](#measured-results)
- [Configuration](#configuration)
- [What this does not tell you](#what-this-does-not-tell-you)

---

## Running it

```bash
# Web UI at http://localhost:8089 - pick users and spawn rate interactively
make perf

# Headless, 20 users, 60 seconds, with an HTML report
make perf-headless

# Directly, with full control
python -m locust -f tests/performance/locust/locustfile.py \
    --host http://127.0.0.1:8000 \
    --headless -u 50 -r 5 -t 5m \
    --html artifacts/locust-report.html --csv artifacts/locust
```

The staged profile ignores `-u`/`-r`/`-t` and drives the user count itself:

```bash
python -m locust \
    -f tests/performance/locust/locustfile.py,tests/performance/locust/shapes.py \
    --headless --host http://127.0.0.1:8000
```

Seed the database first (`make reseed`). A run against an empty catalogue
measures 404 handling.

---

## The load model

Three user classes, weighted to resemble the traffic a real shop receives:

| Class | Share | What it does |
| --- | ---: | --- |
| `AnonymousBrowser` | ~60% | Browses, searches, opens products. Never logs in. |
| `ShopperUser` | ~35% | Registers, signs in, fills a cart. A minority check out. |
| `AdminUser` | ~5% | Refreshes dashboards, inventory and order lists. |

**Why the weighting matters more than the volume.** A run made entirely of
checkouts would hammer the payment path and the inventory row locks while
proving nothing about the catalogue queries that serve the overwhelming majority
of real requests — and those queries are what fall over first when an index goes
missing. Modelling the shape wrong produces a green load test and a red
production.

### Task weights

`ShopperUser`, in descending weight:

| Weight | Task |
| ---: | --- |
| 28 | browse the catalogue |
| 16 | view a product |
| 10 | search |
| 8 | view the cart |
| 6 | filtered browse · add to cart |
| 5 | view categories |
| 3 | view order history · request a checkout quote |
| 2 | update a cart line · view an order |
| 1 | **check out** |

One checkout for every ~90 other actions. That is roughly what a real
conversion funnel looks like, and it keeps the expensive path from dominating
the statistics.

### Two conventions that make the output usable

**Explicit `name=` on every request.** Without it, `/products/17` and
`/products/42` become separate rows and the statistics table degenerates into
one line per product id — burying the percentile that actually matters. All of
them aggregate into `GET /products/[id]`.

**`catch_response` where a rejection is correct.** A checkout that returns 409
because the catalogue genuinely ran out of stock is the shop working properly.
Counting it as an error would make the failure-rate metric meaningless — the one
number you most need to trust. Only unexpected statuses are marked as failures.

---

## The staged shape

`shapes.py` walks the system through six stages rather than holding one flat
level:

| Stage | Users | Spawn rate | What it answers |
| --- | ---: | ---: | --- |
| warm-up | 5 | 1/s | Are caches warm and connections established? |
| ramp | 40 | 2/s | Where does latency start to climb? |
| steady | 40 | 2/s | Does latency creep while load is *constant*? |
| spike | 120 | 20/s | What happens to a sudden 3x burst? |
| recovery | 40 | 20/s | Does it return to the steady-state baseline? |
| ramp-down | 5 | 10/s | Any lingering degradation? |

The steady and recovery stages are the interesting ones. A flat run tells you
what happens *at* fifty users; it cannot tell you whether latency drifts upward
while nothing changes — which is exactly how connection-pool exhaustion and
unbounded caches announce themselves.

Scale the whole profile without editing it:

```bash
PERF_SHAPE_USER_SCALE=2.0 ...   # double every user count
PERF_SHAPE_TIME_SCALE=0.1 ...   # a 90-second rehearsal of the 15-minute profile
```

---

## Metrics that matter

| Metric | Why | Where to look |
| --- | --- | --- |
| **Failure rate** | The only metric where the target is an absolute. Anything above zero on the happy path is a defect, not a performance characteristic. | `# fails` column |
| **p95** | What a slow-but-not-pathological customer experiences. Averages hide the tail. | percentile table |
| **p99** | Where timeouts and lock contention surface first. | percentile table |
| **Requests/s** | Throughput at the given user count. Meaningless without the user count and the think time beside it. | `reqs/s` |
| **Median (p50)** | The typical experience. Useful as a floor, useless as a guarantee. | `50%` column |

**Average response time is deliberately not on this list.** One 8-second
checkout among a hundred 10 ms product reads moves the average by 80 ms and
tells you nothing about either. Percentiles do.

---

## Baseline targets

For **this stack on one machine, seeded, at ~20 concurrent users**. They are
regression thresholds, chosen from observed behaviour — not SLOs and not
capacity figures.

| Endpoint group | p95 target | Rationale |
| --- | ---: | --- |
| `GET /health` | < 50 ms | No database work. Anything slower means the process is starved. |
| `GET /products [list]` | < 300 ms | Indexed, paginated, joined to inventory. The most-hit endpoint. |
| `GET /products [search]` | < 400 ms | `ILIKE '%term%'` cannot use a B-tree index — a sequential scan by design. Its slope against catalogue size is what to watch. |
| `GET /products/[id]` | < 200 ms | Primary-key lookup with two joins. |
| `GET /cart` | < 300 ms | Re-prices every line on read, on purpose. |
| `POST /auth/login` | < 800 ms | Dominated by bcrypt, which is deliberate CPU burn. |
| `POST /orders [checkout]` | < 2000 ms | Row locks plus a real HTTP round trip to the payment provider. |
| **Failure rate** | **0%** | Every non-checkout request. |

A regression is a sustained breach, not one slow sample. Re-run before believing
a single bad number.

---

## Measured results

A **smoke run**, not a benchmark. Reported because it is what was actually
observed:

```bash
python -m locust -f tests/performance/locust/locustfile.py \
    --headless -u 20 -r 5 -t 45s --host http://127.0.0.1:8000 --only-summary
```

Machine: Windows 11, 16 cores, Python 3.13, PostgreSQL 17 — all local, all on
the same box as the load generator.

| | |
| --- | --- |
| Requests | 348 |
| Failures | **0** |
| Throughput | 7.9 req/s (20 users, think time included) |
| p50 / p95 / p99 | 11 ms / 41 ms / 58 ms |

Per endpoint:

| Endpoint | Requests | p50 | p95 | Max |
| --- | ---: | ---: | ---: | ---: |
| `GET /products [list]` | 121 | 12 ms | 37 ms | 54 ms |
| `GET /products/[id]` | 69 | 8 ms | 23 ms | 30 ms |
| `GET /products [search]` | 37 | 10 ms | 24 ms | 28 ms |
| `GET /categories` | 32 | 10 ms | 23 ms | 23 ms |
| `POST /cart/items` | 12 | 24 ms | 31 ms | 31 ms |
| `POST /auth/register` | 7 | 47 ms | 58 ms | 58 ms |
| `POST /orders [checkout]` | 2 | 520 ms | — | 520 ms |

**Reading this honestly.** 7.9 req/s is a function of 20 simulated users with
realistic think time, not a throughput ceiling — the system was nowhere near
saturated. The checkout figure comes from two samples and is dominated by a real
HTTP round trip to the payment provider; it is a plausibility check, not a
percentile. The run is 45 seconds, which is long enough to catch a gross
regression and far too short to say anything about sustained behaviour.

What it *does* establish: the endpoints work under concurrent load, nothing
fails, and the catalogue queries are in the tens of milliseconds rather than the
hundreds.

---

## Configuration

Everything comes from the environment, with the test platform's own variables as
fallbacks — so a developer with a working `.env` needs no extra setup.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PERF_HOST` | `API_BASE_URL`, else `http://127.0.0.1:8000` | Target |
| `PERF_API_PREFIX` | `/api/v1` | API prefix |
| `PERF_ADMIN_EMAIL` / `PERF_ADMIN_PASSWORD` | the seeded admin | `AdminUser` credentials |
| `PERF_SHAPE_USER_SCALE` | `1.0` | Multiply every stage's user count |
| `PERF_SHAPE_TIME_SCALE` | `1.0` | Multiply every stage's duration |

`ShopperUser` **registers its own account** on start rather than sharing a
seeded one. A shared account would serialise every virtual user on the same cart
row and measure lock contention that no real shop experiences.

---

## What this does not tell you

- **Capacity.** One machine running everything, including the database and the
  load generator. The generator competes with the system under test for CPU.
- **Behaviour under sustained load.** The longest profile here is fifteen
  minutes. Memory leaks and connection-pool exhaustion often need hours.
- **Network reality.** Everything is loopback. No TLS handshakes, no latency, no
  packet loss, no CDN.
- **Database scale.** 63 seeded products. Search is `ILIKE '%term%'` and does a
  sequential scan — perfectly fast here and a genuine problem at 100,000 rows.
  That is called out in the README's *Known limitations*.
- **Concurrency correctness.** Load testing measures speed. Whether the system
  stays *correct* under concurrency is tested separately and deterministically,
  in `tests/integration/tests/test_concurrency.py` — six simultaneous buyers
  against three units, asserting that exactly three succeed.
