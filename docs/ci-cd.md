# CI/CD

The pipeline lives in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).
This document explains *why* it is shaped the way it is — the file itself covers
what each step does.

---

## The shape

```mermaid
flowchart LR
    lint["lint<br/><i>~2 min</i>"]
    fe["frontend-checks<br/><i>~2 min</i>"]
    unit["unit-tests<br/><i>~2 min</i>"]
    svc["service-tests<br/><i>~8 min</i><br/>API · DB · contract<br/>security · integration"]
    ui["ui-tests<br/><i>~10 min</i>"]
    docker["docker-build<br/><i>~6 min</i>"]
    report["report<br/><i>Allure</i>"]
    gate["ci-passed<br/><i>the required check</i>"]

    lint --> svc
    lint --> docker
    unit --> ui
    fe --> ui

    unit & svc & ui --> report
    lint & fe & unit & svc & ui & docker --> gate

    classDef fast fill:#e3f5ec,stroke:#0f7b4f,color:#16202c
    classDef slow fill:#fdf1dc,stroke:#a76200,color:#16202c
    classDef meta fill:#e8eefc,stroke:#2f5bd7,color:#16202c
    class lint,fe,unit fast
    class svc,ui,docker slow
    class report,gate meta
```

**Cheapest feedback first.** Lint, frontend checks and unit tests start
immediately and in parallel; none of them needs a database, a browser or a
built image. A formatting mistake fails in about two minutes rather than after
twenty.

**Expensive jobs gate on cheap ones.** `service-tests` waits for `lint`, and
`ui-tests` waits for `unit-tests` and `frontend-checks`. There is no point
spinning up browsers for a branch that does not compile.

---

## Job-by-job reasoning

### `lint`

Ruff, Black `--check`, MyPy. Ruff runs with `--output-format=github`, so
findings appear as inline annotations on the diff rather than only in the log.

MyPy is **advisory** (`|| echo "::warning::..."`). The codebase is fully typed
and clean, but a new release of a third-party stub package should not block a
correct change from landing. Ruff and Black are blocking, because formatting and
lint findings are always the author's to fix.

### `frontend-checks`

ESLint, `tsc --noEmit`, and a production build. The build is not decoration: a
type error that only surfaces during bundling is exactly the kind of thing that
would otherwise be discovered by the UI job ten minutes later. The `dist/`
output is uploaded so it can be inspected without rebuilding.

### `unit-tests`

235 tests, no services, coverage collected. This is the fastest signal that the
business rules are still correct, and it is the only test job with no
infrastructure to go wrong.

### `service-tests`

API, database, contract, security and integration suites share one job because
they need the identical environment — starting the stack five times would cost
five times the setup for no benefit.

They run as **separate pytest invocations** with `if: always()`, so a failure in
the API suite still lets the security suite run and report. A single invocation
would stop at the first failure and hide everything after it.

PostgreSQL runs as a GitHub **service container** with a healthcheck, so the
job does not begin until it genuinely accepts connections. The API and payment
provider are started as background processes and waited on with
`scripts/wait_for_stack.py`, which polls `/health/ready` — the probe that is only
green once the API can actually reach its database.

Nothing in the pipeline sleeps.

### `ui-tests`

The slowest and most fragile job, so it runs last and only after the cheap
suites have had their chance to fail.

**Browser caching.** Playwright's browsers are roughly 400 MB. They are cached
with a key derived from `requirements-ui.txt`, so a Playwright version bump
invalidates the cache rather than silently reusing browsers from the previous
version.

**Matrix on demand.** Chromium on every push. Firefox and WebKit only via
`workflow_dispatch` with `full_browser_matrix: true`. A three-engine matrix on
every pull request triples the slowest job to catch a class of bug that appears
perhaps twice a year — the trade is not worth it on every commit, and it is
one click away when it matters.

**Artifacts on failure only.** Screenshots, videos, traces and all three service
logs are uploaded when the job fails, kept for 14 days. On success, nothing —
otherwise every green run would deposit hundreds of megabytes nobody opens.

### `docker-build`

Builds all four images and brings the stack up with `docker compose up -d`, then
curls it:

```bash
curl -fsS http://localhost:8000/health/ready | grep -q '"database":"ok"'
curl -fsS "http://localhost:3000/api/v1/products?page_size=1" | grep -q '"items"'
```

That second line is the important one. It goes through **nginx**, which proves
the proxy configuration works, the SPA is being served, migrations ran and
seeding ran — the whole `docker compose up --build` promise in the README, on
every push.

On failure it dumps `docker compose logs` before tearing down, because a build
that fails in CI and cannot be reproduced locally is the worst kind.

### `report`

Downloads every `allure-results-*` artifact, merges them, and generates one
report covering all suites.

Runs with `if: always()` — a report is *most* useful for a failing run.

**History** is restored from the `gh-pages` branch before generating, which is
what produces trend graphs. Without it, every report looks like the first one
ever produced. Publishing is restricted to the default branch, so a pull request
can never overwrite the published report or the history it depends on.

The job also writes a summary table to `$GITHUB_STEP_SUMMARY`, so the outcome of
each suite is visible on the run page without opening an artifact.

### `ci-passed`

One job that depends on all the others and fails if any of them did. Branch
protection needs a single required check instead of six, and adding a seventh
job later does not mean updating the branch protection rule.

It prints each job's result before deciding, so the reason for a failure is on
screen rather than requiring a click into the graph.

---

## Environment

Set once at the workflow level, so every job agrees:

```yaml
DATABASE_URL: postgresql+psycopg://shopsphere:shopsphere@localhost:5432/shopsphere
API_BASE_URL:  http://127.0.0.1:8000
SECRET_KEY:    ci-only-secret-not-used-anywhere-real
BCRYPT_ROUNDS: "4"
```

**`BCRYPT_ROUNDS=4`** deserves its own note. bcrypt's cost is deliberate CPU
burn, and 12 rounds is correct in production. In a suite where hundreds of tests
register a user, it becomes the single largest cost in the run. Four rounds
exercises the identical code path and removes minutes. It is set only in CI; the
application default stays at 12.

**No real secrets.** Every value here is a placeholder. The application refuses
to start in `ENVIRONMENT=production` while `SECRET_KEY` is still the default, so
a misconfiguration fails loudly rather than signing tokens with a published key.

---

## Concurrency and cost

```yaml
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
```

A new push supersedes an in-flight run for the same branch. Without it, pushing
three times in five minutes queues three full runs, two of which are already
obsolete.

Timeouts are set per job (10–35 minutes) so a hung browser or a wedged database
connection fails the job rather than consuming a runner for six hours.

---

## What is deliberately not here

**No deployment.** There is nowhere to deploy to. A pipeline with a fake deploy
step would be theatre.

**No coverage gate.** Coverage is collected and uploaded, but no threshold is
enforced. A percentage gate mostly teaches people to write tests that execute
lines without asserting anything. The suite's value is in what it asserts, and
[mutation testing](../README.md#future-improvements) is the honest way to
measure that.

**No automatic retries.** `pytest-rerunfailures` is installed but not enabled.
A retry that hides a real intermittent defect is worse than a red build. It is
available for genuine infrastructure flakiness, and any use would be a
deliberate, reviewed decision rather than a default.

**No scheduled runs.** Nothing changes when nobody pushes. A nightly run against
a static repository produces noise, not signal.

---

## Running the same checks locally

Every CI step maps to a Make target, so nothing in the pipeline is a mystery
that can only be reproduced by pushing:

```bash
make lint             # the lint job
make frontend-lint    # the frontend-checks job
make test-unit        # the unit-tests job
make test-api test-db test-contract test-security test-integration
make test-ui          # the ui-tests job
docker compose build && docker compose up -d && python scripts/wait_for_stack.py
make report           # the report job
make check            # lint + unit: what to run before pushing
```
