# Architecture Decision Records (ADR) — taskq-api

> Phase 2 ADR collection. Each decision below is binding for Phase 3+ implementation.
> Source documents: `02-architecture/SAD.md` (architecture), `SPEC.md` (requirements specification), `01-requirements/SRS.md` (SRS, the per-FR requirements specification).

This ADR file is itself an **architectural specification**: each decision below
documents what is binding for Phase 3+ and the SRS FR-IDs / NFR-IDs it satisfies.
The mapping between every ADR and the SRS requirements it implements is captured
in the **ADR↔FR traceability matrix** at the end of this file.

---

## ADR-001: Python 3.11 Runtime

### Status
Accepted

### Context
The service runs as an async ASGI application with subprocess orchestration, structured concurrency (`asyncio.TaskGroup` requires Python 3.11+), and pinned dependencies per NFR-07. The `.venv/bin/python --version` reports **Python 3.11.15**.

### Decision
Pin the runtime to Python 3.11 (3.11.15 in the development venv). Use the CPython reference interpreter; no PyPy, no alternative runtimes.

### Alternatives Considered
- **CPython 3.12** — newer, faster, but breaks `asyncio.TaskGroup` exit semantics used by the runner; SPEC §5.1 names 3.11.
- **CPython 3.10** — lacks `asyncio.TaskGroup` (added in 3.11); would force the runner to manage task lifecycles manually with `asyncio.gather` + ad-hoc cancellation, complicating NFR-03 guarantees.
- **stdlib-only / no third-party deps** — rejected: RFC 7807 rendering, ORM, schema validation, and Alembic migrations all benefit from FastAPI / SQLAlchemy / pydantic / Alembic; rewriting them in pure stdlib would violate Simplicity First.

### Consequences
- Positive: structured concurrency (`TaskGroup`) simplifies the runner; `tomllib` in stdlib for config; stable async semantics.
- Negative: locks the project to one minor version; upgrade requires re-verifying `TaskGroup` + `asyncio.timeout` behavior.

---

## ADR-002: FastAPI as HTTP Framework

### Status
Accepted

### Context
FR-01 / FR-02 require an HTTP API with typed request/response bodies, dependency injection for auth/scope/rate-limit, and OpenAPI exposure. The framework must integrate with the layered architecture (NFR-06) without leaking HTTP concerns into `service/`.

### Decision
Use FastAPI for the HTTP edge layer (`taskq_api.api`). Pydantic models from `taskq_api.models.schemas` double as request/response types. Sub-routers split by concern: `tasks.py`, `health.py`; cross-cutting deps live in `deps.py`.

### Alternatives Considered
- **Starlette (no FastAPI)** — lighter, but loses automatic OpenAPI generation and pydantic-typed route handlers; would force manual schema duplication.
- **Flask** — synchronous, no native ASGI; would require a worker (gunicorn/uvicorn) and a separate async adapter for the runner; higher ceremony for dependency injection.
- **Raw ASGI (`uvicorn` only)** — maximum control but reinvents request validation and routing; violates Simplicity First.

### Consequences
- Positive: typed handlers via pydantic; built-in DI for `Depends(...)`; OpenAPI for free; mature ecosystem.
- Negative: ties `api/` to FastAPI; dependency-injection graph must remain inside `api/` (NFR-06 forbids `api → repository`).

---

## ADR-003: SQLAlchemy 2.x Declarative ORM

### Status
Accepted

### Context
FR-06 / FR-07 require a real RDBMS (SQLite dev, PostgreSQL prod) with N+1-safe queries, transactional boundaries, and Alembic-managed migrations. The repository layer is the **only** layer allowed to `import sqlalchemy` (NFR-06).

### Decision
Use SQLAlchemy 2.x in declarative mode for ORM definitions (`taskq_api.models.orm`) and query construction in `taskq_api.repository.*`. Use `selectinload` / `joinedload` on every list query to eliminate N+1 (NFR-01).

### Alternatives Considered
- **Raw SQL + `sqlite3` / `psycopg`** — minimum dependencies but loses portable query building, eager-loading, and Alembic autogenerate; reinvents what SQLAlchemy already provides.
- **Django ORM** — bundled, but irrelevant: this is not a Django project.
- **SQLAlchemy 1.4 legacy API** — older `Query` interface; 2.x style is the supported path and required by Alembic 1.13+.

### Consequences
- Positive: portable across SQLite ↔ PostgreSQL; eager-loading APIs enforce NFR-01; Alembic autogenerate works against the declarative metadata.
- Negative: 2.x syntax requires `select()`/`Session.execute()` discipline; legacy patterns break the layering contract if imported outside `repository/`.

---

## ADR-004: Alembic for Schema Evolution (Three Reversible Revisions)

### Status
Accepted

### Context
FR-07 requires three schema migrations (`v1_initial`, `v2_tags`, `v3_split_results`) that must round-trip without data loss. SPEC §8 #13 / NFR-09 forbids `op.execute("DROP TABLE ...")` shortcuts.

### Decision
Use Alembic with three revisions:
1. `v1_initial` — `tasks`, `api_keys`, `rate_buckets`
2. `v2_tags` — `tags`, `task_tags`, plus `UNIQUE INDEX tasks.name`
3. `v3_split_results` — `tasks.result_json → task_results` with per-row INSERT before DROP COLUMN

Each revision's `downgrade()` mirrors `upgrade()` byte-for-byte; round-trip invariant is asserted by integration test on a real SQLite file.

### Alternatives Considered
- **Manual SQL files** — no downgrade path, no autogenerate, brittle to drift.
- **Single migration covering v1+v2+v3** — faster to write but loses the granular FR-07 evidence; breaks the per-FR traceability demanded by SPEC §5.
- **Atomic file-rewrite of the DB** — irrelevant: this is a relational store with concurrent transactions; file-level rewrite is not a transactional substitute.

### Consequences
- Positive: per-FR traceability; reversible downgrades; Alembic `alembic_version` table checked by `/readyz` (FR-09).
- Negative: per-row data migration in `v3_split_results` requires O(rows) time; integration test must run against a real SQLite, not a mock.

---

## ADR-005: Pydantic v2 for Request/Response Validation

### Status
Accepted

### Context
T-01 (security design) requires that malformed task payloads are rejected at the HTTP edge. FastAPI uses pydantic natively; reusing pydantic models across `models/` and `api/` keeps one source of truth.

### Decision
Define all request/response types as pydantic v2 models in `taskq_api.models.schemas` (`TaskCreate`, `TaskOut`, `RunOut`, `MetricOut`). Import them in `taskq_api.api.tasks` and `taskq_api.api.health`. Each pydantic class carries `[FR-XX]` docstring (NFR-05).

### Alternatives Considered
- **Marshmallow** — independent of FastAPI but adds a parallel validation path; doubles the type system.
- **Manual dict parsing** — saves a dependency but loses validation guarantees and OpenAPI types; violates NFR-02.

### Consequences
- Positive: one type system; rejected fields bubble up as `ValidationProblem` (RFC 7807, FR-10); OpenAPI types are accurate.
- Negative: pydantic v2's `model_dump()` vs `dict()` distinction requires discipline; FastAPI handles it automatically inside route handlers.

---

## ADR-006: Layered Architecture (api → service → repository → models + 2 independence modules)

### Status
Accepted

### Context
NFR-06 forbids circular dependencies and restricts `sqlalchemy` imports to `repository/`. The design must be mechanically enforceable via `import-linter` (`.importlinter`).

### Decision
Adopt a four-layer spine with two independence modules:

```
api  →  service  →  repository  →  models
                 ↘   config / errors (any layer may import)
```

- `taskq_api.config` — stdlib-only env loader (`os`, `dataclasses`).
- `taskq_api.errors` — FastAPI-only RFC 7807 renderer.
- `taskq_api.repository` — the only layer allowed to import `sqlalchemy`.
- `taskq_api.api` may NOT import `taskq_api.repository` directly (must go through `service/`).

### Alternatives Considered
- **Flat module layout** — simpler at first, but cannot express layering; `import-linter` cannot detect circularity in a flat tree.
- **Hexagonal / ports-and-adapters** — heavier ceremony; the SPEC does not require swapping adapters at runtime; would be over-engineering.
- **Single `core/` blob** — hides dependency direction; fails NFR-06 enforcement.

### Consequences
- Positive: mechanically enforced layering; each layer is independently testable; clear ownership of `sqlalchemy`.
- Negative: cross-layer refactors require explicit new imports in `__init__.py`; violation is loud (CI grep gate).

---

## ADR-007: asyncio.create_subprocess_exec + TaskGroup for Task Execution (not ThreadPoolExecutor)

### Status
Accepted

### Context
FR-02 / FR-08 require async subprocess execution with strict timeouts, graceful drain on shutdown, and `kill()`+`wait()` semantics on timeout. The runner owns the subprocess lifecycle; cancellation must propagate (NFR-03).

### Decision
Use `asyncio.create_subprocess_exec(*shlex.split(command))` inside an `asyncio.TaskGroup` (singleton, owned by `service.runner`). On timeout (`asyncio.wait_for(..., TASKQ_TASK_TIMEOUT)`), call `proc.kill()` then `await proc.wait()` before raising. `asyncio.CancelledError` is re-raised unmodified — never caught by `except Exception`.

### Alternatives Considered
- **`concurrent.futures.ThreadPoolExecutor` + `subprocess.run`** — simpler API but blocks the event loop on each `run()`, defeating the ASGI edge's concurrency; would force a thread pool sized by `TASKQ_TASK_TIMEOUT` × max QPS, which is wasteful.
- **`subprocess.Popen` in a thread per call** — same blocking problem; manual lifecycle management.
- **`asyncio.create_subprocess_shell` (`shell=True`)** — easier command parsing but allows metacharacter interpretation; NFR-02 grep-gates `shell=True` to 0 hits. **Rejected.**
- **Circuit-breaker around the runner** — tempting for FR-08 graceful drain, but the runner already isolates failures via `TaskGroup` cancellation; a circuit breaker would only add state for state-without-failure-mode (no upstream caller to protect). **Rejected** as speculative.

### Consequences
- Positive: non-blocking subprocess I/O; structured concurrency catches background-task exceptions; `kill()+wait()` orphan-process guard is testable.
- Negative: requires `shlex.split(cmd)` discipline; one shell-quoting mistake bypasses the exec-safety guarantee (mitigated by NFR-02 grep gate).

---

## ADR-008: Single Transaction + Row-Level Lock for Rate Limiting (not a circuit breaker)

### Status
Accepted

### Context
FR-05 requires cross-worker rate limiting. Multiple uvicorn workers must agree on per-key token counts. The naive in-memory token bucket fails the moment a second worker starts.

### Decision
Implement the token bucket in a DB table `rate_buckets`. On each request, `rate_repo.lock_bucket(key_id)` runs `SELECT ... FOR UPDATE` (SQLite: `BEGIN IMMEDIATE`) inside a single `unit_of_work()` transaction; `compute_refill(now - row.updated_at)` is added; one UPDATE commits; the lock releases. Over-limit returns 429 + `Retry-After`.

### Alternatives Considered
- **In-memory token bucket per worker** — fails cross-worker by construction; cannot satisfy FR-05 with N>1 workers.
- **Redis with `INCR` + `EXPIRE`** — adds a runtime dependency; SPEC §5.1 has no Redis; NFR-07 forbids unlisted deps without justification.
- **Circuit breaker in front of the rate-limit call** — would shed load, but rate limiting **is** the shedding mechanism; stacking them adds state without protecting a downstream service that doesn't exist here.

### Consequences
- Positive: correct under multi-worker; transactional; `/v1/metrics` exposes `last_decision='reject'` counts.
- Negative: each request takes a row lock; throughput bounded by DB write rate (acceptable per NFR-01 p95 < 30 ms target on local SQLite).

---

## ADR-009: SHA-256 + hmac.compare_digest for API Key Storage

### Status
Accepted

### Context
T-02 requires that the API key plaintext is never stored at rest and that comparison is constant-time. NFR-04 forbids logging plaintext.

### Decision
Hash the key with SHA-256 at creation; persist `key_hash` in `api_keys`. Verify with `hmac.compare_digest(stored_hash, sha256(presented_key))`. Revoked keys (`revoked_at IS NOT NULL`) are rejected before any scope check.

### Alternatives Considered
- **bcrypt / argon2 for the key** — designed for low-entropy passwords; API keys are high-entropy (~256 bits), so SHA-256 is sufficient and cheaper. **Rejected** to avoid an extra dependency (NFR-07).
- **Plaintext with DB ACL** — defeats T-11 (operator CLI cannot leak what isn't stored).
- **HMAC with a server-side pepper** — slightly stronger, but the SPEC does not require it; would complicate the CLI (`key create`) without an explicit threat.

### Consequences
- Positive: constant-time compare; no plaintext at rest; cheap verification.
- Negative: SHA-256 is one-way — once a key is lost, recovery is impossible (intentional; matches T-11 mitigation).

---

## ADR-010: Single Dependency Module (`api/deps.py`) for Auth/Scope/Rate-Limit

### Status
Accepted

### Context
FR-04 requires that every `/v1/*` route enforces scope through one mechanism. T-03 demands that 403 is returned **before** any resource lookup, so authorization checks cannot be skipped by a handler that forgets to call them.

### Decision
All `/v1/*` routes use `Depends(get_current_key)`, `Depends(require_scope(...))`, `Depends(enforce_rate_limit)` from `taskq_api.api.deps`. The handler body itself never touches `key_repo` or `scope_allows` — these are dependency-injected.

### Alternatives Considered
- **Decorators on each route** — spread scope logic across files; easy to forget on a new route; harder to test in isolation.
- **Middleware** — runs before route resolution; cannot see path-level scope requirements (e.g. `tasks:write` vs `tasks:read`); would need per-route config anyway.

### Consequences
- Positive: one source of truth; impossible to ship a route without auth/scope; 403-before-lookup is structural.
- Negative: `deps.py` is a hub (CRG cohesion); must stay ≤400 lines (NFR-11) and avoid becoming a god-module.

---

## ADR-011: RFC 7807 `application/problem+json` for All Errors

### Status
Accepted

### Context
FR-10 requires a uniform error contract across all non-2xx responses. T-05 forbids leaking the DB password through any error body.

### Decision
Define `ProblemDetail`, `problem_response(...)`, and typed exception classes (`ValidationProblem`, `AuthProblem`, `ForbiddenProblem`, `NotFoundProblem`, `ConflictProblem`, `RateLimitedProblem`, `NotReadyProblem`, `InternalProblem`) in `taskq_api.errors`. Every handler/middleware catches these and renders via `problem_response`. The `detail` field is whitelisted per exception type — never the raw user input or exception message. Every response carries `correlation_id` (also echoed in `X-Correlation-Id`).

### Alternatives Considered
- **Plain JSON `{"error": "..."}`** — RFC-7807-incompatible; no `type` URI; weaker tooling support.
- **Status-code-only responses (no body)** — breaks clients that need machine-readable error codes.
- **HTTPException with FastAPI's default renderer** — leaks internal details; no `correlation_id`; no `type` URI.

### Consequences
- Positive: uniform contract; OpenAPI documents each problem type; redaction filter in `errors` covers T-05.
- Negative: every new error type needs a class and a registered handler; mitigated by the small enum-like set of problems.

---

## ADR-012: `unit_of_work()` Context Manager for All Transactional Boundaries

### Status
Accepted

### Context
NFR-03 requires explicit commit/rollback boundaries. Bare `session.commit()` calls scattered across `service/` make rollback semantics inconsistent.

### Decision
All DB access flows through `unit_of_work()` in `taskq_api.repository.session`. The context manager commits on success, rolls back on exception, and closes the session. `rate_repo` uses it to bundle `SELECT FOR UPDATE` + UPDATE in one transaction (FR-05, ADR-008).

### Alternatives Considered
- **Manual `try/except/finally`** — repetitive and easy to forget; the lint rule "no bare `except: pass`" doesn't catch the asymmetry.
- **SQLAlchemy 2.x `begin()` context manager** — viable, but we wrap it in our own `unit_of_work()` to enforce project-specific defaults (naming, event listeners for NFR-01 SQL-count assertion).
- **Atomic file-rewrite of the DB on each request** — irrelevant: SQLAlchemy already gives us per-statement atomicity within a transaction.

### Consequences
- Positive: one entry point for transactions; SQL-count event listener attaches in one place; rollback is automatic.
- Negative: nested `unit_of_work()` calls must use sub-transactions explicitly; documented in `session.py`.

---

## ADR-013: import-linter for Architecture Enforcement (with CI Grep Gate)

### Status
Accepted

### Context
NFR-06 requires mechanical enforcement of layering and the `sqlalchemy`-only-in-`repository/` rule. Hand-written lint comments are not enforceable.

### Decision
Declare layer contracts in `.importlinter`:
- `api > service > repository > models` (no upward imports)
- `config` and `errors` are independence modules (no upward or downward constraints)
- `sqlalchemy` must not appear in `api/`, `service/`, `models/`

CI runs `lint-imports` (exits 0 required) and a `grep -rE '^(import|from) sqlalchemy' taskq_api/api taskq_api/service taskq_api/models` that must return 0 hits.

### Alternatives Considered
- **Custom AST checker in `pre-commit`** — works but requires re-implementing the layering rules; `import-linter` already does this.
- **Code review only** — unenforced; drifts within a sprint.
- **`pyright` / `mypy` strict mode** — catches types, not architectural direction.

### Consequences
- Positive: violations fail CI; layering is part of the contract.
- Negative: `import-linter` is a third-party dep; pinned in `requirements.lock` per NFR-07.

---

## ADR-014: CRG Community Design (hub-and-spoke + linear pipeline)

### Status
Accepted

### Context
The Phase 3+ CRG score depends on community cohesion ≥ 0.3. Each directory becomes one community. The risk is "isolated file" dilution — a directory of N files with zero cross-imports produces cohesion = 0.

### Decision
For every source directory, designate a hub module imported by ≥70% of siblings and called from function bodies (not just module level):
- `taskq_api.repository/__init__.py` — re-exports all repo modules so `service/` calls one hub.
- `taskq_api.models/schemas.py` — hub for `models/orm.py` and tests.
- `taskq_api.service/auth.py` — hub for `runner.py`, `tasks.py`, `ratelimit.py` (each calls `auth.scope_allows` from at least one function body).
- `taskq_api.api/health.py` — hub for `tasks.py` and `deps.py`.
- `taskq_api.app` and `__main__` — sit next to `api/health.py` and call `config.validate_config()` / `errors.problem_response` builders from function bodies.

Each function body must call a sibling hub function — module-level calls alone are insufficient (CRG counts edges per `(caller_node, callee_node)` pair).

### Alternatives Considered
- **Flat `src/` with 10+ files** — CRG's Leiden algorithm produces unpredictable communities; risk of falling below 0.3.
- **Single god-module per directory** — file ≤ 400 lines (NFR-11); god-module exceeds the cap.
- **Circuit-breaker hub across runners** — would create a new cross-cutting hub that no other module naturally depends on; speculative coupling.

### Consequences
- Positive: predicted CRG cohesion ≥ 0.3 across all communities; edge budget satisfied per SAD §2.1.
- Negative: every new sibling must call the hub from a function body — discipline enforced by code review (CRG score is the visible signal).

---

## ADR-015: Makefile `verify-system` Target for Phase 3 Gate 2

### Status
Accepted

### Context
NFR-12 requires `make verify-system` to chain the full lifecycle: migrate up → test → service start + smoke → migrate down → migrate up. The target name is fixed (harness calls it as-is).

### Decision
Define `verify-system` in the project `Makefile`:
```
verify-system:
    alembic upgrade head
    pytest -q
    uvicorn taskq_api.app:app &  (smoke /healthz, /readyz)
    alembic downgrade base
    alembic upgrade head
```
Exit 0 with `verify-system: PASS` on stdout. Failure on any step exits non-zero.

### Alternatives Considered
- **`harness_cli.py verify-system` instead of `make`** — the harness explicitly invokes `make verify-system`; renaming the entry point breaks Gate 2.
- **`tox` / `nox`** — viable runners but the harness calls `make` directly; an inner `tox -e verify` would still be wrapped in a `make` target.
- **CI-only script (`.github/workflows/verify.yml`)** — not invocable locally; the harness has no GitHub Actions runner.

### Consequences
- Positive: local reproduction of Gate 2; one command for the full lifecycle.
- Negative: locks the project to GNU Make (acceptable; SPEC §5.1 mandates it).

---

## ADR-016: Connection Pooling via SQLAlchemy Engine (no separate pool layer)

### Status
Accepted

### Context
NFR-01 p95 targets require predictable connection acquisition under multi-worker uvicorn. The repository layer owns the engine.

### Decision
Create the SQLAlchemy engine once per process via `taskq_api.repository.session.engine()`. Connection pool defaults are sufficient (SQLite: `StaticPool`; PostgreSQL: `QueuePool` with size from env). The session factory is module-level; `unit_of_work()` borrows from it.

### Alternatives Considered
- **Hand-rolled connection pool (`threading.Lock` + queue)** — reinventing SQLAlchemy's pool; would lose `pool_pre_ping` and connection-recycle semantics.
- **A circuit breaker between request and DB** — would shed load on DB failure, but the rate-limiter (ADR-008) already sheds by key, not by DB health; DB-down is surfaced by `/readyz` (FR-09) and the load balancer, not per-request.

### Consequences
- Positive: one engine per process; SQLAlchemy handles `pool_pre_ping` for stale connections.
- Negative: tuning knobs (`pool_size`, `max_overflow`) are env-driven; mismatched values cause latency spikes — surfaced in NFR-01 benchmark.

---

## ADR-017: Patterns Explicitly Considered and Rejected

### Status
Accepted (as a record of negative decisions)

### Context
The architecture must document patterns that were considered and not adopted, so future contributors do not re-litigate them.

### Decisions (rejected alternatives)
1. **`concurrent.futures.ThreadPoolExecutor` for task execution** — rejected in ADR-007; blocking per-call defeats ASGI concurrency.
2. **`subprocess` `shell=True`** — rejected in ADR-007; NFR-02 grep-gates `shell=True` to 0 hits.
3. **Circuit breaker around the runner / DB / rate-limit** — rejected in ADR-007, ADR-008, ADR-016; no upstream caller to protect from cascading failure inside this service.
4. **Atomic file-write of the SQLite DB on each request** — rejected; SQLite transactions already provide per-statement atomicity; file-level rewrite would defeat WAL mode and concurrent readers.
5. **In-memory token bucket (per worker)** — rejected in ADR-008; fails cross-worker (FR-05).
6. **bcrypt/argon2 for API keys** — rejected in ADR-009; high-entropy keys do not need a KDF; adds a dep (NFR-07).
7. **`shell=True` parser shortcut in `runner.py`** — rejected; even one occurrence bypasses T-07's mitigation.
8. **Decorator-based scope check on routes** — rejected in ADR-010; spread logic and easy to forget.
9. **FastAPI `HTTPException` default renderer** — rejected in ADR-011; no RFC 7807 / `correlation_id` / T-05 redaction.
10. **`mypy --strict` as the layering guard** — rejected in ADR-013; `import-linter` + grep gate cover architectural direction; `mypy` covers types.

### Consequences
- Positive: future contributors see the rejected set; less re-litigation; ADR file is the canonical record.
- Negative: rejected decisions still need maintenance if their assumptions change (e.g. if a new threat introduces a circuit-breaker requirement, ADR-017 must be revisited and a new ADR added).

---

## ADR↔FR Traceability Matrix

The traceability matrix below maps every accepted decision in this file to the
SRS FR/NFR requirements (and SPEC §5 / §8 acceptance items) it implements. The
SRS is the source of truth for FR/NFR IDs; this matrix is the contract that
each ADR is justified by at least one requirement, and each requirement is
satisfied by at least one ADR.

| ADR      | Satisfies (SRS FR-/NFR-IDs) | SPEC reference         | Decision (summary) |
|----------|-----------------------------|------------------------|--------------------|
| ADR-001  | NFR-07                      | SPEC §5.1 (Python 3.11) | Pin Python 3.11 runtime |
| ADR-002  | FR-01, FR-02                | SPEC §3, §4            | FastAPI at the HTTP edge |
| ADR-003  | FR-06, NFR-01               | SPEC §6                | SQLAlchemy 2.x declarative ORM |
| ADR-004  | FR-07, NFR-09               | SPEC §5.2, §8 #13      | Alembic three reversible revisions |
| ADR-005  | FR-10, NFR-02, NFR-05       | SPEC §7                | Pydantic v2 request/response validation |
| ADR-006  | NFR-06                      | SPEC §6                | Layered architecture with two independence modules |
| ADR-007  | FR-02, FR-08, NFR-02, NFR-03| SPEC §3, §8 #25        | `asyncio.create_subprocess_exec` + `TaskGroup` |
| ADR-008  | FR-05, NFR-01               | SPEC §4                | DB row-level token bucket for rate limit |
| ADR-009  | NFR-04                      | SPEC §4                | SHA-256 + `hmac.compare_digest` for API keys |
| ADR-010  | FR-04, NFR-11               | SPEC §4, §11           | Single `api/deps.py` for auth/scope/rate-limit (≤400 lines per NFR-11) |
| ADR-011  | FR-10                       | SPEC §7                | RFC 7807 `application/problem+json` errors |
| ADR-012  | NFR-03                      | SPEC §6                | `unit_of_work()` context manager for transactions |
| ADR-013  | NFR-06                      | SPEC §6                | `import-linter` + CI grep gate |
| ADR-014  | NFR-10 (testability), NFR-11| SPEC §6, §8, §11       | CRG community design (hub-and-spoke); god-module ban = NFR-11 |
| ADR-015  | NFR-12                      | SPEC §8                | `make verify-system` target |
| ADR-016  | NFR-01                      | SPEC §6                | SQLAlchemy engine connection pool |
| ADR-017  | (cross-cutting)             | SPEC §7, §9            | Record of rejected alternatives |
| —        | NFR-08                      | SPEC §4, §8 #24        | **No architectural owner**; mutation score ≥ 70 is a Phase-4 test concern discharged by `mutmut` over `service/` + `repository/`. |
| —        | NFR-09                      | SPEC §4, §8 #1, #12    | **No architectural owner**; zero-skip rule + real-DB migration test is a Phase-4 test methodology concern. |

**Satisfies column convention:** IDs are taken verbatim from `01-requirements/SRS.md`.
Where an ADR spans multiple requirements, each ID is listed. The SRS is the
authoritative source for these IDs; this matrix is the architectural specification
that maps each binding decision to the requirement it discharges.

**Rejected-pattern cross-reference:** the patterns collected in ADR-017 are
preserved as the "rejected specification" so that re-litigation in code review
is unnecessary — every code-rejected pattern in Phase 3 can be traced here and
back to a SRS requirement that prohibits it (e.g. ADR-017 #4 = atomic file-rewrite
of SQLite is forbidden because NFR-03 requires transactional boundaries; ADR-017
#6 = bcrypt/argon2 is forbidden because NFR-07 limits dependencies).

### Cross-cutting NFRs

The following NFRs are satisfied structurally across multiple ADRs rather than
by a single owning decision:

- **NFR-02 (input validation & secure-by-default)** — satisfied jointly by
  ADR-005 (pydantic validation), ADR-007 (`shell=False`), ADR-009 (constant-time
  compare), ADR-011 (`detail`-whitelisted error bodies, T-05 redaction), and
  ADR-013 (grep gate forbidding `shell=True`).
- **NFR-06 (mechanical layering)** — satisfied jointly by ADR-006 (layer
  topology) and ADR-013 (`import-linter` + grep gate); the rule is the
  combination, not either alone.
- **NFR-10 (testability)** — satisfied jointly by ADR-006 (independence
  modules), ADR-014 (CRG community design with importable hubs), and the SRS
  specification of `httpx.ASGITransport` integration tests.

### NFRs without an architectural owner

The following NFRs are non-architectural concerns: they are discharged by Phase 4
test methodology, not by an architectural decision. They are listed here so
the traceability table is complete and so reviewers do not expect an ADR-XXX
row for them.

- **NFR-08 (mutation testing, `mutmut` ≥ 70)** — no architectural decision
  owns mutation score. It is a property of the test suite and is verified by
  running `mutmut run` / `mutmut results` over `service/` + `repository/` per
  the SRS specification of NFR-08. Scope-limited to those two layers in
  `harness_config.json`. Listed here for traceability completeness only.
- **NFR-11 (readability: MI ≥ 80, ≤400 lines/file)** — partially architectural
  (ADR-010 caps `api/deps.py` ≤ 400 lines; ADR-014 forbids god-modules per
  file); the remaining scoring is enforced by Phase 4 metric tooling. The
  architectural contribution is captured in the ADR-010 and ADR-014 rows above.
