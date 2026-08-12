# Software Requirements Specification (SRS) — taskq-api

> **Document version**: v1.0.0 (2026-07-30)
> **Round**: progressive test-bed round 2 / 3 (Round 1 = `taskq-plus` CLI; Round 3 = TypeScript, deferred)
> **Source of truth**: `SPEC.md` at the project root (v1.0.0)
> **Ingestion mode**: this SRS is a 100% transcription of the `### FR-01..FR-10` and `### NFR-01..NFR-12` headings and §5 env / §5.2 schema / §6 module layout / §7 error map / §8 acceptance items / §9 risk matrix from canonical `SPEC.md`. No invention, no omission. TBD / TODO / placeholders are absent from the canonical and therefore not silently inserted here.

---

## 1. Introduction

### 1.1 Project Identity
- **Name**: `taskq-api`
- **Purpose** (verbatim from SPEC §1): "任務佇列的 HTTP 服務化 — 以 REST API 提交、查詢、執行任務;資料持久化於關聯式資料庫;schema 隨版本演進;支援認證、授權與流量控制"
- **Language**: Python 3.11
- **Form**: ASGI service launched as `uvicorn taskq_api.app:app`; management entry point `python -m taskq_api` (migrate / seed / healthcheck)

### 1.2 Business Goals (verbatim from PROJECT_BRIEF §Business Goals)
- Provide a task-queue HTTP service with authentication, authorisation, rate limiting, dependency-free horizontal scaling and a versioned schema.
- Demonstrate the full Phase 1–8 harness-methodology pipeline on a layered web-service project (10 FR).
- Exercise axes neither previous test-bed could reach: HTTP boundary (authn/authz/input validation), a real database (ORM, transactions, N+1), real schema migration (Alembic with a data-moving step and a reversible downgrade), and async Python.

### 1.3 Test-Bed Intent (verbatim from PROJECT_BRIEF §Why this project exists)
Round 1 lit up `license_compliance`, `architecture_constraints`, `mutation_testing` and `test_assertion_quality`, but it was still a single-process CLI. The countermeasures for axes that produced no signal in earlier test-beds:

| Uncovered axis | Round-2 countermeasure | Clause |
|---|---|---|
| No HTTP layer → `security` only ever saw subprocess calls | REST API + API-key auth + per-token scope + rate limiting | FR-03/04/05, NFR-02 |
| No database → ORM, transactions, connection pools, N+1 all absent | SQLAlchemy ORM + explicit transaction boundaries + N+1 assertions | FR-06, NFR-01 |
| "Schema migration" was a hand-rolled JSON `version` field, and its tests were all skipped | **Alembic: three real revisions, one with data migration, every step reversible** | FR-07, NFR-03 |
| No async → the framework's scanners have never met `async def` | async endpoints + asyncio background runner | FR-08, NFR-03 |
| Shallow dependency tree | fastapi / sqlalchemy / alembic / uvicorn plus transitives, lock-file pinned | NFR-07 |
| Integration tests only ever drove a CLI subprocess | `httpx.ASGITransport` end-to-end incl. every error code | NFR-10 |

---

## 2. Constraints

The following constraints are transcribed verbatim from PROJECT_BRIEF §Key Constraints and reflect the canonical spec body without re-interpretation.

### 2.1 Technical
- Python 3.11; FastAPI ASGI app (`uvicorn taskq_api.app:app`); SQLAlchemy 2.x with explicit `Session` transaction boundaries; Alembic for migrations; `asyncio.create_subprocess_exec` for task execution — **`shell=True` forbidden everywhere**.
- Database: SQLite (dev/test), PostgreSQL (prod) — single ORM model (SPEC §2).
- HTTP framework: FastAPI (ASGI). Data validation: pydantic v2 request/response models (SPEC §2).
- Migration tool: **Alembic** with v1 → v2 → v3, every step reversible (SPEC §2 / FR-07).
- Async: `async def` endpoints + `asyncio.TaskGroup` background runner (SPEC §2 / FR-08).
- Task execution: `asyncio.create_subprocess_exec`; `shell=True` forbidden (SPEC §2).

### 2.2 Architecture
Four layers `api > service > repository > models` enforced by a mandatory `.importlinter` contract; `config` and `errors` are independence modules; **`sqlalchemy` may only be imported by `repository/`** — ORM leakage into the business layer is the specific anti-pattern this round guards against (NFR-06).

### 2.3 Security
- API keys stored as SHA-256 hashes and compared with `hmac.compare_digest` (FR-03, NFR-02).
- 403 responses must not reveal whether the resource exists (FR-04, NFR-02).
- No string-concatenated SQL anywhere (FR-06, NFR-02).
- CORS denies all origins by default; allowlist set via `TASKQ_CORS_ORIGINS` (NFR-02).
- Error bodies must not carry stack traces, SQL or file paths (FR-10, NFR-02).

### 2.4 Migration
Three revisions — v1 base tables, v2 tags many-to-many, **v3 moves `tasks.result_json` into a `task_results` table with real data migration**; `upgrade head` → sample write → `downgrade -1` → `upgrade head` must leave every column byte-identical (FR-07).

### 2.5 Async Correctness
- `asyncio.CancelledError` must propagate — it must never be swallowed by `except Exception` (FR-08, NFR-03).
- Task timeouts must actually kill the child process (`kill()` then `await wait()`), leaving no orphans (FR-08, NFR-03).
- Shutdown drains in-flight work up to `TASKQ_DRAIN_TIMEOUT` (FR-08).

### 2.6 Query Efficiency
Relationship loads must be explicit (`selectinload` / `joinedload`); **N+1 is an acceptance failure** — the list endpoint's SQL statement count must be constant regardless of how many rows come back (NFR-01).

### 2.7 Readiness
`/readyz` returns 503 when the database is unreachable **or** when `alembic current` is not at head — deploying new code without running the migration must fail closed (FR-09).

### 2.8 Verification Honesty
Same zero-skip rule as round 1, plus a specific clause — the three-step migration must be tested against a **real database file**, not a mock, and may not be downgraded to a skip on the grounds that "migration logic is hard to test" (NFR-09).

---

## 3. Functional Requirements

> 100% transcription from canonical SPEC.md §3. Headings kept as canonical. Each FR ends with one or more testable AC lines tied to SPEC.md §8 commands by ID.

### FR-01: 任務資源 CRUD API

| Method | Path | Scope | Behaviour |
|--------|------|-------|-----------|
| `POST` | `/v1/tasks` | `write` | 建立任務;body 由 `TaskCreate` pydantic 模型驗證 |
| `GET` | `/v1/tasks/{id}` | `read` | 取得單一任務全欄位 |
| `GET` | `/v1/tasks` | `read` | 分頁列表,支援 `?status=`、`?limit=`、`?cursor=` |
| `DELETE` | `/v1/tasks/{id}` | `admin` | 刪除任務(連同結果列,同一交易) |

- 驗證規則同第 1 輪 FR-01(非空 / ≤1000 字元 / 注入字元黑名單 / 名稱唯一);違反 → **HTTP 422** + problem+json
- 未知 id → **HTTP 404** + problem+json
- 分頁為 **cursor-based**(不得用 offset —— 大表 offset 掃描是 N+1 的親戚)
- 列表端點的預設 `limit` 為 50,上限 200;超過上限 → 422

**Acceptance Criteria** (canonical SPEC §8 commands #4–#8, #14):
- `POST /v1/tasks`(有效 write key) → 201 + task id (SPEC §8 #4).
- `POST /v1/tasks`(無 `X-API-Key`) → 401 + problem+json (SPEC §8 #5).
- `GET /v1/tasks/{unknown}` → 404 + problem+json (SPEC §8 #7).
- `POST /v1/tasks` 重複 name → 409 (SPEC §8 #8).
- `GET /v1/tasks?limit=50`(10,000 筆)的 SQL 陳述計數 → 常數(與筆數無關 — N+1 防護,NFR-01) (SPEC §8 #14).
- Pagination contract is cursor-based (not offset); list default `limit=50`, upper bound 200 (SPEC §3 FR-01).

---

### FR-02: 任務執行端點

- `POST /v1/tasks/{id}/run`(scope `write`)→ **HTTP 202 Accepted**,body 含 `run_id`
- 實際執行以 `asyncio.create_subprocess_exec(*shlex.split(command))` 進行,**禁 `shell=True`**,timeout 為 `TASKQ_TASK_TIMEOUT`
- 狀態機:`pending → running → done | failed | timeout`
- 執行結果寫入 `task_results` 表(FR-07 的 v3 schema),欄位:`exit_code` / `stdout_tail` / `stderr_tail` / `duration_ms` / `finished_at`
- `GET /v1/tasks/{id}/runs`(scope `read`)→ 該任務的歷史執行紀錄,新到舊排序

**Acceptance Criteria** (canonical SPEC §8 #25):
- 服務關閉時有進行中的任務 → graceful drain;逾時者標記 `interrupted`,無孤兒進程(FR-08 / NFR-03).
- `shell=True` 不得出現在 codebase(SPEC §8 #16) — checks FR-02's execution primitive as a corollary.
- 任務 timeout 必須確實終止子進程 —— child process kill + wait verification (SPEC §3 FR-08 / §8 #25; NFR-03).

> **DERIVED: SPEC §3 FR-02 (`shlex.split(command)`)** — the canonical names `shlex.split(command)` as the argument splitter; this SRS transcribes the canonical phrase verbatim. The exact measurement boundary (what counts as `shlex.split` vs alternative tokenisers) is owned by the test harness per SPEC §8 #25 / §11 monitoring table.

---

### FR-03: API Key 認證

- 全部 `/v1/*` 端點要求 `X-API-Key` header;缺少或無效 → **HTTP 401** + problem+json
- 金鑰**以 SHA-256 雜湊儲存**於 `api_keys` 表,**不得存明文**;比對用 `hmac.compare_digest`(常數時間)
- 金鑰由 `python -m taskq_api key create --scope <scope>` 產生,明文**只在建立當下印出一次**
- 停用金鑰:`revoked_at` 非空的金鑰一律視為無效
- `/healthz`、`/readyz` 不要求認證(FR-09)

**Acceptance Criteria** (canonical SPEC §8 #5, #18, #20):
- `POST /v1/tasks`(無 `X-API-Key`) → 401 + problem+json (SPEC §8 #5).
- 查 `api_keys` 表 → 無明文金鑰;`key_hash` 為 64 hex(NFR-02) (SPEC §8 #18).
- 日誌與 `/v1/metrics` 全文 → 不含 `TASKQ_DB_URL` 的密碼片段(NFR-04) (SPEC §8 #20).
- API key 明文只在 `key create` 當下輸出一次,不得寫入任何持久化位置 (SPEC §3 FR-03 / NFR-04).

---

### FR-04: Scope 授權

- 每把金鑰帶一個 scope:`read` < `write` < `admin`(階層包含)
- 端點所需 scope 見 FR-01/02 表;不足 → **HTTP 403** + problem+json,且 **body 不得洩漏該資源是否存在**
- 授權判定必須在**單一中介層(dependency)**完成,不得散落於各 handler —— 以測試斷言「每個 `/v1` 路由都經過同一個 dependency」

**Acceptance Criteria** (canonical SPEC §8 #6, #21):
- `DELETE /v1/tasks/{id}`(write key,非 admin) → 403,body 不透露該 id 是否存在 (SPEC §8 #6).
- `lint-imports` exit 0,且 `service`/`api` 層 import `sqlalchemy` 會被擋(NFR-06) (SPEC §8 #21) — architectural corollary to the single-dependency decision point.
- 授權判定以測試斷言「每個 `/v1` 路由都經過同一個 dependency」(SPEC §3 FR-04).

> **DERIVED: SPEC §3 FR-04 (`單一中介層(dependency)`)** — the canonical names the single-dependency contract via "以測試斷言「每個 `/v1` 路由都經過同一個 dependency」". This SRS transcribes the canonical phrase verbatim. The exact mechanism of the single decision point (FastAPI `Depends(...)` vs custom middleware vs both) is owned by the test harness per SPEC §3 FR-04 and §6 module layout (which places the dep in `taskq_api/api/deps.py`).

---

### FR-05: 流量控制

- per-token 令牌桶:容量 `TASKQ_RATE_BURST`,補充速率 `TASKQ_RATE_PER_SEC`
- 超限 → **HTTP 429** + problem+json + `Retry-After` header(秒)
- 令牌桶狀態存於資料庫(跨 worker 一致),更新必須在單一交易內以 row-level lock 進行
- `/healthz`、`/readyz` 不受限

**Acceptance Criteria** (canonical SPEC §8 #9):
- 連續請求超過 `TASKQ_RATE_BURST` → 429 + `Retry-After` header (SPEC §8 #9).

> **DERIVED: SPEC §3 FR-05 (`row-level lock`)** — the canonical uses the database-agnostic term "row-level lock". This SRS transcribes the canonical phrase verbatim. The specific locking primitive (`SELECT ... FOR UPDATE` on PostgreSQL, or SQLite serialised-transaction equivalent) is owned by the test harness per SPEC §3 FR-05 and §6 module layout (where `rate_repo.py` lives in `repository/`).

---

### FR-06: 持久化層與交易邊界

- 全部資料存取經由 `repository/` 層,**業務層不得直接持有 `Session`**
- 每個 API 請求一個 `Session`,交易邊界明確:成功 commit、例外 rollback(以 context manager 保證)
- **禁止字串拼接 SQL**;一律使用 ORM 或參數化查詢(NFR-02)
- 關聯查詢必須用 `selectinload` / `joinedload` 顯式預載 —— **N+1 為驗收失敗條件**(NFR-01)
- 連線池:`pool_size=TASKQ_DB_POOL_SIZE`,`pool_pre_ping=True`

**Acceptance Criteria** (canonical SPEC §8 #14, #17, #21):
- 列表端點 SQL 陳述計數 → 常數(與筆數無關 — N+1 防護) (SPEC §8 #14).
- 掃描 SQL 字串拼接(f-string / `%` / `+` 組 SQL) → 0 命中(NFR-02) (SPEC §8 #17).
- `lint-imports` exit 0;且 `service`/`api` 層 import `sqlalchemy` 會被擋(NFR-06) (SPEC §8 #21).

---

### FR-07: Schema Migration(Alembic 三步演進)

三個 revision,每一步都必須有可運作的 `downgrade`:

| revision | upgrade 內容 | downgrade 要求 |
|---|---|---|
| **v1** | 建立 `tasks`、`api_keys` 兩表 | drop 兩表 |
| **v2** | 新增 `tags`、`task_tags`(多對多)+ `tasks.name` 唯一索引 | drop 新表與索引,不影響 v1 資料 |
| **v3** | **含資料搬遷**:把 `tasks.result_json` 拆為獨立的 `task_results` 表,搬遷既有資料後移除原欄位 | 反向搬遷回 `tasks.result_json` 後 drop `task_results`,**資料不得遺失** |

- `alembic upgrade head` 與 `alembic downgrade base` 必須都成功
- **往返可逆性驗收**:`upgrade head` → 寫入樣本資料 → `downgrade -1` → `upgrade head`,樣本資料的欄位值必須逐欄相同(v3 的資料搬遷是本條的重點)
- 禁止以 `op.execute("DROP TABLE ...")` 之類的破壞性捷徑取代真正的 downgrade
- migration 檔本身納入測試覆蓋(以 `alembic` 的 offline SQL 產生 + 斷言)

**Acceptance Criteria** (canonical SPEC §8 #11, #12, #13):
- `alembic downgrade -1` 後 `GET /readyz` → 503,detail 指明 migration 未到 head (SPEC §8 #11).
- `alembic upgrade head` → 寫樣本 → `downgrade -1` → `upgrade head` → 樣本資料逐欄相同(FR-07) (SPEC §8 #12).
- `alembic downgrade base` → exit 0,無殘留表 (SPEC §8 #13).
- `make verify-system` 必須 exit 0(含 migration 往返) (SPEC §8 #27 / NFR-12).

> **DERIVED: SPEC §3 FR-07 (`op.execute("DROP TABLE ...")`)** — the canonical lists `op.execute("DROP TABLE ...")` as one example of a destructive shortcut that may not replace a real downgrade. This SRS transcribes the canonical example verbatim. The exact enumeration of which destructive shortcuts are forbidden beyond that example (e.g. `DROP COLUMN` without reverse, `TRUNCATE` in upgrade) is owned by the test harness per SPEC §3 FR-07's "破壞性捷徑" generic.

---

### FR-08: 非同步執行器

- 背景執行以 `asyncio.TaskGroup` 管理;服務關閉時必須 **graceful drain**(等待進行中的任務至 `TASKQ_DRAIN_TIMEOUT`,逾時則標記 `interrupted`)
- 併發上限 `TASKQ_MAX_CONCURRENT`;超過時新任務排隊,不得無限制生成 coroutine
- 任務 timeout 以 `asyncio.wait_for` 實作;逾時必須**確實終止子進程**(`process.kill()` 後 `await process.wait()`),不得留下孤兒進程
- 取消語意:`asyncio.CancelledError` 必須向上傳播,**不得被 `except Exception` 吞掉**(NFR-03)

**Acceptance Criteria** (canonical SPEC §7 / §8 #25 / §11 orphan row):
- 服務關閉時有進行中的任務 → graceful drain;逾時者標記 `interrupted`,無孤兒進程 (SPEC §8 #25).
- 孤兒子進程 → 0 (SPEC §11 monitoring row).
- `asyncio.CancelledError` 不得被吞掉 ── 必須重新拋出 (SPEC §3 FR-08 / NFR-03).
- 任務 timeout 必須確實終止子進程 (`process.kill()` 後 `await process.wait()`)(SPEC §3 FR-08 / §7 timeout row).

> **DERIVED: SPEC §3 FR-08 (`mark interrupted` on drain-timeout)** — the canonical phrases the drain-timeout outcome as "逾時則標記 `interrupted`". This SRS transcribes the canonical phrase verbatim. The exact `interrupted` status enum value, its interaction with `done | failed | timeout`, and whether such a task is surfaced in `GET /v1/tasks/{id}/runs` as an additional row are owned by the test harness per SPEC §3 FR-02 status machine + SPEC §3 FR-08 graceful drain.

---

### FR-09: 健康檢查與可觀測性

| 端點 | 認證 | 行為 |
|------|------|------|
| `GET /healthz` | 無 | 進程存活 → 200 `{"status":"ok"}` |
| `GET /readyz` | 無 | DB 連線可用 **且** `alembic current` == head → 200;否則 **503** 並在 body 說明哪一項失敗 |
| `GET /v1/metrics` | `admin` | 任務計數(按狀態)、執行延遲分位數、rate-limit 拒絕數 |

- `/readyz` 的「migration 未到 head」判定是關鍵:部署了新程式碼但忘記跑 migration 時必須 **fail closed**

**Acceptance Criteria** (canonical SPEC §8 #10, #11):
- 停掉 DB 後 `GET /readyz` → 503,detail 指明 DB 不可用 (SPEC §8 #10).
- `alembic downgrade -1` 後 `GET /readyz` → 503,detail 指明 migration 未到 head (SPEC §8 #11).
- `/healthz` 不要求認證(SPEC §3 FR-09 / FR-03 exception).

---

### FR-10: 錯誤契約(RFC 7807)

- 全部非 2xx 回應的 `Content-Type` 為 `application/problem+json`
- body 欄位:`type`(URI)、`title`、`status`、`detail`、`instance`、`correlation_id`
- **`detail` 不得洩漏內部細節**:不得含 SQL 陳述、堆疊追蹤、檔案路徑、資料庫結構描述
- `correlation_id` 同時出現在回應 header `X-Correlation-Id` 與伺服器日誌,可用於串接
- 錯誤碼對照:422 驗證 / 401 未認證 / 403 scope 不足 / 404 未知資源 / 409 名稱衝突 / 429 超限 / 503 未就緒 / 500 其他

**Acceptance Criteria** (canonical SPEC §8 #19):
- 觸發 500 後檢查回應 body → 不含堆疊 / SQL / 檔案路徑(FR-10 / NFR-02) (SPEC §8 #19).
- 全部非 2xx 回應 `Content-Type` 為 `application/problem+json`,且 `correlation_id` 同時出現在回應 header `X-Correlation-Id` 與伺服器日誌(SPEC §3 FR-10).

---

## 4. Non-Functional Requirements

> 100% transcription from canonical SPEC.md §4. All `dimension:` fields below are **verified against the current `harness/harness/ssi/prompts/evaluate_dimension.md` roster**:
> `linting`, `type_safety`, `test_coverage`, `test_assertion_quality`, `security`, `secrets_scanning`, `license_compliance`, `mutation_testing`, `architecture_constraints`, `integration_coverage`, `execute_verification_target`, `architecture`, `readability`, `error_handling`, `documentation`, `performance`, `adversarial_review`.
> Every dimension cited below is present in that roster; no canonical dimension is missing.

### NFR-01: 效能與查詢效率
- **dimension**: `performance` (roster-confirmed)
- `GET /v1/tasks/{id}` 在 10,000 筆資料下 **p95 < 30ms**(不含網路,以 ASGI transport 量測)
- `GET /v1/tasks?limit=50` 在 10,000 筆資料下 **p95 < 80ms**
- **N+1 為失敗條件**:列表端點回應一次請求所發出的 SQL 陳述數必須是 **常數**(與回傳筆數無關),以 SQLAlchemy event listener 計數斷言
- 量測方式:`pytest-benchmark`

**Acceptance Criteria** (canonical SPEC §8 #14, #15 / §11 monitoring):
- 列表端點 SQL 陳述數 → 常數(與筆數無關)(SPEC §8 #14).
- `GET /v1/tasks/{id}` p95(10,000 筆) → < 30ms(SPEC §8 #15).
- `GET /v1/tasks?limit=50` p95(10,000 筆) → < 80ms(SPEC §11 monitoring row; SPEC §4 NFR-01).

---

### NFR-02: HTTP 與資料層安全
- **dimension**: `security` (roster-confirmed)
- 全 codebase 禁用 `shell=True`、`eval(`、`exec(`(grep 0 命中)
- **禁止字串拼接 SQL**:不得出現 f-string / `%` / `+` 組成的 SQL;一律 ORM 或參數化(以 grep + code review 雙重驗證)
- API key **雜湊儲存**,比對用 `hmac.compare_digest`(FR-03)
- 403 回應不得洩漏資源存在性(FR-04)
- 錯誤 body 不得含堆疊/SQL/路徑(FR-10)
- CORS 預設**拒絕所有來源**;允許清單由 `TASKQ_CORS_ORIGINS` 明示
- `bandit -r 03-development/src/`:**0 HIGH、0 MEDIUM**

**Acceptance Criteria** (canonical SPEC §8 #6, #16, #17, #18, #19, #21, #23):
- `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` → 0 命中 (SPEC §8 #16).
- 掃描 SQL 字串拼接(f-string / `%` / `+` 組 SQL) → 0 命中 (SPEC §8 #17).
- 查 `api_keys` 表 → 無明文金鑰;`key_hash` 為 64 hex (SPEC §8 #18).
- `DELETE /v1/tasks/{id}`(write key,非 admin) → 403,body 不透露該 id 是否存在 (SPEC §8 #6).
- 觸發 500 後檢查回應 body → 不含堆疊 / SQL / 檔案路徑 (SPEC §8 #19).
- `lint-imports` exit 0;且 `service`/`api` 層 import `sqlalchemy` 會被擋 (SPEC §8 #21).
- `bandit -r 03-development/src/` → 0 HIGH,0 MEDIUM (SPEC §8 #23).

---

### NFR-03: 錯誤處理、交易與非同步正確性
- **dimension**: `error_handling` (roster-confirmed)
- 每個請求的交易邊界明確:成功 commit、例外 rollback,以 context manager 保證(FR-06)
- **不得**出現裸 `except:`、`except Exception: pass`
- **`asyncio.CancelledError` 不得被吞掉** —— 必須重新拋出(async 專屬的吞噬陷阱)
- 資料庫連線失敗 → `/readyz` 503 + 明確 detail;不得靜默重試至無限
- 任務 timeout 必須確實終止子進程,不留孤兒(FR-08)
- migration 失敗 → 交易 rollback,資料庫維持在前一個 revision(FR-07)

**Acceptance Criteria** (canonical SPEC §7 / §8 #10 / §11 monitoring row):
- 停掉 DB 後 `GET /readyz` → 503,detail 指明 DB 不可用 (SPEC §8 #10).
- `asyncio.CancelledError` 不得被吞掉 ── 必須重新拋出 (SPEC §3 NFR-03).
- 孤兒子進程 → 0 (SPEC §11).
- 每個請求的交易邊界明確:成功 commit、例外 rollback,以 context manager 保證 (SPEC §3 NFR-03 / FR-06).
- `asyncio.CancelledError` is on none of SPEC §7 rows; it propagates (SPEC §7 inline note).

> **DERIVED: SPEC §4 NFR-03 (`不得靜默重試至無限`)** — the canonical uses "至無限" (open-ended) as the failure boundary. This SRS transcribes the canonical phrase verbatim. The exact retry policy (number of attempts, backoff curve, circuit-breaker) is owned by the test harness per SPEC §4 NFR-03.

---

### NFR-04: 敏感資料遮蔽
- **dimension**: `security` (roster-confirmed)
- `stdout_tail` / `stderr_tail` / 日誌 / 錯誤 body 落盤或送出前,匹配
  `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` 的行整行以 `[REDACTED]` 取代
- **資料庫連線字串**(含密碼)不得出現在任何日誌、錯誤訊息或 `/v1/metrics` 回應中
- API key 明文只在 `key create` 當下輸出一次,不得寫入任何持久化位置

**Acceptance Criteria** (canonical SPEC §8 #20):
- 日誌與 `/v1/metrics` 全文 → 不含 `TASKQ_DB_URL` 的密碼片段 (SPEC §8 #20).

> **DERIVED: SPEC §4 NFR-04 (regex `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`)** — the canonical specifies an exact redact-then-substitute regex in line-form. This SRS transcribes the canonical regex verbatim into the AC; the regex is the canonical-contracted detection surface and is not augmented by additional patterns here.

---

### NFR-05: 文件覆蓋
- **dimension**: `documentation` (roster-confirmed)
- 全部公開函式/類別有 docstring 且含 `[FR-XX]` 或 `[NFR-XX]` 引用,覆蓋率 **100%**
- 每個 API 端點在 OpenAPI schema 中有 `summary` 與 `description`(FastAPI 自動產生的 `/openapi.json` 以測試斷言)

**Acceptance Criteria** (canonical SPEC §10 / §11):
- 全部公開函式/類別有 docstring 且含 `[FR-XX]` 或 `[NFR-XX]` 引用,覆蓋率 **100%** (SPEC §4 NFR-05).
- 每個 API 端點在 OpenAPI schema 中有 `summary` 與 `description` ── `/openapi.json` 以測試斷言 (SPEC §4 NFR-05).

---

### NFR-06: 架構分層契約
- **dimension**: `architecture_constraints` (roster-confirmed)
- 專案根目錄**必須存在 `.importlinter`**,宣告 layers contract:

  ```
  api > service > repository > models
  ```

  上層可 import 下層,**下層不得 import 上層**;`config` 與 `errors` 為 independence 模組
- **額外禁令(forbidden contract)**:`repository` 以外的任何層**不得 import `sqlalchemy`** —— ORM 洩漏到業務層是本輪要防的具體反模式
- `lint-imports` 必須 **exit 0**
- 禁止以刪除 `.importlinter`、萬用字元 `ignore_imports`、或降級 contract 的方式取得通過

**Acceptance Criteria** (canonical SPEC §8 #21):
- `lint-imports` exit 0;且 `service`/`api` 層 import `sqlalchemy` 會被擋 (SPEC §8 #21).
- 專案根目錄**必須存在 `.importlinter`**;禁止以刪除 `.importlinter`、萬用字元 `ignore_imports`、或降級 contract 的方式取得通過 (SPEC §4 NFR-06).

---

### NFR-07: 依賴與授權合規
- **dimension**: `license_compliance` (roster-confirmed)
- 全部 runtime 依賴在 `requirements.txt` 以 `==` 釘版;**transitive 依賴以 lock 檔(`requirements.lock`)完整鎖定**
- 允許的 license:MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF;出現其他 → 該依賴不得使用
- **掃描範圍必須包含完整依賴樹**(直接 + transitive),證據命令:`pip-licenses --format=json --with-system`
- 產出 SBOM 於 `08-config/SBOM.json`,含每個依賴的 `name` / `version` / `license` / `direct|transitive`

**Acceptance Criteria** (canonical SPEC §8 #22):
- `pip-licenses --format=json --with-system` → 每個依賴 license ∈ allowlist(NFR-07) (SPEC §8 #22).
- SBOM 產出於 `08-config/SBOM.json`,含每個依賴的 `name` / `version` / `license` / `direct|transitive` (SPEC §4 NFR-07).

> **DERIVED: SPEC §4 NFR-07 (allowlist `MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF`)** — the canonical lists a fixed allowlist. This SRS transcribes the canonical list verbatim. Any additional compatibility-FOSS license additions (e.g. `MPL-2.0`, `ISC`) are owned by the test harness per SPEC §4 NFR-07's "出現其他 → 該依賴不得使用" rule.

---

### NFR-08: 變異測試

> **DERIVED: SPEC §4 NFR-08 — `dimension: mutation_testing` annotation** — the canonical `§4 NFR-08` body is 100% transcribed below; the bracketed `dimension: mutation_testing` annotation is added to bind the NFR to the current harness roster key (`mutation_testing`), required by Phase 2 SAB generation. The body, `mutation score ≥ 70` threshold, scope limitation, and AC (`mutmut run` / `mutmut results`) are verbatim from canonical SPEC §4 NFR-08 and §8 #24.

- **dimension**: `mutation_testing` (roster-confirmed)
- `.methodology/harness_config.json` 設 `features.mutation_testing: true`
- **mutation score ≥ 70**
- 範圍限定於 `service/` 與 `repository/` 兩層,並在 `harness_config.json` 註記限定理由(執行時間預算)

**Acceptance Criteria** (canonical SPEC §8 #24):
- `mutmut run` 後 `mutmut results` → mutation score **≥ 70**(NFR-08) (SPEC §8 #24).

---

### NFR-09: 驗證真實性(零 skip 鐵律)
- **dimension**: `test_assertion_quality` (roster-confirmed)
- **任何 FR / NFR 的驗證測試不得是 `pytest.skip` / `skipif` / `xfail` / 無斷言的 stub**
- `pytest 03-development/tests -q` 的 **skipped 計數必須為 0**
- 每個測試函式至少一個 `assert`(`zero_assert == 0`)
- **反造假條款**:不得以 `--ignore` / `-k` / `--deselect` / `collect_ignore` / 從 `testpaths` 移除目錄的方式排除測試
- **本輪特別條款**:`FR-07` 的三步 migration 必須以**真實資料庫**測試(SQLite 檔案,非 in-memory mock),往返可逆性以實際資料比對驗證。**不得**以「migration 邏輯太難測」為由降級為 skip —— 這正是前兩輪失敗的形態
- `TRACEABILITY_MATRIX.md` 的 `VERIFIED` 只能在測試實際執行並通過時給出

**Acceptance Criteria** (canonical SPEC §8 #1, #12):
- `pytest 03-development/tests -q` → 全綠,**skipped 計數為 0**(NFR-09) (SPEC §8 #1).
- migration 三步以真實資料庫檔案測試;往返可逆性以實際資料比對驗證 (SPEC §4 NFR-09 / SPEC §8 #12).

---

### NFR-10: 整合覆蓋
- **dimension**: `integration_coverage` (roster-confirmed)
- `03-development/tests/integration/` 行覆蓋 **≥ 80%**
- 整合測試以 `httpx.AsyncClient(transport=ASGITransport(app))` 驅動,**不得直接呼叫 handler 函式**
- 至少涵蓋:CRUD 全鏈、401/403/404/409/422/429/503 每個錯誤碼各一例、migration 往返、rate limit 觸發與恢復、graceful drain

**Acceptance Criteria** (canonical SPEC §8 #3):
- `pytest 03-development/tests/integration --cov=03-development/src --cov-report=term` → TOTAL **≥ 80%**(NFR-10) (SPEC §8 #3).
- 至少涵蓋:CRUD 全鏈、401/403/404/409/422/429/503 每個錯誤碼各一例、migration 往返、rate limit 觸發與恢復、graceful drain (SPEC §4 NFR-10).

---

### NFR-11: 可讀性
- **dimension**: `readability` (roster-confirmed)
- 專案 MI(LLOC 加權)**≥ 80**;單一函式 CC **≤ 10**
- 單一檔案 ≤ 400 行;單一目錄 ≤ 15 檔
- 每個 API handler ≤ 40 行(業務邏輯必須下沉到 `service/`)

**Acceptance Criteria** (canonical SPEC §11 monitoring row):
- 專案 MI ≥ 80 (SPEC §11; SPEC §4 NFR-11).

---

### NFR-12: 系統驗證目標

> **DERIVED: SPEC §4 NFR-12 — `dimension: execute_verification_target` annotation** — the canonical `§4 NFR-12` body is 100% transcribed below; the bracketed `dimension: execute_verification_target` annotation is added to bind the NFR to the current harness roster key (`execute_verification_target`), required by Phase 2 SAB generation. The body, numbered list 1–4, and the `make verify-system` exit-code constraint are verbatim from canonical SPEC §4 NFR-12.

- **dimension**: `execute_verification_target` (roster-confirmed)
- `Makefile` 的 `verify-system` target 必須串接:
  1. `alembic upgrade head`
  2. 全套測試
  3. 服務啟動 + `/healthz`、`/readyz` 冒煙
  4. `alembic downgrade base` 後再 `upgrade head`(往返驗證)
- `make verify-system` 必須 **exit 0** 並在 stdout 印出 `verify-system: PASS`

**Acceptance Criteria** (canonical SPEC §8 #27):
- `make verify-system` → exit 0 且 stdout 含 `verify-system: PASS`(NFR-12) (SPEC §8 #27).

---

## 5. Acceptance Criteria Summary (canonical SPEC §8)

> 27 acceptance items, **each a single machine-decidable command with an expected output** (verbatim from SPEC §8).

| # | Command | Expected |
|---|---------|----------|
| 1 | `pytest 03-development/tests -q` | 全綠,**skipped 計數為 0**(NFR-09) |
| 2 | `pytest 03-development/tests --cov=03-development/src --cov-report=term` | TOTAL **100%** |
| 3 | `pytest 03-development/tests/integration --cov=03-development/src --cov-report=term` | TOTAL **≥ 80%**(NFR-10) |
| 4 | `POST /v1/tasks`(有效 write key) | 201 + task id |
| 5 | `POST /v1/tasks`(無 `X-API-Key`) | **401** + problem+json |
| 6 | `DELETE /v1/tasks/{id}`(write key,非 admin) | **403**,body 不透露該 id 是否存在 |
| 7 | `GET /v1/tasks/{unknown}` | **404** + problem+json |
| 8 | `POST /v1/tasks` 重複 name | **409** |
| 9 | 連續請求超過 `TASKQ_RATE_BURST` | **429** + `Retry-After` header |
| 10 | 停掉 DB 後 `GET /readyz` | **503**,detail 指明 DB 不可用 |
| 11 | `alembic downgrade -1` 後 `GET /readyz` | **503**,detail 指明 migration 未到 head |
| 12 | `alembic upgrade head` → 寫樣本 → `downgrade -1` → `upgrade head` | 樣本資料逐欄相同(FR-07) |
| 13 | `alembic downgrade base` | exit 0,無殘留表 |
| 14 | `GET /v1/tasks?limit=50`(10,000 筆)的 SQL 陳述計數 | **常數**(與筆數無關) |
| 15 | `GET /v1/tasks/{id}` p95(10,000 筆) | **< 30ms**(NFR-01) |
| 16 | `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` | **0 命中** |
| 17 | 掃描 SQL 字串拼接(f-string / `%` / `+` 組 SQL) | **0 命中**(NFR-02) |
| 18 | 查 `api_keys` 表 | 無明文金鑰;`key_hash` 為 64 hex(NFR-02) |
| 19 | 觸發 500 後檢查回應 body | 不含堆疊 / SQL / 檔案路徑(FR-10 / NFR-02) |
| 20 | 日誌與 `/v1/metrics` 全文 | 不含 `TASKQ_DB_URL` 的密碼片段(NFR-04) |
| 21 | `lint-imports` | **exit 0**,且 `service`/`api` 層 import `sqlalchemy` 會被擋(NFR-06) |
| 22 | `pip-licenses --format=json --with-system` | 每個依賴 license ∈ allowlist(NFR-07) |
| 23 | `bandit -r 03-development/src/` | 0 HIGH,0 MEDIUM |
| 24 | `mutmut run` 後 `mutmut results` | mutation score **≥ 70**(NFR-08) |
| 25 | 服務關閉時有進行中的任務 | graceful drain;逾時者標記 `interrupted`,無孤兒進程(FR-08) |
| 26 | `grep -c "^TASKQ_" .env.example` | **12**(§5.1 全部宣告) |
| 27 | `make verify-system` | exit 0 且 stdout 含 `verify-system: PASS`(NFR-12) |

---

## 6. Out-of-Scope (transcribed from canonical PROJECT_BRIEF/SPEC silent omissions)

The canonical spec scopes the project to the 10 FRs and 12 NFRs above, plus the 12 env vars (§7). The following are explicitly not part of this round:

- Anything not listed in SPEC §3 (FRs), §4 (NFRs), §5.1 (env), §5.2 (schema), §5.3 (config files), §6 (modules), §7 (errors), §8 (acceptance) of the canonical.
- TypeScript round 3 (deferred per PROJECT_BRIEF §Stakeholders).
- Any FR beyond FR-10 (e.g. user-management, RBAC beyond three scopes, multi-tenancy) — not in canonical.
- Anything beyond FR-07's three-revision Alembic line (no fourth revision, no greenfield DB sharding, etc.).

---

## 7. Open Issues (deferred items)

> Canonical spec contains **no TBD / TODO / `<placeholder>` markers** (per the INGESTION MODE scan of SPEC.md). The following `NFR-99` / `FR-XX-deferred` items are captured to satisfy the framework's deferral surface **without inventing content** — they record ambiguity that is present in the canonical phrasing and signal Phase 3+ to confirm a precise interpretation with the stakeholder:

- **NFR-99-01**: Resolve `SPEC §3 FR-02` `shlex.split(command)` boundary — the canonical names a specific argument splitter; the SRS transcribes the canonical phrase and defers the exact "what tokeniser / escaping rules count as FR-02's execution primitive" decision to the test harness.
- **NFR-99-02**: Resolve `SPEC §3 FR-04` `單一中介層(dependency)` mechanism — FastAPI `Depends(...)`, custom middleware, or both; canonical uses the term `dependency` without further disambiguation.
- **NFR-99-03**: Resolve `SPEC §3 FR-05` `row-level lock` primitive — PostgreSQL `SELECT ... FOR UPDATE` vs SQLite serialised-transaction equivalent; canonical uses the database-agnostic term.
- **NFR-99-04**: Resolve `SPEC §3 FR-07` `破壞性捷徑` enumeration — canonical lists `op.execute("DROP TABLE ...")` as one example; the SRS defers the broader taxonomy of "destructive shortcuts" to the test harness.
- **NFR-99-05**: Resolve `SPEC §3 FR-08` `mark interrupted` status — canonical places `interrupted` adjacent to `done | failed | timeout`; the exact enum value's role in `GET /v1/tasks/{id}/runs` history rows is owned by the test harness.
- **NFR-99-06**: Resolve `SPEC §4 NFR-03` retry policy (attempts, backoff, circuit-breaker) — canonical only forbids "至無限".
- **NFR-99-07**: Resolve `SPEC §4 NFR-07` allowlist additions beyond `MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF` — canonical fixes the list and says "出現其他 → 該依賴不得使用".
- **NFR-99-08**: Resolve `SPEC §4 NFR-05` summary/description schema for OpenAPI endpoints (FastAPI auto-emits summary from the route decorator or requires explicit `description=` kwargs per route).
- **NFR-99-09**: Resolve `SPEC §3 FR-09` metrics endpoint file placement — canonical §6 module layout lists only `api/health.py` under `api/`; the SRS §15 FR Block places the `/v1/metrics` endpoint in `taskq_api.api.health` per that canonical-only mapping. Any later stake-holder decision to split `metrics.py` out of `health.py` is to be confirmed before Phase 3 implementation.

---

## 8. Risks (verbatim from canonical SPEC §9)

| ID | 風險 | 影響 | 可能性 | 緩解 |
|----|------|------|--------|------|
| R1 | **v3 資料搬遷遺失資料** | **高** | 中 | 往返可逆性測試以真實 DB 逐欄比對(FR-07 / §8 #12) |
| R2 | SQL injection | 高 | 低 | 禁字串拼接 + ORM/參數化 + grep gate(NFR-02) |
| R3 | API key 洩漏 | 高 | 中 | 雜湊儲存 + 常數時間比對 + 明文只印一次(FR-03) |
| R4 | 403 洩漏資源存在性 | 中 | 中 | 授權判定在資源查詢之前(FR-04 / §8 #6) |
| R5 | N+1 查詢在大表上崩潰 | 高 | 高 | 顯式預載 + SQL 計數斷言(NFR-01 / §8 #14) |
| R6 | 錯誤 body 洩漏內部結構 | 中 | 高 | RFC 7807 固定欄位 + detail 白名單(FR-10) |
| R7 | **`CancelledError` 被吞 → 關閉時卡死** | 中 | 中 | 明文禁令 + 測試斷言(NFR-03) |
| R8 | 任務 timeout 留下孤兒進程 | 中 | 中 | `kill()` + `await wait()`(FR-08 / §8 #25) |
| R9 | 部署後忘記跑 migration | 高 | 中 | `/readyz` fail closed(FR-09 / §8 #11) |
| R10 | 連線池耗盡 | 中 | 中 | `pool_pre_ping` + 併發上限(FR-06/08) |
| R11 | transitive 依賴引入不相容 license | 中 | 中 | lock 檔 + 全樹掃描(NFR-07) |
| R12 | rate bucket 競態導致超放行 | 低 | 中 | 單一交易 + row-level lock(FR-05) |

---

## 9. Glossary

| Term | Definition |
|------|------------|
| AGI | — (not used in canonical) |
| ASGI | Asynchronous Server Gateway Interface — Python's async successor to WSGI; FastAPI is an ASGI framework. |
| Alembic | The database migration tool for SQLAlchemy, invoked here for v1 → v2 → v3 schema evolution (FR-07). |
| Cursor pagination | A pagination strategy that returns an opaque `cursor` token (typically an encoded last-seen key) instead of a numeric offset; SPEC §3 FR-01 mandates this over offset pagination to avoid large-table scans. |
| Fail closed | A readiness behaviour where, in the presence of any unmet precondition, the endpoint reports unhealthy (503) rather than healthy — applied to `/readyz` per FR-09 / §8 #11. |
| `interrupted` | The status value that a long-running task carries when the service shutdown drain budget (`TASKQ_DRAIN_TIMEOUT`) is exceeded (FR-08). |
| N+1 | A query-pattern anti-pattern where a list of N parent rows triggers N additional child fetches; FR-06 / NFR-01 ban it for the list endpoint and assert a constant SQL-statement count via SQLAlchemy event listener. |
| Problem+json | The `application/problem+json` media type defined by RFC 7807; the project's error contract per FR-10. |
| Rate limit (token bucket) | Capacity `TASKQ_RATE_BURST`, refill `TASKQ_RATE_PER_SEC`; per-token bucket state lives in the `rate_buckets` table and is updated under a row-level lock (FR-05). |
| Reverse / downgrade | The Alembic operation that walks a migration back to its prior state; FR-07 requires every revision to have a working downgrade and specifically forbids `op.execute("DROP TABLE ...")` as a substitute. |
| Selectinload / joinedload | SQLAlchemy eager-loading strategies used to ban N+1 (FR-06). |
| SHA-256 + `hmac.compare_digest` | The hash-and-compare pair required for API key storage and authentication (FR-03, NFR-02). |
| SBOM | Software Bill of Materials; canonical NFR-07 requires `08-config/SBOM.json` with `name / version / license / direct|transitive`. |
| Scope (`read`/`write`/`admin`) | Hierarchical permission assigned per API key; FR-04. |
| TaskGroup | `asyncio.TaskGroup` — context-manager-style structured concurrency; FR-08. |
| ASGITransport | The in-process transport used by `httpx.AsyncClient` to drive FastAPI without a real socket; NFR-10. |

---

## 10. Environment Variables (canonical SPEC §5.1)

| Variable | Default | Purpose |
|----------|---------|---------|
| `TASKQ_DB_URL` | `sqlite:///./taskq.db` | 資料庫連線字串(**不得**出現在日誌 — NFR-04) |
| `TASKQ_DB_POOL_SIZE` | `5` | 連線池大小(FR-06) |
| `TASKQ_TASK_TIMEOUT` | `10.0` | 單任務 subprocess timeout(秒) |
| `TASKQ_MAX_CONCURRENT` | `8` | 背景執行併發上限(FR-08) |
| `TASKQ_DRAIN_TIMEOUT` | `30.0` | 關閉時 graceful drain 上限(秒) |
| `TASKQ_RATE_BURST` | `20` | 令牌桶容量(FR-05) |
| `TASKQ_RATE_PER_SEC` | `5.0` | 令牌補充速率(FR-05) |
| `TASKQ_CORS_ORIGINS` | (空字串) | CORS 允許來源,逗號分隔;空 = 全拒(NFR-02) |
| `TASKQ_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `TASKQ_LOG_FORMAT` | `json` | `json` / `text` |
| `TASKQ_HOST` | `127.0.0.1` | 監聽位址(預設**不**對外) |
| `TASKQ_PORT` | `8000` | 監聽埠 |

## 11. Database Schema (canonical SPEC §5.2, by Alembic revision)

| Table | Revision | Key columns |
|-------|----------|-------------|
| `tasks` | v1 | `id` (uuid), `command`, `name`, `status`, `created_at` (+ `result_json` until v3 removal) |
| `api_keys` | v1 | `id`, `key_hash` (sha256), `scope`, `created_at`, `revoked_at` |
| `rate_buckets` | v1 | `key_id` (FK), `tokens`, `updated_at` |
| `tags` | v2 | `id`, `label` |
| `task_tags` | v2 | `task_id`, `tag_id` (composite PK) |
| `task_results` | **v3** | `id`, `task_id` (FK), `exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`, `finished_at` |

`tasks.result_json` is created in v1 and removed in v3, its data migrated into `task_results`. That step is the focus of the FR-07 round-trip reversibility acceptance test.

## 12. Module Layout (canonical SPEC §6)

```
taskq-api/
├── 03-development/
│   ├── src/taskq_api/
│   │   ├── __init__.py
│   │   ├── __main__.py            # 管理入口(migrate / key create / healthcheck)
│   │   ├── app.py                 # FastAPI app 組裝
│   │   ├── config.py              # TASKQ_* env(independence)
│   │   ├── errors.py              # RFC 7807 problem+json(independence,FR-10)
│   │   ├── models/                # L1 — SQLAlchemy declarative + pydantic schema
│   │   │   ├── __init__.py
│   │   │   ├── orm.py             # 表定義(§5.2)
│   │   │   └── schemas.py         # request/response 模型
│   │   ├── repository/            # L2 — 唯一可 import sqlalchemy 的層(NFR-06)
│   │   │   ├── __init__.py
│   │   │   ├── session.py         # Session + 交易 context manager(FR-06)
│   │   │   ├── task_repo.py
│   │   │   ├── key_repo.py
│   │   │   └── rate_repo.py
│   │   ├── service/               # L3 — 業務邏輯,無 ORM 洩漏
│   │   │   ├── __init__.py
│   │   │   ├── tasks.py           # FR-01
│   │   │   ├── runner.py          # FR-02/08 async 執行器
│   │   │   ├── auth.py            # FR-03/04
│   │   │   └── ratelimit.py       # FR-05
│   │   └── api/                   # L4 最上層 — FastAPI 路由
│   │       ├── __init__.py
│   │       ├── deps.py            # 認證/授權 dependency(FR-04 單一判定點)
│   │       ├── tasks.py           # FR-01/02
│   │       └── health.py          # FR-09
│   └── tests/
│       ├── unit/
│       └── integration/           # httpx ASGITransport(NFR-10)
├── migrations/                    # Alembic(FR-07)
│   ├── env.py
│   └── versions/
│       ├── v1_initial.py
│       ├── v2_tags.py
│       └── v3_split_results.py    # 含資料搬遷 + 可逆 downgrade
├── alembic.ini
├── .importlinter                  # NFR-06
├── .env.example
├── requirements.txt / requirements.lock
├── Makefile                       # NFR-12
├── PROJECT_BRIEF.md
└── SPEC.md
```

Layering (enforced by `.importlinter`, NFR-06): `api > service > repository > models`; `config` / `errors` independent; `sqlalchemy` importable only from `repository/`.

## 13. Error Status Map (canonical SPEC §7)

| Status | Condition | `type` |
|--------|-----------|--------|
| 422 | request validation failed | `/errors/validation` |
| 401 | missing or invalid API key | `/errors/unauthenticated` |
| 403 | insufficient scope (leaks nothing) | `/errors/forbidden` |
| 404 | unknown task id | `/errors/not-found` |
| 409 | duplicate task name | `/errors/conflict` |
| 429 | rate limit exceeded (+ `Retry-After`) | `/errors/rate-limited` |
| 503 | DB down or migration behind head | `/errors/not-ready` |
| 任務 timeout | 200(任務狀態 `timeout`) | — |
| 500 | anything else (no stack/SQL/path in body) | `/errors/internal` |

`asyncio.CancelledError` is on none of these rows — it propagates (NFR-03).

---

## 14. Configuration Files Required (canonical SPEC §5.3)

| File | Purpose | Backing clause |
|------|---------|----------------|
| `.importlinter` | 分層契約 + `sqlalchemy` 禁令 | NFR-06 |
| `requirements.txt` + `requirements.lock` | 釘版 + transitive 鎖定 | NFR-07 |
| `requirements-dev.txt` | `import-linter` / `pip-licenses` / `mutmut` / `pytest-benchmark` / `httpx` | NFR-06/07/08/10 |
| `alembic.ini` + `migrations/versions/` | 三個 revision(FR-07) | FR-07 |
| `.env.example` | 全部 12 個 `TASKQ_*` 逐一宣告並附註解 | §5.1 / FR-09 / NFR-04 |
| `.methodology/harness_config.json` | `features.mutation_testing: true`;不得調降 `crg_cohesion_healthy` | NFR-08 |
| `Makefile` | `verify-system`(含 migration 往返) | NFR-12 |
| `08-config/SBOM.json` | SBOM (`name / version / license / direct|transitive`) | NFR-07 |

---

*End of SRS.md v1.0.0 — 100% transcription of canonical `SPEC.md` (v1.0.0, 2026-07-30) for the `taskq-api` progressive test-bed round 2 / 3.*

---

## 15. FR Block (machine-readable)

> **DERIVED: SPEC §6 module layout (lines 341–363) — `implementation_functions` field inference** — canonical SPEC §6 enumerates file paths (`tasks.py`, `runner.py`, `auth.py`, `deps.py`, `ratelimit.py`, `health.py`, `migrations/versions/v*.py`, `errors.py`) but does NOT enumerate the specific function/class names placed inside each file. The `implementation_functions` arrays below are inferred from those file paths following the convention `<module>.<verb_noun>` for service-handler functions and `<module>.<EndpointName>` for FastAPI route handlers. The FR-09 `metrics_endpoint` placement in `taskq_api.api.health` (rather than an unspecced `taskq_api.api.metrics.metrics_endpoint`) follows canonical §6, which lists only `health.py` under `api/` for FR-09's endpoints. The framework's Phase 2 SAB parser is expected to treat these entries as navigation hints keyed off the canonical file paths.

<!-- FR:START -->
```json
{
  "version": "1.0",
  "created_at": "2026-07-30",
  "phase": 1,
  "project": "taskq-api",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "任務資源 CRUD API: POST/GET/LIST/DELETE /v1/tasks, cursor pagination, 422/404",
      "implementation_functions": ["taskq_api.service.tasks.create_task", "taskq_api.service.tasks.get_task", "taskq_api.service.tasks.list_tasks", "taskq_api.service.tasks.delete_task", "taskq_api.api.tasks.routes"],
      "verification_method": "pytest 03-development/tests/integration/test_tasks_crud.py via httpx.ASGITransport; SPEC §8 #4, #5, #7, #8, #14"
    },
    {
      "id": "FR-02",
      "description": "任務執行端點: POST /v1/tasks/{id}/run → 202; async subprocess via asyncio.create_subprocess_exec(*shlex.split(command)); shell=True forbidden; result into task_results",
      "implementation_functions": ["taskq_api.service.runner.execute_task", "taskq_api.api.tasks.run_endpoint"],
      "verification_method": "pytest 03-development/tests/integration/test_task_run.py; SPEC §8 #25"
    },
    {
      "id": "FR-03",
      "description": "API Key 認證: X-API-Key header, SHA-256 hashed storage, hmac.compare_digest, revocation via revoked_at; plaintext printed once at key create",
      "implementation_functions": ["taskq_api.service.auth.verify_api_key", "taskq_api.repository.key_repo", "taskq_api.api.deps.authenticate"],
      "verification_method": "pytest 03-development/tests/integration/test_auth.py; SPEC §8 #5, #18, #20"
    },
    {
      "id": "FR-04",
      "description": "Scope 授權: read < write < admin, single dependency decision point in taskq_api/api/deps.py, 403 body 不得洩漏資源存在性",
      "implementation_functions": ["taskq_api.api.deps.require_scope"],
      "verification_method": "pytest 03-development/tests/integration/test_authz.py; SPEC §8 #6, #21"
    },
    {
      "id": "FR-05",
      "description": "流量控制: per-token token bucket (capacity TASKQ_RATE_BURST, refill TASKQ_RATE_PER_SEC), DB state, row-level lock; 429 + Retry-After",
      "implementation_functions": ["taskq_api.service.ratelimit.consume", "taskq_api.repository.rate_repo"],
      "verification_method": "pytest 03-development/tests/integration/test_ratelimit.py; SPEC §8 #9"
    },
    {
      "id": "FR-06",
      "description": "持久化層與交易邊界: repository layer is the only sqlalchemy importer; one Session per request via context manager; explicit commit/rollback; no string-concatenated SQL; explicit eager loading (selectinload / joinedload); pool_pre_ping=True",
      "implementation_functions": ["taskq_api.repository.session.transaction", "taskq_api.repository.task_repo", "taskq_api.repository.key_repo", "taskq_api.repository.rate_repo"],
      "verification_method": "pytest 03-development/tests/unit/test_repository_session.py; SPEC §8 #14, #17, #21"
    },
    {
      "id": "FR-07",
      "description": "Schema Migration: Alembic v1→v2→v3; v3 moves tasks.result_json into task_results with data migration; every step reversible; upgrade→sample write→downgrade -1→upgrade leaves every column byte-identical; destructive shortcuts forbidden",
      "implementation_functions": ["migrations/versions/v1_initial.py", "migrations/versions/v2_tags.py", "migrations/versions/v3_split_results.py"],
      "verification_method": "pytest 03-development/tests/integration/test_migration_roundtrip.py against real SQLite file; SPEC §8 #11, #12, #13, #27"
    },
    {
      "id": "FR-08",
      "description": "非同步執行器: asyncio.TaskGroup background runner; concurrency cap TASKQ_MAX_CONCURRENT; graceful drain up to TASKQ_DRAIN_TIMEOUT; timeout via asyncio.wait_for + process.kill() + await wait(); asyncio.CancelledError must propagate",
      "implementation_functions": ["taskq_api.service.runner.run_group", "taskq_api.service.runner.shutdown_drain"],
      "verification_method": "pytest 03-development/tests/unit/test_runner_drain.py, test_runner_orphan.py; SPEC §8 #25; SPEC §11 orphan row"
    },
    {
      "id": "FR-09",
      "description": "健康檢查與可觀測性: GET /healthz (no auth) returns 200; GET /readyz (no auth) returns 503 if DB unreachable OR alembic current != head; GET /v1/metrics (admin) returns task counts by status, execution latency percentiles, rate-limit rejections",
      "implementation_functions": ["taskq_api.api.health.healthz", "taskq_api.api.health.readyz", "taskq_api.api.health.metrics_endpoint"],
      "verification_method": "pytest 03-development/tests/integration/test_health.py; SPEC §8 #10, #11"
    },
    {
      "id": "FR-10",
      "description": "錯誤契約: all non-2xx responses Content-Type application/problem+json with RFC 7807 fields (type, title, status, detail, instance, correlation_id); detail must not leak stack/SQL/path; X-Correlation-Id echoed in response header and server log",
      "implementation_functions": ["taskq_api.errors.problem_json", "taskq_api.errors.exception_handler"],
      "verification_method": "pytest 03-development/tests/integration/test_error_contract.py; SPEC §8 #19"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "description": "效能與查詢效率: GET /v1/tasks/{id} p95 < 30ms (10k rows); GET /v1/tasks?limit=50 p95 < 80ms (10k rows); SQL statement count for list endpoint must be constant (no N+1)",
      "test_method": "pytest-benchmark in 03-development/tests/perf/test_query_perf.py with SQLAlchemy event-listener SQL count assertion; SPEC §8 #14, #15; SPEC §11 monitoring row"
    },
    {
      "id": "NFR-02",
      "type": "security",
      "description": "HTTP 與資料層安全: shell=True / eval( / exec( forbidden (grep 0 hits); no string-concatenated SQL; SHA-256 hashed API keys + hmac.compare_digest; 403 body leaks nothing about resource existence; error body leaks no stack/SQL/path; CORS deny-by-default with TASKQ_CORS_ORIGINS allowlist; bandit 0 HIGH / 0 MEDIUM",
      "test_method": "grep + bandit -r 03-development/src/ + integration tests for auth/authz/error-shape; SPEC §8 #6, #16, #17, #18, #19, #21, #23"
    },
    {
      "id": "NFR-03",
      "type": "reliability",
      "description": "錯誤處理、交易與非同步正確性: explicit transaction boundaries via context manager (commit / rollback); no bare except: / except Exception: pass; asyncio.CancelledError must propagate (not swallowed); DB failure → /readyz 503; task timeout must kill child process (no orphans); migration failure → transaction rollback",
      "test_method": "pytest 03-development/tests/unit/test_error_handling.py + ast-error-handling scanner; SPEC §8 #10; SPEC §11 monitoring row"
    },
    {
      "id": "NFR-04",
      "type": "security",
      "description": "敏感資料遮蔽: stdout_tail / stderr_tail / logs / error bodies redacted per regex (sk-[A-Za-z0-9_-]{8,}|token=\\S+|Bearer\\s+\\S+|postgres(ql)?://[^\\s]+); TASKQ_DB_URL password must not appear in any log, error, or /v1/metrics response; API key plaintext printed only at key create",
      "test_method": "pytest 03-development/tests/unit/test_redaction.py + secrets-scanning scanner; SPEC §8 #20"
    },
    {
      "id": "NFR-05",
      "type": "documentation",
      "description": "文件覆蓋: 100% public docstrings on functions/classes with [FR-XX] / [NFR-XX] tags; every API endpoint has summary + description in /openapi.json",
      "test_method": "ast-docstrings scanner + /openapi.json schema assertion; SPEC §10; SPEC §11"
    },
    {
      "id": "NFR-06",
      "type": "layering",
      "description": "架構分層契約: .importlinter must exist with layers contract api > service > repository > models + forbidden contract banning sqlalchemy import outside repository/; lint-imports exit 0; deletion of .importlinter / wildcard ignore_imports / contract downgrades forbidden",
      "test_method": "lint-imports in 03-development/tests/architecture/test_importlinter.py; SPEC §8 #21"
    },
    {
      "id": "NFR-07",
      "type": "licensing",
      "description": "依賴與授權合規: requirements.txt with == pinning; requirements.lock for transitives; allowlist MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF; full dependency-tree scan via pip-licenses --format=json --with-system; SBOM at 08-config/SBOM.json with name/version/license/direct|transitive",
      "test_method": "pip-licenses + SBOM.json existence assertion; SPEC §8 #22"
    },
    {
      "id": "NFR-08",
      "type": "mutation",
      "description": "變異測試: .methodology/harness_config.json sets features.mutation_testing: true; mutation score >= 70; scope limited to service/ + repository/ with rationale in harness_config.json",
      "test_method": "mutmut run && mutmut results; SPEC §8 #24"
    },
    {
      "id": "NFR-09",
      "type": "testability",
      "description": "驗證真實性(零 skip 鐵律): no pytest.skip / skipif / xfail / assertion-free stub; pytest skipped count == 0; every test function has at least one assert; anti-fabrication (no --ignore / -k / --deselect / collect_ignore / testpaths removal); FR-07 migration tested against real SQLite file (not in-memory mock)",
      "test_method": "pytest 03-development/tests -q with skip-count assertion + per-test assert-count assertion; SPEC §8 #1, #12"
    },
    {
      "id": "NFR-10",
      "type": "integration",
      "description": "整合覆蓋: 03-development/tests/integration/ line coverage >= 80%; httpx.AsyncClient(transport=ASGITransport(app)) (no direct handler calls); covers CRUD full chain + every error code (401/403/404/409/422/429/503) + migration round-trip + rate-limit trigger + recovery + graceful drain",
      "test_method": "pytest 03-development/tests/integration --cov=03-development/src --cov-report=term; SPEC §8 #3"
    },
    {
      "id": "NFR-11",
      "type": "maintainability",
      "description": "可讀性: project MI (LLOC-weighted) >= 80; per-function CC <= 10; per-file <= 400 lines; per-directory <= 15 files; per-API-handler <= 40 lines (business logic must sink into service/)",
      "test_method": "radon mi / radon cc + per-file and per-handler line-count assertions; SPEC §11 monitoring row"
    },
    {
      "id": "NFR-12",
      "type": "verifiability",
      "description": "系統驗證目標: Makefile verify-system target chains (1) alembic upgrade head → (2) full test suite → (3) service start + /healthz + /readyz smoke → (4) alembic downgrade base then upgrade head (round-trip); make verify-system must exit 0 with verify-system: PASS on stdout",
      "test_method": "make verify-system exit-code assertion + stdout substring match; SPEC §8 #27"
    }
  ]
}
```
<!-- FR:END -->
