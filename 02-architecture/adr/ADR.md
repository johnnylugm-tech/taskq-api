# Architecture Decision Records (ADR) — taskq-api

> Collection of architecture decisions for `taskq-api`. Source of
> truth: `SPEC.md` v1.0.0 (2026-07-30) — the normative product
> specification — together with `01-requirements/SRS.md` (10 FR /
> 12 NFR with explicit acceptance criteria) and
> `02-architecture/SAD.md` (Phase 2 deliverable). Each decision
> below names the modules it constrains, the SRS FR-IDs / NFR-IDs
> it satisfies, and the acceptance criteria it locks in.
>
> A **Traceability Matrix** at the end of this document maps every
> decision (ADR-001 … ADR-017) back to the SRS requirement,
> specification clause, and acceptance criterion it discharges.

---

## ADR-001: Python 3.11 + FastAPI / uvicorn ASGI runtime

### Status
Accepted

### Context
`taskq-api` is a publicly-exposed HTTP service (SAD §1, FR-03,
FR-04, FR-05) that needs to handle many concurrent client requests
while also running async subprocess tasks per request (FR-08). A
runtime that supports both `async/await` request handlers and
long-running background coroutines is required. NFR-01 demands
predictable read latency (p95 < 30 ms GET-by-id, p95 < 80 ms list
at 10 k rows), so an event-loop-based server is preferred over a
thread-per-request model.

### Decision
Adopt Python 3.11 as the implementation language (verified:
`.venv/bin/python --version` → `Python 3.11.15`). Run the service
as an ASGI application on `uvicorn`:

```
uvicorn taskq_api.app:app
```

with FastAPI as the HTTP framework. Management operations
(`migrate`, `key create`, `seed`, `healthcheck`) enter via
`python -m taskq_api` (`taskq_api/__main__.py`).

### Rationale
- ASGI + `uvicorn` gives one event loop for HTTP, the async
  subprocess runner, and the graceful-drain path. The SRS
  acceptance criteria for FR-08 AC-8.1 (bounded drain) and
  FR-08 AC-8.3 (no orphan subprocess) are both directly enabled
  by this single-loop design.
- FastAPI is dependency-injection-native, which the single
  auth+scope+rate-limit dependency (`api/deps.py`) relies on.
  The SRS acceptance criteria FR-04 AC-4.3 (one auth dep per
  request) is met by FastAPI's `Depends` mechanism; ADR-010 makes
  that contract binding.
- Python 3.11 ships `asyncio.TaskGroup`, `asyncio.timeout`, and
  improved exception groups — directly used by
  `service.runner.submit` (FR-08) and `errors.problem_response`
  (FR-10). The structured-concurrency primitives are the
  foundation the SRS acceptance criteria NFR-03 AC-03.3
  (`CancelledError` propagation) is built on.

### Alternatives Considered
- **Flask + gunicorn (WSGI)**: rejected — WSGI forces a sync
  shim over `asyncio.create_subprocess_exec`, breaking FR-08
  AC-8.3 (no-orphan kill).
- **Node.js / Go**: rejected — SPEC §6 pins the module tree to
  Python; cross-language stack would violate NFR-06's layer
  contract.

### Consequences
- **Positive**: single event loop for HTTP + subprocess + drain;
  FastAPI's `Depends` keeps handlers thin (≤ 40 lines, NFR-11
  AC-11.3).
- **Negative**: blocking work (e.g. sync DB calls) in handlers
  blocks the loop; mitigated by keeping all DB I/O in
  `repository/` and using SQLAlchemy 2.x async sessions.

---

## ADR-002: Strict four-layer architecture enforced by `.importlinter`

### Status
Accepted

### Context
NFR-06 (architecture_constraints) requires that the codebase
enforce `api > service > repository > models` with a single,
documented test (`lint-imports` exits 0). The SAD §1 commits to a
four-layer contract; SAD §2.4 lists the layer DAG and the
forbidden imports. Without an automated gate, layer violations
drift in unnoticed.

### Decision
Hard-enforce the layer DAG via `.importlinter`. Mapping (SAD §2.1
Table):

| Layer | Directory | Allowed to import | Forbidden |
|-------|-----------|-------------------|-----------|
| L4 | `taskq_api/api/` | service, repository, models, errors, config | — |
| L3 | `taskq_api/service/` | repository, models, errors, config | `sqlalchemy`, `api` |
| L2 | `taskq_api/repository/` | models, errors, config | `service`, `api`, **sqlalchemy (only here)** |
| L1 | `taskq_api/models/` | errors, config | service, api, repository |
| Indep. | `config.py`, `errors.py` | — | all layers except as needed |

A dedicated test
`test_sqlalchemy_import_outside_repository_blocked` asserts
NFR-06 AC-06.2 with a clear failure message.

### Rationale
- One-way dependency flow keeps `repository/` swappable (e.g.
  switching DB engines later does not touch `service/`).
- Restricting `sqlalchemy` to `repository/` keeps the persistence
  boundary visible; nothing else is allowed to construct queries.
- `importlinter` is a static check — no runtime cost, catches
  drift in CI.

### Alternatives Considered
- **No enforcement, only docstring discipline**: rejected —
  drift accumulates silently; SPEC §8 #5 demands a failing test
  when the contract breaks.
- **Enforce via runtime assertions**: rejected — runtime
  enforcement is too late and breaks callers.

### Consequences
- **Positive**: refactors stay scoped to one layer; test
  `lint-imports` is a single binary gate.
- **Negative**: any cross-layer shortcut (e.g. handlers wanting
  ORM objects directly) must be routed through `repository/` —
  intentional friction.

---

## ADR-003: SQLAlchemy 2.x ORM as the only persistence layer

### Status
Accepted

### Context
`taskq-api` persists tasks, API keys, rate-bucket state, tags,
and task results across SQLite (dev / test) and PostgreSQL (prod)
with the same code path (SAD §1). NFR-02 AC-02.2 forbids
string-concatenated SQL everywhere; NFR-06 AC-06.2 confines
SQL access to one directory.

### Decision
Use SQLAlchemy 2.x with declarative ORM tables in
`taskq_api/models/orm.py` and explicit session boundaries in
`taskq_api/repository/session.py` (`session_scope()` context
manager). All persistence flows through `repository/{task_repo,
key_repo, rate_repo}.py`. Tables: `tasks`, `api_keys`, `tags`,
`task_tags`, `task_results`, `rate_buckets`.

Eager-loading is mandatory for read paths that traverse 1-to-N
relationships: `selectinload` / `joinedload` (FR-06 AC-6.4;
NFR-01 AC-01.3) so SQL-statement count is constant regardless of
result rows.

### Rationale
- A single ORM model works on SQLite (dev/test) and PostgreSQL
  (prod) without per-engine forks.
- Eager-loading prevents N+1, the timing oracle that NFR-01
  targets (`test_n_plus_one_sql_count_constant_within_list_endpoint`).
- Restricting SQLAlchemy imports to `repository/` (NFR-06) makes
  audit and refactor of the persistence layer mechanical.

### Alternatives Considered
- **Raw SQL with `sqlite3` / `psycopg`**: rejected — NFR-02
  AC-02.2 requires parameterised queries only and forbids
  string concatenation; an ORM enforces this by construction.
- **Per-engine repository implementations**: rejected — duplicates
  logic; ORM model abstracts the dialect.

### Consequences
- **Positive**: one model, two DBs; parameterised queries are
  enforced; eager-loading tests are precise.
- **Negative**: ORM overhead is small but present; mitigated by
  the p95 KPI test at 10 k rows.

---

## ADR-004: Alembic schema migrations with reversible downgrades (v1 → v2 → v3)

### Status
Accepted

### Context
FR-07 requires three schema revisions whose downgrade path is
working — a v3 revision that splits `tasks.result_json` into a
dedicated `task_results` table. NFR-09 AC-09.5 demands this be
tested against a real SQLite file with no skip. NFR-12 AC-12.1
adds `alembic upgrade head` → tests → `alembic downgrade base`
→ `upgrade head` round-trip into `make verify-system`.

### Decision
Adopt Alembic with three revisions under
`migrations/versions/`:

1. `v1_initial.py` — creates `tasks`, `api_keys`, `rate_buckets`.
2. `v2_tags.py` — adds `tags` / `task_tags` and a unique index
   on `tasks.name`.
3. `v3_split_results.py` — data-moving split of
   `tasks.result_json` into `task_results`, with a reversible
   downgrade that re-merges the rows.

### Rationale
- Round-trip is a binary contract — `alembic downgrade -1` then
  `upgrade head` must yield a byte-identical schema and data.
- Data-moving downgrade is harder than additive; declaring it
  high-risk early lets Phase 3 pin tests
  (`test_migration_round_trip_against_real_sqlite_file`,
  `test_failed_migration_rolls_back_to_previous_revision`).

### Alternatives Considered
- **Hand-rolled `CREATE TABLE` scripts**: rejected — no
  downgrade path, no transaction semantics (NFR-03 AC-03.6).
- **One large initial migration**: rejected — defeats the
  purpose of FR-07's three-step evolution narrative.

### Consequences
- **Positive**: every schema change is auditable; downgrades are
  first-class; `make verify-system` can exercise round-trip.
- **Negative**: Alembic adds tooling weight; offset by mandatory
  contract.

---

## ADR-005: Async subprocess execution via `asyncio.create_subprocess_exec` (no `shell=True`)

### Status
Accepted

### Context
FR-02 AC-2.2 and FR-08 require shell-command tasks executed with
predictable timeouts and no orphans (NFR-03 AC-03.5). NFR-02
AC-02.1 project-wide forbids `shell=True`, `eval(`, and `exec(`,
enforced by a grep gate.

### Decision
`taskq_api/service/runner.py` runs commands via
`asyncio.create_subprocess_exec(*shlex.split(command))` wrapped in
`asyncio.wait_for(timeout=TASKQ_TASK_TIMEOUT)`. On timeout or
cancellation, `process.kill()` followed by `await process.wait()`
ensures no orphan. A semaphore sized at `TASKQ_MAX_CONCURRENT`
caps in-flight runs; over-cap requests are queued (FR-08 AC-8.2).
Graceful drain on shutdown uses `asyncio.TaskGroup` with a budget
`TASKQ_DRAIN_TIMEOUT`; tasks exceeding the budget are marked
`interrupted` (FR-08 AC-8.1).

### Rationale
- Argv form (`create_subprocess_exec`) defeats shell-metachar
  injection (NFR-02; threat T-03).
- `shlex.split` preserves user-intended quoting without invoking
  the shell.
- `wait_for` + `process.kill` + `process.wait` is the documented
  pattern for cancelling a subprocess cleanly in modern asyncio.

### Alternatives Considered
- **`shell=True` with manual quoting**: rejected — NFR-02 AC-02.1
  forbids it.
- **ThreadPoolExecutor per command**: rejected — defeats the
  async loop and complicates drain semantics (the user's brief
  mentions `ThreadPoolExecutor` for unrelated workloads; SAD does
  not use one for command execution).

### Consequences
- **Positive**: no shell-escape vector; clean drain; bounded
  concurrency.
- **Negative**: `shlex.split` does not parse shell pipelines
  (`|`, `&&`, redirections) — by design.

---

## ADR-006: API-key authentication with SHA-256 + constant-time compare

### Status
Accepted

### Context
FR-03 specifies hashed API-key auth. NFR-02 AC-02.3 mandates
constant-time compare. NFR-04 AC-04.3 requires the plaintext key
to be visible only at `key create` time, never persisted.

### Decision
- `api_keys.key_hash` stores `hashlib.sha256(key_bytes).hexdigest()`.
- `service/auth.authenticate(key, scope)` looks up by hash and
  compares with `hmac.compare_digest`.
- Plaintext is printed once to stdout at `python -m taskq_api key
  create` time and never written to disk, logs, or error bodies.
- A single dependency `api/deps.authenticate(request)` runs once
  per request (FR-04 AC-4.3; asserted by
  `test_all_v1_routes_pass_through_same_dependency`).

### Rationale
- Hashing + constant-time compare defeats timing oracles and
  offline-recovery from a leaked DB dump.
- The "single dependency per request" rule keeps the auth path
  auditable — no handler can opt out.

### Alternatives Considered
- **Bearer JWT**: rejected — FR-03 names opaque API keys, not
  tokens; JWT adds key-management surface.
- **Plaintext keys compared by value**: rejected — fails
  NFR-02 AC-02.3 and NFR-04 AC-04.3.

### Consequences
- **Positive**: keys recoverable only by holder; auth path is
  one place in the code.
- **Negative**: no key rotation without DB write — offset by the
  `revoked_at` column.

---

## ADR-007: Per-token DB-backed rate-limit bucket with row-level lock

### Status
Accepted

### Context
FR-05 requires per-API-key rate limiting with cross-worker
consistency (FR-05 AC-5.3). In-process counters do not satisfy
this when multiple ASGI workers run behind a load balancer.

### Decision
`service/ratelimit.take(api_key.id)` calls
`repository/rate_repo.take_token(api_key.id)` inside a single
transaction that issues `SELECT … FOR UPDATE` on the bucket row.
State lives in the `rate_buckets` table; parameters
`TASKQ_RATE_BURST` and `TASKQ_RATE_PER_SEC` configure capacity
and refill. On exceed → HTTP 429 + `Retry-After` header (FR-05
AC-5.2).

### Rationale
- DB-persisted state is cross-worker consistent by definition.
- Row-level lock guarantees no over-admission under concurrent
  take (FR-05 AC-5.3; threat T-11).

### Alternatives Considered
- **Redis token bucket**: rejected — adds a dependency that
  SPEC §6 does not list; SQLite/PostgreSQL is already mandatory.
- **In-memory bucket per worker**: rejected — fails
  cross-worker consistency.

### Consequences
- **Positive**: single source of truth; correct under
  concurrency; works on both SQLite and PostgreSQL.
- **Negative**: one DB round-trip per request — mitigated by
  pool sizing and pre-ping (FR-06 AC-6.5).

---

## ADR-008: RFC 7807 `application/problem+json` error envelope

### Status
Accepted

### Context
FR-10 mandates a uniform error contract. NFR-02 AC-02.5 forbids
leaking SQL, stack, or paths in error bodies; NFR-04 AC-04.2
forbids leaking DB URLs in metrics.

### Decision
`taskq_api/errors.py` defines `Problem(type, title, status, detail,
instance, correlation_id)` and a FastAPI exception handler
`problem_response(request, exc) -> JSONResponse`. Every non-2xx
response is produced through this path with
`Content-Type: application/problem+json`. The handler sanitises
`detail` before serialization. Status mapping (SPEC §7): 422 /
401 / 403 / 404 / 409 / 429 / 503 / 500 (FR-10 AC-10.5).
`asyncio.CancelledError` propagates (NFR-03 AC-03.3) — it is not
converted to 500.

### Rationale
- A single envelope keeps clients parser-stable and lets the
  `correlation_id` thread through logs and metrics.
- Sanitisation at the boundary, not at the throw site, prevents
  regressions when new exception types are added.

### Alternatives Considered
- **Ad-hoc JSON per endpoint**: rejected — fails FR-10's
  uniformity requirement.
- **Plain text bodies**: rejected — defeats automated error
  handling on the client side.

### Consequences
- **Positive**: uniform clients; centralised redaction.
- **Negative**: every new error class must register in one
  place; intentional.

---

## ADR-009: Pydantic v2 schemas for request/response validation

### Status
Accepted

### Context
FastAPI integrates with Pydantic for body and response
validation. SAD §2.3 names `models.schemas.TaskCreate`,
`TaskRead`, etc. NFR-05 AC-05.2 requires every endpoint's
OpenAPI to carry `summary` and `description`.

### Decision
Pydantic v2 models in `taskq_api/models/schemas.py` describe
request and response bodies. Endpoints declare them as
FastAPI `response_model=` and parameter-typed bodies; FastAPI
emits 422 (unprocessable entity) automatically. Each endpoint
carries explicit `summary=` and `description=` for OpenAPI
(NFR-05 AC-05.2; verified by
`test_openapi_schema_has_summary_and_description_per_endpoint`).

### Rationale
- Pydantic + FastAPI give 422 for free with no handler code.
- Schemas double as the contract document surfaced via
  `/openapi.json`.

### Alternatives Considered
- **Hand-rolled validators**: rejected — duplicates FastAPI
  machinery and breaks OpenAPI fidelity.
- **Marshmallow / dataclasses-json**: rejected — weaker FastAPI
  integration.

### Consequences
- **Positive**: 422 is uniform; schemas live next to the ORM in
  `models/`.
- **Negative**: Pydantic v2 migration cost is real but bounded —
  one module.

---

## ADR-010: Single auth + scope + rate-limit dependency (`api/deps.py`)

### Status
Accepted

### Context
FR-04 AC-4.3 requires one middleware/dependency that enforces
auth, scope, and rate-limit per request — so that no handler can
opt out silently. SAD §2.3.9 names `api/deps.py` as the hub.

### Decision
`api/deps.py` exposes three FastAPI `Depends` callables:
`authenticate(request) -> ApiKeyContext`,
`require_scope(min: Scope)`,
`check_rate_limit(api_key: ApiKeyContext)`. Every `/v1/*`
handler depends on all three. Scope hierarchy is
`read < write < admin` (FR-04 AC-4.1). Insufficient scope → 403
whose body does not leak resource existence (FR-04 AC-4.2;
verified by `test_403_body_does_not_leak_resource_existence`).

### Rationale
- A single dependency stack means a missing `Depends(...)` is
  visible at code-review time and the test
  `test_all_v1_routes_pass_through_same_dependency` will fail.
- Hub-and-spoke: `deps.py` is the hub other `api/` modules call
  into, supporting CRG community cohesion (SAD §2.1).

### Alternatives Considered
- **Per-handler inline auth**: rejected — fails FR-04 AC-4.3 and
  is the bug pattern NFR-06 most wants to prevent.
- **Global middleware only**: rejected — middleware cannot know
  the per-route `Scope` requirement.

### Consequences
- **Positive**: one place to audit the auth chain; uniform 401 /
  403 / 429 across the API.
- **Negative**: `deps.py` becomes load-bearing — covered by
  per-FR TDD and CC ≤ 10 (NFR-11).

---

## ADR-011: `httpx.AsyncClient(ASGITransport(app))` for in-process integration tests

### Status
Accepted

### Context
NFR-10 AC-10.2 requires integration coverage via in-process HTTP,
not a live server. The integration suite must cover CRUD, every
error code (401/403/404/409/422/429/503), migration round-trip,
rate-limit trigger / recovery, and graceful drain (NFR-10
AC-10.3) at ≥ 80 % line coverage (AC-10.1).

### Decision
Integration tests instantiate
`httpx.AsyncClient(transport=ASGITransport(app=app))` so requests
run in the same event loop as the FastAPI app — no port binding,
no subprocess. Tests live under
`03-development/tests/integration/` and are discovered by pytest
without `--ignore` / `-k` / `--deselect` (NFR-09 AC-09.4).

### Rationale
- In-process HTTP removes socket-flake noise and keeps tests
  fast enough to be part of the `make verify-system` chain.
- ASGI transport preserves the actual middleware / exception
  handler stack.

### Alternatives Considered
- **`requests` against a real `uvicorn` subprocess**: rejected —
  slows CI and adds port-management flakiness.
- **`TestClient` (sync)**: rejected — sync client cannot drive
  the async runner's drain path.

### Consequences
- **Positive**: integration coverage is fast and deterministic;
  shared event loop with the runner.
- **Negative**: tests cannot exercise cross-worker race
  conditions directly — covered by dedicated race tests in
  `repository/rate_repo`.

---

## ADR-012: Correlation-ID middleware for end-to-end traceability

### Status
Accepted

### Context
FR-10 AC-10.4 requires every request to carry a `correlation_id`
that appears in `X-Correlation-Id` response header and in the
server log for that request. Threat T-07 (repudiation) depends on
this being universal.

### Decision
A middleware in `taskq_api/app.py` (or `api/deps.py`, depending on
Phase 3 split) generates a `correlation_id` per request, stores it
on `request.state`, attaches it to `X-Correlation-Id` on the
response, and binds it to the logger context. The RFC 7807
envelope carries `correlation_id` in its body (FR-10).

### Rationale
- A request-scoped id in both header and log lines lets
  operators follow an incident end-to-end without changing
  application code.
- Storing it on `request.state` makes it accessible from any
  dependency or handler without a thread-local.

### Alternatives Considered
- **Per-log-line UUID only**: rejected — clients cannot correlate.
- **Per-handler explicit pass-through**: rejected — bug-prone and
  defeats the purpose.

### Consequences
- **Positive**: every log line for a request is joinable; tests
  assert header + body + log (T-07).
- **Negative**: every log call must use the bound logger — offset
  by a single helper.

---

## ADR-013: SQLite (dev/test) and PostgreSQL (prod) via the same ORM models

### Status
Accepted

### Context
SPEC §6 mandates a single SQLAlchemy model that runs on SQLite
for development and tests, and on PostgreSQL for production. SAD
§1 names both. The `make verify-system` chain runs against
SQLite by default.

### Decision
The same `taskq_api/models/orm.py` declarative tables are used
on both engines; the database URL is selected via
`TASKQ_DATABASE_URL`. Production deployments point to PostgreSQL;
tests point to a per-test SQLite file. Production-only features
(`SELECT … FOR UPDATE`) degrade gracefully on SQLite for tests
(transaction still serialises in SQLite's default journal mode).

### Rationale
- One model, two engines — no dialect forks; no per-engine
  repository implementations.
- Tests against SQLite run fast and require no container.

### Alternatives Considered
- **PostgreSQL-only (with testcontainers)**: rejected — slows CI
  for tests that do not need it.
- **SQLite-only**: rejected — fails the production-grade
  requirement that the same code path handles both engines.

### Consequences
- **Positive**: one set of models; test parity.
- **Negative**: dialect-specific features must be guarded — the
  codebase does not currently rely on any beyond `FOR UPDATE`.

---

## ADR-014: `asyncio.TaskGroup` graceful drain on shutdown

### Status
Accepted

### Context
FR-08 AC-8.1 requires graceful drain up to `TASKQ_DRAIN_TIMEOUT`
on shutdown; tasks exceeding the budget are marked `interrupted`.
`uvicorn` signals shutdown via the lifespan protocol.

### Decision
`service/runner.py` keeps a top-level `asyncio.TaskGroup` that
owns every running subprocess task. On `lifespan.shutdown`,
the runner waits up to `TASKQ_DRAIN_TIMEOUT` for the group to
finish; tasks still running at the deadline are cancelled, killed
via `process.kill()` + `await process.wait()`, and persisted
with `status='interrupted'`.

### Rationale
- `asyncio.TaskGroup` (Python 3.11+) is the canonical structured
  concurrency primitive for "wait for all of these, or fail
  together."
- Cancelling the group cleanly propagates `CancelledError`
  through handlers (NFR-03 AC-03.3).

### Alternatives Considered
- **Manual list + loop**: rejected — reinvents `TaskGroup` and
  loses exception-group semantics.
- **Hard cancel on SIGTERM**: rejected — leaves tasks in
  `running` state in the DB.

### Consequences
- **Positive**: structured drain; bounded shutdown time;
  interrupted state is observable.
- **Negative**: requires Python 3.11 — already pinned (ADR-001).

---

## ADR-015: Redaction regex applied at every log / error / metrics boundary

### Status
Accepted

### Context
NFR-04 AC-04.1 specifies a redaction regex:
`(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`
applied before any stdout / stderr / log / error-body / metrics
write. NFR-04 AC-04.2 forbids DB URLs in metrics.

### Decision
`taskq_api/errors.py` exposes `redact(line: str) -> str` (and
helpers). All emit paths — server logs, stdout/stderr from the
subprocess runner, RFC 7807 envelopes, `/v1/metrics` response —
funnel through `redact`. DB URLs are stripped from the metrics
serialisation layer, not the log layer (defence in depth).

### Rationale
- One redaction function, called at every boundary, prevents the
  failure mode where a new emitter forgets to sanitise.
- Defence in depth (strip + redact) protects against future
  emitters that bypass `redact` by accident.

### Alternatives Considered
- **Logger filter only**: rejected — RFC 7807 bodies and metrics
  payloads are not log lines.
- **Manual scrubbing per call site**: rejected — the
  failure-mode the NFR is designed to prevent.

### Consequences
- **Positive**: secret leaks are blocked at the boundary;
  testable (`test_log_redacts_sk_token_bearer_dburl`,
  `test_metrics_response_omits_db_url_password`).
- **Negative**: a malformed secret pattern (e.g. new provider
  prefix) needs a regex update — accepted cost.

---

## ADR-016: `make verify-system` as the system verification target

### Status
Accepted

### Context
NFR-12 AC-12.1 / AC-12.2 define the system verification target.
Phase 3 Gate 2 invokes it via the harness; the target name is
fixed.

### Decision
The project `Makefile` exposes a `verify-system` target whose
chain is:

1. `alembic upgrade head`
2. full test suite (`pytest 03-development/tests -q`)
3. service start + `/healthz`, `/readyz` smoke
4. `alembic downgrade base` then `upgrade head` (round-trip)

It exits 0 and prints `verify-system: PASS` on success.

### Rationale
- One Makefile target chains the round-trip that exercises
  migrations, tests, runtime health, and a second migration
  round-trip — four orthogonal failure modes in one command.
- The harness calls it by name; the target is the contract.

### Alternatives Considered
- **`tox` / `nox` configs**: rejected — Makefile is the SPEC
  contract; both add a layer without removing the Makefile.
- **Custom Python script**: rejected — Makefile is portable and
  visible.

### Consequences
- **Positive**: a single command proves the system end-to-end.
- **Negative**: the Makefile must be kept in sync with the test
  command — owned by the build layer.

---

## ADR-017: Per-FR test-driven development for high-risk modules

### Status
Accepted

### Context
SAD §2.4 names four high-risk modules:
`service.runner`, `service.auth`, `repository.session`,
`migrations/versions/v3_split_results.py`. NFR-09 forbids
skipping and demands real assertions; NFR-08 demands mutation
score ≥ 70 on `service/` + `repository/`.

### Decision
For each high-risk module, the failing test is written before
the implementation (Phase 3 P3/P5/P7/P8 per-FR). Mutation
testing (`mutmut`) is scoped to `service/` + `repository/`
with a runtime-budget rationale recorded in
`.methodology/harness_config.json` (NFR-08 AC-08.3).

### Rationale
- TDD on high-risk paths makes the regression set explicit.
- Mutation score is meaningful only where the test suite is
  dense; restricting scope keeps the runtime tractable.

### Alternatives Considered
- **Mutation testing across the whole package**: rejected —
  runtime budget exceeds practical CI windows.
- **Tests after implementation**: rejected — defeats the
  per-FR gate (Gate 1) which the harness measures.

### Consequences
- **Positive**: high-risk modules have a green-by-construction
  baseline; mutation score reflects reality.
- **Negative**: TDD discipline is required up-front; offset by
  the explicit per-FR gate.

---

## ADR-018: Dependency & license compliance (NFR-07)

### Status
Accepted

### Context
NFR-07 (`license_compliance`, SRS §NFR-07) requires every
runtime dependency and its transitive tree to be pinned,
license-allowlisted, and shipped with a machine-readable SBOM.
SRS defines four explicit acceptance criteria — AC-07.1
through AC-07.4 — anchored to `SPEC.md` §4 NFR-07 and §5.3.
SPEC §8 #22 names `pip-licenses --format=json --with-system`
as the verification command. None of ADR-001 … ADR-017
discharges this contract; the requirement needs its own
owning decision.

### Decision
- Runtime dependencies are pinned with `==` in
  `requirements.txt`. Transitive dependencies are pinned in
  `requirements.lock` generated by `pip-compile` (NFR-07
  AC-07.1; SPEC §5.3).
- The license allowlist is exactly five identifiers —
  MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF. Any
  dependency whose resolved license falls outside the list
  is rejected at install time (NFR-07 AC-07.2).
- A whole-tree license scan runs as part of the build with
  `pip-licenses --format=json --with-system`; the JSON output
  is the evidence artifact referenced from `make
  verify-system` (NFR-07 AC-07.3; SPEC §8 #22).
- An SBOM is produced at `08-config/SBOM.json` whose entries
  carry `name / version / license / direct|transitive` —
  the four required fields verbatim (NFR-07 AC-07.4).
- Dev-only tooling (`import-linter`, `pip-licenses`,
  `mutmut`, `pytest-benchmark`, `httpx`) lives in
  `requirements-dev.txt` per SPEC §6 / NFR-07 and is exempt
  from the SBOM entry but still subject to the license
  allowlist (NFR-07 AC-07.2).

### Rationale
- `==` pinning plus a lock file is the only way to make the
  pinned-version column deterministic; `>=` allows drift and
  breaks reproducibility for the mutation-test budget
  (NFR-08 AC-08.3).
- A whole-tree scan (direct + transitive) is required
  because the threat in `SPEC.md` row R11 is a transitive
  dependency pulling in a non-allowlist license; a
  direct-only check misses that case.
- Generating the SBOM as a side effect of the install scan
  keeps the source of truth singular — one tool
  (`pip-licenses`) produces both the JSON evidence and the
  SBOM input.

### Alternatives Considered
- **`pip freeze` only, no lock file**: rejected — `pip
  freeze` lists the resolved environment, but the
  reproducibility contract requires a curated transitive
  set the operator has approved (SPEC §5.3).
- **`scancode-toolkit` instead of `pip-licenses`**: rejected
  — `scancode` is heavier and its output schema does not
  include the `direct|transitive` flag NFR-07 AC-07.4
  requires; `pip-licenses --with-system` produces both in
  one pass (SPEC §8 #22).
- **CycloneDX / SPDX generator**: rejected — the
  specification explicitly names `08-config/SBOM.json` with
  the four-field schema; introducing a standard generator
  would not change the contract and adds a maintenance
  surface.

### Consequences
- **Positive**: the dependency graph is reproducible,
  license-clean by construction, and traceable via SBOM;
  Phase 3 tests named in the SRS NFR-07 coverage note
  (`test_requirements_txt_pins_with_double_equals`,
  `test_requirements_lock_present_and_pinned`,
  `test_sbom_markdown_present_at_08_config_path`,
  `test_all_runtime_dependencies_license_in_allowlist`)
  have a real owning decision to assert against.
- **Negative**: a new transitive dep with a non-allowlist
  license blocks install — accepted; this is the threat
  NFR-07 is designed to prevent (SPEC R11).

---

*End of ADR collection. Phase 2 deliverable. Mirrors the
constraints of `02-architecture/SAD.md`; see SAD for the
machine-readable SAB and security-design blocks.*

---

## Traceability Matrix — ADR ↔ SRS ↔ SPEC

The matrix below makes each architectural decision auditable
against the SRS requirement it discharges and the SPEC
specification clause that drives it. Every row is the closed-loop
contract the Phase 3 test suite must keep green; every column is
a real artifact in this repository, not invented.

| ADR | Title | SRS FR / NFR | SRS acceptance criteria | SPEC.md clause |
|-----|-------|--------------|-------------------------|----------------|
| ADR-001 | Python 3.11 + FastAPI / uvicorn ASGI runtime | FR-03, FR-04, FR-05, FR-08, FR-10 | FR-08 AC-8.1, FR-08 AC-8.3, FR-04 AC-4.3, NFR-03 AC-03.3 | §1 Overview, §6 Module Map |
| ADR-002 | Four-layer architecture via `.importlinter` | NFR-06 | NFR-06 AC-06.2 | §6 Module Map, §8 #5 |
| ADR-003 | SQLAlchemy 2.x ORM as the only persistence layer | FR-06, NFR-01, NFR-02, NFR-06 | FR-06 AC-6.4, NFR-01 AC-01.3, NFR-02 AC-02.2, NFR-06 AC-06.2 | §6 Module Map, §3 Data Model |
| ADR-004 | Alembic schema migrations (v1 → v2 → v3) | FR-07, NFR-03, NFR-09, NFR-12 | NFR-09 AC-09.5, NFR-03 AC-03.6, NFR-12 AC-12.1 | §3 Data Model |
| ADR-005 | Async subprocess execution (no `shell=True`) | FR-02, FR-08, NFR-02, NFR-03 | FR-02 AC-2.2, FR-08 AC-8.2, FR-08 AC-8.3, NFR-02 AC-02.1, NFR-03 AC-03.5 | §4 API, threat T-03 |
| ADR-006 | API-key auth with SHA-256 + constant-time compare | FR-03, FR-04, NFR-02, NFR-04 | FR-04 AC-4.3, NFR-02 AC-02.3, NFR-04 AC-04.3 | §4 API, threat T-01 |
| ADR-007 | Per-token DB-backed rate-limit bucket | FR-05, FR-06 | FR-05 AC-5.2, FR-05 AC-5.3, FR-06 AC-6.5, threat T-11 | §4 API |
| ADR-008 | RFC 7807 `application/problem+json` error envelope | FR-10, NFR-02, NFR-03, NFR-04 | FR-10 AC-10.5, NFR-02 AC-02.5, NFR-03 AC-03.3, NFR-04 AC-04.2 | §7 Error Contract |
| ADR-009 | Pydantic v2 schemas for request/response validation | FR-10, NFR-05 | NFR-05 AC-05.2 | §4 API |
| ADR-010 | Single auth + scope + rate-limit dependency | FR-04, NFR-06, NFR-11 | FR-04 AC-4.1, FR-04 AC-4.2, FR-04 AC-4.3, NFR-11 AC-11.3 | §6 Module Map |
| ADR-011 | `httpx.AsyncClient(ASGITransport)` for integration tests | NFR-10, NFR-09 | NFR-09 AC-09.4, NFR-10 AC-10.1, NFR-10 AC-10.2, NFR-10 AC-10.3 | §5 Test Strategy |
| ADR-012 | Correlation-ID middleware for end-to-end traceability | FR-10 | FR-10 AC-10.4, threat T-07 | §7 Error Contract |
| ADR-013 | SQLite (dev/test) and PostgreSQL (prod) via the same ORM | NFR-02, NFR-06 | NFR-06 AC-06.2 | §6 Module Map, §3 Data Model |
| ADR-014 | `asyncio.TaskGroup` graceful drain on shutdown | FR-08, NFR-03 | FR-08 AC-8.1, NFR-03 AC-03.3 | §6 Module Map |
| ADR-015 | Redaction regex at every log / error / metrics boundary | NFR-04 | NFR-04 AC-04.1, NFR-04 AC-04.2 | threat T-02 |
| ADR-016 | `make verify-system` as the system verification target | NFR-12 | NFR-12 AC-12.1, NFR-12 AC-12.2 | §5 Test Strategy |
| ADR-017 | Per-FR TDD for high-risk modules | NFR-08, NFR-09 | NFR-08 AC-08.3, NFR-09 (no-skip) | §5 Test Strategy |
| ADR-018 | Dependency & license compliance (NFR-07) | NFR-07 | NFR-07 AC-07.1, NFR-07 AC-07.2, NFR-07 AC-07.3, NFR-07 AC-07.4 | §4 NFR-07, §5.3, §8 #22, R11 |

### Cross-cutting NFRs

Some NFRs span every decision and are not owned by a single ADR;
they are documented here for honesty rather than distributed
across rows where each row would be partly true and partly
aspirational:

- **NFR-07 (dependency & license compliance)** has a real
  owning decision: **ADR-018**. Earlier drafts of this
  matrix had it in the cross-cutting list as a joint product
  of ADR-008 / ADR-012 / ADR-015 — that was wrong; those
  ADRs cover correlation-id and redaction, not license
  compliance. ADR-018 is the only row that cites AC-07.1 …
  AC-07.4.
- **NFR-11 (code quality)**: discharged jointly by ADR-002
  (layer enforcement), ADR-010 (single auth hub keeps handlers
  ≤ 40 lines), and the Phase 3 P5 CC ≤ 10 gate enforced by the
  harness. No single ADR owns it.
- **NFR-05 (API documentation)**: discharged jointly by ADR-008
  (uniform error contract), ADR-009 (Pydantic schemas drive
  OpenAPI), and the Phase 3 OpenAPI export. No single ADR owns
  it.

### How to read this matrix

- The **SRS acceptance criteria** column names the exact
  AC-IDs the Phase 3 test suite must assert. A row without an AC
  reference is a decision that discharges a structural NFR
  (e.g. NFR-06 layer enforcement) rather than a testable AC.
- The **SPEC.md clause** column points to the product
  specification section that originated the requirement. Any
  drift between this matrix and SPEC.md is treated as a
  specification change request (P9 CR), not a silent ADR edit.
- The matrix is updated by hand whenever an ADR is added or
  accepted; it is the audit trail the Agent B review reads on
  every Phase 2 exit.