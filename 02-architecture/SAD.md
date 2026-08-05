# Software Architecture Document (SAD) — taskq-api

> Phase 2 deliverable. Source of truth: `SPEC.md` v1.0.0 (2026-07-30)
> and `01-requirements/SRS.md` (10 FR / 12 NFR). Module layout follows
> `SPEC.md` §6 directory structure; FR/NFR traceability is 1:1 with
> `SRS.md` headings.

---

## 1. Architecture Overview

`taskq-api` is a Python 3.11 ASGI service that exposes a REST API for
submitting, querying, and executing shell-command tasks. The system
persists state to a relational database (SQLite dev/test, PostgreSQL
prod, same SQLAlchemy 2.x ORM model), evolves the schema via Alembic
(v1 → v2 → v3, each with a working `downgrade`), authenticates clients
by hashed API keys, authorises by per-token scope, and throttles per
token via a DB-backed token bucket.

The runtime is `uvicorn taskq_api.app:app`; management operations
(migrate / seed / healthcheck / `key create`) enter via
`python -m taskq_api`. Subprocess execution is async
(`asyncio.create_subprocess_exec`, `shell=True` forbidden), with a
`TASKQ_MAX_CONCURRENT` concurrency cap and a graceful drain on
shutdown. Errors are returned uniformly as RFC 7807
`application/problem+json`.

The architecture is a strict four-layer contract — `api > service >
repository > models` — enforced by `.importlinter` (NFR-06), with
`config` and `errors` as independent modules and `sqlalchemy`
forbidden outside `repository/`. A `code-review-graph` (CRG) community
view judges cohesion; the directory layout is designed for cohesion
≥ 0.3 with edge budgets (see §2.1).

### 1.1 System Verification Target
> **Phase 3 Gate 2 Requirement**: The harness executes `make verify-system` at Gate 2.
> If it exits with a non-zero status Gate 2 fails. Add a `verify-system` target to your
> project `Makefile` that assembles and exercises the system end-to-end (e.g. runs your
> integration tests or smoke-test suite). The target name is fixed — the harness always
> calls `make verify-system`.
**Makefile target**: `verify-system`

The `verify-system` chain (NFR-12, AC-12.1) is:
1. `alembic upgrade head`
2. full test suite (`pytest 03-development/tests -q`)
3. service start + `/healthz`, `/readyz` smoke
4. `alembic downgrade base` then `upgrade head` (round-trip)

## 2. Module Design

### 2.1 Directory Structure Design Principles

The module tree is fixed by `SPEC.md` §6 and is the contract for
`.importlinter` (NFR-06). Four source directories map to the four
layers; migrations live outside the package; tests are split into
`unit/` and `integration/`. CRG will see one community per directory,
so each layer directory is designed as a hub-and-spoke with a hub
module that the siblings call from multiple function bodies (to keep
edge-budget I ≥ 0.4286·E, see CRG Scoring below).

**Layer mapping (per SPEC.md §6 + NFR-06):**

| Layer | Directory | Allowed to import | Forbidden to import |
|-------|-----------|-------------------|---------------------|
| L4 (top) | `taskq_api/api/` | service, repository(via service), models, errors, config | — |
| L3 | `taskq_api/service/` | repository, models, errors, config | `sqlalchemy`, `api` |
| L2 | `taskq_api/repository/` | models, errors, config | `service`, `api`, **sqlalchemy (only here)** |
| L1 (bottom) | `taskq_api/models/` | errors, config | service, api, repository |
| Independent | `taskq_api/config.py`, `taskq_api/errors.py` | — | all layers except as needed |

**Six CRG Design Principles applied to taskq-api:**

1. **Subdirectories control community boundaries.** The four layer
   directories (`api/`, `service/`, `repository/`, `models/`) each
   become one predictable CRG community. Total of 4 source
   directories (within the 3-6 target).
2. **Hub module per directory (≥2 functions).** Each layer has a
   hub: `api/deps.py` (auth+scope+rate-limit dependency),
   `service/auth.py` (scope decision helper), `repository/session.py`
   (transaction context manager), `models/orm.py` (declarative
   table-base). Siblings call these hubs from their function bodies.
3. **Entry points inside a hub dir.** `app.py` and `__main__.py`
   live at the package root (the only place they can — `uvicorn
   taskq_api.app:app` is the canonical launch path) and import
   from `api/deps.py` heavily, balancing external edges (FastAPI,
   uvicorn, alembic) with internal edges into the layer below.
4. **Function bodies call hub functions, not just module level.**
   Every handler in `api/tasks.py`, `api/health.py` calls
   `deps.require_scope(...)` and `deps.check_rate_limit(...)` so
   each handler contributes internal edges. `service/auth.py` is
   called from every handler's dependency resolution path.
5. **Edge-detection-friendly calls.** Cross-file calls use
   standalone assignment (`resolved = deps.authenticate(...))`)
   to maximise Tree-sitter detection. Class-internal `self.method`
   calls in `repository/session.py` count.
6. **Community size cap.** The largest community (`api/`) has ~5
   files × ~6 functions ≈ 30 nodes — well under the 50-node cap.

### 2.2 FR → Module Traceability

Every FR (10 total) maps to one or more modules. Modules are taken
verbatim from `SPEC.md` §6. Multiple FRs share modules; no FR is
unmapped; no module is orphaned.

| FR | Title | Owning Module(s) | Supporting Modules |
|----|-------|------------------|---------------------|
| FR-01 | 任務資源 CRUD API | `taskq_api.api.tasks`, `taskq_api.service.tasks` | `taskq_api.repository.task_repo`, `taskq_api.models.schemas`, `taskq_api.models.orm` |
| FR-02 | 任務執行端點 | `taskq_api.api.tasks`, `taskq_api.service.tasks`, `taskq_api.service.runner` | `taskq_api.repository.task_repo` |
| FR-03 | API Key 認證 | `taskq_api.api.deps`, `taskq_api.service.auth` | `taskq_api.repository.key_repo`, `taskq_api.models.orm` |
| FR-04 | Scope 授權 | `taskq_api.api.deps` (single middleware/dependency — AC-4.3) | `taskq_api.service.auth` |
| FR-05 | 流量控制 | `taskq_api.api.deps`, `taskq_api.service.ratelimit` | `taskq_api.repository.rate_repo` |
| FR-06 | 持久化層與交易邊界 | `taskq_api.repository.session` (context manager) | `taskq_api.repository.task_repo`, `taskq_api.repository.key_repo`, `taskq_api.repository.rate_repo` |
| FR-07 | Schema Migration (Alembic 三步演進) | `migrations/versions/v1_initial.py`, `migrations/versions/v2_tags.py`, `migrations/versions/v3_split_results.py` | `migrations/env.py`, `taskq_api.models.orm`, `alembic.ini` |
| FR-08 | 非同步執行器 | `taskq_api.service.runner` (asyncio.TaskGroup, wait_for, graceful drain) | `taskq_api.service.tasks`, `taskq_api.config` |
| FR-09 | 健康檢查與可觀測性 | `taskq_api.api.health` (`/healthz`, `/readyz`, `/v1/metrics`) | `taskq_api.repository.session`, `migrations/env.py` |
| FR-10 | 錯誤契約 (RFC 7807) | `taskq_api.errors` (problem+json envelope), `taskq_api.api.deps` (correlation_id middleware) | All layers (consumers) |

### 2.3 Module Catalog

#### 2.3.1 `taskq_api/__init__.py`

| Attribute | Value |
|-----------|-------|
| Responsibility | Package marker; re-export `app` for `uvicorn taskq_api.app:app`. |
| External Interface | `app: FastAPI` (re-export from `app.py`). |
| Dependencies | `taskq_api.app` |
| Layer | L4 (top) — only entry-point surface. |

#### 2.3.2 `taskq_api/__main__.py` (FR-09, FR-03)

| Attribute | Value |
|-----------|-------|
| Responsibility | Management entry point: `migrate`, `key create`, `seed`, `healthcheck`. |
| External Interface | `python -m taskq_api <subcommand> [args]`. |
| Dependencies | `taskq_api.config`, `taskq_api.repository.session`, `taskq_api.service.auth`, `alembic.config`. |
| Layer | L4. |

#### 2.3.3 `taskq_api/app.py` (FR-09)

| Attribute | Value |
|-----------|-------|
| Responsibility | FastAPI app factory; mount routers; install exception handlers (FR-10); install correlation-id middleware. |
| External Interface | `app: FastAPI`. |
| Dependencies | `taskq_api.api.tasks`, `taskq_api.api.health`, `taskq_api.errors`, `taskq_api.config`. |
| Layer | L4. |

#### 2.3.4 `taskq_api/config.py` (independence)

| Attribute | Value |
|-----------|-------|
| Responsibility | Read & validate the 12 `TASKQ_*` env vars (§5.1); expose a `Settings` dataclass. |
| External Interface | `Settings.from_env() -> Settings`, `settings.task_timeout: float`. |
| Dependencies | `os`, `dataclasses`, standard library only. No DB / no HTTP. |
| Layer | Independence (NFR-06). |

#### 2.3.5 `taskq_api/errors.py` (independence; FR-10)

| Attribute | Value |
|-----------|-------|
| Responsibility | RFC 7807 envelope (`type`, `title`, `status`, `detail`, `instance`, `correlation_id`); redaction helpers (NFR-04). |
| External Interface | `Problem(type=..., title=..., status=..., detail=...)`, `problem_response(request, exc) -> JSONResponse`. |
| Dependencies | Standard library only. No SQLAlchemy. |
| Layer | Independence. |

#### 2.3.6 `taskq_api/models/` (L1)

| Attribute | Value |
|-----------|-------|
| Responsibility | Declarative ORM tables (`tasks`, `api_keys`, `tags`, `task_tags`, `task_results`, `rate_buckets`) and pydantic request/response schemas. |
| External Interface | `models.orm.Task`, `models.orm.ApiKey`, …; `models.schemas.TaskCreate`, `models.schemas.TaskRead`. |
| Files | `__init__.py`, `orm.py`, `schemas.py` |
| Dependencies | `taskq_api.errors` (for schema-validation errors), `sqlalchemy.orm`. |
| Layer | L1. |

#### 2.3.7 `taskq_api/repository/` (L2; only layer permitted to import `sqlalchemy` per NFR-06)

| Attribute | Value |
|-----------|-------|
| Responsibility | All DB access; explicit `Session`/transaction boundaries; ORM or parameterised queries only; explicit eager-loading (`selectinload`/`joinedload`) — N+1 is an acceptance failure (FR-06 AC-6.4). |
| External Interface | `session.session_scope() -> ContextManager[Session]`; `task_repo.{create, get, list_by_cursor, delete}`; `key_repo.{create, find_active}`; `rate_repo.{take_token, refill}`. |
| Files | `__init__.py`, `session.py` (hub), `task_repo.py`, `key_repo.py`, `rate_repo.py` |
| Dependencies | `taskq_api.models.orm`, `taskq_api.errors`, `sqlalchemy`. |
| Layer | L2. |

#### 2.3.8 `taskq_api/service/` (L3; no `sqlalchemy` import per NFR-06 AC-06.2)

| Attribute | Value |
|-----------|-------|
| Responsibility | Business logic: task CRUD orchestration, async subprocess runner, API-key scope decision, rate-limit decisions. Holds no `Session` (FR-06 AC-6.1). |
| External Interface | `tasks.create/get/list/delete/run/list_runs`; `runner.submit(task_id)`; `auth.authenticate(key, scope)`; `ratelimit.take(key_id)`. |
| Files | `__init__.py`, `tasks.py`, `runner.py` (high-risk), `auth.py` (high-risk), `ratelimit.py` |
| Dependencies | `taskq_api.repository.*`, `taskq_api.models.schemas`, `taskq_api.errors`, `taskq_api.config`. |
| Layer | L3. |

#### 2.3.9 `taskq_api/api/` (L4 top)

| Attribute | Value |
|-----------|-------|
| Responsibility | FastAPI routers, request/response wiring, single auth+scope+rate-limit dependency (FR-04 AC-4.3). Each handler ≤ 40 lines (NFR-11 AC-11.3); business logic sinks to `service/`. |
| External Interface | FastAPI `APIRouter` instances; `deps.{authenticate, require_scope, check_rate_limit}` (FastAPI `Depends`). |
| Files | `__init__.py`, `deps.py` (hub), `tasks.py`, `health.py` |
| Dependencies | `taskq_api.service.*`, `taskq_api.repository.*`, `taskq_api.models.schemas`, `taskq_api.errors`, `taskq_api.config`. |
| Layer | L4. |

#### 2.3.10 `migrations/` (Alembic; FR-07)

| Attribute | Value |
|-----------|-------|
| Responsibility | Three revisions: `v1_initial.py` (create `tasks`, `api_keys`, `rate_buckets`), `v2_tags.py` (add `tags`/`task_tags` + unique index on `tasks.name`), `v3_split_results.py` (data-moving split of `tasks.result_json` into `task_results`; reversible downgrade). |
| External Interface | `alembic upgrade head`, `alembic downgrade base`, `alembic downgrade -1`. |
| Files | `env.py`, `versions/v1_initial.py`, `versions/v2_tags.py`, `versions/v3_split_results.py` (high-risk) |
| Dependencies | `taskq_api.models.orm`, `alembic`. |
| Layer | Independent of `taskq_api/` package layers (Alembic runtime contract). |

#### 2.3.11 `taskq_api/tests/` (test-only)

| Attribute | Value |
|-----------|-------|
| Responsibility | `unit/` for fast, isolated tests; `integration/` for end-to-end via `httpx.AsyncClient(transport=ASGITransport(app))` (NFR-10 AC-10.2). Integration must cover CRUD, all error codes (401/403/404/409/422/429/503), migration round-trip, rate-limit trigger/recovery, graceful drain. |
| External Interface | pytest discovery. |
| Files | split per FR/NFR; no test excluded via `--ignore`/`-k`/`--deselect`/`collect_ignore` (NFR-09 AC-09.4). |
| Dependencies | Same as production modules. |
| Layer | Out-of-band; not subject to layer contract. |

### 2.4 Logical Constraints

- **Layer ordering (NFR-06 AC-06.1)**: `api > service > repository >
  models`. Upper layers may import lower; lower layers MUST NOT
  import upper. `.importlinter` enforces; `lint-imports` exits 0
  (AC-06.3).
- **`sqlalchemy` forbidden outside `repository/`** (AC-06.2). A
  dedicated test (`test_sqlalchemy_import_outside_repository_blocked`)
  asserts this with a clear failure message.
- **No god-module**: each directory ≤ 15 files (NFR-11 AC-11.2);
  each file ≤ 400 lines; each handler ≤ 40 lines.
- **No circular dependencies**: layer DAG is acyclic by construction
  (edges only flow downward; `config`/`errors` are sinks with no
  cross-layer imports).
- **Per-FR TDD** is required for high-risk modules
  (`service.runner`, `service.auth`, `repository.session`,
  `migrations/versions/v3_split_results.py`).

## 3. Interfaces & Data Flows

### 3.1 Request lifecycle (write path: `POST /v1/tasks` → `run`)

```
client ──HTTP──▶ uvicorn ──▶ FastAPI router (api/tasks.py)
                                    │
                                    ▼
                         deps.authenticate (api/deps.py)         [FR-03]
                         deps.require_scope("write")              [FR-04]
                         deps.check_rate_limit                    [FR-05]
                                    │
                                    ▼
                         service.tasks.create(task_in)            [FR-01]
                                    │
                                    ▼
                         repository.session.session_scope()       [FR-06]
                                    │
                                    ▼
                         repository.task_repo.create(task)        [FR-06]
                                    │
                                    ▼
                              SQLAlchemy Session → DB (commit)
                                    │
                                    ▼
                         service.tasks.run(task.id)               [FR-02]
                                    │
                                    ▼
                         service.runner.submit(task)               [FR-08]
                         (asyncio.TaskGroup, asyncio.wait_for)
                                    │
                                    ▼
                         repository.task_repo.append_result(...)   [FR-02, FR-06]
                                    │
                                    ▼
                              201 / 202 + RFC 7807 body            [FR-10]
```

### 3.2 Read path: `GET /v1/tasks/{id}` (NFR-01 p95 < 30ms)

```
client ──HTTP──▶ api/tasks.get ──▶ deps.authenticate (read)
                                  deps.require_scope("read")
                                  deps.check_rate_limit
                                  service.tasks.get(id) ──▶
                                  repository.task_repo.get_with_results(id)  (joinedload)
                                  ◀── TaskRead (pydantic)
                                  200 JSON
```

`selectinload`/`joinedload` (FR-06 AC-6.4) ensures a constant
SQL-statement count regardless of result rows (NFR-01 AC-01.3,
asserted via SQLAlchemy event listener in
`test_n_plus_one_sql_count_constant_within_list_endpoint`).

### 3.3 Authentication & scope decision (FR-03, FR-04)

A single dependency `deps.authenticate(request) -> ApiKeyContext`
runs once per request (asserted by
`test_all_v1_routes_pass_through_same_dependency`, FR-04 AC-4.3).
Inside it: read `X-API-Key`, SHA-256 hash, `SELECT … FROM api_keys
WHERE key_hash = ? AND revoked_at IS NULL` via `key_repo.find_active`,
constant-time compare via `hmac.compare_digest` (NFR-02 AC-02.3).
`deps.require_scope(min)` checks hierarchy
`read < write < admin` (FR-04 AC-4.1). On insufficient scope →
403 with body that **does not leak resource existence** (AC-4.2;
verified by `test_403_body_does_not_leak_resource_existence`).

### 3.4 Rate-limit (FR-05)

`deps.check_rate_limit(api_key)` calls
`service.ratelimit.take(api_key.id)`, which calls
`repository.rate_repo.take_token(api_key.id)` in a single
transaction with `SELECT … FOR UPDATE` (row-level lock — AC-5.3).
Bucket state is DB-persisted (cross-worker consistent — AC-5.3).
On exceed → 429 + `Retry-After` header (AC-5.2).

### 3.5 Async runner (FR-08)

`service.runner.submit(task)` schedules a coroutine on an
`asyncio.TaskGroup`. The coroutine uses
`asyncio.create_subprocess_exec(*shlex.split(command))` (FR-02
AC-2.2; `shell=True` forbidden — NFR-02 AC-02.1) wrapped in
`asyncio.wait_for(timeout=TASKQ_TASK_TIMEOUT)`. On timeout →
`process.kill()` then `await process.wait()` (no orphan, NFR-03
AC-03.5, FR-08 AC-8.3). Concurrency cap `TASKQ_MAX_CONCURRENT` is
a semaphore; over-cap requests are queued (AC-8.2). On shutdown,
`asyncio.TaskGroup` performs graceful drain up to
`TASKQ_DRAIN_TIMEOUT`; tasks exceeding the budget are marked
`interrupted` (AC-8.1).

### 3.6 Migration flow (FR-07)

```
alembic upgrade head   ──▶ v1 → v2 → v3 (data-moving)
alembic downgrade -1   ──▶ v3 reverse-migrates data back to
                          tasks.result_json, drops task_results
alembic upgrade head   ──▶ re-splits; data byte-identical (AC-7.2)
```

The v3 revision (`migrations/versions/v3_split_results.py`) is
high-risk; its round-trip is verified on a real SQLite file in
`test_migration_round_trip_against_real_sqlite_file` (NFR-09 AC-09.5,
`zero skip`).

### 3.7 Health / readiness / metrics (FR-09)

`GET /healthz` returns 200 if the process is alive (no DB check).
`GET /readyz` runs two checks: `SELECT 1` via `repository.session`
and `alembic current` vs head (via `migrations/env.py`). Any
failure → 503 with `detail` naming which check failed (FR-09
AC-9.1, NFR-03 AC-03.4).
`GET /v1/metrics` (admin scope) returns task counts by status,
execution-latency percentiles, rate-limit rejection counts.
DB URL is **not** in the response (NFR-04 AC-04.2; asserted by
`test_metrics_response_omits_db_url_password`).

### 3.8 Error contract (FR-10)

All non-2xx responses are produced by `errors.problem_response` as
`application/problem+json`. Fields: `type`, `title`, `status`,
`detail`, `instance`, `correlation_id`. `detail` is
sanitised — no SQL, no stack, no path (NFR-02 AC-02.5; FR-10
AC-10.3). `correlation_id` appears in `X-Correlation-Id` header
and in the server log (AC-10.4). Mapping (SPEC §7): 422 / 401 / 403 /
404 / 409 / 429 / 503 / 500 (AC-10.5). `asyncio.CancelledError`
propagates (NFR-03 AC-03.3) — it is **not** converted to 500.

## 4. NFR Handling

Each NFR (12 total, transcribed verbatim from `SPEC.md` §4) maps to
one or more modules. No NFR is dropped; citations to `SPEC.md` are
inline.

| NFR | dimension | Title | Owning Module(s) | Approach |
|-----|-----------|-------|------------------|----------|
| NFR-01 | performance | 效能與查詢效率 | `taskq_api.repository.task_repo`, `taskq_api.api.tasks`, `taskq_api.models.orm` | Explicit `joinedload`/`selectinload` (FR-06 AC-6.4); pytest-benchmark cases for p95 (<30ms / <80ms); SQLAlchemy event listener asserts constant statement count. |
| NFR-02 | security | HTTP 與資料層安全 | `taskq_api.errors`, `taskq_api.service.auth`, `taskq_api.api.deps`, `taskq_api.config`, `taskq_api.repository.task_repo` | Project-wide grep gate for `shell=True` / `eval(` / `exec(` (AC-02.1); ORM/parameterised queries only (AC-02.2); SHA-256 + `hmac.compare_digest` (AC-02.3); 403 body leak test (AC-02.4); error body sanitisation (AC-02.5); CORS deny-by-default (AC-02.6); `bandit -r` 0/0 (AC-02.7). |
| NFR-03 | error_handling | 錯誤處理、交易與非同步正確性 | `taskq_api.repository.session`, `taskq_api.service.runner`, `taskq_api.api.health`, `migrations/versions/v3_split_results.py` | Context-manager transaction boundaries (AC-03.1); AST/lint gate against bare `except:` and `except Exception: pass` (AC-03.2); explicit `CancelledError` propagation test (AC-03.3); `/readyz` returns 503 on DB failure (AC-03.4); `process.kill()` + `await wait()` (AC-03.5); Alembic transaction rollback semantics (AC-03.6). |
| NFR-04 | security | 敏感資料遮蔽 | `taskq_api.errors`, `taskq_api.service.runner`, `taskq_api.api.health`, `taskq_api.config` | `errors.redact(line)` runs the SPEC regex `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` before any stdout/stderr/log/error body write (AC-04.1); DB URL never logged (AC-04.2); key plaintext only at `key create` time (AC-04.3). |
| NFR-05 | documentation | 文件覆蓋 | All modules | Docstring on every public function/class with `[FR-XX]` or `[NFR-XX]` marker (AC-05.1); OpenAPI `summary`+`description` per endpoint (AC-05.2); dedicated tests `test_docstrings_cite_fr_or_nfr_marker`, `test_openapi_schema_has_summary_and_description_per_endpoint`. |
| NFR-06 | architecture_constraints | 架構分層契約 | `taskq_api.api`, `taskq_api.service`, `taskq_api.repository`, `taskq_api.models`, `.importlinter` | `.importlinter` declares `api > service > repository > models` (AC-06.1) + `sqlalchemy` forbidden outside `repository/` (AC-06.2); `lint-imports` exits 0 (AC-06.3); no downgrade via wildcard `ignore_imports` (AC-06.4); dedicated test `test_sqlalchemy_import_outside_repository_blocked`. |
| NFR-07 | license_compliance | 依賴與授權合規 | `requirements.txt`, `requirements.lock`, `08-config/SBOM.json` | `==` pinning + lock file (AC-07.1); allowlist (MIT/BSD-2/BSD-3/Apache-2.0/PSF, AC-07.2); `pip-licenses --with-system` whole-tree scan (AC-07.3); SBOM at `08-config/SBOM.json` (AC-07.4); dedicated tests `test_requirements_txt_pins_with_double_equals`, `test_requirements_lock_present_and_pinned`, `test_sbom_markdown_present_at_08_config_path`, `test_all_runtime_dependencies_license_in_allowlist`. |
| NFR-08 | mutation_testing | 變異測試 | `taskq_api.service.*`, `taskq_api.repository.*` | `.methodology/harness_config.json` `features.mutation_testing: true` (AC-08.1); `mutmut run` score ≥ 70 (AC-08.2); scope limited to `service/` + `repository/` with run-time-budget rationale in `harness_config.json` (AC-08.3). |
| NFR-09 | test_assertion_quality | 驗證真實性 (零 skip 鐵律) | `03-development/tests/**` | No `pytest.skip` / `skipif` / `xfail` / assertion-free stubs (AC-09.1); `pytest -q` skipped == 0 (AC-09.2); every test ≥ 1 assert (AC-09.3); no `--ignore`/`-k`/`--deselect`/`collect_ignore` exclusions (AC-09.4); FR-07 tested against a real SQLite file (AC-09.5); `TRACEABILITY_MATRIX.md` `VERIFIED` only after the test ran and passed (AC-09.6). |
| NFR-10 | integration_coverage | 整合覆蓋 | `03-development/tests/integration/**` | ≥ 80% integration coverage (AC-10.1); driven via `httpx.AsyncClient(transport=ASGITransport(app))` (AC-10.2); covers CRUD chain, every error code (401/403/404/409/422/429/503), migration round-trip, rate-limit trigger+recovery, graceful drain (AC-10.3). |
| NFR-11 | readability | 可讀性 | All source files | `radon mi` ≥ 80 (LLOC-weighted, AC-11.1); per-file ≤ 400 lines, per-dir ≤ 15 files (AC-11.2); per-handler ≤ 40 lines (AC-11.3); dedicated tests `test_per_function_cc_le_10`, `test_per_file_lines_le_400`, `test_per_dir_files_le_15`, `test_per_api_handler_lines_le_40`. |
| NFR-12 | execute_verification_target | 系統驗證目標 | `Makefile`, `migrations/env.py`, `03-development/tests/**` | `verify-system` chains `alembic upgrade head` → full tests → service start + `/healthz` + `/readyz` smoke → `alembic downgrade base` then `upgrade head` (AC-12.1); `make verify-system` exits 0 and prints `verify-system: PASS` (AC-12.2). |

**Coverage notes** (NFRs whose eval-dimension section is narrower than
their ACs; mirror of `SRS.md` §4 coverage notes) — these dedicated
tests are added in Phase 3:

- NFR-01: `test_kpi_p95_get_by_id_under_30ms_at_10k`,
  `test_kpi_p95_list_under_80ms_at_10k`,
  `test_n_plus_one_sql_count_constant_within_list_endpoint`.
- NFR-02: `test_grep_no_shell_true_or_eval_or_exec`,
  `test_grep_no_string_concatenated_sql`,
  `test_403_body_does_not_leak_resource_existence`,
  `test_500_body_contains_no_stack_or_sql_or_path`,
  `test_cors_default_deny_all_origins`.
- NFR-03: `test_cancelled_error_propagates_under_async_runner`,
  `test_readyz_returns_503_when_db_unreachable`,
  `test_failed_migration_rolls_back_to_previous_revision`.
- NFR-04: `test_log_redacts_sk_token_bearer_dburl`,
  `test_metrics_response_omits_db_url_password`.
- NFR-05: `test_docstrings_cite_fr_or_nfr_marker`,
  `test_openapi_schema_has_summary_and_description_per_endpoint`.
- NFR-06: `test_sqlalchemy_import_outside_repository_blocked`.
- NFR-07: `test_requirements_txt_pins_with_double_equals`,
  `test_requirements_lock_present_and_pinned`,
  `test_sbom_markdown_present_at_08_config_path`,
  `test_all_runtime_dependencies_license_in_allowlist`.
- NFR-09: `test_no_skipped_tests_in_pytest_q_output`,
  `test_no_tests_excluded_via_ignore_k_deselect`,
  `test_migration_round_trip_against_real_sqlite_file`.
- NFR-11: `test_per_function_cc_le_10`, `test_per_file_lines_le_400`,
  `test_per_dir_files_le_15`, `test_per_api_handler_lines_le_40`.

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
  created_at: "2026-08-05"
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
      allowed_dependencies: ["service", "repository", "models", "independence"]

    - name: service
      modules:
        - name: "taskq_api.service.tasks"
        - name: "taskq_api.service.runner"
        - name: "taskq_api.service.auth"
        - name: "taskq_api.service.ratelimit"
      allowed_dependencies: ["repository", "models", "independence"]

    - name: repository
      modules:
        - name: "taskq_api.repository.session"
        - name: "taskq_api.repository.task_repo"
        - name: "taskq_api.repository.key_repo"
        - name: "taskq_api.repository.rate_repo"
      allowed_dependencies: ["models", "independence"]

    - name: models
      modules:
        - name: "taskq_api.models.orm"
        - name: "taskq_api.models.schemas"
      allowed_dependencies: ["independence"]

    - name: independence
      modules:
        - name: "taskq_api.config"
        - name: "taskq_api.errors"
      allowed_dependencies: []

  allowed_dependencies:
    - from: api
      to: service
    - from: api
      to: repository
    - from: api
      to: models
    - from: api
      to: independence
    - from: service
      to: repository
    - from: service
      to: models
    - from: service
      to: independence
    - from: repository
      to: models
    - from: repository
      to: independence
    - from: models
      to: independence

  quality_targets:
    max_complexity: 10        # per-function CC, NFR-11 AC-11.1
    min_coverage: 100         # full unit coverage, SPEC §8 #2
    max_coupling: 0.3         # CRG community cohesion threshold

  nfr_dimension_mapping: {}

  nfr_traceability:
    NFR-01:
      type: performance
      dimension: performance
      target: "p95 < 30ms (GET by id, 10k rows); p95 < 80ms (list, 10k rows)"
      module: taskq_api.repository.task_repo
    NFR-02:
      type: security
      dimension: security
      target: "==0 bandit HIGH; ==0 bandit MEDIUM"
      module: taskq_api.api.deps
    NFR-03:
      type: reliability
      dimension: error_handling
      target: "==0 bare-except / swallow"
      module: taskq_api.repository.session
    NFR-04:
      type: security
      dimension: security
      target: "==0 secret-leak events"
      module: taskq_api.errors
    NFR-05:
      type: documentation
      dimension: documentation
      target: ">=100% public docstrings with [FR-XX]/[NFR-XX]"
      module: taskq_api.api.tasks
    NFR-06:
      type: layering
      dimension: architecture_constraints
      target: "lint-imports exit 0"
      module: taskq_api.api
    NFR-07:
      type: licensing
      dimension: license_compliance
      target: "==0 license violations"
      module: taskq_api.config
    NFR-08:
      type: mutation
      dimension: mutation_testing
      target: ">=70 mutation score"
      scope_layers: ["service", "repository"]  # NFR-08 AC-08.3: mutation scope limited to service/+repository/
      module: taskq_api.service.tasks
    NFR-09:
      type: testability
      dimension: test_assertion_quality
      target: "==0 skipped; ==0 zero-assert tests"
      module: taskq_api.repository.session
    NFR-10:
      type: integration
      dimension: integration_coverage
      target: ">=80% integration line coverage"
      module: taskq_api.api.tasks
    NFR-11:
      type: maintainability
      dimension: readability
      target: ">=80 MI (LLOC-weighted); <=10 per-function CC"
      module: taskq_api.api.tasks
    NFR-12:
      type: verifiability
      dimension: execute_verification_target
      target: "verify-system: PASS on stdout"
      module: taskq_api.app

  advisory_only: []

  gate_score_overrides: {}

  fr_module_traceability:
    FR-01:
      - "taskq_api.api.tasks"
      - "taskq_api.service.tasks"
    FR-02:
      - "taskq_api.api.tasks"
      - "taskq_api.service.tasks"
      - "taskq_api.service.runner"
    FR-03:
      - "taskq_api.api.deps"
      - "taskq_api.service.auth"
    FR-04:
      - "taskq_api.api.deps"
      - "taskq_api.service.auth"
    FR-05:
      - "taskq_api.api.deps"
      - "taskq_api.service.ratelimit"
    FR-06:
      - "taskq_api.repository.session"
    FR-07:
      - "migrations.versions.v1_initial"
      - "migrations.versions.v2_tags"
      - "migrations.versions.v3_split_results"
    FR-08:
      - "taskq_api.service.runner"
    FR-09:
      - "taskq_api.api.health"
    FR-10:
      - "taskq_api.errors"
      - "taskq_api.api.deps"

  architecture_constraints:
    - "no_circular_dependencies"
    - "api_over_service_over_repository_over_models"
    - "sqlalchemy_only_in_repository"
    - "config_and_errors_are_independent"

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

`taskq-api` is a publicly-exposed HTTP service (FR-03, FR-04, FR-05),
holds authentication credentials (API keys, FR-03 / NFR-02), accepts
subprocess commands from authenticated clients (FR-02, FR-08), and
persists state to a relational DB (FR-06, FR-07). It has a real
attack surface; `applicability: full` is declared.

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full
  justification: ""
  trust_boundaries:
    - id: TB-01
      name: "unauthenticated external HTTP"
      description: "requests crossing the network boundary from untrusted clients into the FastAPI app; /healthz and /readyz live here unauthenticated"
    - id: TB-02
      name: "authenticated /v1 endpoint surface"
      description: "all /v1/* paths behind the X-API-Key + scope dependency (FR-03, FR-04)"
    - id: TB-03
      name: "subprocess execution"
      description: "authenticated user-supplied command strings executed via asyncio.create_subprocess_exec (FR-02, FR-08)"
    - id: TB-04
      name: "database persistence"
      description: "the relational DB (SQLite dev / PostgreSQL prod) holding tasks, api_keys, rate_buckets, task_results (FR-06, FR-07)"
    - id: TB-05
      name: "log and metrics emission"
      description: "structured logs and /v1/metrics response bodies (NFR-04, FR-09)"
  threats:
    - id: T-01
      boundary: TB-01
      category: spoofing
      description: "unauthenticated request presents a forged or absent X-API-Key to gain access"
      mitigation: "single authentication dependency (api/deps.py) requires X-API-Key for every /v1 route; missing/invalid returns 401 problem+json (FR-03 AC-3.1)"
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_sec_t01_unauthenticated_request_rejected_with_401"
    - id: T-02
      boundary: TB-02
      category: elevation_of_privilege
      description: "read-scoped key attempts write/admin-only endpoints (POST /v1/tasks, DELETE /v1/tasks/{id}, POST /v1/tasks/{id}/run, GET /v1/metrics)"
      mitigation: "single scope-check dependency (api/deps.py) enforces read < write < admin hierarchy; insufficient scope returns 403 with body that does not leak resource existence (FR-04 AC-4.2, NFR-02 AC-02.4)"
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_sec_t02_insufficient_scope_returns_403_without_existence_leak"
    - id: T-03
      boundary: TB-03
      category: tampering
      description: "authenticated user supplies a command string designed to escape argument parsing (shell metacharacters, command substitution)"
      mitigation: "asyncio.create_subprocess_exec with *shlex.split(command) is the only execution path; shell=True is project-wide forbidden (grep gate); ORM-only data access forbids SQL concatenation (FR-02 AC-2.2, NFR-02 AC-02.1, AC-02.2)"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-02
      verified_by: "test_sec_t03_shell_metacharacters_do_not_cause_injection"
    - id: T-04
      boundary: TB-04
      category: information_disclosure
      description: "SQL injection or string-concatenated SQL leaks data or corrupts state"
      mitigation: "string-concatenated SQL is project-wide forbidden; ORM and parameterised queries only; explicit grep gate (NFR-02 AC-02.2); explicit eager-loading (selectinload/joinedload) prevents N+1 that could be probed for timing"
      owner_module: "taskq_api.repository.task_repo"
      nfr: NFR-02
      verified_by: "test_sec_t04_no_string_concatenated_sql_in_repository"
    - id: T-05
      boundary: TB-02
      category: information_disclosure
      description: "API-key plaintext recoverable from the database or log files"
      mitigation: "keys stored as SHA-256 hash only (api_keys.key_hash); comparison via hmac.compare_digest (constant-time); plaintext printed once at key-create time only and never persisted (FR-03 AC-3.2, AC-3.3, NFR-02 AC-02.3, NFR-04 AC-04.3)"
      owner_module: "taskq_api.service.auth"
      nfr: NFR-04
      verified_by: "test_sec_t05_api_keys_table_contains_no_plaintext"
    - id: T-06
      boundary: TB-05
      category: information_disclosure
      description: "sensitive tokens (sk-…, token=…, Bearer …, postgres://…) or DB connection string leak into stdout/stderr/log/error body/metrics"
      mitigation: "redaction regex (sk-[A-Za-z0-9_-]{8,}|token=\\S+|Bearer\\s+\\S+|postgres(ql)?://[^\\s]+) applied before any write or emit; DB URL never logged or returned in /v1/metrics (NFR-04 AC-04.1, AC-04.2)"
      owner_module: "taskq_api.errors"
      nfr: NFR-04
      verified_by: "test_sec_t06_sensitive_tokens_and_db_url_redacted_from_logs_and_metrics"
    - id: T-07
      boundary: TB-02
      category: repudiation
      description: "request cannot be correlated to server-side action because correlation_id is missing or mismatched"
      mitigation: "correlation_id is generated per request, included in X-Correlation-Id response header and in every server log line for that request (FR-10 AC-10.4)"
      owner_module: "taskq_api.errors"
      nfr: NFR-02
      verified_by: "test_sec_t07_correlation_id_present_in_response_and_logs"
    - id: T-08
      boundary: TB-02
      category: information_disclosure
      description: "error response body leaks SQL, stack trace, file path, or DB schema"
      mitigation: "RFC 7807 envelope with allowlisted detail fields; errors.problem_response sanitises detail before serialization (FR-10 AC-10.3, NFR-02 AC-02.5)"
      owner_module: "taskq_api.errors"
      nfr: NFR-02
      verified_by: "test_sec_t08_error_body_omits_stack_sql_path"
    - id: T-09
      boundary: TB-01
      category: denial_of_service
      description: "unauthenticated burst against /v1/* exhausts rate-limit bucket, db connections, or worker capacity"
      mitigation: "per-token token bucket (TASKQ_RATE_BURST, TASKQ_RATE_PER_SEC) with row-level lock and 429+Retry-After (FR-05 AC-5.1..5.3); TASKQ_MAX_CONCURRENT semaphore (FR-08 AC-8.2); pool_pre_ping + pool_size (FR-06 AC-6.5)"
      owner_module: "taskq_api.service.ratelimit"
      nfr: NFR-02
      verified_by: "test_sec_t09_burst_exceeding_burst_returns_429_with_retry_after"
    - id: T-10
      boundary: TB-02
      category: elevation_of_privilege
      description: "browser-origin cross-site request bypasses authn/authz via permissive CORS"
      mitigation: "CORS defaults to deny-all origins; TASKQ_CORS_ORIGINS allowlist is required to enable any cross-origin access (NFR-02 AC-02.6)"
      owner_module: "taskq_api.app"
      nfr: NFR-02
      verified_by: "test_sec_t10_cors_default_deny_all_origins"
    - id: T-11
      boundary: TB-04
      category: tampering
      description: "rate-limit row updated without row-level lock leads to over-admission under concurrency"
      mitigation: "rate_repo.take_token issues SELECT … FOR UPDATE within a single transaction (FR-05 AC-5.3, R12)"
      owner_module: "taskq_api.repository.rate_repo"
      nfr: NFR-02
      verified_by: "test_sec_t11_rate_bucket_concurrent_take_does_not_over_admit"
```
<!-- SEC:END -->

Note: `owner_module` must name a module declared in the §5 SAB block;
`nfr` (optional) must exist in SRS.md; `verified_by` names the test that
proves the mitigation — from Phase 5 onward, `check-artifact-consistency`
blocks if that test doesn't exist yet. Threats also seed
`bug-hunt-targets`' adversarial-review targeting and force NFR-pattern
test cases in `derive_test_cases.md` Step 1c regardless of SRS keywords.

---

*End of SAD. Phase 2 deliverable. Source of truth: `SPEC.md` v1.0.0
(2026-07-30) and `01-requirements/SRS.md` (10 FR / 12 NFR). Next
phases: ADR.md (architecture decisions), TEST_SPEC.md (test design).*