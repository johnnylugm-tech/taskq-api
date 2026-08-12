# Software Architecture Document (SAD) — taskq-api

> Phase 2 deliverable — Round 1 architecture for the harness-methodology 2nd-round
> test bed. Single source of architectural truth. Implementation MUST follow SPEC.md;
> this document elaborates HOW the SPEC's FRs are realized in the module tree.

---

## 1. Architecture Overview

### 1.1 System Identity

`taskq-api` is a Python 3.11 ASGI service that exposes a task-queue over HTTP. It
ingests task definitions, persists them to a relational database (SQLite in dev/test,
PostgreSQL in prod), executes them asynchronously as subprocesses with strict
timeouts, and returns results through a uniform RFC 7807 error contract.

The design is **layered with strict directional dependencies**:

```
api  →  service  →  repository  →  models
                 ↘   config / errors (independence — importable by any layer)
```

`api` (HTTP entry) calls `service` (business rules); `service` calls `repository`
(the ONLY layer that may `import sqlalchemy`); `repository` operates on `models`.
The `config` and `errors` modules are **independence** modules — they sit outside
the four-layer spine and may be imported by any layer without violating direction.

This contract is enforced mechanically by `import-linter` (`.importlinter`) at
NFR-06.

### 1.2 Architectural Drivers

| Driver | Source | Architectural Consequence |
|--------|--------|---------------------------|
| HTTP API | FR-01, FR-02 | FastAPI router tree under `api/`; pydantic request/response models under `models/` |
| Real RDBMS | FR-06, FR-07 | SQLAlchemy declarative models + Alembic three-step schema evolution with reversible downgrades |
| Auth/Authz | FR-03, FR-04 | Single-dependency scope gate in `api/deps.py`; hashed-key repo behind `repository/key_repo.py` |
| Async subprocess execution | FR-02, FR-08 | `service/runner.py` owns `asyncio.TaskGroup`, graceful drain, `kill()`+`wait()` semantics |
| Rate limiting (cross-worker) | FR-05 | Token bucket in DB table `rate_buckets` with row-level lock inside one transaction |
| Uniform error contract | FR-10 | `errors.py` independence module renders RFC 7807 `application/problem+json` |

### 1.3 System Verification Target

> **Phase 3 Gate 2 Requirement**: The harness executes `make verify-system` at Gate 2.
> If it exits with a non-zero status Gate 2 fails. Add a `verify-system` target to your
> project `Makefile` that assembles and exercises the system end-to-end (e.g. runs your
> integration tests or smoke-test suite). The target name is fixed — the harness always
> calls `make verify-system`.
**Makefile target**: `verify-system` (defined in SPEC.md NFR-12; runs alembic upgrade
head → full test suite → service start + `/healthz`/`/readyz` smoke → alembic
downgrade base → alembic upgrade head, exiting 0 with `verify-system: PASS` on stdout).

---

## 2. Module Design

### 2.1 Directory Structure Design Principles

> **CRG Architecture Scoring**: Phase 3+ judges your code's community cohesion via
> the Code Review Graph (CRG).  CRG groups files by **directory** — each directory
> is one community.  The architecture score is the fraction of communities that are
> "healthy" (internal edge density ≥ 0.3 AND size ≤ 50 nodes).
>
> **CRG scoring formula**: Each community's cohesion = internal_edges / (internal_edges + external_edges).
> External edges = calls to libraries (stdlib, frameworks) + calls to other communities.
> Internal edge dilution is the primary risk — entry points (CLI, main.py) import many libraries,
> producing external edges with no offsetting internal edges unless they also call sibling modules.
> The fix is **not** to reduce library imports — it is to ensure every function body also calls at least one
> sibling within the same directory.
>
> **Required edge budget**: To reach cohesion ≥ 0.3 with E external edges, you need
> I ≥ ceil(0.4286 × E) internal edges. Each function-body call to a hub function = 1 internal edge.
> Module-level calls create 1 edge per file, but per-function-body calls multiply the count.
> Example: 48 external edges → need ≥21 internal edges. With 5 sibling files each having
> 4 function bodies calling 2 hub functions → 40 internal edges — safely above threshold.

**Design for high cohesion from the start — 6 Universal CRG Design Principles:**

**Principle 1 — Use subdirectories to control CRG community boundaries.** CRG assigns one community per directory. If you dump 10+ files into a flat `src/`, CRG's Leiden algorithm freely splits them into unpredictable communities — some will likely fall below the 0.3 cohesion threshold. Explicit subdirectories (`src/api/`, `src/core/`, `src/infrastructure/`) each become one predictable community. Aim for 3-6 source directories total (excluding tests). Fewer than 3 → oversized single community; more than 6 → too many communities to keep all above 0.3.

**Principle 2 — Every directory needs a hub module (≥2 functions for 4+ siblings).** Each directory with ≥2 files must have a shared module (`utils.py`, `common.py`, `helpers.py`) that ≥70% of sibling files import and call via standalone function calls: `result = hub.fn(...)`. This creates cross-file internal edges. Pure library-utility files that no sibling calls produce zero internal edges — they only dilute the community.

For directories with ≥4 sibling files, **one hub function is rarely enough** — a single function called from 5 files produces ~5 edges, which may not offset ~40+ external edges. Use **≥2 hub functions** so each sibling can call both from multiple function bodies, multiplying internal edge count. The tts-new infrastructure directory (5 siblings, 48 external edges) required 2 hub functions (`validate_config` + `get_config_snapshot`) called from every function body to reach ~32 internal edges and pass 0.3.

Exception: directories that form a linear processing pipeline (A→B→C) where each file calls the next in chain.

**Principle 3 — Entry points must live inside a hub directory.** Entry-point modules (CLI, `main.py`, `app.py`, daemon) unavoidably import many external libraries — httpx, FastAPI, argparse, asyncio, etc. Each external import adds an external edge. If the entry point sits alone at the project root (e.g. `src/cli.py`), those external edges dominate and cohesion drops below 0.3. Place entry points inside a directory that also contains a hub module — the entry point calls the hub (internal edges) to compensate for its external edges.

**Principle 4 — Every function body must call a hub function (not just module-level).** A file that is never imported or called by any other file in its directory contributes only external edges (its own imports) and zero internal edges — pure dilution. For each file in your design, verify it is either: (a) the hub module itself, (b) called by the hub, or (c) calls the hub. Files that fail this check should be merged into another file or directory.

Critically, **module-level calls alone are insufficient**. A module-level `_ = validate_config()` creates 1 internal edge per file regardless of how many functions it has. CRG counts edges per (caller_node, callee_node) pair — each function body that calls the hub creates a separate edge. To accumulate enough internal edges (see edge budget above), the hub function must be called **from every accessible function body** in each sibling file, not just at module level. Example: a 5-sibling directory needs ~21 internal edges; 5 module-level calls + 5×4 function-body calls = 25 edges.

**Principle 5 — Respect CRG edge-detection limits.** CRG uses Tree-sitter AST parsing and detects cross-file function calls resolved through imports. These limitations are cross-language:
- Calls between functions in the **same** file — NOT detected (zero cohesion contribution)
- `self.method()` calls inside a class — DETECTED (class hierarchy contributes edges)
- `import sibling` → `sibling.fn()` — DETECTED (cross-file import resolved)
- `result = hub.fn(...)` then `log.info(..., extra=result)` — DETECTED (standalone assignment)
- `log.info(..., extra=hub.fn(...))` — INCONSISTENTLY detected (nested arg position)
- Calls through imports at runtime (lazy imports in `__getattr__`, `__init__.py` re-exports) — may be missed if not statically resolvable

**Principle 6 — Size cap: communities stay under 50 nodes.** CRG marks any community with >50 nodes as unhealthy regardless of cohesion. A node ≈ one function or class in a file. If your directory design would produce >50 nodes (roughly 4-6 modules with 8-12 functions each), split into subdirectories. Unlike Principles 1-5, this can be relaxed slightly — the cap is 50, not 30 — so this is rarely the binding constraint unless you have large god-modules.

| Quick reference | check |
|----------------|-------|
| Source directories count? | 3-6 |
| Each dir has a hub file? | Yes |
| Hub has ≥2 functions if ≥4 sibling files? | Yes |
| Entry points inside a hub dir? | Yes |
| Each function body calls a hub function? | Yes (not just module-level) |
| Cross-file calls use standalone assignment? | Yes |
| Community size ≤ 50 nodes? | Yes |
| Edge budget: I ≥ 0.4286 × E? | Yes |

**Anti-patterns that produce low scores:**

```
❌ src/__init__.py, src/main.py, src/models.py, src/cli.py, src/audio.py
   → 5 isolated files in flat src/, zero cross-imports → cohesion=0.0

❌ src/cli.py  (imports httpx, argparse, asyncio — all external, no internal sibling calls)
   → pure external edges, no compensation → cohesion near 0

❌ tests/test_fr01.py, tests/test_fr02.py, ... tests/test_fr08.py
   → 80 nodes in one dir, no internal edges → oversized + zero cohesion

✅ src/api/{cli,main,speech,utils}.py with utils imported by all siblings → hub-and-spoke
✅ src/engines/{synthesis,splitter,parser}.py with synthesis calling both → pipeline chain
✅ src/infrastructure/{circuit,health,config,models}.py → shared domain layer
```

### 2.2 `taskq_api.config` — independence (env loader)

| Attribute | Value |
|-----------|-------|
| Responsibility | Read all `TASKQ_*` environment variables with defaults from SPEC §5.1; expose frozen settings |
| External Interface | `get_settings() -> Settings` (immutable singleton), `validate_config() -> None` |
| Dependencies | stdlib only (`os`, `dataclasses`) — no FastAPI / SQLAlchemy imports |
| FR coverage | supporting (every FR reads settings) |

#### Logical Constraints
- All 12 env vars from SPEC §5.1 declared with their default values
- Module is `frozen=True` dataclass; mutation raises
- Never logs the value of `TASKQ_DB_URL` (password leak — NFR-04)

### 2.3 `taskq_api.errors` — independence (RFC 7807)

| Attribute | Value |
|-----------|-------|
| Responsibility | Single source of RFC 7807 `application/problem+json` rendering; define `type` URIs from SPEC §7 |
| External Interface | `ProblemDetail`, `problem_response(...)`, exception classes `ValidationProblem`/`AuthProblem`/`ForbiddenProblem`/`NotFoundProblem`/`ConflictProblem`/`RateLimitedProblem`/`NotReadyProblem`/`InternalProblem` |
| Dependencies | FastAPI only (`Response`, `JSONResponse`) — no SQLAlchemy |
| FR coverage | FR-10 (and every FR that returns a non-2xx code uses this) |

#### Logical Constraints
- `detail` field is whitelisted by exception type — never accepts raw user-supplied strings or exception messages (NFR-02, R6)
- Every response includes `correlation_id` (also echoed in `X-Correlation-Id` header)
- `asyncio.CancelledError` is **not** converted to 500 here — it propagates past this module (NFR-03)

### 2.4 `taskq_api.models` — L1 (schema + ORM)

| Attribute | Value |
|-----------|-------|
| Responsibility | SQLAlchemy 2.x declarative table definitions + pydantic request/response models |
| External Interface | ORM classes (`Task`, `TaskResult`, `ApiKey`, `RateBucket`, `Tag`, `TaskTag`); pydantic classes (`TaskCreate`, `TaskOut`, `RunOut`, `MetricOut`) |
| Dependencies | SQLAlchemy, pydantic — does NOT call `service` or `api` |
| FR coverage | FR-01 (schema), FR-02 (`TaskResult`), FR-03 (`ApiKey`), FR-05 (`RateBucket`), FR-07 (all five tables across v1–v3) |

#### Logical Constraints
- ORM tables MUST match SPEC §5.2 column list exactly
- v1 → v2 → v3 evolution is owned by Alembic revisions; `models/orm.py` declares the **head** state only
- Pydantic models contain `[FR-XX]` docstring references (NFR-05)
- `models/schemas.py` is the CRG hub for this community — both `models/orm.py` and tests reference it

### 2.5 `taskq_api.repository` — L2 (data access)

| Attribute | Value |
|-----------|-------|
| Responsibility | The ONLY layer that may `import sqlalchemy`. Owns `Session` lifecycle, transaction context manager, and one repo per aggregate |
| External Interface | `unit_of_work()` context manager, `task_repo`, `key_repo`, `rate_repo` modules exposing CRUD + lookup functions |
| Dependencies | `taskq_api.models`, SQLAlchemy; NEVER `taskq_api.api` or `taskq_api.service` |
| FR coverage | FR-01 (task_repo), FR-03 (key_repo), FR-05 (rate_repo), FR-06 (session + UoW + N+1-safe queries), FR-07 (all migrations), FR-09 (readyz DB probe) |

#### Logical Constraints
- `session.py` exports `unit_of_work()` — every commit/rollback boundary goes through it (FR-06)
- All queries use `selectinload`/`joinedload` for relations; bare lazy loads are forbidden (NFR-01)
- `rate_repo` performs its bucket update + row-level lock in **one** transaction (FR-05, R12)
- The `__init__.py` re-exports the repo modules so `service/*` imports them as a single hub

### 2.6 `taskq_api.service` — L3 (business rules)

| Attribute | Value |
|-----------|-------|
| Responsibility | Orchestrate business operations: validate input, call repositories, schedule async execution. Owns the `asyncio.TaskGroup` runner |
| External Interface | `tasks.create_or_409`, `tasks.get_or_404`, `tasks.list_paginated`, `tasks.schedule_run`, `tasks.list_runs`; `runner.run_subprocess`, `runner.graceful_drain`, `runner.spawn`; `auth.hash_key`, `auth.verify_key`, `auth.revoke`; `ratelimit.consume` |
| Dependencies | `taskq_api.repository`, `taskq_api.models`, `taskq_api.config`; NEVER `taskq_api.api` |
| FR coverage | FR-01 (`tasks`), FR-02 (`runner.spawn` + result persistence), FR-03 (`auth.hash_key` / `verify_key`), FR-04 (`auth.scope_allows`), FR-05 (`ratelimit.consume`), FR-06 (business validation), FR-08 (`runner.graceful_drain` + `TaskGroup`) |

#### Logical Constraints
- No `import sqlalchemy` (NFR-06 forbidden contract)
- Subprocess execution uses `asyncio.create_subprocess_exec` with `shlex.split(cmd)`; `shell=True` is forbidden at import time and grep-gated (NFR-02)
- On timeout: `process.kill()` then `await process.wait()` before raising (FR-08, R8)
- `asyncio.CancelledError` is re-raised unmodified — never wrapped in `except Exception` (NFR-03, R7)
- `auth.py` is the hub for this community; `runner.py`, `tasks.py`, `ratelimit.py` all call `auth.scope_allows(...)` in at least one function body to seed internal edges

### 2.7 `taskq_api.api` — L4 (HTTP edge)

| Attribute | Value |
|-----------|-------|
| Responsibility | FastAPI router registration, request/response serialization, dependency wiring for auth/scope/rate-limit |
| External Interface | `router` (FastAPI APIRouter), `deps.get_current_key`, `deps.require_scope`, `deps.enforce_rate_limit`; sub-routers `tasks.router`, `health.router` |
| Dependencies | `taskq_api.service`, `taskq_api.errors`, `taskq_api.config`; NEVER `taskq_api.repository` |
| FR coverage | FR-01 (`/v1/tasks` CRUD), FR-02 (`/v1/tasks/{id}/run`, `/v1/tasks/{id}/runs`), FR-03 (`X-API-Key` header → `get_current_key`), FR-04 (`require_scope`), FR-05 (`enforce_rate_limit`), FR-09 (`/healthz`, `/readyz`, `/v1/metrics`), FR-10 (problem+json via `errors`) |

#### Logical Constraints
- `deps.py` is the **single** dependency module — every `/v1/*` route depends on it (FR-04 explicit test)
- Handlers stay ≤ 40 lines (NFR-11) — business logic delegates to `service/*`
- 403 responses are returned **before** any resource lookup completes (FR-04, R4)
- `health.py` is the hub for this community — `tasks.py` and `deps.py` both call `health.probe_ready` in lifespan/handlers

### 2.8 `taskq_api.app` and `taskq_api.__main__` — composition root

| Attribute | Value |
|-----------|-------|
| Responsibility | Compose the FastAPI app (`app.py`) and provide CLI subcommands (`__main__.py`: `migrate`, `key create`, `healthcheck`) |
| External Interface | `app: FastAPI`; CLI via `python -m taskq_api {migrate\|key create\|healthcheck}` |
| Dependencies | All layers — sits at the top of the spine |
| FR coverage | FR-03 (CLI key creation), FR-07 (CLI migrate), FR-09 (CLI healthcheck) |

#### Logical Constraints
- `app.py` lives next to other api/* modules so `api/health.py` (the hub) is a sibling and called from `lifespan`
- `__main__.py` calls `config.validate_config()` and `errors.problem_response` builders to seed internal edges into both independence communities

### 2.9 `migrations/versions/` — schema evolution

| Attribute | Value |
|-----------|-------|
| Responsibility | Three Alembic revisions (`v1_initial.py`, `v2_tags.py`, `v3_split_results.py`); each MUST have working `downgrade()` |
| External Interface | `alembic upgrade head`, `alembic downgrade base`, `alembic downgrade -1` |
| Dependencies | Alembic, SQLAlchemy metadata from `taskq_api.models` |
| FR coverage | FR-07 (the entire FR — data-preserving `v3` split is the focal point) |

#### Logical Constraints
- `v3_split_results.py` performs the `tasks.result_json → task_results` move with **per-row data preservation** (R1)
- Each downgrade mirrors the upgrade; no `op.execute("DROP TABLE ...")` shortcuts (SPEC §8 #13 / NFR-09)
- The round-trip `upgrade head → sample → downgrade -1 → upgrade head` must produce byte-identical column values

### 2.10 Module ↔ FR Traceability

| FR | Owning module(s) |
|----|------------------|
| FR-01 | `taskq_api.api.tasks`, `taskq_api.service.tasks`, `taskq_api.repository.task_repo`, `taskq_api.models.{orm,schemas}` |
| FR-02 | `taskq_api.api.tasks`, `taskq_api.service.runner`, `taskq_api.repository.task_repo`, `taskq_api.models.orm` (TaskResult) |
| FR-03 | `taskq_api.api.deps`, `taskq_api.service.auth`, `taskq_api.repository.key_repo`, `taskq_api.models.orm` (ApiKey) |
| FR-04 | `taskq_api.api.deps`, `taskq_api.service.auth` |
| FR-05 | `taskq_api.api.deps`, `taskq_api.service.ratelimit`, `taskq_api.repository.rate_repo`, `taskq_api.models.orm` (RateBucket) |
| FR-06 | `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.service.tasks`, `taskq_api.models.orm` |
| FR-07 | `migrations/versions/v1_initial.py`, `migrations/versions/v2_tags.py`, `migrations/versions/v3_split_results.py` |
| FR-08 | `taskq_api.service.runner`, `taskq_api.app` (lifespan drain) |
| FR-09 | `taskq_api.api.health`, `taskq_api.app` (startup probe) |
| FR-10 | `taskq_api.errors`, `taskq_api.api.{tasks,health,deps}` (consumers) |

---

## 3. Interfaces & Data Flows

### 3.1 Request Lifecycle (CRUD path — FR-01)

```
Client ──HTTP──▶ FastAPI router (api/tasks.py)
                    │
                    ▼
              deps.get_current_key ──▶ service.auth.verify_key ──▶ repository.key_repo
                    │                                                          │
                    ▼                                                          │
              deps.require_scope (FR-04 single point)                          │
                    │                                                          │
                    ▼                                                          │
              deps.enforce_rate_limit ──▶ service.ratelimit.consume ──▶ rate_repo
                    │
                    ▼
              handler body (≤40 lines) ──▶ service.tasks.<op> ──▶ repository.task_repo
                    │                                              │
                    ▼                                              ▼
              errors.problem_response on exception           Session.commit/rollback
                    │                                              │
                    ▼                                              ▼
              HTTP 200/201/422/401/403/404/409/429           SQLAlchemy → SQLite/Postgres
```

### 3.2 Async Execution Path (FR-02 + FR-08)

```
POST /v1/tasks/{id}/run
        │
        ▼
api/tasks.run()
        │
        ▼
service.tasks.schedule_run(task_id)         # sync part: validate, mark 'running'
        │
        ▼
service.runner.spawn(task_id, command)
        │
        ▼
asyncio.create_task(_run_subprocess(...))    # inside the singleton TaskGroup
        │
        ▼ (coroutine body)
shlex.split(command)  (NEVER shell=True)
asyncio.create_subprocess_exec(*args)
        │
        ▼
asyncio.wait_for(proc.wait(), TASKQ_TASK_TIMEOUT)
        │ on timeout:
        ▼
proc.kill()  →  await proc.wait()           # kill orphan guard — NFR-03, R8
        │
        ▼
repository.task_repo.persist_result(...)
        │
        ▼
status transitions to done | failed | timeout
```

### 3.3 Schema Evolution Flow (FR-07)

```
alembic upgrade head
   ├─ v1_initial:        CREATE tasks, api_keys, rate_buckets
   ├─ v2_tags:           CREATE tags, task_tags + UNIQUE INDEX tasks.name
   └─ v3_split_results:  CREATE task_results
                          FOR each row in tasks WHERE result_json IS NOT NULL:
                              INSERT INTO task_results (...)
                          DROP COLUMN tasks.result_json

alembic downgrade -1
   └─ reverse of v3:     ALTER TABLE tasks ADD COLUMN result_json JSON
                          FOR each row in task_results:
                              UPDATE tasks SET result_json = json_object(...) WHERE id = ...
                          DROP TABLE task_results

Round-trip invariant (SPEC §8 #12): every column value identical after
   upgrade head → insert sample → downgrade -1 → upgrade head
```

### 3.4 Rate Limit Flow (FR-05)

```
Request → enforce_rate_limit(key)
   │
   ▼
session = unit_of_work()              # ONE transaction
   │
   ▼
row = rate_repo.lock_bucket(key_id)   # SELECT ... FOR UPDATE
   │
   ▼
tokens = compute_refill(now - row.updated_at)
   │
   ▼
if tokens >= 1:
    UPDATE rate_buckets SET tokens = tokens - 1, updated_at = now() WHERE key_id = ?
    return ALLOW
else:
    return DENY (Retry-After computed from deficit / TASKQ_RATE_PER_SEC)
   │
   ▼
unit_of_work commits → row lock released → next request proceeds
```

### 3.5 Health & Readiness Flow (FR-09)

```
GET /healthz       → return 200 {"status":"ok"}       (no auth, no DB)

GET /readyz        → within unit_of_work():
                        SELECT 1 (probe DB)
                        if probe fails → 503 {"detail":"db unavailable"}
                     compare alembic_version table to expected head:
                        if mismatch → 503 {"detail":"migration not at head"}
                     else 200

GET /v1/metrics    → require_scope("admin")
                        COUNT(*) FROM tasks GROUP BY status
                        latency histogram (p50/p95/p99) from runner
                        COUNT(*) FROM rate_buckets WHERE last_decision='reject'
```

---

## 4. NFR Handling

Every NFR from SPEC.md §4 is mapped here to the concrete mechanism that satisfies
it and the gate tool that proves it.

| NFR | Dimension | Mechanism | Proving Tool / Test |
|-----|-----------|-----------|---------------------|
| **NFR-01** Performance | `performance` | `selectinload` / `joinedload` on every list query (FR-06); SQL count event listener asserts constant statement count | `pytest-benchmark` (p95 < 30ms / < 80ms); SQLAlchemy event listener test |
| **NFR-02** HTTP & Data Security | `security` | `shell=True` grep gate (0 hits); no SQL string concat; SHA-256 hashed keys with `hmac.compare_digest`; CORS deny-by-default; 403 returned before resource lookup | `bandit -r 03-development/src/` (0 HIGH / 0 MEDIUM); grep gate; CORS unit test; 403-doesn't-leak test |
| **NFR-03** Error Handling, Tx, Async | `error_handling` | `unit_of_work()` context manager enforces commit/rollback; no bare `except` / `except Exception: pass`; `CancelledError` re-raised; subprocess `kill()+wait()` | ast-error-handling scan; integration tests for `/readyz` 503 on DB down; orphan-process test |
| **NFR-04** Sensitive Data Redaction | `security` | Redaction regex on stdout_tail / stderr_tail / log lines / error body / metrics: `sk-…`, `token=`, `Bearer …`, `postgres(ql)://…`; key plaintext only printed once at creation | unit tests for redaction; log capture test asserting no DB URL fragment |
| **NFR-05** Documentation Coverage | `documentation` | Every public function/class carries docstring with `[FR-XX]` or `[NFR-XX]` reference; OpenAPI `summary`+`description` on every endpoint | ast-docstrings scan (100% coverage); OpenAPI schema assertion test |
| **NFR-06** Architecture Layering | `architecture_constraints` | `.importlinter` declares `api > service > repository > models`; `config`/`errors` independent; forbidden contract: non-repo layers MAY NOT import `sqlalchemy` | `lint-imports` exits 0; CI grep on `sqlalchemy` outside `repository/` returns 0 |
| **NFR-07** Dependency Compliance | `license_compliance` | `requirements.txt` (pinned `==`) + `requirements.lock` (full transitive); allowlist (MIT/BSD-{2,3}-Clause/Apache-2.0/PSF); `SBOM.json` at `08-config/` | `pip-licenses --format=json --with-system`; SBOM file presence check |
| **NFR-08** Mutation Testing | `mutation_testing` | `.methodology/harness_config.json` sets `features.mutation_testing: true`; mutation scope limited to `service/` and `repository/` (time budget) | `mutmut run` then `mutmut results` reports score ≥ 70 |
| **NFR-09** Verification Truthfulness | `test_assertion_quality` | No `pytest.skip` / `skipif` / `xfail` / stub tests; every test has `assert`; `FR-07` round-trip uses real SQLite file | `pytest -q` reports `0 skipped`; ast-assertions scan; integration test asserting v3 data round-trip |
| **NFR-10** Integration Coverage | `integration_coverage` | `03-development/tests/integration/` drives app via `httpx.AsyncClient(transport=ASGITransport(app))`; covers CRUD + every error code | `pytest --cov=03-development/src --cov-report=term` reports ≥ 80% on integration suite |
| **NFR-11** Readability | `readability` | File ≤ 400 lines; directory ≤ 15 files; API handler ≤ 40 lines; MI ≥ 80; CC ≤ 10 | readability-v2 scan; ruff line-length + complexity checks |
| **NFR-12** System Verification | `execute_verification_target` | `Makefile` target `verify-system` chains alembic upgrade head → full tests → service start + `/healthz`/`/readyz` smoke → alembic downgrade base → alembic upgrade head | `make verify-system` exits 0; stdout contains `verify-system: PASS` |

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as int must
> match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`.
> Do NOT hand-write the YAML — paste from the canonical template and replace
> EXAMPLE values with your project's real values.
> Validate before committing: `python3 scripts/generate_sab.py --validate --project .`

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-08-12"
  phase: 2  # MUST be int, NOT a string — parser raises on 'phase: "2"'
  project: "taskq-api"

  layers:
    - name: api
      modules:
        - name: "taskq_api.api.tasks"
        - name: "taskq_api.api.health"
        - name: "taskq_api.api.deps"
        - name: "taskq_api.app"
        - name: "taskq_api.__main__"
      allowed_dependencies: ["service", "errors", "config", "models"]
    - name: service
      modules:
        - name: "taskq_api.service.tasks"
        - name: "taskq_api.service.runner"
        - name: "taskq_api.service.auth"
        - name: "taskq_api.service.ratelimit"
      allowed_dependencies: ["repository", "models", "errors", "config"]
    - name: repository
      modules:
        - name: "taskq_api.repository.session"
        - name: "taskq_api.repository.task_repo"
        - name: "taskq_api.repository.key_repo"
        - name: "taskq_api.repository.rate_repo"
      allowed_dependencies: ["models", "config"]
    - name: models
      modules:
        - name: "taskq_api.models.orm"
        - name: "taskq_api.models.schemas"
      allowed_dependencies: []
    - name: config
      modules:
        - name: "taskq_api.config"
      allowed_dependencies: []
    - name: errors
      modules:
        - name: "taskq_api.errors"
      allowed_dependencies: []

  allowed_dependencies:
    - {from: api, to: service}
    - {from: api, to: errors}
    - {from: api, to: config}
    - {from: api, to: models}
    - {from: service, to: repository}
    - {from: service, to: models}
    - {from: service, to: errors}
    - {from: service, to: config}
    - {from: repository, to: models}
    - {from: repository, to: config}

  quality_targets:
    max_complexity: 10          # NFR-11 — handler CC ≤ 10
    min_coverage: 100           # SPEC §8 #2 — TOTAL 100%
    max_coupling: 0.3           # CRG healthy threshold

  nfr_dimension_mapping: {}

  nfr_traceability:
    NFR-01:
      type: performance
      dimension: performance
      target: "p95 < 30ms for GET /v1/tasks/{id}; p95 < 80ms for GET /v1/tasks?limit=50"
      module: taskq_api.repository.task_repo
    NFR-02:
      type: security
      dimension: security
      target: "bandit 0 HIGH/0 MEDIUM; 0 SQL string concat hits; 0 shell=True hits"
      module: taskq_api.api.deps
    NFR-03:
      type: reliability
      dimension: error_handling
      target: "no bare except; CancelledError re-raised; subprocess killed on timeout"
      module: taskq_api.repository.session
    NFR-04:
      type: security
      dimension: security
      target: "0 leakage of DB URL password, API key plaintext, or Bearer token in logs/responses"
      module: taskq_api.service.runner
    NFR-05:
      type: documentation
      dimension: documentation
      target: "100% public symbol docstring coverage with [FR-XX]/[NFR-XX] reference"
      module: taskq_api.api.tasks
    NFR-06:
      type: layering
      dimension: architecture_constraints
      target: "lint-imports exit 0; 0 sqlalchemy imports outside repository/"
      module: taskq_api.api.tasks
    NFR-07:
      type: licensing
      dimension: license_compliance
      target: "every dependency (direct + transitive) license in allowlist; SBOM.json present"
      module: taskq_api.app
    NFR-08:
      type: mutation
      dimension: mutation_testing
      target: ">=70"
      scope_layers: ["service", "repository"]
      module: taskq_api.service.runner
    NFR-09:
      type: testability
      dimension: test_assertion_quality
      target: "0 skipped tests; 0 zero-assertion tests; FR-07 round-trip verified on real SQLite"
      module: taskq_api.repository.session
    NFR-10:
      type: integration
      dimension: integration_coverage
      target: ">=80% integration line coverage; httpx ASGITransport end-to-end"
      module: taskq_api.api.tasks
    NFR-11:
      type: maintainability
      dimension: readability
      target: "MI >=80; file <=400 lines; dir <=15 files; handler <=40 lines; CC <=10"
      module: taskq_api.api.tasks
    NFR-12:
      type: verifiability
      dimension: execute_verification_target
      target: "make verify-system exits 0; stdout contains 'verify-system: PASS'"
      module: taskq_api.app

  advisory_only: []

  gate_score_overrides: {}

  fr_module_traceability:
    FR-01: ["taskq_api.api.tasks", "taskq_api.service.tasks", "taskq_api.repository.task_repo", "taskq_api.models.schemas"]
    FR-02: ["taskq_api.api.tasks", "taskq_api.service.runner", "taskq_api.repository.task_repo", "taskq_api.models.orm"]
    FR-03: ["taskq_api.api.deps", "taskq_api.service.auth", "taskq_api.repository.key_repo", "taskq_api.models.orm"]
    FR-04: ["taskq_api.api.deps", "taskq_api.service.auth"]
    FR-05: ["taskq_api.api.deps", "taskq_api.service.ratelimit", "taskq_api.repository.rate_repo", "taskq_api.models.orm"]
    FR-06: ["taskq_api.repository.session", "taskq_api.repository.task_repo", "taskq_api.service.tasks", "taskq_api.models.orm"]
    FR-07: ["migrations.versions.v1_initial", "migrations.versions.v2_tags", "migrations.versions.v3_split_results"]
    FR-08: ["taskq_api.service.runner", "taskq_api.app"]
    FR-09: ["taskq_api.api.health", "taskq_api.app"]
    FR-10: ["taskq_api.errors", "taskq_api.api.tasks", "taskq_api.api.deps"]

  architecture_constraints:
    - "no_circular_dependencies"
    - "sqlalchemy_only_in_repository"
    - "single_auth_dependency_at_api_layer"
    - "errors_and_config_are_independence_modules"
    - "fr07_round_trip_must_preserve_data"
    - "rate_limit_update_in_single_transaction_with_row_lock"

  high_risk_modules:
    - "taskq_api.service.runner"
    - "taskq_api.service.auth"
    - "taskq_api.repository.session"
    - "migrations.versions.v3_split_results"
```
<!-- SAB:END -->

Note: Fill in the YAML above — it is used for Drift Detection and gate scoring.
Generate: `python3 scripts/generate_sab.py --project . [--overwrite]`

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

> **CONTRACT**: Field names and the `security_design:` root key are parsed
> by `core/quality_gate/security_design.py:extract_security_block()`.
> Do NOT hand-write the YAML — paste from the canonical template and
> replace EXAMPLE values with your project's real values.
> Validate: `python3 harness_cli.py check-artifact-consistency --project .`
>
> `applicability: none` is a fully valid, honest declaration for a project
> with no real attack surface (e.g. a pure CLI formatting tool) — it
> requires a `justification` (>=20 chars) and skips the rest of this
> block. This is a decidable structural check, not a keyword scorer: an
> honest `none` always passes.

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full
  justification: ""

  trust_boundaries:
    - id: TB-01
      name: "external HTTP client to api layer"
      description: "all inbound /v1/* traffic from unauthenticated network clients crossing into the FastAPI app"
    - id: TB-02
      name: "api to service"
      description: "handler functions crossing from HTTP layer into business orchestration; receives already-validated inputs and an authenticated key"
    - id: TB-03
      name: "service to repository/database"
      description: "ORM access crossing from business code into SQLAlchemy session and the SQLite/PostgreSQL backend"
    - id: TB-04
      name: "service runner to host subprocess"
      description: "asyncio subprocess invocation crossing from the Python process into the host OS for task execution"
    - id: TB-05
      name: "operator CLI to data plane"
      description: "`python -m taskq_api key create` and `migrate` commands crossing operator input into persistent state"

  threats:
    - id: T-01
      boundary: TB-01
      category: tampering
      description: "malformed task payload mutates task state without validation"
      mitigation: "pydantic TaskCreate schema rejects unknown fields, empty body, oversize (>1000 chars), and injection characters"
      owner_module: "taskq_api.api.tasks"
      nfr: NFR-02
      verified_by: "test_sec_t01_malformed_payload_rejected"
    - id: T-02
      boundary: TB-01
      category: spoofing
      description: "attacker forges or brute-forces X-API-Key header"
      mitigation: "SHA-256 hash stored at rest; hmac.compare_digest for constant-time compare; revoked keys have non-null revoked_at and are rejected"
      owner_module: "taskq_api.service.auth"
      nfr: NFR-02
      verified_by: "test_sec_t02_invalid_or_revoked_key_rejected"
    - id: T-03
      boundary: TB-01
      category: elevation_of_privilege
      description: "write-scoped key calls admin-only DELETE /v1/tasks/{id}"
      mitigation: "single api/deps.py require_scope dependency gates every /v1/* route; insufficient scope returns 403 BEFORE any resource lookup"
      owner_module: "taskq_api.api.deps"
      nfr: NFR-04
      verified_by: "test_sec_t03_insufficient_scope_returns_403_no_leak"
    - id: T-04
      boundary: TB-03
      category: tampering
      description: "SQL injection via string-concatenated query"
      mitigation: "ORM only; import-linter forbids sqlalchemy outside repository/; CI grep gate for f-string/%/+ SQL assembly"
      owner_module: "taskq_api.repository.task_repo"
      nfr: NFR-02
      verified_by: "test_sec_t04_no_string_concat_sql_in_repo"
    - id: T-05
      boundary: TB-03
      category: information_disclosure
      description: "TASKQ_DB_URL password leaks via error body, log line, or /v1/metrics response"
      mitigation: "redaction regex applied to stdout_tail, stderr_tail, log lines, error body, and metrics payload; config module never logs DB URL value"
      owner_module: "taskq_api.errors"
      nfr: NFR-04
      verified_by: "test_sec_t05_db_url_password_redacted"
    - id: T-06
      boundary: TB-01
      category: denial_of_service
      description: "token-bucket exhaustion from a single key floods the API"
      mitigation: "rate_repo.consume runs in one transaction with SELECT ... FOR UPDATE row lock; over-limit request returns 429 + Retry-After"
      owner_module: "taskq_api.service.ratelimit"
      nfr: NFR-02
      verified_by: "test_sec_t06_rate_limit_returns_429_with_retry_after"
    - id: T-07
      boundary: TB-04
      category: elevation_of_privilege
      description: "command injection via shell metacharacters in task command field"
      mitigation: "asyncio.create_subprocess_exec(*shlex.split(command)); shell=True grep-gated to 0 hits; explicit shlex parsing prevents metacharacter interpretation"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-02
      verified_by: "test_sec_t07_subprocess_exec_no_shell"
    - id: T-08
      boundary: TB-02
      category: denial_of_service
      description: "asyncio.CancelledError swallowed by except Exception during shutdown; service hangs and fails graceful drain"
      mitigation: "explicit re-raise of CancelledError outside any except Exception scope; lint rule forbids bare except / except Exception: pass"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-03
      verified_by: "test_sec_t08_cancelled_error_propagates"
    - id: T-09
      boundary: TB-04
      category: denial_of_service
      description: "subprocess timeout leaves orphan process when proc.kill() is not awaited"
      mitigation: "on wait_for timeout, runner calls proc.kill() then awaits proc.wait() before raising; orphan-process count asserted in integration test"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-03
      verified_by: "test_sec_t09_subprocess_killed_on_timeout"
    - id: T-10
      boundary: TB-03
      category: tampering
      description: "Alembic v3 downgrade loses task result data"
      mitigation: "v3_split_results migration performs per-row INSERT before DROP COLUMN; downgrade path mirrors the move back into result_json; round-trip test on real SQLite asserts byte-equal column values"
      owner_module: "migrations.versions.v3_split_results"
      nfr: NFR-09
      verified_by: "test_sec_t10_migration_roundtrip_preserves_data"
    - id: T-11
      boundary: TB-05
      category: information_disclosure
      description: "API key plaintext persisted to disk or logged during `key create`"
      mitigation: "plaintext only printed once to stdout at creation; SHA-256 hash stored in api_keys.key_hash; stdout/stderr/log all flow through the NFR-04 redaction filter"
      owner_module: "taskq_api.__main__"
      nfr: NFR-04
      verified_by: "test_sec_t11_key_plaintext_not_persisted"
```
<!-- SEC:END -->

Note: `owner_module` must name a module declared in the §5 SAB block;
`nfr` (optional) must exist in SRS.md; `verified_by` names the test that
proves the mitigation — from Phase 5 onward, `check-artifact-consistency`
blocks if that test doesn't exist yet. Threats also seed
`bug-hunt-targets`' adversarial-review targeting and force NFR-pattern
test cases in `derive_test_cases.md` Step 1c regardless of SRS keywords.
