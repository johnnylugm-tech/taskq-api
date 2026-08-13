# Test Plan — taskq-api

> **Document version**: v1.0.0 (2026-08-13)
> **Phase**: 4 (Testing)
> **Source of truth**: `01-requirements/SRS.md` (FR §3, NFR §4) + `.methodology/quality_manifest.json` (FR list, NFR mapping)
> **Scope**: Per-FR testing playbook executed once before per-FR test authoring; covers **all 10 FRs** and **all 12 NFRs** from the manifest.
> **Categories used**: P (Positive), N (Negative), B (Boundary), E (Edge case), S (Security/PII), P-NFR (Performance/NFR driven).
> **Priority values**: `P0` = blocking acceptance, `P1` = high, `P2` = medium, `P3` = low/observability.

## 0. Scope and Conventions

- All endpoints exercised via `httpx.AsyncClient(transport=ASGITransport(app))` (NFR-10).
- Tests must execute against a **real SQLite file** (not in-memory mock) per NFR-09 for FR-07 round-trip.
- Test names follow `test_frNN_xxx` convention where applicable (D4 spec-coverage).
- One pytest test per row in this plan where the row maps to an automated check; rows flagged `MANUAL` are executed via shell and asserted by exit code / stdout.
- All assertions listed under "Expected" are mandatory; the test fails if any expected assertion does not hold.

### Test Case ID Legend (TC-* aliases)

Each row's `Test ID` (e.g. `TP-FR01-001`) is also referenced in the harness
traceability audit as a short TC-* alias (one per FR for quick scanning):

- TC-001 — FR-01 AC1 (create task, P)
- TC-002 — FR-02 AC1 (rate-limit window, P)
- TC-003 — FR-03 AC1 (auth gate on read, N)

---

## 1. FR-01 — Task Resource CRUD API

**Linked modules** (per `quality_manifest.json#fr_module_traceability`):
`taskq_api.api.tasks`, `taskq_api.service.tasks`, `taskq_api.repository.task_repo`, `taskq_api.models.schemas`.

### FR-01.AC1 — POST /v1/tasks creates a task (positive)
- **Test ID**: TP-FR01-001
- **Description**: Create a new task with a valid `X-API-Key` (write scope), valid `name` and `command`.
- **Input**: `POST /v1/tasks`, headers `X-API-Key: <write-key>`, JSON `{"name":"compile-proj","command":"echo hello"}`.
- **Expected**:
  1. HTTP status `201`.
  2. Response JSON contains `id` (UUIDv4).
  3. `Location` header (or body) carries the new task id.
  4. DB row exists in `tasks` with `status='pending'`.
- **Priority**: P0
- **Category**: P

### FR-01.AC2 — POST /v1/tasks with missing X-API-Key
- **Test ID**: TP-FR01-002
- **Description**: Authentication gate on resource creation.
- **Input**: `POST /v1/tasks` with no `X-API-Key`.
- **Expected**:
  1. HTTP status `401`.
  2. `Content-Type: application/problem+json`.
  3. Body fields: `type`, `title`, `status=401`, `detail`, `instance`, `correlation_id`.
  4. No `id` field leaked in the body.
- **Priority**: P0
- **Category**: N

### FR-01.AC3 — GET /v1/tasks/{unknown} returns 404
- **Test ID**: TP-FR01-003
- **Description**: Unknown task id lookup.
- **Input**: `GET /v1/tasks/00000000-0000-0000-0000-000000000000` with valid `X-API-Key`.
- **Expected**:
  1. HTTP status `404`.
  2. `Content-Type: application/problem+json`.
  3. `detail` does not reveal internal structure; `type` is `/errors/not-found`.
- **Priority**: P0
- **Category**: N

### FR-01.AC4 — POST /v1/tasks duplicate name → 409
- **Test ID**: TP-FR01-004
- **Description**: Unique-name constraint on tasks.
- **Input**: Two sequential `POST /v1/tasks` requests with identical `name` and a write key.
- **Expected**:
  1. First request: `201`.
  2. Second request: `409`, `Content-Type: application/problem+json`, `type=/errors/conflict`.
- **Priority**: P0
- **Category**: N

### FR-01.AC5 — Validation errors return 422 (negative validation)
- **Test ID**: TP-FR01-005
- **Description**: Validation rule violations (empty string, > 1000 chars, injection blacklist chars, missing fields).
- **Input**: Edge cases applied to `POST /v1/tasks`:
  - empty `name`
  - `name` longer than 1000 chars
  - `name` containing injection chars from blacklist
  - missing required fields
- **Expected**:
  1. All variants: HTTP `422`.
  2. `Content-Type: application/problem+json`.
  3. `type=/errors/validation`, `detail` cites offending field.
  4. No DB row created.
- **Priority**: P0
- **Category**: N / B / E

### FR-01.AC6 — GET /v1/tasks list with cursor pagination
- **Test ID**: TP-FR01-006
- **Description**: Cursor-based pagination contract (not offset).
- **Input**: Seed N=200 tasks; call `GET /v1/tasks?limit=50` repeatedly.
- **Expected**:
  1. Default `limit=50`.
  2. Each response includes a cursor token.
  3. Concatenation of pages yields unique non-overlapping ids covering the N tasks.
  4. No `offset` query parameter accepted.
- **Priority**: P0
- **Category**: P / B

### FR-01.AC7 — LIMIT upper bound (boundary)
- **Test ID**: TP-FR01-007
- **Description**: `limit>200` rejected; lower bounds exercised.
- **Input**: `GET /v1/tasks?limit=201` and `GET /v1/tasks?limit=0`, `GET /v1/tasks?limit=-5`.
- **Expected**:
  1. `limit=201` → `422`, `type=/errors/validation`.
  2. `limit=0` → `422`.
  3. `limit=-5` → `422`.
  4. `limit=200` (boundary inclusive) → `200`.
- **Priority**: P0
- **Category**: B

### FR-01.AC8 — DELETE /v1/tasks/{id} cascade (positive)
- **Test ID**: TP-FR01-008
- **Description**: Delete task removes associated `task_results` row in same transaction.
- **Input**: Create task, attach a result row, call `DELETE /v1/tasks/{id}` with an admin key.
- **Expected**:
  1. HTTP `204`.
  2. `tasks` row gone.
  3. `task_results` row gone.
  4. Wrapped in single transaction (either both removed or both intact if failure).
- **Priority**: P0
- **Category**: P

### FR-01.AC9 — Status filter (?status=)
- **Test ID**: TP-FR01-009
- **Description**: Status filter narrows list.
- **Input**: Seed mix of `pending|done|failed`; query `GET /v1/tasks?status=pending`.
- **Expected**: only `status=pending` rows returned. `status=invalid_value` → `422`.
- **Priority**: P1
- **Category**: P / N

### FR-01.AC10 — Pagination edge (empty + last partial page)
- **Test ID**: TP-FR01-010
- **Description**: Empty result set; last page smaller than `limit`.
- **Input**: Seed 0 tasks; seed N=130 and paginate to last page.
- **Expected**:
  1. Empty list → `200` with `items=[]` and no cursor.
  2. Last page returns the remaining rows (≤ `limit`).
- **Priority**: P1
- **Category**: E

---

## 2. FR-02 — Task Execution Endpoint

**Linked modules**: `taskq_api.api.tasks`, `taskq_api.service.runner`, `taskq_api.repository.task_repo`, `taskq_api.models.orm`.

### FR-02.AC1 — POST /v1/tasks/{id}/run returns 202
- **Test ID**: TP-FR02-001
- **Description**: Submit run for existing pending task with write scope.
- **Input**: Seed task with command `echo hello`, call `POST /v1/tasks/{id}/run`.
- **Expected**:
  1. HTTP `202 Accepted`.
  2. Body contains `run_id`.
  3. Within bounded time, task reaches terminal status; result row inserted.
- **Priority**: P0
- **Category**: P

### FR-02.AC2 — State machine transitions
- **Test ID**: TP-FR02-002
- **Description**: `pending → running → done` happy path.
- **Input**: Task with `command:echo hi`.
- **Expected**: status captures `running` then `done`; `task_results` row with `exit_code=0` and `stdout_tail` containing `hi`.
- **Priority**: P0
- **Category**: P

### FR-02.AC3 — Failing subprocess path
- **Test ID**: TP-FR02-003
- **Description**: Non-zero exit code path.
- **Input**: Task with `command:exit 7`.
- **Expected**: status `failed`, `exit_code=7`, results row persists.
- **Priority**: P0
- **Category**: E

### FR-02.AC4 — Task timeout path
- **Test ID**: TP-FR02-004
- **Description**: Subprocess exceeding `TASKQ_TASK_TIMEOUT` is killed.
- **Input**: `command:sleep 60` with `TASKQ_TASK_TIMEOUT=0.5`.
- **Expected**: status `timeout`; child process is dead (`wait()` resolves); no orphan process (see also NFR-03 / FR-08).
- **Priority**: P0
- **Category**: E

### FR-02.AC5 — GET /v1/tasks/{id}/runs history
- **Test ID**: TP-FR02-005
- **Description**: Multiple runs returned newest-first.
- **Input**: Task with 3 runs.
- **Expected**: 3 result rows in descending `finished_at` order; each includes `exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`, `finished_at`.
- **Priority**: P1
- **Category**: P

### FR-02.AC6 — Async subprocess uses `asyncio.create_subprocess_exec` (no shell=True)
- **Test ID**: TP-FR02-006
- **Description**: Static enforcement that the runner never invokes `shell=True`.
- **Input**: `grep -rn "shell=True" 03-development/src/taskq_api/service/runner.py`.
- **Expected**: 0 hits.
- **Priority**: P0
- **Category**: S (negative code-scanning)

### FR-02.AC7 — Run with non-existent task id → 404
- **Test ID**: TP-FR02-007
- **Description**: Negative path.
- **Input**: `POST /v1/tasks/{unknown}/run`.
- **Expected**: `404`, problem+json.
- **Priority**: P0
- **Category**: N

### FR-02.AC8 — Write-key required (cross with FR-04)
- **Test ID**: TP-FR02-008
- **Description**: Read-only key cannot trigger run.
- **Input**: `POST /v1/tasks/{id}/run` with `read` scope key.
- **Expected**: `403`, problem+json, body reveals nothing about task existence.
- **Priority**: P0
- **Category**: N

---

## 3. FR-03 — API Key Authentication

**Linked modules**: `taskq_api.api.deps`, `taskq_api.service.auth`, `taskq_api.repository.key_repo`, `taskq_api.models.orm`.

### FR-03.AC1 — Missing X-API-Key → 401
- **Test ID**: TP-FR03-001
- **Description**: Auth gate missing.
- **Input**: any `/v1/*` request (sample: `GET /v1/tasks`) with no header.
- **Expected**:
  1. `401` + `application/problem+json`.
  2. `type=/errors/unauthenticated`.
- **Priority**: P0
- **Category**: N

### FR-03.AC2 — Invalid X-API-Key → 401
- **Test ID**: TP-FR03-002
- **Description**: Wrong key rejected.
- **Input**: Header `X-API-Key: clearly-not-a-real-key`.
- **Expected**: `401`, no information leak about whether a key prefix exists.
- **Priority**: P0
- **Category**: N

### FR-03.AC3 — Valid key stores SHA-256 hash only
- **Test ID**: TP-FR03-003
- **Description**: Plaintext must never persist.
- **Input**: `python -m taskq_api key create --scope write`; inspect `api_keys`.
- **Expected**:
  1. Captured stdout contains the plaintext exactly once (printed at creation).
  2. `key_hash` column is 64-char hexadecimal (SHA-256).
  3. No column stores plaintext; no log line persists plaintext.
- **Priority**: P0
- **Category**: S

### FR-03.AC4 — Comparison uses `hmac.compare_digest`
- **Test ID**: TP-FR03-004
- **Description**: Constant-time comparison enforced (code scan).
- **Input**: `grep -n "compare_digest\\|==\\|!=" 03-development/src/taskq_api/service/auth.py` (excluding docstrings).
- **Expected**: comparison block uses `hmac.compare_digest(hash_a, hash_b)`; no `==` for key equality.
- **Priority**: P0
- **Category**: S

### FR-03.AC5 — Revoked key rejected
- **Test ID**: TP-FR03-005
- **Description**: Revocation via `revoked_at` timestamp.
- **Input**: Create key, set `revoked_at = now()` directly in DB; call `GET /v1/tasks`.
- **Expected**: `401` even though the same plaintext is presented.
- **Priority**: P0
- **Category**: P / E

### FR-03.AC6 — DB URL password redaction (cross with NFR-04)
- **Test ID**: TP-FR03-006
- **Description**: Connection string never appears in plain in logs.
- **Input**: Set `TASKQ_DB_URL=postgresql://u:supersecret@host/db`; trigger a 500.
- **Expected**: no log line contains `supersecret`; no `/v1/metrics` body contains `supersecret`.
- **Priority**: P0
- **Category**: S

### FR-03.AC7 — `/healthz` and `/readyz` bypass auth
- **Test ID**: TP-FR03-007
- **Description**: Health endpoints public.
- **Input**: `GET /healthz`, `GET /readyz` with no `X-API-Key`.
- **Expected**: `200` (or `503` if DB unreachable; never `401`).
- **Priority**: P0
- **Category**: P

---

## 4. FR-04 — Scope Authorisation

**Linked modules**: `taskq_api.api.deps`, `taskq_api.service.auth`.

### FR-04.AC1 — Hierarchical scope includes
- **Test ID**: TP-FR04-001
- **Description**: `read < write < admin`, higher covers lower.
- **Input**: For each pair, prove that admin can call admin/write/read endpoints; write can call write/read; read only read.
- **Expected**: All combos behave per the hierarchy.
- **Priority**: P0
- **Category**: P

### FR-04.AC2 — DELETE with non-admin → 403, no existence leak
- **Test ID**: TP-FR04-002
- **Description**: Cross-version. Critical: the 403 body must not differ for `id-existing` vs `id-not-existing`.
- **Input**: Two requests with a `write` (non-admin) key: `DELETE /v1/tasks/{known}` and `DELETE /v1/tasks/{unknown}`.
- **Expected**:
  1. Both return `403`.
  2. Bodies are byte-identical apart from `correlation_id` and any RFC 7807 noise. No `id` echo, no "not found" message.
- **Priority**: P0
- **Category**: N

### FR-04.AC3 — Single dependency decision point
- **Test ID**: TP-FR04-003
- **Description**: Every `/v1/*` route flows through the same auth dep.
- **Input**: Inspect OpenAPI schema and dependency tree; programmatic check.
- **Expected**:
  1. Every `/v1/*` route has `Depends(authenticate)` and (for protected operations) `Depends(require_scope(...))`.
  2. No per-handler `if scope == ...` logic — only the single `require_scope` helper enforces.
- **Priority**: P0
- **Category**: S

### FR-04.AC4 — Scope-string tampering rejected
- **Test ID**: TP-FR04-004
- **Description**: A revoked-or-corrupt scope row must not promote.
- **Input**: Patch `api_keys.scope` to admin in DB, attempt protected admin endpoint.
- **Expected**: Either treated as the new scope (positive — DB is source of truth) or, if revoked, `401`. The test verifies the system favours the latest DB state.
- **Priority**: P1
- **Category**: E

### FR-04.AC5 — Lower-bound empty scope
- **Test ID**: TP-FR04-005
- **Description**: Empty-string scope.
- **Input**: Create key with `scope=""`.
- **Expected**: Falls back to `read` default or rejected at creation; never promotes above `read`.
- **Priority**: P1
- **Category**: B / E

---

## 5. FR-05 — Rate Limiting

**Linked modules**: `taskq_api.api.deps`, `taskq_api.service.ratelimit`, `taskq_api.repository.rate_repo`, `taskq_api.models.orm`.

### FR-05.AC1 — Burst exceeded → 429 with Retry-After
- **Test ID**: TP-FR05-001
- **Description**: Per-token token bucket capacity.
- **Input**: With `TASKQ_RATE_BURST=3`, sequentially call `GET /v1/tasks` four times under the same key.
- **Expected**:
  1. First 3 → `200`.
  2. 4th → `429`, body `application/problem+json`, response header `Retry-After: <integer seconds>` ≥ 1.
- **Priority**: P0
- **Category**: P / B

### FR-05.AC2 — Token refill recovers
- **Test ID**: TP-FR05-002
- **Description**: After `1 / TASKQ_RATE_PER_SEC` seconds capacity is restored.
- **Input**: Burst to 429, sleep `2 / TASKQ_RATE_PER_SEC` seconds, retry.
- **Expected**: Request succeeds (`200`).
- **Priority**: P0
- **Category**: P / B

### FR-05.AC3 — Per-token isolation
- **Test ID**: TP-FR05-003
- **Description**: One key's exhaustion does not affect another.
- **Input**: Burn key-A to 429; immediately call with key-B.
- **Expected**: key-B returns `200`.
- **Priority**: P0
- **Category**: P

### FR-05.AC4 — Health endpoints not rate-limited
- **Test ID**: TP-FR05-004
- **Description**: `/healthz`, `/readyz` skip the bucket.
- **Input**: Hammer `/healthz` and `/readyz` beyond burst; never receive 429.
- **Expected**: all `200` (or `503` for `/readyz` unrelated to limiting).
- **Priority**: P0
- **Category**: P

### FR-05.AC5 — Update is single-transaction with row-lock
- **Test ID**: TP-FR05-005
- **Description**: Architectural invariant (rate_limit_update_in_single_transaction_with_row_lock).
- **Input**: Static code review of `rate_repo`; concurrent-burst test from N=20 parallel callers.
- **Expected**: exactly `TASKQ_RATE_BURST` requests succeed, rest are 429; no double-counting (allowed-burst-off-by-one is a fail).
- **Priority**: P0
- **Category**: S / Concurrency

### FR-05.AC6 — Boundary: boundary tokens=0 vs 1
- **Test ID**: TP-FR05-006
- **Description**: Edge case at bucket floor.
- **Input**: Burn bucket to exactly 0; one more call; ensure deterministic 429.
- **Expected**: deterministic `429` (no flakes under repeated runs).
- **Priority**: P1
- **Category**: B

---

## 6. FR-06 — Persistence Layer & Transaction Boundaries

**Linked modules**: `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.service.tasks`, `taskq_api.models.orm`.

### FR-06.AC1 — No string-concatenated SQL anywhere
- **Test ID**: TP-FR06-001
- **Description**: Static grep gate.
- **Input**: `grep -rnE "f['\"][^'\"]*(SELECT|INSERT|UPDATE|DELETE|FROM)" 03-development/src/` and `grep -rnE "% (SELECT|INSERT|UPDATE|DELETE|FROM)"` and concatenation pattern scan.
- **Expected**: 0 hits in `service/`, `api/`, `models/`; ORM/`text()` only inside `repository/`.
- **Priority**: P0
- **Category**: S

### FR-06.AC2 — One Session per request, context-manager boundaries
- **Test ID**: TP-FR06-002
- **Description**: Success commit / failure rollback.
- **Input**: Instrument `repository.session.transaction`; force exception in a handler.
- **Expected**:
  1. Successful path commits exactly once.
  2. Exception path enters rollback.
- **Priority**: P0
- **Category**: E / S

### FR-06.AC3 — Eager-loading prevents N+1
- **Test ID**: TP-FR06-003
- **Description**: List endpoint SQL statement count is constant.
- **Input**: Seed 10,000 tasks with associated tags; instrument SQLAlchemy events; call `GET /v1/tasks?limit=50`.
- **Expected**: Total statement count for the request is bounded by a small constant (e.g. ≤ 5) regardless of result row count.
- **Priority**: P0
- **Category**: P (performance proxy)

### FR-06.AC4 — Connection pool settings
- **Test ID**: TP-FR06-004
- **Description**: `pool_size` honours `TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True`.
- **Input**: Inspect engine configuration.
- **Expected**: `pool_size = int(os.getenv("TASKQ_DB_POOL_SIZE", "5"))`, `pool_pre_ping=True`.
- **Priority**: P1
- **Category**: P

### FR-06.AC5 — Service/api layer imports `sqlalchemy` is blocked
- **Test ID**: TP-FR06-005
- **Description**: Cross with NFR-06 / import-linter.
- **Input**: `lint-imports`.
- **Expected**: `exit 0`; a deliberate `import sqlalchemy` placed in `service/` is rejected.
- **Priority**: P0
- **Category**: S

### FR-06.AC6 — Business layer never holds a Session
- **Test ID**: TP-FR06-006
- **Description**: Session ownership is repository-only.
- **Input**: AST scan; assert `service/` modules do not accept/return `Session`.
- **Expected**: 0 violations.
- **Priority**: P0
- **Category**: S

### FR-06.AC7 — Transaction rollback on error
- **Test ID**: TP-FR06-007
- **Description**: A failed mid-transaction step leaves DB intact.
- **Input**: Inject `IntegrityError` mid-operation in a test handler.
- **Expected**: no partial write persisted; subsequent GET shows prior state.
- **Priority**: P0
- **Category**: E

---

## 7. FR-07 — Schema Migration (Alembic Three Steps)

**Linked modules**: `migrations.versions.v1_initial`, `migrations.versions.v2_tags`, `migrations.versions.v3_split_results`.

### FR-07.AC1 — `alembic upgrade head` succeeds
- **Test ID**: TP-FR07-001
- **Description**: Single-step migration in fresh DB.
- **Input**: `alembic upgrade head`.
- **Expected**: exit 0; `tasks`, `api_keys`, `rate_buckets`, `tags`, `task_tags`, `task_results` tables exist per v3 schema.
- **Priority**: P0
- **Category**: P

### FR-07.AC2 — Three-step round-trip preserves data
- **Test ID**: TP-FR07-002
- **Description**: v3 data migration reversibility — the keystone test (NFR-09).
- **Input**: Run `upgrade head` against real on-disk SQLite file; write a sample task with `result_json` content (force down to v2 if needed by seeding with prepared fixture); `downgrade -1` → re-`upgrade head`.
- **Expected**:
  1. Each column on the test row is byte-identical pre- and post-round-trip (compare via SQL SELECT — no mock).
  2. After round-trip, the row lives in `task_results` (v3) and `tasks.result_json` is gone.
- **Priority**: P0
- **Category**: P / B

### FR-07.AC3 — `alembic downgrade base` clean
- **Test ID**: TP-FR07-003
- **Description**: Drop all tables.
- **Input**: `alembic downgrade base`.
- **Expected**: exit 0; introspection shows no application tables remain; only `alembic_version` and SQLite internals.
- **Priority**: P0
- **Category**: P

### FR-07.AC4 — `/readyz` reflects migration behind head
- **Test ID**: TP-FR07-004
- **Description**: Fail-closed readiness.
- **Input**: `downgrade -1` then `GET /readyz`.
- **Expected**: `503`, detail mentions `migration_not_at_head`.
- **Priority**: P0
- **Category**: N

### FR-07.AC5 — No destructive shortcuts
- **Test ID**: TP-FR07-005
- **Description**: Forbid `op.execute("DROP TABLE ...")` etc. without reverse.
- **Input**: AST/regex scan over `migrations/versions/`.
- **Expected**: every `DROP`, `TRUNCATE`, destructive mutation has a paired reverse; no orphan destructive ops.
- **Priority**: P0
- **Category**: S

### FR-07.AC6 — Each step reversible individually
- **Test ID**: TP-FR07-006
- **Description**: `upgrade head`, `downgrade -1`, `upgrade head` for v1 ↔ v2 ↔ v3 transitions independently.
- **Input**: run each permutation.
- **Expected**: each permutation exits 0; row counts and shapes match.
- **Priority**: P0
- **Category**: P / B

### FR-07.AC7 — Migration files covered
- **Test ID**: TP-FR07-007
- **Description**: Alembic offline-SQL generation + asserts.
- **Input**: `alembic upgrade head --sql` and parse output.
- **Expected**: contains expected `CREATE TABLE`, `ALTER TABLE`, `INSERT` data-migration step.
- **Priority**: P1
- **Category**: P

---

## 8. FR-08 — Async Executor

**Linked modules**: `taskq_api.service.runner`, `taskq_api.app`.

### FR-08.AC1 — Graceful drain on shutdown
- **Test ID**: TP-FR08-001
- **Description**: In-flight task finished before exit.
- **Input**: Start server with `TASKQ_DRAIN_TIMEOUT=10`, post slow task, immediately trigger shutdown.
- **Expected**: task reaches terminal status; no orphan process listed in `ps`.
- **Priority**: P0
- **Category**: P / E

### FR-08.AC2 — Drain timeout marks `interrupted`
- **Test ID**: TP-FR08-002
- **Description**: If drain exceeds budget, task tagged `interrupted`.
- **Input**: Slow task with `TASKQ_DRAIN_TIMEOUT=0.1`; force shutdown.
- **Expected**: terminal status of still-running tasks is `interrupted`; no orphan process.
- **Priority**: P0
- **Category**: E

### FR-08.AC3 — Concurrency cap respected
- **Test ID**: TP-FR08-003
- **Description**: Cap equals `TASKQ_MAX_CONCURRENT`.
- **Input**: Start N=`TASKQ_MAX_CONCURRENT+10` long-running tasks; observe parallelism (cap `<= TASKQ_MAX_CONCURRENT`).
- **Expected**: at any moment concurrent `running` tasks ≤ `TASKQ_MAX_CONCURRENT`.
- **Priority**: P0
- **Category**: B

### FR-08.AC4 — Task timeout kills child process
- **Test ID**: TP-FR08-004
- **Description**: `process.kill()` + `await wait()` — orphan-free.
- **Input**: Long-running `sleep`; `TASKQ_TASK_TIMEOUT=0.5`.
- **Expected**: status `timeout`; `ps` shows no child pid; `wait()` completes within bounded time.
- **Priority**: P0
- **Category**: E

### FR-08.AC5 — `asyncio.CancelledError` propagates
- **Test ID**: TP-FR08-005
- **Description**: Must not be swallowed by `except Exception`.
- **Input**: Raise `CancelledError` in a runner-managed task; AST scan confirms no `except Exception: pass`/`except:` swallowing.
- **Expected**: CancelledError propagates; trace shown in test report.
- **Priority**: P0
- **Category**: S / E

### FR-08.AC6 — Orphan-free (SPEC §11 monitoring row)
- **Test ID**: TP-FR08-006
- **Description**: 0 orphan subprocesses after various shutdown modes.
- **Input**: Run a battery of timeout + cancel + crash scenarios; enumerate `/proc/<pid>` or `pgrep`.
- **Expected**: orphan count = 0 across all scenarios.
- **Priority**: P0
- **Category**: E

### FR-08.AC7 — Concurrency cap queue (overflow)
- **Test ID**: TP-FR08-007
- **Description**: Excess tasks queued, not unbounded.
- **Input**: Submit 100 tasks when cap=4; monitor queue length growth.
- **Expected**: queue length grows; memory bounded; no `asyncio.gather` of unbounded set.
- **Priority**: P1
- **Category**: B

---

## 9. FR-09 — Health Checks & Observability

**Linked modules**: `taskq_api.api.health`, `taskq_api.app`.

### FR-09.AC1 — `/healthz` 200 when process up
- **Test ID**: TP-FR09-001
- **Description**: Liveness probe.
- **Input**: `GET /healthz`.
- **Expected**: `200`, body `{"status":"ok"}`, no auth required.
- **Priority**: P0
- **Category**: P

### FR-09.AC2 — `/readyz` 503 when DB unreachable
- **Test ID**: TP-FR09-002
- **Description**: Stop DB (point `TASKQ_DB_URL` at unreachable path).
- **Input**: `GET /readyz`.
- **Expected**: `503`, `type=/errors/not-ready`, detail mentions `db_unreachable`.
- **Priority**: P0
- **Category**: N

### FR-09.AC3 — `/readyz` 503 when migration behind head (cross FR-07)
- **Test ID**: TP-FR09-003
- **Description**: Fail closed.
- **Input**: `downgrade -1` then `GET /readyz`.
- **Expected**: `503`, detail mentions `migration_not_at_head`.
- **Priority**: P0
- **Category**: N

### FR-09.AC4 — `/readyz` 200 when all green
- **Test ID**: TP-FR09-004
- **Description**: Positive path.
- **Input**: freshly-migrated, DB available.
- **Expected**: `200`, no auth required.
- **Priority**: P0
- **Category**: P

### FR-09.AC5 — `/v1/metrics` requires admin scope
- **Test ID**: TP-FR09-005
- **Description**: Cross with FR-04.
- **Input**: Call with read/write/admin keys.
- **Expected**: admin → `200` (counters present); read/write → `403`.
- **Priority**: P0
- **Category**: S

### FR-09.AC6 — Metrics body shape
- **Test ID**: TP-FR09-006
- **Description**: Contains task counts by status, latency percentiles, rate-limit rejections.
- **Input**: Trigger a fixed scenario, fetch `/v1/metrics`.
- **Expected**: keys for `tasks_by_status`, `execution_latency_p50/p95/p99`, `rate_limit_rejections_total`; no leaked `TASKQ_DB_URL` password.
- **Priority**: P0
- **Category**: P

---

## 10. FR-10 — Error Contract (RFC 7807)

**Linked modules**: `taskq_api.errors`, `taskq_api.api.tasks`, `taskq_api.api.deps`.

### FR-10.AC1 — All non-2xx carry `application/problem+json`
- **Test ID**: TP-FR10-001
- **Description**: Content-type contract.
- **Input**: Trigger one each of 401/403/404/409/422/429/500/503.
- **Expected**: every response `Content-Type: application/problem+json`.
- **Priority**: P0
- **Category**: P / N / B

### FR-10.AC2 — Body shape conforms to RFC 7807
- **Test ID**: TP-FR10-002
- **Description**: Required fields.
- **Input**: any error response.
- **Expected**: body contains `type` (URI), `title`, `status`, `detail`, `instance`, `correlation_id`.
- **Priority**: P0
- **Category**: P

### FR-10.AC3 — `detail` never leaks internal info
- **Test ID**: TP-FR10-003
- **Description**: Trigger 500 with deliberate exception.
- **Input**: handler raises an internal exception with Python message string containing a fake SQL fragment `SELECT * FROM secret_table` and fake stack frame path `/srv/app/private.py`.
- **Expected**: response body contains neither the SQL fragment nor the file path or stack trace; `detail` is generic.
- **Priority**: P0
- **Category**: S

### FR-10.AC4 — Correlation id echoed in response header
- **Test ID**: TP-FR10-004
- **Description**: `X-Correlation-Id`.
- **Input**: send request with header `X-Correlation-Id: trace-abc`; send request with no header (server generates).
- **Expected**: response header mirrors (or carries generated) correlation id; same id appears in server log entries related to that request.
- **Priority**: P0
- **Category**: P / E

### FR-10.AC5 — Status code-to-type mapping table
- **Test ID**: TP-FR10-005
- **Description**: Cross with SRS §13 (error status map).
- **Input**: Trigger each status code.
- **Expected**:
  - 422 → `/errors/validation`
  - 401 → `/errors/unauthenticated`
  - 403 → `/errors/forbidden`
  - 404 → `/errors/not-found`
  - 409 → `/errors/conflict`
  - 429 → `/errors/rate-limited`
  - 503 → `/errors/not-ready`
  - 500 → `/errors/internal`
- **Priority**: P0
- **Category**: P

### FR-10.AC6 — 429 carries Retry-After header
- **Test ID**: TP-FR10-006
- **Description**: Cross with FR-05.
- **Input**: rate-limit scenario.
- **Expected**: header present, integer ≥ 1.
- **Priority**: P0
- **Category**: B

---

## 11. NFR Coverage Matrix

The following NFRs each have dedicated tests in their modules. The matrix below binds the test IDs above to the dimension they exercise.

| NFR | Dimension | Test IDs | Acceptance Command (per SPEC §8) |
|-----|-----------|----------|----------------------------------|
| NFR-01 | performance | TP-FR06-003, TP-FR01-006, plus `tests/perf/test_query_perf.py` (pytest-benchmark) | §8 #14, #15 |
| NFR-02 | security | TP-FR03-003, TP-FR03-004, TP-FR03-006, TP-FR04-002, TP-FR04-003, TP-FR05-005, TP-FR06-001, TP-FR06-005, TP-FR06-006, TP-FR08-005, TP-FR09-005, TP-FR10-003, plus `bandit -r 03-development/src/` | §8 #6, #16, #17, #18, #19, #21, #23 |
| NFR-03 | error_handling | TP-FR08-005, TP-FR08-006, TP-FR02-004, TP-FR07-002, TP-FR07-004, TP-FR09-002, plus AST scanner for bare `except` | §8 #10, §11 monitoring row |
| NFR-04 | security | TP-FR03-006, `tests/unit/test_redaction.py`, secrets-scanning scanner | §8 #20 |
| NFR-05 | documentation | `tests/architecture/test_docstrings.py` (AST docstring + tag scan) + `/openapi.json` assertion | §10/§11 |
| NFR-06 | architecture_constraints | TP-FR06-005, `tests/architecture/test_importlinter.py` | §8 #21 |
| NFR-07 | license_compliance | `tests/compliance/test_licenses.py`, `08-config/SBOM.json` content check, `pip-licenses --format=json --with-system` | §8 #22 |
| NFR-08 | mutation_testing | `mutmut run && mutmut results` (score ≥ 70 over `service/` + `repository/`) | §8 #24 |
| NFR-09 | test_assertion_quality | `pytest 03-development/tests -q` with skip-count = 0, per-test assert-count ≥ 1, collection-only checks against `--ignore`/`-k`/`--deselect` | §8 #1, #12 |
| NFR-10 | integration_coverage | All `tests/integration/` runs with `--cov`, ≥ 80% line coverage; every error code TP from FR-10.AC1 plus FR-07.AC2 round-trip | §8 #3 |
| NFR-11 | readability | `radon mi / radon cc` and per-file / per-handler line-count assertions | §11 |
| NFR-12 | execute_verification_target | `make verify-system` exit-0 + stdout `verify-system: PASS` | §8 #27 |

### NFR-09 zero-skip verification gate
- **Test ID**: TP-NFR09-001
- **Description**: Repo-wide skip detector (anti-fabrication).
- **Input**: parse test files for `@pytest.mark.skip`, `skipif`, `xfail`; scan CLI args for `--ignore`, `-k`, `--deselect`, `collect_ignore`, `testpaths`.
- **Expected**: 0 hits; pytest run with empty `-q` reports `0 skipped`.
- **Priority**: P0
- **Category**: S

### NFR-09 real-DB migration verification gate (cross FR-07)
- **Test ID**: TP-NFR09-002
- **Description**: Anti-mock for migration.
- **Input**: Confirm the round-trip TP-FR07-002 uses `sqlite:///./<tmpfile>.db`, not `:memory:`.
- **Expected**: file persists on disk during the test; assertion reads it back.
- **Priority**: P0
- **Category**: S

### NFR-10 integration-coverage threshold
- **Test ID**: TP-NFR10-001
- **Description**: Coverage gate.
- **Input**: `pytest 03-development/tests/integration --cov=03-development/src --cov-report=term`.
- **Expected**: TOTAL ≥ 80%.
- **Priority**: P0
- **Category**: S

### NFR-11 readability gates
- **Test ID**: TP-NFR11-001
- **Description**: Project-wide style metrics.
- **Input**: `radon mi -s 03-development/src/`; per-file line count; per-handler line count.
- **Expected**: MI ≥ 80; CC ≤ 10; file ≤ 400 lines; dir ≤ 15 files; handler ≤ 40 lines.
- **Priority**: P1
- **Category**: S

### NFR-12 verify-system smoke
- **Test ID**: TP-NFR12-001
- **Description**: Macro-level verification.
- **Input**: `make verify-system`.
- **Expected**: exit 0, stdout contains `verify-system: PASS`.
- **Priority**: P0
- **Category**: P

---

## 12. Cross-cutting Negative & Edge Categories (applied across FRs)

### Cross-NEG-1 — Boundary: empty body / wrong Content-Type
- **Description**: Send POSTs with wrong content-type, empty body, etc.
- **Expected**: All yield 422 with deterministic RFC 7807 body.

### Cross-NEG-2 — Concurrency stress
- **Description**: Hammer the system with N=100 parallel requests across keys.
- **Expected**: rate-limit + DB row-lock semantics remain exact (no over-allow, no under-allow).

### Cross-NEG-3 — Time-zone / clock injection
- **Description**: Tasks scheduled across DST boundaries.
- **Expected**: persistence and listing pagination remain consistent.

### Cross-NEG-4 — Large payload boundary
- **Description**: `name` exactly 1000 chars (boundary).
- **Expected**: accepted; 1001-char rejected (422).

### Cross-NEG-5 — Unicode in `name`
- **Description**: CJK and emoji strings.
- **Expected**: encoded as UTF-8; comparison byte-stable; round-trip identical.

### Cross-NEG-6 — Database migrations during running service
- **Description**: Trigger request on a v3 schema, force reversion to v2 while running.
- **Expected**: `/readyz` flips to 503; service does not crash.

### Cross-NEG-7 — Bad env values
- **Description**: Invalid TASKQ_DB_URL, negative TASKQ_DRAIN_TIMEOUT, non-integer pool size.
- **Expected**: process refuses to start with a clear log message — no half-started state.

---

## 13. Coverage Checklist (FR-by-FR)

The following matrix explicitly asserts coverage of every FR in `.methodology/quality_manifest.json#fr_ids`:

| FR | Has tests in §? | Test IDs touching it |
|----|-----------------|----------------------|
| FR-01 | §1 | TP-FR01-001..010 |
| FR-02 | §2 | TP-FR02-001..008 |
| FR-03 | §3 | TP-FR03-001..007 |
| FR-04 | §4 | TP-FR04-001..005 |
| FR-05 | §5 | TP-FR05-001..006 |
| FR-06 | §6 | TP-FR06-001..007 |
| FR-07 | §7 | TP-FR07-001..007 |
| FR-08 | §8 | TP-FR08-001..007 |
| FR-09 | §9 | TP-FR09-001..006 |
| FR-10 | §10 | TP-FR10-001..006 |

Every NFR in `quality_manifest.json#nfr_traceability` is covered in §11.

---

## 14. Execution Order (preflight hand-off to per-FR TDD)

1. Static gates (CI step 0): `grep`, `bandit`, `lint-imports`, `pip-licenses`, `radon mi`, `radon cc`.
2. Unit layer: per-function unit tests (transaction context, bucket math, redaction regex, etc.).
3. Migration gates (FR-07) on real SQLite.
4. Integration via `httpx.AsyncClient(transport=ASGITransport(app))`.
5. Performance gate (NFR-01) with `pytest-benchmark` over 10k-row fixture.
6. Mutation gate (NFR-08): `mutmut run && mutmut results`.
7. Macro: `make verify-system`.

---

## 15. Out of Scope (per `01-requirements/SRS.md §6`)

- TypeScript round 3.
- RBAC beyond `read < write < admin`.
- Fourth alembic revision.
- Any FR beyond FR-10.

*End of TEST_PLAN.md v1.0.0.*
