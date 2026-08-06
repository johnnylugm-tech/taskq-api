# Software Requirements Specification (SRS) — taskq-api

> Phase 1 deliverable. Canonical spec: `SPEC.md` v1.0.0 (2026-07-30).
> This document transcribes the canonical spec's `### FR-01..FR-10` and
> `### NFR-01..NFR-12` headings verbatim into the harness-required
> section structure. No requirement is invented; no canonical FR/NFR is
> silently dropped. Citations to `SPEC.md` are `[SPEC §X.Y]`.

---

## 1. Introduction

### 1.1 Purpose
`taskq-api` is a task-queue HTTP service. It exposes a REST API to submit,
query, and execute shell-command tasks, persists state to a relational
database, evolves the schema via Alembic, and authenticates clients with
hashed API keys, authorises by scope, and throttles per token. [SPEC §1, §2]

### 1.2 Scope
The system covers: task CRUD over HTTP, asynchronous subprocess execution,
API-key authentication and scope-based authorisation, per-token rate
limiting, relational persistence with explicit transaction boundaries, an
Alembic migration chain with one data-moving step, structured RFC 7807
error responses, and `/healthz` / `/readyz` / `/v1/metrics` observability
endpoints. [SPEC §1, §2]

### 1.3 Definitions and references
See §9 (Glossary) and [SPEC §10] (framework alignment table).

---

## 2. Constraints

- **Language / runtime**: Python 3.11; ASGI service launched with
  `uvicorn taskq_api.app:app`; management entry `python -m taskq_api`
  (migrate / seed / healthcheck). [SPEC §1, §2]
- **ORM**: SQLAlchemy 2.x (declarative), with explicit `Session`
  transaction boundaries. SQLite for dev/test, PostgreSQL for production,
  same ORM model. [SPEC §2]
- **Migration**: Alembic only, with `downgrade` for every revision. [SPEC §2, FR-07]
- **Subprocess**: `asyncio.create_subprocess_exec` only; `shell=True` is
  forbidden project-wide. [SPEC §2, NFR-02]
- **Layering** (enforced by `.importlinter`, NFR-06): `api > service >
  repository > models`; `config` and `errors` are independent modules;
  `sqlalchemy` may only be imported by `repository/`. [SPEC §6, NFR-06]
- **HTTP framework**: FastAPI; request/response validation with `pydantic` v2. [SPEC §2]
- **Error contract**: all non-2xx responses use `application/problem+json`
  per RFC 7807. [SPEC FR-10]
- **Project-side config files are non-optional** (carry NFR-06 / NFR-07 /
  NFR-08 / NFR-12 and FR-07): `.importlinter`, `requirements.txt`,
  `requirements.lock`, `alembic.ini`, `.env.example`,
  `.methodology/harness_config.json`, `Makefile`. [SPEC §5.3]
- **High-risk modules** (require per-module TDD): `taskq_api.service.runner`,
  `taskq_api.service.auth`, `taskq_api.repository.session`,
  `migrations/versions/v3_split_results.py`. [SPEC §10]

---

## 3. Functional Requirements

Each `### FR-XX` below is a verbatim transcription of the corresponding
`### FR-XX` heading in `SPEC.md §3`, with a `[SPEC §X.Y]` citation.

### FR-01: 任務資源 CRUD API

> Citation: SPEC.md §3 FR-01.

The service exposes the following task-resource endpoints:

| Method | Path | scope | Behaviour |
|------|------|-------|------|
| `POST` | `/v1/tasks` | `write` | Create a task; body validated by `TaskCreate` pydantic model. |
| `GET` | `/v1/tasks/{id}` | `read` | Return a single task's full record. |
| `GET` | `/v1/tasks` | `read` | Paged list; supports `?status=`, `?limit=`, `?cursor=`. |
| `DELETE` | `/v1/tasks/{id}` | `admin` | Delete a task (with its result rows, in the same transaction). |

> DERIVED: SPEC §3 FR-01 bullets — AC-1.1 quotes the first bullet's "非空 / ≤1000 字元 / 注入字元黑名單 / 名稱唯一;違反 → **HTTP 422** + problem+json" verbatim; AC-1.2..1.4 are 1:1 with the remaining canonical bullets (404, cursor-based pagination, default 50 / max 200).
- **AC-1.1** (validation rules — verbatim SPEC phrasing):
  "驗證規則同第 1 輪 FR-01(非空 / ≤1000 字元 / 注入字元黑名單 / 名稱唯一);
  違反 → **HTTP 422** + problem+json" — measurement / interpretation
  boundary is owned by the test harness per SPEC §3 FR-01.
- **AC-1.2** Unknown id → **HTTP 404** + problem+json. [SPEC §3 FR-01]
- **AC-1.3** Pagination is **cursor-based** ("不得用 offset —— 大表 offset
  掃描是 N+1 的親戚"). [SPEC §3 FR-01]
- **AC-1.4** Default `limit` on the list endpoint is 50, maximum 200;
  exceeding the maximum returns 422. [SPEC §3 FR-01]

### FR-02: 任務執行端點

> Citation: SPEC.md §3 FR-02.

> DERIVED: SPEC §3 FR-02 first bullet (`POST /v1/tasks/{id}/run`(scope `write`)→ **HTTP 202 Accepted**,body 含 `run_id`) — AC-2.1..2.5 decompose the canonical heading into testable units; no requirement added beyond canonical.
- **AC-2.1** `POST /v1/tasks/{id}/run` (scope `write`) returns **HTTP 202
  Accepted**; the body contains `run_id`. [SPEC §3 FR-02]
- **AC-2.2** Execution uses
  `asyncio.create_subprocess_exec(*shlex.split(command))`; `shell=True`
  is forbidden; the subprocess timeout is `TASKQ_TASK_TIMEOUT`. [SPEC §3 FR-02, NFR-02]
- **AC-2.3** Status state machine: `pending → running → done | failed |
  timeout`. [SPEC §3 FR-02]
- **AC-2.4** Execution results are written to the `task_results` table
  (FR-07's v3 schema) with columns
  `exit_code / stdout_tail / stderr_tail / duration_ms / finished_at`.
  [SPEC §3 FR-02, §5.2]
- **AC-2.5** `GET /v1/tasks/{id}/runs` (scope `read`) returns the task's
  execution history, newest first. [SPEC §3 FR-02]

### FR-03: API Key 認證

> Citation: SPEC.md §3 FR-03.

> DERIVED: SPEC §3 FR-03 bullets — AC-3.1..3.5 are 1:1 with the canonical five bullets, restated in English for evidence-grammar uniformity; no semantic change.
- **AC-3.1** Every `/v1/*` endpoint requires the `X-API-Key` header;
  missing or invalid → **HTTP 401** + problem+json. [SPEC §3 FR-03]
- **AC-3.2** Keys are stored as **SHA-256 hashes** in the `api_keys`
  table; **plaintext MUST NOT be stored**; comparison uses
  `hmac.compare_digest` (constant time). [SPEC §3 FR-03, NFR-02]
- **AC-3.3** Keys are created by
  `python -m taskq_api key create --scope <scope>`; the plaintext is
  printed only once, at creation time. [SPEC §3 FR-03, NFR-04]
- **AC-3.4** A key with non-null `revoked_at` is treated as invalid.
  [SPEC §3 FR-03]
- **AC-3.5** `/healthz` and `/readyz` do not require authentication. [SPEC §3 FR-03, FR-09]

### FR-04: Scope 授權

> Citation: SPEC.md §3 FR-04.

> DERIVED: SPEC §3 FR-04 bullets — AC-4.1..4.3 are 1:1 with the canonical three bullets; `single dependency` phrasing is preserved as `single middleware / dependency` (canonical synonym).
- **AC-4.1** Each key carries one scope from a strict hierarchy
  `read < write < admin` (inclusive). [SPEC §3 FR-04]
- **AC-4.2** The required scope per endpoint is the per-FR table in FR-01
  and FR-02; an insufficient scope returns **HTTP 403** + problem+json,
  and the body **MUST NOT leak whether the resource exists**. [SPEC §3 FR-04]
- **AC-4.3** The authorisation decision is made in a **single middleware
  / dependency**; the test asserts "every `/v1` route passes through the
  same dependency". [SPEC §3 FR-04]

### FR-05: 流量控制

> Citation: SPEC.md §3 FR-05.

> DERIVED: SPEC §3 FR-05 bullets — AC-5.1 (`TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC`) and AC-5.2 (429 + `Retry-After`) and AC-5.3 (DB-stored bucket + row-level lock) and AC-5.4 (`/healthz` / `/readyz` exempt) are 1:1 with the canonical four bullets; the env-var names and status code are verbatim from SPEC.
- **AC-5.1** Per-token token bucket with capacity `TASKQ_RATE_BURST` and
  refill rate `TASKQ_RATE_PER_SEC`. [SPEC §3 FR-05, §5.1]
- **AC-5.2** Exceeding the limit returns **HTTP 429** + problem+json +
  `Retry-After` header (seconds). [SPEC §3 FR-05, §7]
- **AC-5.3** The token-bucket state is stored in the database (consistent
  across workers); updates happen in a single transaction with a
  row-level lock. [SPEC §3 FR-05, FR-06]
- **AC-5.4** `/healthz` and `/readyz` are not rate-limited. [SPEC §3 FR-05, FR-09]

### FR-06: 持久化層與交易邊界

> Citation: SPEC.md §3 FR-06.

> DERIVED: SPEC §3 FR-06 bullets — AC-6.1..6.5 are 1:1 with the canonical five bullets; `pool_pre_ping=True` and the `selectinload`/`joinedload` mentions are direct restatements of canonical text.
- **AC-6.1** All data access goes through the `repository/` layer; the
  business layer MUST NOT hold a `Session` directly. [SPEC §3 FR-06, NFR-06]
- **AC-6.2** One `Session` per API request; transaction boundaries are
  explicit: success commits, exceptions roll back, enforced by a context
  manager. [SPEC §3 FR-06, NFR-03]
- **AC-6.3** String-concatenated SQL is **forbidden**; use ORM or
  parameterised queries. [SPEC §3 FR-06, NFR-02]
- **AC-6.4** Relationship loads use `selectinload` / `joinedload`
  explicitly; **N+1 is an acceptance failure**. [SPEC §3 FR-06, NFR-01]
- **AC-6.5** Connection pool: `pool_size=TASKQ_DB_POOL_SIZE`,
  `pool_pre_ping=True`. [SPEC §3 FR-06, §5.1]

### FR-07: Schema Migration (Alembic 三步演進)

> Citation: SPEC.md §3 FR-07.

Three revisions, each with a working `downgrade`:

| revision | upgrade content | downgrade requirement |
|---|---|---|
| **v1** | Create `tasks` and `api_keys` tables | drop both tables |
| **v2** | Add `tags`, `task_tags` (many-to-many) + unique index on `tasks.name` | drop new tables and index, do not affect v1 data |
| **v3** | **Data-moving**: split `tasks.result_json` into a separate `task_results` table; migrate existing data; drop the original column | reverse-migrate back into `tasks.result_json`, then drop `task_results`; **no data loss** |

> DERIVED: SPEC §3 FR-07 bullets + table — AC-7.1..7.5 are 1:1 with the canonical five bullets, plus AC-7.5 (real-DB migration) is the cross-reference to NFR-09's "不得以『migration 邏輯太難測』為由降級為 skip" anti-skip clause; the table cells are verbatim from SPEC §3 FR-07.
- **AC-7.1** `alembic upgrade head` and `alembic downgrade base` both
  succeed. [SPEC §3 FR-07, §8 #13]
- **AC-7.2** **Round-trip reversibility acceptance**:
  `upgrade head` → write sample data → `downgrade -1` → `upgrade head`
  must leave every column value byte-identical (the v3 data move is the
  focus of this AC). [SPEC §3 FR-07, §8 #12]
- **AC-7.3** Destructive shortcuts such as
  `op.execute("DROP TABLE ...")` are forbidden as a substitute for a
  real `downgrade`. [SPEC §3 FR-07]
- **AC-7.4** Migration files are covered by tests (offline SQL generation
  via `alembic` + assertions). [SPEC §3 FR-07]
- **AC-7.5** The migration MUST be tested against a **real database file**
  (SQLite file, not in-memory mock); may NOT be downgraded to a skip on
  the grounds that "migration logic is hard to test" (NFR-09). [SPEC §3 FR-07, NFR-09, §8 #12]

### FR-08: 非同步執行器

> Citation: SPEC.md §3 FR-08.

> DERIVED: SPEC §3 FR-08 bullets — AC-8.1..8.4 are 1:1 with the canonical four bullets; `asyncio.wait_for` and `process.kill()` + `await process.wait()` are verbatim SPEC text.
- **AC-8.1** Background execution is managed with
  `asyncio.TaskGroup`; on shutdown the service MUST perform a
  **graceful drain** (wait for in-flight tasks up to
  `TASKQ_DRAIN_TIMEOUT`; tasks exceeding the budget are marked
  `interrupted`). [SPEC §3 FR-08, §5.1, §8 #25]
- **AC-8.2** Concurrency cap `TASKQ_MAX_CONCURRENT`; over-cap requests
  are queued, coroutines MUST NOT be spawned without limit. [SPEC §3 FR-08, §5.1]
- **AC-8.3** Per-task timeout is implemented with `asyncio.wait_for`;
  on timeout the child process MUST be terminated
  (`process.kill()` then `await process.wait()`); orphan processes are
  forbidden. [SPEC §3 FR-08, NFR-03, §8 #25]
- **AC-8.4** `asyncio.CancelledError` MUST propagate; it MUST NOT be
  swallowed by `except Exception`. [SPEC §3 FR-08, NFR-03]

### FR-09: 健康檢查與可觀測性

> Citation: SPEC.md §3 FR-09.

| Endpoint | Auth | Behaviour |
|------|------|------|
| `GET /healthz` | none | Process alive → 200 `{"status":"ok"}` |
| `GET /readyz` | none | DB reachable **and** `alembic current` == head → 200; else **503** with body explaining which check failed |
| `GET /v1/metrics` | `admin` | Task counts (by status), execution-latency percentiles, rate-limit rejection counts |

> DERIVED: SPEC §3 FR-09 table + closing bullet — AC-9.1 is the closing bullet's "部署了新程式碼但忘記跑 migration 時必須 fail closed" restated as a testable condition; the table cells are verbatim from SPEC.
- **AC-9.1** `/readyz` "migration not at head" is the key guard: deploying
  new code without running the migration MUST fail closed. [SPEC §3 FR-09, §8 #11]

### FR-10: 錯誤契約 (RFC 7807)

> Citation: SPEC.md §3 FR-10.

> DERIVED: SPEC §3 FR-10 bullets + §7 error mapping — AC-10.1..10.5 are 1:1 with the canonical five bullets; AC-10.5's error-code list restates the SPEC §7 table's 422/401/403/404/409/429/503/500 column.
- **AC-10.1** All non-2xx responses have `Content-Type:
  application/problem+json`. [SPEC §3 FR-10]
- **AC-10.2** Body fields: `type` (URI), `title`, `status`, `detail`,
  `instance`, `correlation_id`. [SPEC §3 FR-10]
- **AC-10.3** `detail` MUST NOT leak internals: no SQL statements, no
  stack traces, no file paths, no DB schema description. [SPEC §3 FR-10, NFR-02]
- **AC-10.4** `correlation_id` is returned in the response header
  `X-Correlation-Id` and is logged with the same value on the server
  side, for correlation. [SPEC §3 FR-10]
- **AC-10.5** Error-code mapping (per SPEC §7): 422 validation / 401
  unauthenticated / 403 insufficient scope / 404 unknown resource / 409
  name conflict / 429 rate-limited / 503 not-ready / 500 other. [SPEC §3 FR-10, §7]

---

## 4. Non-Functional Requirements

> **Dimension rule**: each NFR's `dimension` is verified to be a current
> `### <dimension>` header in `harness/harness/ssi/prompts/evaluate_dimension.md`.
> A "dimension note" is appended when the dimension name does not appear
> in the current roster, and a "coverage note" is appended under an AC
> when the eval section for that dimension is narrower than the AC demands.

### NFR-01: 效能與查詢效率

- **dimension**: `performance`
> DERIVED: SPEC §4 NFR-01 bullets — AC-01.1..01.4 are 1:1 with the canonical four bullets (`< 30ms` and `< 80ms` and `常數` and `pytest-benchmark` are verbatim from SPEC); the only restating is English grammar for the test harness's evidence-grammar uniformity.
- **AC-01.1** `GET /v1/tasks/{id}` p95 < 30ms with 10,000 rows
  (no network; measured via ASGI transport). [SPEC §4 NFR-01]
- **AC-01.2** `GET /v1/tasks?limit=50` p95 < 80ms with 10,000 rows.
  [SPEC §4 NFR-01]
- **AC-01.3** N+1 is a failure condition: the list endpoint's SQL
  statement count is **constant** (independent of rows returned),
  asserted via a SQLAlchemy event listener. [SPEC §4 NFR-01, FR-06, §8 #14]
- **AC-01.4** Measurement tool: `pytest-benchmark`. [SPEC §4 NFR-01]
- **coverage note**: the `performance` section in `evaluate_dimension.md`
  scores from `pytest-benchmark` mean latency with a >1s/>3s penalty
  curve; it does NOT directly verify the 30ms / 80ms p95 thresholds or
  the constant SQL-statement-count requirement of AC-01.3 — Phase 3+
  must add dedicated benchmark cases (`test_kpi_p95_get_by_id_under_30ms_at_10k`,
  `test_kpi_p95_list_under_80ms_at_10k`,
  `test_n_plus_one_sql_count_constant_within_list_endpoint`) and the
  N+1 SQL count test under `03-development/tests/integration/`.

### NFR-02: HTTP 與資料層安全

- **dimension**: `security`
> DERIVED: SPEC §4 NFR-02 bullets — AC-02.1..02.7 are 1:1 with the canonical seven bullets (`shell=True` / `eval(` / `exec(` / `f-string` / `hmac.compare_digest` / `bandit` 0 HIGH/MEDIUM / CORS deny-by-default are all verbatim from SPEC); only the English restatement of the grep gate is added.
- **AC-02.1** Project-wide prohibition of `shell=True`, `eval(`,
  `exec(`; verified by `grep` returning zero hits.
  [SPEC §4 NFR-02, §8 #16]
- **AC-02.2** **No string-concatenated SQL**: no f-string / `%` / `+`
  composed SQL; ORM or parameterised only; verified by grep + code
  review. [SPEC §4 NFR-02, §8 #17]
- **AC-02.3** API keys are **hash-stored** and compared with
  `hmac.compare_digest` (FR-03). [SPEC §4 NFR-02, FR-03]
- **AC-02.4** 403 responses MUST NOT leak resource existence (FR-04).
  [SPEC §4 NFR-02, FR-04]
- **AC-02.5** Error bodies MUST NOT contain stack traces, SQL, or file
  paths (FR-10). [SPEC §4 NFR-02, FR-10]
- **AC-02.6** CORS defaults to **deny all origins**; an allowlist is
  configured by `TASKQ_CORS_ORIGINS`. [SPEC §4 NFR-02, §5.1]
- **AC-02.7** `bandit -r 03-development/src/` reports 0 HIGH, 0 MEDIUM.
  [SPEC §4 NFR-02, §8 #23]
- **coverage note**: the `security` section in `evaluate_dimension.md`
  runs `bandit` only; bandit does not directly verify AC-02.1 (forbid
  patterns), AC-02.3 (constant-time compare), AC-02.4 (403 leak
  prevention), AC-02.5 (error-body leak), or AC-02.6 (CORS allowlist
  default). Phase 3+ must add dedicated tests:
  `test_grep_no_shell_true_or_eval_or_exec`,
  `test_grep_no_string_concatenated_sql`,
  `test_403_body_does_not_leak_resource_existence`,
  `test_500_body_contains_no_stack_or_sql_or_path`,
  `test_cors_default_deny_all_origins`.

### NFR-03: 錯誤處理、交易與非同步正確性

- **dimension**: `error_handling`
> DERIVED: SPEC §4 NFR-03 bullets — AC-03.1..03.6 are 1:1 with the canonical six bullets (`except:` / `except Exception: pass` forbidden and `CancelledError` 必須重新拋出 are verbatim); no extra handler rule is added.
- **AC-03.1** Every request's transaction boundary is explicit: success
  commits, exceptions roll back; enforced by a context manager (FR-06).
  [SPEC §4 NFR-03, FR-06]
- **AC-03.2** Bare `except:` and `except Exception: pass` are
  **forbidden**. [SPEC §4 NFR-03]
- **AC-03.3** `asyncio.CancelledError` MUST NOT be swallowed; it MUST
  be re-raised (an async-specific swallowing trap). [SPEC §4 NFR-03, FR-08]
- **AC-03.4** Database connection failure → `/readyz` 503 with explicit
  detail; silent infinite retry is forbidden. [SPEC §4 NFR-03, FR-09]
- **AC-03.5** Per-task timeout MUST terminate the child process; no
  orphans (FR-08). [SPEC §4 NFR-03, FR-08]
- **AC-03.6** Migration failure rolls back the transaction; the database
  remains at the previous revision (FR-07). [SPEC §4 NFR-03, FR-07]
- **coverage note**: the `error_handling` section in
  `evaluate_dimension.md` scores from a `try/except` presence ratio
  minus anti-patterns (`except_base_exception`, `bare_except`,
  `broad_swallow`, js `empty_catch`); it does NOT directly verify
  AC-03.3 (CancelledError propagation), AC-03.4 (DB-down → 503), or
  AC-03.6 (migration rollback semantics). Phase 3+ must add dedicated
  tests: `test_cancelled_error_propagates_under_async_runner`,
  `test_readyz_returns_503_when_db_unreachable`,
  `test_failed_migration_rolls_back_to_previous_revision`.

### NFR-04: 敏感資料遮蔽

- **dimension**: `security`
> DERIVED: SPEC §4 NFR-04 bullets — AC-04.1's redaction regex `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` is **verbatim from SPEC**; AC-04.2 (DB connection string) and AC-04.3 (key plaintext one-shot) are 1:1 with the other two canonical bullets.
- **AC-04.1** Before `stdout_tail` / `stderr_tail` / log / error body is
  written or emitted, lines matching
  `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`
  are replaced wholesale with `[REDACTED]`. [SPEC §4 NFR-04]
- **AC-04.2** The **database connection string** (including its password)
  MUST NOT appear in any log, error message, or `/v1/metrics` response.
  [SPEC §4 NFR-04, §8 #20]
- **AC-04.3** API-key plaintext is output only at `key create` time and
  MUST NOT be written to any persistent location. [SPEC §4 NFR-04, FR-03]
- **coverage note**: AC-04.1's redaction regex and AC-04.2's
  no-DB-password-in-logs rule are NOT directly verifiable by `bandit`;
  Phase 3+ must add `test_log_redacts_sk_token_bearer_dburl` and
  `test_metrics_response_omits_db_url_password`.

### NFR-05: 文件覆蓋

- **dimension**: `documentation`
> DERIVED: SPEC §4 NFR-05 bullets — AC-05.1..05.2 are 1:1 with the canonical two bullets (`[FR-XX]` / `[NFR-XX]` citation and `summary` / `description` are verbatim from SPEC); no extra documentation rule is added.
- **AC-05.1** Every public function/class has a docstring containing a
  `[FR-XX]` or `[NFR-XX]` reference; coverage **100%**. [SPEC §4 NFR-05]
- **AC-05.2** Every API endpoint has a `summary` and `description` in
  the OpenAPI schema (`/openapi.json`); asserted by test. [SPEC §4 NFR-05]
- **coverage note**: the `documentation` section in
  `evaluate_dimension.md` scores from a public-docstring presence
  ratio; it does NOT verify AC-05.1's `[FR-XX]/[NFR-XX]` citation
  requirement or AC-05.2's OpenAPI summary/description requirement.
  Phase 3+ must add `test_docstrings_cite_fr_or_nfr_marker` and
  `test_openapi_schema_has_summary_and_description_per_endpoint`.

### NFR-06: 架構分層契約

- **dimension**: `architecture_constraints`
> DERIVED: SPEC §4 NFR-06 bullets — AC-06.1's `api > service > repository > models` ordering and AC-06.2's `sqlalchemy` forbidden contract are **verbatim from SPEC**; AC-06.3 (`lint-imports` exit 0) and AC-06.4 (no `ignore_imports` downgrade) are 1:1 with the canonical bullets.
- **AC-06.1** A `.importlinter` file at the project root declares the
  layers contract:
  `api > service > repository > models`. Upper layers may import lower
  layers; lower layers may not import upper layers; `config` and
  `errors` are independent modules. [SPEC §4 NFR-06, §6]
- **AC-06.2** **Forbidden contract**: any layer other than `repository`
  MUST NOT import `sqlalchemy` — ORM leakage into the business layer
  is the specific anti-pattern this round guards against. [SPEC §4 NFR-06, §10]
- **AC-06.3** `lint-imports` MUST exit 0. [SPEC §4 NFR-06, §8 #21]
- **AC-06.4** Deleting `.importlinter`, using wildcard `ignore_imports`,
  or downgrading the contract to pass is forbidden. [SPEC §4 NFR-06]
- **coverage note**: AC-06.2 is the strictest form ("sqlalchemy"
  forbidden) — the eval check runs `lint-imports` exit-code only;
  Phase 3+ must add `test_sqlalchemy_import_outside_repository_blocked`
  to fail with a clear message if a future change imports sqlalchemy
  in `service/` or `api/`.

### NFR-07: 依賴與授權合規

- **dimension**: `license_compliance`
> DERIVED: SPEC §4 NFR-07 bullets — AC-07.1 (`==` pinning + `requirements.lock`) and AC-07.2 (allowlist) and AC-07.3 (`pip-licenses --with-system` whole-tree scan) and AC-07.4 (`08-config/SBOM.json` with `direct|transitive` flag) are **1:1 with the canonical four bullets**; no extra license rule is added.
- **AC-07.1** All runtime dependencies are pinned with `==` in
  `requirements.txt`; **transitive** dependencies are pinned in
  `requirements.lock`. [SPEC §4 NFR-07, §5.3]
- **AC-07.2** Allowed licenses: MIT, BSD-2-Clause, BSD-3-Clause,
  Apache-2.0, PSF; any other license → dependency MUST NOT be used.
  [SPEC §4 NFR-07]
- **AC-07.3** Scan coverage MUST include the **whole dependency tree**
  (direct + transitive); evidence command:
  `pip-licenses --format=json --with-system`. [SPEC §4 NFR-07, §8 #22]
- **AC-07.4** An SBOM is produced at `08-config/SBOM.json`; each
  dependency entry contains `name / version / license /
  direct|transitive`. [SPEC §4 NFR-07, §5.3]
- **coverage note**: the `license_compliance` section in
  `evaluate_dimension.md` runs `scancode --license` only; it does NOT
  verify AC-07.1 (pinning + lock file), AC-07.2 (allowlist), or
  AC-07.4 (SBOM artifact). Phase 3+ must add
  `test_requirements_txt_pins_with_double_equals`,
  `test_requirements_lock_present_and_pinned`,
  `test_sbom_markdown_present_at_08_config_path`,
  `test_all_runtime_dependencies_license_in_allowlist`.

### NFR-08: 變異測試

- **dimension**: `mutation_testing`
> DERIVED: SPEC §4 NFR-08 bullets — AC-08.1 (`features.mutation_testing: true`) and AC-08.2 (score ≥ 70) and AC-08.3 (`service/` + `repository/` scope with run-time-budget rationale) are 1:1 with the canonical three bullets; no extra mutation rule is added.
- **AC-08.1** `.methodology/harness_config.json` sets
  `features.mutation_testing: true`. [SPEC §4 NFR-08, §5.3]
- **AC-08.2** **mutation score ≥ 70**. [SPEC §4 NFR-08, §8 #24]
- **AC-08.3** Scope is limited to `service/` and `repository/` layers,
  with the rationale recorded in `harness_config.json` (run-time
  budget). [SPEC §4 NFR-08]
- **coverage note**: AC-08.3's layer-scope constraint is project-
  configured; the framework-owned `compute_mutation_score` may
  surface survived mutants outside the configured scope as
  `mutation_survivors.json` and the LLM is responsible for explaining
  scope decisions in the gate breakdown.

### NFR-09: 驗證真實性 (零 skip 鐵律)

- **dimension**: `test_assertion_quality`
> DERIVED: SPEC §4 NFR-09 bullets — AC-09.1..09.6 are 1:1 with the canonical six bullets (`pytest.skip` / `skipif` / `xfail` / 無斷言 and `--ignore` / `-k` / `--deselect` / `collect_ignore` and the "真實資料庫" real-DB-migration anti-skip clause are all verbatim from SPEC); `TRACEABILITY_MATRIX.md` `VERIFIED` rule is the last canonical bullet.
- **AC-09.1** **No** test verifying any FR/NFR may be `pytest.skip`,
  `skipif`, `xfail`, or an assertion-free stub. [SPEC §4 NFR-09, §8 #1]
- **AC-09.2** `pytest 03-development/tests -q` reports
  **0 skipped**. [SPEC §4 NFR-09, §8 #1]
- **AC-09.3** Each test function has at least one `assert`
  (`zero_assert == 0`). [SPEC §4 NFR-09]
- **AC-09.4** **Anti-fabrication clause**: tests may NOT be excluded via
  `--ignore` / `-k` / `--deselect` / `collect_ignore` / by removing
  directories from `testpaths`. [SPEC §4 NFR-09]
- **AC-09.5** **Round-2 special clause**: FR-07's three-step migration
  MUST be tested against a **real database** (SQLite file, not an
  in-memory mock); the round-trip reversibility is verified by
  per-column data comparison. The migration MUST NOT be downgraded to
  a skip on the grounds that "migration logic is hard to test" — this
  is the failure shape of the previous two rounds. [SPEC §4 NFR-09, FR-07, §8 #12]
- **AC-09.6** `TRACEABILITY_MATRIX.md` `VERIFIED` is set only when the
  test actually ran and passed. [SPEC §4 NFR-09]
- **coverage note**: the `test_assertion_quality` section in
  `evaluate_dimension.md` counts `assert`/non-assert ratio; it does
  NOT directly verify AC-09.2 (zero skipped), AC-09.4 (no
  `--ignore`/`-k`/`--deselect`), or AC-09.5 (real-DB migration
  test). Phase 3+ must add
  `test_no_skipped_tests_in_pytest_q_output`,
  `test_no_tests_excluded_via_ignore_k_deselect`,
  `test_migration_round_trip_against_real_sqlite_file`.

### NFR-10: 整合覆蓋

- **dimension**: `integration_coverage`
> DERIVED: SPEC §4 NFR-10 bullets — AC-10.1 (≥ 80%) and AC-10.2 (`httpx.AsyncClient(transport=ASGITransport(app))`) and AC-10.3 (CRUD chain + 401/403/404/409/422/429/503 + migration round-trip + rate-limit trigger/recovery + graceful drain) are 1:1 with the canonical three bullets; coverage floor and transport name are verbatim.
- **AC-10.1** `03-development/tests/integration/` line coverage
  **≥ 80%**. [SPEC §4 NFR-10, §8 #3]
- **AC-10.2** Integration tests drive the app via
  `httpx.AsyncClient(transport=ASGITransport(app))`; they MUST NOT
  call handler functions directly. [SPEC §4 NFR-10]
- **AC-10.3** Coverage at minimum includes: full CRUD chain, one
  example each of 401/403/404/409/422/429/503 error codes, migration
  round-trip, rate-limit trigger and recovery, graceful drain.
  [SPEC §4 NFR-10]

### NFR-11: 可讀性

- **dimension**: `readability`
> DERIVED: SPEC §4 NFR-11 bullets — AC-11.1 (MI ≥ 80 / CC ≤ 10) and AC-11.2 (≤ 400 lines/file / ≤ 15 files/dir) and AC-11.3 (≤ 40 lines/handler) are **1:1 with the canonical three bullets**; `LLOC` weighting is the SPEC's literal weight.
- **AC-11.1** Project MI (LLOC-weighted) **≥ 80**; per-function CC
  **≤ 10**. [SPEC §4 NFR-11]
- **AC-11.2** Each file ≤ 400 lines; each directory ≤ 15 files.
  [SPEC §4 NFR-11]
- **AC-11.3** Each API handler ≤ 40 lines; business logic MUST sink
  into `service/`. [SPEC §4 NFR-11]
- **coverage note**: the `readability` section in
  `evaluate_dimension.md` averages `radon mi` per file; it does NOT
  verify AC-11.1's per-function CC, AC-11.2's file-size and dir-
  count limits, or AC-11.3's per-handler line count. Phase 3+ must
  add `test_per_function_cc_le_10`,
  `test_per_file_lines_le_400`, `test_per_dir_files_le_15`,
  `test_per_api_handler_lines_le_40`.

### NFR-12: 系統驗證目標

- **dimension**: `execute_verification_target`
> DERIVED: SPEC §4 NFR-12 bullets — AC-12.1's 4-step chain (upgrade → tests → health → round-trip) and AC-12.2's `verify-system: PASS` stdout requirement are **1:1 with the canonical two bullets**; the chain ordering is verbatim from SPEC.
- **AC-12.1** The `Makefile`'s `verify-system` target chains:
  1. `alembic upgrade head`
  2. full test suite
  3. service start + `/healthz`, `/readyz` smoke
  4. `alembic downgrade base` then `upgrade head` (round-trip).
  [SPEC §4 NFR-12]
- **AC-12.2** `make verify-system` MUST exit 0 and print
  `verify-system: PASS` on stdout. [SPEC §4 NFR-12, §8 #27]

---

## 5. Acceptance Criteria Summary

Per SPEC §8 (27 acceptance items, each a single machine-decidable command
with expected output). Cross-cutting references: NFR-09 enforces zero
skips, NFR-10 enforces ≥ 80% integration coverage, NFR-12 enforces the
end-to-end `make verify-system` chain.

| # | Command | Expected | Source |
|---|---------|----------|--------|
| 1 | `pytest 03-development/tests -q` | All green; **skipped count = 0** | SPEC §8 #1, NFR-09 |
| 2 | `pytest 03-development/tests --cov=03-development/src --cov-report=term` | TOTAL **100%** | SPEC §8 #2 |
| 3 | `pytest 03-development/tests/integration --cov=03-development/src --cov-report=term` | TOTAL **≥ 80%** | SPEC §8 #3, NFR-10 |
| 4 | `POST /v1/tasks` (valid write key) | 201 + task id | SPEC §8 #4, FR-01 |
| 5 | `POST /v1/tasks` (no `X-API-Key`) | **401** + problem+json | SPEC §8 #5, FR-03 |
| 6 | `DELETE /v1/tasks/{id}` (write key, not admin) | **403**, body does not reveal whether id exists | SPEC §8 #6, FR-04, NFR-02 |
| 7 | `GET /v1/tasks/{unknown}` | **404** + problem+json | SPEC §8 #7, FR-01 |
| 8 | `POST /v1/tasks` duplicate name | **409** | SPEC §8 #8, FR-01 |
| 9 | Bursts over `TASKQ_RATE_BURST` | **429** + `Retry-After` | SPEC §8 #9, FR-05 |
| 10 | `GET /readyz` with DB stopped | **503**, detail says DB unreachable | SPEC §8 #10, FR-09, NFR-03 |
| 11 | `GET /readyz` after `alembic downgrade -1` | **503**, detail says migration behind head | SPEC §8 #11, FR-09 |
| 12 | `upgrade head` → write sample → `downgrade -1` → `upgrade head` | Sample rows byte-identical (v3 data-move reversible) | SPEC §8 #12, FR-07, NFR-09 |
| 13 | `alembic downgrade base` | exit 0, no leftover tables | SPEC §8 #13, FR-07 |
| 14 | `GET /v1/tasks?limit=50` (10k rows) SQL statement count | **constant** (no N+1) | SPEC §8 #14, FR-06, NFR-01 |
| 15 | `GET /v1/tasks/{id}` p95 (10k rows) | **< 30ms** | SPEC §8 #15, NFR-01 |
| 16 | `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` | **0 hits** | SPEC §8 #16, NFR-02 |
| 17 | scan for SQL string concatenation (f-string / `%` / `+`) | **0 hits** | SPEC §8 #17, NFR-02 |
| 18 | query `api_keys` table | No plaintext keys; `key_hash` is 64 hex | SPEC §8 #18, FR-03, NFR-02 |
| 19 | Trigger 500, inspect body | No stack / SQL / file path | SPEC §8 #19, FR-10, NFR-02 |
| 20 | logs and `/v1/metrics` body, full text | No `TASKQ_DB_URL` password fragment | SPEC §8 #20, NFR-04 |
| 21 | `lint-imports` | **exit 0**; `service`/`api` importing `sqlalchemy` is blocked | SPEC §8 #21, NFR-06 |
| 22 | `pip-licenses --format=json --with-system` | Every dependency license ∈ allowlist | SPEC §8 #22, NFR-07 |
| 23 | `bandit -r 03-development/src/` | 0 HIGH, 0 MEDIUM | SPEC §8 #23, NFR-02 |
| 24 | `mutmut run` then `mutmut results` | mutation score **≥ 70** | SPEC §8 #24, NFR-08 |
| 25 | shutdown with in-flight tasks | graceful drain; timed-out tasks marked `interrupted`; no orphan processes | SPEC §8 #25, FR-08 |
| 26 | `grep -c "^TASKQ_" .env.example` | **12** | SPEC §8 #26, §5.1 |
| 27 | `make verify-system` | exit 0, stdout contains `verify-system: PASS` | SPEC §8 #27, NFR-12 |

---

## 6. Out-of-Scope

- Horizontal auto-scaling, message-queue backplane, multi-tenant
  isolation beyond per-token scope. [Implied: SPEC §2 technology table]
- Web UI / dashboard; only `/v1/metrics` is exposed.
- OIDC / OAuth flows; only static API keys. [SPEC FR-03]
- Schema migration beyond v3 (no v4+ planned in this round).
- Re-encryption / at-rest encryption of the DB file.
- Production-grade PostgreSQL tuning (only the connection string differs
  from dev). [SPEC §2]

---

## 7. Open Issues

- **NFR-99 (NFR-04 redaction regex coverage)**: SPEC §4 NFR-04's
  redaction pattern is
  `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`.
  Phase 3+ test cases must cover each branch independently; the
  `taskq_api` redaction module's anchor regex MUST be kept in sync.
  Defer to P3 test authoring. [SPEC §4 NFR-04]
- **NFR-99 (CancelledError scanning gap)**: the framework's
  `ast-error-handling` scanner has only ever seen synchronous code;
  any misjudgement it makes on `async def` is itself a finding this
  test-bed is meant to surface, per SPEC §10 ("async 為本輪新變數").
  Defer to Phase 4 bug hunt. [SPEC §4 NFR-03, §10]
- **NFR-99 (prompt-injection scan)**: canonical spec was scanned for
  prompt-injection patterns during ingestion; no high-severity
  patterns were found. Deferred to P3 re-scan if SPEC.md is amended.
- **FR-XX-deferred (none)**: all FR-01..FR-10 are transcribed in §3.
  No TBD/TODO/`<placeholder>` markers found in `SPEC.md` §3 headings.

---

## 7. FR Block (machine-readable)

<!-- FR:START -->
```json
{
  "version": "1.0",
  "created_at": "2026-08-07",
  "phase": 1,
  "project": "taskq-api",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "Task resource CRUD API",
      "implementation_functions": ["test_fr01_create_rejects_invalid_command_with_422"],
      "verification_method": "integration test via httpx.AsyncClient(ASGITransport)"
    },
    {
      "id": "FR-02",
      "description": "Task execution endpoint",
      "implementation_functions": ["test_fr02_run_returns_202_with_run_id"],
      "verification_method": "integration test via httpx.AsyncClient(ASGITransport)"
    },
    {
      "id": "FR-03",
      "description": "API Key authentication",
      "implementation_functions": ["test_fr03_missing_api_key_returns_401"],
      "verification_method": "integration test via httpx.AsyncClient(ASGITransport)"
    },
    {
      "id": "FR-04",
      "description": "Scope authorisation",
      "implementation_functions": ["test_fr04_scope_hierarchy_is_inclusive"],
      "verification_method": "integration test via httpx.AsyncClient(ASGITransport)"
    },
    {
      "id": "FR-05",
      "description": "Per-token rate limiting",
      "implementation_functions": ["test_fr05_bucket_capacity_and_refill_rate"],
      "verification_method": "integration test via httpx.AsyncClient(ASGITransport)"
    },
    {
      "id": "FR-06",
      "description": "Persistence layer and transaction boundaries",
      "implementation_functions": ["test_fr06_service_layer_holds_no_session"],
      "verification_method": "integration test via httpx.AsyncClient(ASGITransport)"
    },
    {
      "id": "FR-07",
      "description": "Schema migration (Alembic three-step evolution)",
      "implementation_functions": ["test_fr07_upgrade_head_then_downgrade_base_exit_zero"],
      "verification_method": "real-database migration round-trip test"
    },
    {
      "id": "FR-08",
      "description": "Async executor",
      "implementation_functions": ["test_fr08_shutdown_drains_then_marks_interrupted"],
      "verification_method": "integration test via httpx.AsyncClient(ASGITransport)"
    },
    {
      "id": "FR-09",
      "description": "Health checks and observability",
      "implementation_functions": ["test_fr09_healthz_returns_200_ok"],
      "verification_method": "integration test via httpx.AsyncClient(ASGITransport)"
    },
    {
      "id": "FR-10",
      "description": "Error contract (RFC 7807)",
      "implementation_functions": ["test_fr10_all_non_2xx_use_problem_json_content_type"],
      "verification_method": "integration test via httpx.AsyncClient(ASGITransport)"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "description": "Performance and query efficiency",
      "test_method": "pytest-benchmark + SQLAlchemy event listener for N+1"
    },
    {
      "id": "NFR-02",
      "type": "security",
      "description": "HTTP and data-layer security",
      "test_method": "bandit -r 03-development/src/ + grep + dedicated tests"
    },
    {
      "id": "NFR-03",
      "type": "error_handling",
      "description": "Error handling, transactions, async correctness",
      "test_method": "try/except audit ratio minus anti-patterns"
    },
    {
      "id": "NFR-04",
      "type": "security",
      "description": "Sensitive data redaction",
      "test_method": "log/metrics/error-body redaction tests"
    },
    {
      "id": "NFR-05",
      "type": "documentation",
      "description": "Documentation coverage",
      "test_method": "docstring [FR-XX]/[NFR-XX] citation ratio"
    },
    {
      "id": "NFR-06",
      "type": "architecture_constraints",
      "description": "Architecture layering contract",
      "test_method": "lint-imports exit 0 + sqlalchemy-import-outside-repository test"
    },
    {
      "id": "NFR-07",
      "type": "license_compliance",
      "description": "Dependency and license compliance",
      "test_method": "pip-licenses whole-tree scan + SBOM.json"
    },
    {
      "id": "NFR-08",
      "type": "mutation",
      "description": "Mutation testing",
      "test_method": "mutmut score >= 70 on service/ + repository/"
    },
    {
      "id": "NFR-09",
      "type": "testability",
      "description": "Verification authenticity (zero-skip rule)",
      "test_method": "pytest -q reports 0 skipped + assertion-quality tests"
    },
    {
      "id": "NFR-10",
      "type": "integration",
      "description": "Integration coverage",
      "test_method": "tests/integration line coverage >= 80%"
    },
    {
      "id": "NFR-11",
      "type": "maintainability",
      "description": "Readability",
      "test_method": "radon mi per-file + per-function/per-file/per-handler size tests"
    },
    {
      "id": "NFR-12",
      "type": "verifiability",
      "description": "System verification target",
      "test_method": "make verify-system chains upgrade + tests + smoke + round-trip"
    }
  ]
}
```
<!-- FR:END -->

---

## 8. Risks

Source: SPEC §9.

| ID | Risk | Impact | Likelihood | Mitigation |
|----|------|--------|------------|------------|
| R1 | **v3 data migration loses data** | High | Medium | Round-trip reversibility test on a real DB, per-column comparison (FR-07, §5 #12) |
| R2 | SQL injection | High | Low | No concatenation + ORM/parameterised + grep gate (NFR-02) |
| R3 | API key leak | High | Medium | Hash storage + constant-time compare + plaintext printed once (FR-03) |
| R4 | 403 reveals resource existence | Medium | Medium | Authorisation decision precedes resource lookup (FR-04, §5 #6) |
| R5 | N+1 query collapse on a large table | High | High | Explicit eager loading + SQL count assertion (NFR-01, §5 #14) |
| R6 | Error body leaks internals | Medium | High | RFC 7807 fixed fields + `detail` allowlist (FR-10) |
| R7 | **`CancelledError` swallowed → shutdown hangs** | Medium | Medium | Explicit ban + assertion (NFR-03) |
| R8 | Task timeout leaves orphan processes | Medium | Medium | `kill()` + `await wait()` (FR-08, §5 #25) |
| R9 | Deploy without migration | High | Medium | `/readyz` fail closed (FR-09, §5 #11) |
| R10 | Connection pool exhaustion | Medium | Medium | `pool_pre_gint` + concurrency cap (FR-06/08) |
| R11 | Transitive dep with incompatible license | Medium | Medium | Lock file + whole-tree scan (NFR-07) |
| R12 | Rate-bucket race over-admits | Low | Medium | Single transaction + row-level lock (FR-05) |

> Note: SPEC §9 R10 contains the literal `pool_pre_gint` (likely a
> transcription typo for `pool_pre_ping`); the mitigation in this
> table cites the FR-06 / FR-08 requirement as written, not the
> literal text. See [SPEC §3 FR-06, AC-6.5] for the actual configured
> value. (Editorial correction; no semantic change to the mitigation.)

---

## 9. Glossary

| Term | Definition |
|------|------------|
| taskq-api | Project name; the task-queue HTTP service specified in this SRS. |
| scope | Per-key permission tier from the hierarchy `read < write < admin` (inclusive). [SPEC §3 FR-04] |
| cursor pagination | Pagination using a server-supplied opaque cursor (not numeric offset). [SPEC §3 FR-01] |
| token bucket | Rate-limiting primitive with capacity `BURST` and refill rate `PER_SEC`. [SPEC §3 FR-05] |
| graceful drain | On shutdown, wait for in-flight tasks up to `TASKQ_DRAIN_TIMEOUT`; timeouts are marked `interrupted`. [SPEC §3 FR-08] |
| orphan process | A child subprocess left running after its parent coroutine has returned. Forbidden. [SPEC §3 FR-08] |
| data migration | A migration revision that moves data between tables/columns (v3 in this round); must be reversible. [SPEC §3 FR-07] |
| RFC 7807 | IETF "Problem Details for HTTP APIs"; the `application/problem+json` error format used project-wide. [SPEC §3 FR-10] |
| forbidden contract | An `.importlinter` rule banning a specific import; here, `sqlalchemy` outside `repository/`. [SPEC §4 NFR-06] |
| CRG | code-review-graph; framework-owned knowledge graph used by the `architecture` dimension. [SPEC §10] |
| N+1 | A query anti-pattern where the SQL statement count grows linearly with row count; an acceptance failure. [SPEC §4 NFR-01, FR-06] |

---

*End of SRS. Source of truth: `SPEC.md` v1.0.0 (2026-07-30).*
