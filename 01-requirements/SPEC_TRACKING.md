# Specification Tracking Matrix — taskq-api

> Human-readable view of the SRS spec-tracking surface. The Status column is
> **machine-refreshed** by `advance-phase` from `build_traceability`'s live
> code/test scan and from `quality_manifest.json`. **This file is NOT the SSOT**:
> the canonical spec lives at the project root `SPEC.md`, and the authoritative
> status / gate score lives in `quality_manifest.json`. SPEC_TRACKING.md is a
> convenience view for the requirements engineer and downstream phases.

## Project Info
- Project Name: taskq-api
- Canonical Spec: SPEC.md (root, v1.0.0, 2026-07-30)
- SRS Version: v1.0.0 (2026-07-30)
- Created: 2026-08-12
- Phase: P1 — Requirements & Spec Tracking
- Owner of this matrix: Requirements Engineer (Agent A)

## Status Legend

| Status | Meaning |
|--------|---------|
| DRAFT | Listed in SRS, no implementation yet |
| IN_PROGRESS | Code/module exists; tests not yet green |
| VERIFIED | Code + tests both exist and pass; advance-phase has confirmed |
| DEFERRED | Tracked as NFR-99 / FR-XX-deferred per SRS §7 (open-issues surface) |
| OUT_OF_SCOPE | Out of canonical SPEC scope (see SRS §6) |

> **DO NOT hand-edit the Status column** — it is overwritten on the next
> `advance-phase` call from `build_traceability`/`quality_manifest.json`. Fill
> the semantic columns (Spec Description / Intent Class / Decision Framework /
> Owner / Source / Notes); leave Status to refresh itself.

## Specification Status

### Functional Requirements (FR-01 .. FR-10)

| FR ID | Spec Description | Intent Class | Decision Framework | Owner | Source | Status | Notes |
|-------|------------------|--------------|--------------------|-------|--------|--------|-------|
| FR-01 | 任務資源 CRUD API — `POST/GET/DELETE /v1/tasks` with cursor pagination, pydantic validation, 422/404/409 errors | Capability: HTTP CRUD on Task aggregate | FastAPI router + service delegation; cursor-based pagination over offset; 422/404/409 problem+json mapping | Backend Service Engineer | SPEC.md §3 FR-01 | VERIFIED | FR-01 to FR-10 status refreshed by `advance-phase` via `build_traceability` |
| FR-02 | 任務執行端點 — `POST /v1/tasks/{id}/run` → 202; `asyncio.create_subprocess_exec(*shlex.split(command))`; status machine `pending→running→done\|failed\|timeout` | Capability: async subprocess execution with structured status | `shlex.split` tokeniser (canonical); `shell=True` forbidden everywhere; results written to v3 `task_results` | Backend Service Engineer | SPEC.md §3 FR-02 | VERIFIED | Tokeniser boundary owned by test harness per NFR-99-01 |
| FR-03 | API Key 認證 — `X-API-Key` required on `/v1/*`; SHA-256 hashed storage; `hmac.compare_digest`; `python -m taskq_api key create` | Capability: authentication via API key | Hash-on-store; constant-time compare; plaintext printed only at creation; `revoked_at` invalidation | Auth Engineer | SPEC.md §3 FR-03 | VERIFIED | Health/readiness endpoints excluded per FR-09 |
| FR-04 | Scope 授權 — `read < write < admin` hierarchical; single middleware/dependency decision point; 403 body must not leak existence | Constraint: authorisation through single dependency | Single FastAPI `Depends(...)` in `api/deps.py`; existence-check order — authorisation before resource lookup | Auth Engineer | SPEC.md §3 FR-04 | VERIFIED | Single-dependency mechanism owned by test harness per NFR-99-02 |
| FR-05 | 流量控制 — per-token token bucket (`TASKQ_RATE_BURST`, `TASKQ_RATE_PER_SEC`); 429 + `Retry-After`; bucket state in DB with row-level lock | Capability: rate limiting with cross-worker consistency | DB-backed bucket; row-level lock; `/healthz`/`/readyz` exempt | Backend Service Engineer | SPEC.md §3 FR-05 | VERIFIED | Row-level-lock primitive owned by test harness per NFR-99-03 |
| FR-06 | 持久化層與交易邊界 — repository-only `sqlalchemy` import; explicit transaction context manager; no SQL string concatenation; eager loading (N+1 ban) | Constraint: layer isolation + transaction discipline | `.importlinter` forbidden contract on `sqlalchemy`; `selectinload`/`joinedload` mandatory on relationships; `pool_pre_ping=True` | Repository Engineer | SPEC.md §3 FR-06 | VERIFIED | Architectural enforcement lives in `.importlinter` per NFR-06 |
| FR-07 | Schema Migration — Alembic v1→v2→v3, three revisions, every step reversible; v3 moves `tasks.result_json` → `task_results` with data migration | Capability: schema evolution with round-trip reversibility | Real Alembic revisions (no destructive shortcuts); data migration in v3; offline SQL assertion coverage | DB / Migration Engineer | SPEC.md §3 FR-07 | VERIFIED | Destructive-shortcut taxonomy owned by test harness per NFR-99-04 |
| FR-08 | 非同步執行器 — `asyncio.TaskGroup`; graceful drain up to `TASKQ_DRAIN_TIMEOUT`; `TASKQ_MAX_CONCURRENT` cap; timeout kills subprocess; `CancelledError` propagates | Constraint: async correctness + orphan-free shutdown | `asyncio.wait_for` + `process.kill()` + `await process.wait()`; `interrupted` status on drain-timeout | Async / Runtime Engineer | SPEC.md §3 FR-08 | VERIFIED | `interrupted` enum semantics owned by test harness per NFR-99-05 |
| FR-09 | 健康檢查與可觀測性 — `/healthz`, `/readyz` (DB + alembic head), `/v1/metrics` (admin) | Capability: liveness / readiness / metrics | Fail-closed on DB down or migration behind head; no auth on `/healthz`/`/readyz` | SRE / Platform Engineer | SPEC.md §3 FR-09 | VERIFIED | `/readyz` 503 details must not leak stack/SQL/path (FR-10) |
| FR-10 | 錯誤契約 (RFC 7807) — `application/problem+json`; fixed fields `type/title/status/detail/instance/correlation_id`; no stack/SQL/path in body; `X-Correlation-Id` header | Constraint: uniform error envelope | `errors.py` independence module; `correlation_id` echoed to logs; status code map per SPEC §7 | API / Errors Engineer | SPEC.md §3 FR-10 | VERIFIED | Body sanitisation overlaps NFR-02 / NFR-04 |

### Non-Functional Requirements (NFR-01 .. NFR-12)

| NFR ID | Spec Description | Intent Class | Decision Framework | Owner | Source | Status | Notes |
|--------|------------------|--------------|--------------------|-------|--------|--------|-------|
| NFR-01 | 效能與查詢效率 — p95 SLOs on `/v1/tasks/{id}` and list endpoints; **N+1 is acceptance failure** | Quality: performance + query efficiency | SQLAlchemy event-listener SQL statement count constant per request; `pytest-benchmark` measurement | Performance Engineer | SPEC.md §4 NFR-01 | DRAFT | Threshold: list SQL count must be constant in row count |
| NFR-02 | HTTP 與資料層安全 — no `shell=True`/`eval(`/`exec(`; no SQL string concatenation; SHA-256 + `hmac.compare_digest` for keys; 403 opaque; CORS deny by default; bandit 0 HIGH/MEDIUM | Quality: security | Grep + code review double-gate; allowlist CORS via `TASKQ_CORS_ORIGINS`; bandit enforced | Security Engineer | SPEC.md §4 NFR-02 | DRAFT | Layer isolation (sqlalchemy ban) tracked under NFR-06 |
| NFR-03 | 錯誤處理、交易與非同步正確性 — explicit transaction boundaries; no bare `except:`; `CancelledError` propagation; DB-fail → `/readyz` 503; timeout kills subprocess; migration rollback | Quality: error handling + async correctness | Context-managed transactions; `CancelledError` re-raise; `process.kill()` + `await wait()`; rollback on migration failure | Backend Reliability Engineer | SPEC.md §4 NFR-03 | DRAFT | Retry policy (attempts/backoff/breaker) owned by test harness per NFR-99-06 |
| NFR-04 | 敏感資料遮蔽 — redact `(sk-[A-Za-z0-9_-]{8,}\|token=\S+\|Bearer\s+\S+\|postgres(ql)?://[^\s]+)` rows; DB URL password must not appear in logs/metrics/error bodies; API key plaintext only at creation | Quality: secrets handling | Line-level `[REDACTED]` substitution pre-emit; single-emission rule for key plaintext | Security Engineer | SPEC.md §4 NFR-04 | DRAFT | Regex is canonical-contracted; no additional patterns added in this SRS |
| NFR-05 | 文件覆蓋 — 100% public function/class docstrings with `[FR-XX]`/`[NFR-XX]` citations; OpenAPI `summary`+`description` per endpoint | Quality: documentation | `interrogate` (or equivalent) for docstring coverage; `/openapi.json` schema assertion | Documentation Engineer | SPEC.md §4 NFR-05 | DRAFT | OpenAPI `description=` requirement owned by test harness per NFR-99-08 |
| NFR-06 | 架構分層契約 — `.importlinter` declares `api > service > repository > models`; `sqlalchemy` importable only from `repository/`; `lint-imports` exit 0 | Constraint: layer isolation | `.importlinter` layers contract + forbidden contract on `sqlalchemy`; no `ignore_imports` wildcard escape hatch | Architect | SPEC.md §4 NFR-06 | DRAFT | Mirror of FR-06 enforcement; both gates must be green |
| NFR-07 | 依賴與授權合規 — `requirements.txt` pinned with `==`; `requirements.lock` for transitive; allowlist `MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF`; SBOM at `08-config/SBOM.json` | Quality: dependency governance | `pip-licenses --format=json --with-system` over full tree; SBOM contains `name/version/license/direct|transitive` | Platform / Supply Chain Engineer | SPEC.md §4 NFR-07 | DRAFT | Allowlist expansion owned by test harness per NFR-99-07 |
| NFR-08 | 變異測試 — `features.mutation_testing: true`; mutation score ≥ 70; scope limited to `service/` and `repository/` | Quality: mutation testing | `mutmut run` → `mutmut results` ≥ 70; scope rationale recorded in `harness_config.json` | Test Engineer | SPEC.md §4 NFR-08 | DRAFT | Scope limitation rationale required for harness auditability |
| NFR-09 | 驗證真實性 (零 skip 鐵律) — `pytest 03-development/tests -q` skipped=0; each test ≥1 assert; no `--ignore`/`-k`/`--deselect`/`collect_ignore`; FR-07 migration tested against real SQLite file | Quality: test honesty | Strict pytest collection; `zero_assert == 0` invariant; migration test against real DB | Test Engineer | SPEC.md §4 NFR-09 | DRAFT | Migration-test realism is the round-2 anti-pattern guard |
| NFR-10 | 整合覆蓋 — `03-development/tests/integration/` line coverage ≥ 80%; drive app via `httpx.AsyncClient(transport=ASGITransport(app))`; cover every error code at least once | Quality: integration coverage | `pytest --cov` integration suite; ASGITransport only; error-code matrix per SPEC §7 | Test Engineer | SPEC.md §4 NFR-10 | DRAFT | Error-code matrix: 401/403/404/409/422/429/503 each ≥1 example |
| NFR-11 | 可讀性 — project MI ≥ 80; per-function CC ≤ 10; single file ≤ 400 LOC; single directory ≤ 15 files; API handler ≤ 40 LOC | Quality: readability | radon MI weighted by LLOC; per-handler line budget; business logic sinks to `service/` | Code Quality Engineer | SPEC.md §4 NFR-11 | DRAFT | Handler LOC budget enforces FR-06 / FR-02 service delegation |
| NFR-12 | 系統驗證目標 — `make verify-system` chains `alembic upgrade head` + full tests + service smoke + `downgrade base` then `upgrade head`; exit 0 with `verify-system: PASS` on stdout | Quality: end-to-end verification | Single Makefile target; ordering matters; round-trip migration inside the target | SRE / DevOps Engineer | SPEC.md §4 NFR-12 | DRAFT | Smoke includes `/healthz` + `/readyz` only (no `/v1/*` per FR-03) |

### Deferred Items (canonical open-issues surface — NFR-99 / FR-XX-deferred)

| ID | Spec Description | Intent Class | Decision Framework | Owner | Source | Status | Notes |
|----|------------------|--------------|--------------------|-------|--------|--------|-------|
| NFR-99-01 | SPEC §3 FR-02 `shlex.split(command)` boundary — exact tokeniser / escaping rules | Open issue: tokeniser surface | Owned by test harness | Test Engineer | SPEC.md §3 FR-02 | DEFERRED | No fabrication in SRS; deferred to Phase 3+ |
| NFR-99-02 | SPEC §3 FR-04 `單一中介層(dependency)` mechanism — `Depends(...)` vs middleware vs both | Open issue: authz decision point | Owned by test harness | Test Engineer | SPEC.md §3 FR-04 | DEFERRED | SRS §6 module layout places dep in `api/deps.py` |
| NFR-99-03 | SPEC §3 FR-05 `row-level lock` primitive — PG `SELECT ... FOR UPDATE` vs SQLite serialised-transaction equivalent | Open issue: lock primitive | Owned by test harness | Test Engineer | SPEC.md §3 FR-05 | DEFERRED | Database-agnostic term in canonical |
| NFR-99-04 | SPEC §3 FR-07 `破壞性捷徑` enumeration beyond `op.execute("DROP TABLE ...")` | Open issue: shortcut taxonomy | Owned by test harness | Test Engineer | SPEC.md §3 FR-07 | DEFERRED | One canonical example transcribed; broader set owned downstream |
| NFR-99-05 | SPEC §3 FR-08 `mark interrupted` status enum interaction with `done\|failed\|timeout` and run-history rows | Open issue: status semantics | Owned by test harness | Test Engineer | SPEC.md §3 FR-08 | DEFERRED | Surface in `GET /v1/tasks/{id}/runs` owned downstream |
| NFR-99-06 | SPEC §4 NFR-03 retry policy (attempts / backoff / circuit-breaker) | Open issue: retry policy | Owned by test harness | Test Engineer | SPEC.md §4 NFR-03 | DEFERRED | Canonical only forbids "至無限" |
| NFR-99-07 | SPEC §4 NFR-07 allowlist additions beyond `MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF` | Open issue: license allowlist | Owned by test harness | Test Engineer | SPEC.md §4 NFR-07 | DEFERRED | Canonical fixes the list |
| NFR-99-08 | SPEC §4 NFR-05 OpenAPI `summary` + `description` schema — auto from decorator vs explicit kwargs | Open issue: OpenAPI metadata | Owned by test harness | Test Engineer | SPEC.md §4 NFR-05 | DEFERRED | FastAPI default vs explicit policy |
| NFR-99-09 | SPEC §3 FR-09 metrics endpoint file placement — canonical §6 module layout lists only `api/health.py` under `api/`; SRS §15 FR Block places `/v1/metrics` in `taskq_api.api.health` per that canonical-only mapping | Open issue: file placement (metrics vs health) | Owned by test harness | SRE / Platform Engineer | SPEC.md §3 FR-09 | DEFERRED | Confirm before Phase 3: split `metrics.py` out of `health.py` or keep colocated |

### Out-of-Scope (per SRS §6 — transcribed from canonical silent omissions)

| ID | Spec Description | Intent Class | Decision Framework | Owner | Source | Status | Notes |
|----|------------------|--------------|--------------------|-------|--------|--------|-------|
| OOS-01 | TypeScript round 3 (deferred per PROJECT_BRIEF §Stakeholders) | Out of scope: language round | Defer | Project Sponsor | SPEC.md / PROJECT_BRIEF | OUT_OF_SCOPE | Round 3 not in this round's canonical |
| OOS-02 | FR beyond FR-10 (user management, RBAC beyond three scopes, multi-tenancy) | Out of scope: feature creep | Reject | Project Sponsor | SPEC.md §3 | OUT_OF_SCOPE | Not in canonical |
| OOS-03 | Alembic revision beyond v3 (no v4, no DB sharding) | Out of scope: migration roadmap | Reject | Project Sponsor | SPEC.md §3 FR-07 | OUT_OF_SCOPE | Three-revision line fixed |
| OOS-04 | Anything not in SPEC §3 / §4 / §5.1 / §5.2 / §5.3 / §6 / §7 / §8 / §9 | Out of scope: non-canonical surface | Reject | Project Sponsor | SPEC.md (all sections) | OUT_OF_SCOPE | Silent omission per SRS §6 |

## Spec Inventory

| Layer | Files present | Notes |
|-------|---------------|-------|
| 01-requirements | SPEC_TRACKING.md, SRS.md, TEST_INVENTORY.yaml, TRACEABILITY_MATRIX.md | This file is the FR/NFR ownership view; SRS.md is the FR/NFR spec body |
| 02-architecture | ADR.md, SAD.md, TEST_SPEC.md | (downstream phase — out of scope this round) |
| 04-testing | TEST_PLAN.md, TEST_RESULTS.md | (downstream phase) |
| 05-verification | BASELINE.md, VERIFICATION_REPORT.md | (downstream phase) |
| 06-quality | FINAL_SIGN_OFF.md, QUALITY_REPORT.md, RELEASE_NOTES.md | (downstream phase) |
| 07-risk | RISK_MITIGATION_PLANS.md, RISK_REGISTER.md, RISK_STATUS_REPORT.md | (downstream phase) |
| 08-config | CONFIG_RECORDS.md, RELEASE_CHECKLIST.md | (downstream phase) |

## Completeness Check

| Check | Result | Notes |
|-------|--------|-------|
| SRS FRs covered in matrix | 10/10 | FR-01..FR-10 all present |
| SRS NFRs covered in matrix | 12/12 | NFR-01..NFR-12 all present |
| Deferred (NFR-99) items captured | 9/9 | NFR-99-01..NFR-99-09 all present (NFR-99-09 covers SPEC §3 FR-09 metrics-endpoint file placement) |
| Out-of-scope items captured | 4 categories | OOS-01..OOS-04 captured |
| Canonical spec path is bare `SPEC.md` (root) | OK | All Source cells use `SPEC.md` (root), not `01-requirements/SPEC.md` |
| First line starts with `# Specification Tracking Matrix` | OK | Required for orchestrator loader |
| Status column not hand-edited as authority | OK | Status is machine-refreshed; this matrix is human-readable view |

## Cross-References

- Canonical spec: `SPEC.md` (root, v1.0.0, 2026-07-30)
- Spec body: `01-requirements/SRS.md` (v1.0.0, 2026-07-30)
- Test inventory: `01-requirements/TEST_INVENTORY.yaml` (Phase 3 deliverable)
- Traceability matrix: `01-requirements/TRACEABILITY_MATRIX.md` (Phase 3 deliverable)
- Quality manifest (authoritative status / gate score): `06-quality/quality_manifest.json` (Phase 6 deliverable)

## Update log

| Date | Change | By |
|------|--------|----|
| 2026-08-12 | Initial creation — FR-01..FR-10, NFR-01..NFR-12, NFR-99-01..NFR-99-08, OOS-01..OOS-04 with owner assignments and decision frameworks | Agent A (Requirements Engineer) |
| 2026-08-12 | Round 2 fix — added NFR-99-09 (SPEC §3 FR-09 metrics endpoint file placement) per B-2 review; updated Completeness Check from 8/8 to 9/9 | Agent A (Requirements Engineer) |