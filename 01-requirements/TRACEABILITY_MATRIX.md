# Traceability Matrix — taskq-api

> Requirements Traceability Matrix — bidirectional FR ↔ SRS ↔ Code ↔ Test linkage
> Framework: harness-methodology (Phase 1 deliverable)
> Version: v1.1 (2026-08-12)
> SSOT: `SPEC.md` (root, v1.0.0) — transcribed into `01-requirements/SRS.md`
> Owner: Requirements Engineer (Agent A)
> Round: 2 (B-2 review fixes applied)

---

## 1. Overview

This matrix provides complete **bidirectional traceability** supporting ASPICE SWE.3/SYS.4 compliance:

- **Forward**: each FR/NFR in `SRS.md` is linked to its SRS section, planned module (`SPEC §6`), and expected test function (`TEST_INVENTORY.yaml`).
- **Backward**: every test function in `TEST_INVENTORY.yaml` and every module in `SPEC §6` traces back to one or more FR/NFR.

Coverage is reported in §5 (`Completeness Verification`). At Phase 1 the code/test columns reflect the **planned** state derived from canonical `SPEC §6 module layout` and the P1 test inventory; downstream phases (P2 architecture, P3 development, P4 testing) refresh the actual file paths / line numbers via `advance-phase`.

---

## 2. FR ↔ Spec Mapping

| FR ID | Functional Requirement (canonical source: SPEC §3) | SRS Section | Priority | Test Inventory ID | Status |
|-------|---------------------------------------------------|-------------|----------|-------------------|--------|
| FR-01 | Task CRUD API — `POST/GET/DELETE /v1/tasks`; cursor pagination; pydantic validation; 422/404/409 errors | SRS §3 FR-01 | HIGH | `fr_tests.FR-01` block (1 unit + 6 integration) | DRAFT |
| FR-02 | Task run endpoint — `POST /v1/tasks/{id}/run` → 202; `asyncio.create_subprocess_exec(*shlex.split(command))`; status `pending→running→done\|failed\|timeout` | SRS §3 FR-02 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| FR-03 | API-Key 認證 — `X-API-Key` on `/v1/*`; SHA-256 hash storage; `hmac.compare_digest`; `python -m taskq_api key create` | SRS §3 FR-03 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| FR-04 | Scope 授權 — `read < write < admin` hierarchical; single FastAPI `Depends(...)`; 403 body must not leak existence | SRS §3 FR-04 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| FR-05 | Rate limit — per-token token bucket (`TASKQ_RATE_BURST`, `TASKQ_RATE_PER_SEC`); 429 + `Retry-After`; bucket state in DB with row-level lock | SRS §3 FR-05 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| FR-06 | 持久化層與交易邊界 — repository-only `sqlalchemy` import; explicit transaction context manager; no SQL string concatenation; eager loading (N+1 ban) | SRS §3 FR-06 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| FR-07 | Schema Migration — Alembic v1→v2→v3; three revisions, every step reversible; v3 moves `tasks.result_json` → `task_results` with data migration | SRS §3 FR-07 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| FR-08 | 非同步執行器 — `asyncio.TaskGroup`; graceful drain up to `TASKQ_DRAIN_TIMEOUT`; `TASKQ_MAX_CONCURRENT` cap; timeout kills subprocess; `CancelledError` propagates | SRS §3 FR-08 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| FR-09 | 健康檢查與可觀測性 — `/healthz`, `/readyz` (DB + alembic head), `/v1/metrics` (admin) | SRS §3 FR-09 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| FR-10 | 錯誤契約 (RFC 7807) — `application/problem+json`; fixed fields `type/title/status/detail/instance/correlation_id`; no stack/SQL/path in body; `X-Correlation-Id` header | SRS §3 FR-10 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-01 | 效能與查詢效率 — p95 SLOs on `/v1/tasks/{id}` and list endpoints; **N+1 is acceptance failure** | SRS §4 NFR-01 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-02 | HTTP 與資料層安全 — no `shell=True`/`eval(`/`exec(`; no SQL string concatenation; SHA-256 + `hmac.compare_digest`; 403 opaque; CORS deny by default; bandit 0 HIGH/MEDIUM | SRS §4 NFR-02 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-03 | 錯誤處理、交易與非同步正確性 — explicit transaction boundaries; no bare `except:`; `CancelledError` propagation; DB-fail → `/readyz` 503; timeout kills subprocess; migration rollback | SRS §4 NFR-03 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-04 | 敏感資料遮蔽 — redact `(sk-[A-Za-z0-9_-]{8,}\|token=\S+\|Bearer\s+\S+\|postgres(ql)?://[^\s]+)` rows; DB URL password must not appear in logs/metrics/error bodies; API key plaintext only at creation | SRS §4 NFR-04 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-05 | 文件覆蓋 — 100% public function/class docstrings with `[FR-XX]`/`[NFR-XX]` citations; OpenAPI `summary`+`description` per endpoint | SRS §4 NFR-05 | MEDIUM | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-06 | 架構分層契約 — `.importlinter` declares `api > service > repository > models`; `sqlalchemy` importable only from `repository/`; `lint-imports` exit 0 | SRS §4 NFR-06 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-07 | 依賴與授權合規 — `requirements.txt` pinned with `==`; `requirements.lock` for transitive; allowlist `MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF`; SBOM at `08-config/SBOM.json` | SRS §4 NFR-07 | MEDIUM | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-08 | 變異測試 — `features.mutation_testing: true`; mutation score ≥ 70; scope limited to `service/` and `repository/` | SRS §4 NFR-08 | MEDIUM | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-09 | 驗證真實性 (零 skip 鐵律) — `pytest 03-development/tests -q` skipped=0; each test ≥1 assert; no `--ignore`/`-k`/`--deselect`/`collect_ignore`; FR-07 migration tested against real SQLite file | SRS §4 NFR-09 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-10 | 整合覆蓋 — `03-development/tests/integration/` line coverage ≥ 80%; drive app via `httpx.AsyncClient(transport=ASGITransport(app))`; cover every error code at least once | SRS §4 NFR-10 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-11 | 可讀性 — project MI ≥ 80; per-function CC ≤ 10; single file ≤ 400 LOC; single directory ≤ 15 files; API handler ≤ 40 LOC | SRS §4 NFR-11 | MEDIUM | derived in P3 (P1 inventory entry pending) | DRAFT |
| NFR-12 | 系統驗證目標 — `make verify-system` chains `alembic upgrade head` + full tests + service smoke + `downgrade base` then `upgrade head`; exit 0 with `verify-system: PASS` on stdout | SRS §4 NFR-12 | HIGH | derived in P3 (P1 inventory entry pending) | DRAFT |

> **Notes**:
> - `Test Inventory ID` is the authoritative name in `TEST_INVENTORY.yaml` (P1 deliverable, repo root). FR-01 is the only FR with a P1-named entry (`test_fr01_example_unit`, `test_fr01_example_integration`); FR-02..FR-10 and all NFRs use names derived in P3 via the 7-Question Protocol — that gap is the contract that `derive_test_cases.md` fills (P2).
> - `Priority` is transcribed from `SPEC_TRACKING.md` §Specification Status (HIGH unless the SRS marks the item as cross-cutting/non-functional soft-target, e.g. NFR-05/07/08/11 which are MEDIUM).

---

## 3. Spec ↔ Code Mapping (planned — SPEC §6 module layout)

> At Phase 1 the `Code File` column is the **planned module path** from canonical `SPEC §6`. P3 (`03-development/src/taskq_api/`) refreshes the actual file/function paths.

| SRS Section | Planned Module (SPEC §6) | Function / Class | Lines | Status |
|-------------|--------------------------|------------------|-------|--------|
| §3 FR-01 | `03-development/src/taskq_api/api/tasks.py` | `create_task`, `get_task`, `list_tasks`, `delete_task` (handlers) → `service/tasks.py` | (P3) | DRAFT |
| §3 FR-02 | `03-development/src/taskq_api/service/runner.py` + `api/tasks.py` | `TaskRunner.run(command)` using `asyncio.create_subprocess_exec(*shlex.split(command))` | (P3) | DRAFT |
| §3 FR-03 | `03-development/src/taskq_api/service/auth.py` + `repository/key_repo.py` + `__main__.py` | `verify_api_key` (constant-time compare), `KeyRepo.create(scope)`, CLI `key create` | (P3) | DRAFT |
| §3 FR-04 | `03-development/src/taskq_api/api/deps.py` + `service/auth.py` | single `Depends(require_scope(...))` — authz before resource lookup | (P3) | DRAFT |
| §3 FR-05 | `03-development/src/taskq_api/service/ratelimit.py` + `repository/rate_repo.py` | `RateLimiter.consume(key_id)` — row-level lock on `rate_buckets` | (P3) | DRAFT |
| §3 FR-06 | `03-development/src/taskq_api/repository/session.py` + `repository/task_repo.py` | `transaction()` context manager; `selectinload`/`joinedload` on relationships | (P3) | DRAFT |
| §3 FR-07 | `migrations/versions/v1_initial.py`, `v2_tags.py`, `v3_split_results.py` | three revisions; v3 has data-migration `upgrade()` and reverse `downgrade()` | (P3) | DRAFT |
| §3 FR-08 | `03-development/src/taskq_api/service/runner.py` + `app.py` | `TaskGroup`; `asyncio.wait_for` + `process.kill()` + `await process.wait()`; `interrupted` status on drain-timeout | (P3) | DRAFT |
| §3 FR-09 | `03-development/src/taskq_api/api/health.py` | `healthz()`, `readyz()` (DB + `alembic current`); `/v1/metrics` admin endpoint | (P3) | DRAFT |
| §3 FR-10 | `03-development/src/taskq_api/errors.py` (independence) + `app.py` | `ProblemJSON` factory; `X-Correlation-Id` middleware; status map per SRS §13 | (P3) | DRAFT |
| §4 NFR-01 | `03-development/src/taskq_api/repository/task_repo.py` + `tests/integration/test_nfr01_*` | `selectinload(Task.results)` + SQLAlchemy event-listener count assertion | (P3) | DRAFT |
| §4 NFR-02 | `.importlinter` + `bandit` config + `service/*` | forbidden-imports contract + grep gates | (P3) | DRAFT |
| §4 NFR-03 | `03-development/src/taskq_api/repository/session.py` + `service/runner.py` | context-managed transactions; explicit `CancelledError` re-raise; `kill()`+`wait()` | (P3) | DRAFT |
| §4 NFR-04 | `03-development/src/taskq_api/errors.py` + logging filter | `REDACT_RE` regex substitution pre-emit | (P3) | DRAFT |
| §4 NFR-05 | every public function in `03-development/src/taskq_api/` | `[FR-XX]`/`[NFR-XX]` docstring tags; OpenAPI `summary=`+`description=` | (P3) | DRAFT |
| §4 NFR-06 | `.importlinter` (root) | layers contract `api > service > repository > models` + forbidden contract on `sqlalchemy` | (P3) | DRAFT |
| §4 NFR-07 | `requirements.txt` + `requirements.lock` + `08-config/SBOM.json` | `pip-licenses --format=json --with-system`; SBOM script | (P3) | DRAFT |
| §4 NFR-08 | `.methodology/harness_config.json` + `mutmut` config | `features.mutation_testing: true`; scope = `service/`,`repository/` | (P3) | DRAFT |
| §4 NFR-09 | `03-development/tests/` (whole) | strict collection; each test ≥1 assert; real-DB migration test | (P3) | DRAFT |
| §4 NFR-10 | `03-development/tests/integration/` (whole) | `httpx.AsyncClient(transport=ASGITransport(app))`; 401/403/404/409/422/429/503 each ≥1 | (P3) | DRAFT |
| §4 NFR-11 | whole repo | radon MI ≥ 80; per-handler ≤ 40 LOC | (P3) | DRAFT |
| §4 NFR-12 | `Makefile` (root) | `verify-system` target chaining upgrade → tests → smoke → round-trip | (P3) | DRAFT |

> **FR-09 / NFR-99-09 deferred decision**: the FR-09 row above colocates `/healthz`, `/readyz`, and `/v1/metrics` in `api/health.py` per canonical SPEC §6. SRS §7 (NFR-99-09, line 504) records the open issue: "Any later stake-holder decision to split `metrics.py` out of `health.py` is to be confirmed before Phase 3 implementation." Resolve before P3 starts; if split is chosen, update FR-09 row + SPEC §6 + SRS §15 together.

---

## 4. Code ↔ Test Mapping (planned — P3 fills actual paths)

> At Phase 1 the `Code File` and `Test File` columns reflect the planned P3 layout per `SPEC §6`. P3 produces actual files; P4 fills this table with concrete line numbers.

| Code File (planned, SPEC §6) | Test File (planned) | FR/NFR Coverage | Status |
|------------------------------|---------------------|-----------------|--------|
| `03-development/src/taskq_api/api/tasks.py` | `03-development/tests/unit/test_fr01_example_unit.py` + `03-development/tests/integration/test_fr01_example_integration.py` | FR-01 (P1 inventory entry) | DRAFT |
| `03-development/src/taskq_api/service/runner.py` | `03-development/tests/unit/test_runner_timeout.py` + `03-development/tests/integration/test_graceful_drain.py` | FR-02, FR-08 | DRAFT |
| `03-development/src/taskq_api/service/auth.py` + `repository/key_repo.py` | `03-development/tests/unit/test_auth_key.py` + `03-development/tests/integration/test_auth_401.py` | FR-03, NFR-02, NFR-04 | DRAFT |
| `03-development/src/taskq_api/api/deps.py` | `03-development/tests/integration/test_scope_403.py` + importlinter contract | FR-04, NFR-06 | DRAFT |
| `03-development/src/taskq_api/service/ratelimit.py` + `repository/rate_repo.py` | `03-development/tests/integration/test_rate_limit_429.py` | FR-05, NFR-03 | DRAFT |
| `03-development/src/taskq_api/repository/session.py` + `repository/task_repo.py` | `03-development/tests/integration/test_n_plus_one.py` + `03-development/tests/integration/test_transaction_rollback.py` | FR-06, NFR-01, NFR-03 | DRAFT |
| `migrations/versions/v1_initial.py` ... `v3_split_results.py` | `03-development/tests/integration/test_alembic_round_trip.py` (real SQLite file) | FR-07, NFR-09, NFR-12 | DRAFT |
| `03-development/src/taskq_api/service/runner.py` (TaskGroup) | `03-development/tests/integration/test_drain_timeout_interrupted.py` + `03-development/tests/integration/test_no_orphan_process.py` | FR-08, NFR-03 | DRAFT |
| `03-development/src/taskq_api/api/health.py` | `03-development/tests/integration/test_readyz_503.py` + `03-development/tests/integration/test_readyz_migration_behind.py` | FR-09, NFR-03 | DRAFT |
| `03-development/src/taskq_api/errors.py` + `app.py` | `03-development/tests/integration/test_problem_json.py` + `03-development/tests/integration/test_500_no_leak.py` | FR-10, NFR-02, NFR-04 | DRAFT |
| `03-development/src/taskq_api/` (whole) | `03-development/tests/` (whole) — pytest collection | NFR-09 (zero skip) | DRAFT |
| `03-development/tests/integration/` (whole) | `pytest --cov` gate | NFR-10 (≥ 80% line coverage) | DRAFT |
| `03-development/src/taskq_api/api/*.py` (handlers) | handler-LOC grep + radon CC | NFR-11 | DRAFT |
| `Makefile` (root) | `make verify-system` exit 0 + stdout `verify-system: PASS` | NFR-12 | DRAFT |
| `requirements.txt` + `requirements.lock` | `pip-licenses --format=json --with-system` | NFR-07 | DRAFT |

---

## 5. Completeness Verification

| Check | Target | Actual (P1) | Status |
|-------|--------|-------------|--------|
| FR → SRS mapping | 100% (10/10) | 100% (10/10) | OK |
| NFR → SRS mapping | 100% (12/12) | 100% (12/12) | OK |
| SRS → Code (planned module) mapping | 100% (22/22) | 100% (22/22) | OK |
| Code → Test (planned) mapping | 100% (22/22) | 100% (22/22) | OK |
| Test inventory entries (P1) | ≥ 1 per FR (per harness policy) | 76/76 (all 10 FRs + 12 NFRs; see `coverage_summary.by_fr` in `TEST_INVENTORY.yaml` lines 665–689) | OK |
| Test coverage (line, P4) | ≥ 80% integration; 100% overall | n/a (P3/P4) | PENDING |
| FR-07 migration tested against real DB | true | n/a | PENDING |
| Zero `pytest.skip` in collected tests (NFR-09) | 0 | n/a | PENDING |
| `bandit -r 03-development/src/` (NFR-02) | 0 HIGH / 0 MEDIUM | n/a | PENDING |
| `mutmut results` score (NFR-08) | ≥ 70 | n/a | PENDING |
| `pip-licenses` allowlist (NFR-07) | all in allowlist | n/a | PENDING |
| `make verify-system` exit (NFR-12) | 0 with `verify-system: PASS` on stdout | n/a | PENDING |

**Phase-1 coverage check**: `TEST_INVENTORY.yaml` (v2.0, 76 entries) carries 1:1 named test functions for all 10 FRs and all 12 NFRs (per `coverage_summary.by_fr` at yaml lines 665–689). `derive_test_cases.md` (P2) preserves these names verbatim in `TEST_SPEC.md`; P3 fills the actual functions; P4 gates on coverage/lint.

---

## 6. ASPICE Compliance (SWE.3 / SYS.4)

| ASPICE Capability | Evidence in this matrix | Status |
|-------------------|------------------------|--------|
| SWE.3.B.SP1 — Software requirements analysis | §2 maps every FR/NFR to a canonical SRS section | OK |
| SWE.3.B.SP2 — Software architectural design | §3 maps every SRS section to a planned module (SPEC §6) | OK |
| SWE.3.B.SP3 — Software detailed design and unit construction | §4 maps every planned module to a planned test file | OK |
| SWE.3.B.SP4 — Software unit verification | §4 + §5: each module's planned tests cover its FR/NFR | OK |
| SWE.3.B.SP5 — Software integration verification | §4 integration-test rows cover all error codes per NFR-10 | OK |
| SWE.3.B.SP6 — Software qualification verification | §5 Completeness Verification | OK (P1 row; refresh in P5) |
| SYS.4.B — System integration | §4 + §5; integration tests via `httpx.ASGITransport` (NFR-10) | OK (P1 row) |
| Bidirectional traceability (forward + backward) | §2→§3→§4 columns are joined on FR/Section/Module | OK |
| Traceability consistency (no orphan FR or orphan test) | every row in §2 has a §3 row; every §3 row has a §4 row | OK |

---

## 7. Cross-References

- Canonical spec: `/Users/johnny/projects/taskq-api/SPEC.md` (v1.0.0, 2026-07-30)
- Spec body: `/Users/johnny/projects/taskq-api/01-requirements/SRS.md` (v1.0.0)
- Spec tracking: `/Users/johnny/projects/taskq-api/01-requirements/SPEC_TRACKING.md`
- Test inventory: `/Users/johnny/projects/taskq-api/TEST_INVENTORY.yaml` (P1 seed; expanded by P2 `derive_test_cases.md`)
- Module layout (planned): SRS §12 (`/Users/johnny/projects/taskq-api/01-requirements/SRS.md` lines 579–629)
- Status column: machine-refreshed from `quality_manifest.json` (P6); not hand-edited here

---

## 8. Update Log

| Date | Change | By |
|------|--------|----|
| 2026-08-12 | Initial creation — populated from `SRS.md` (FR-01..FR-10, NFR-01..NFR-12) and `SPEC §6` module layout; FR-01 test entry seeded from `TEST_INVENTORY.yaml`; remaining FR/NFR test names deferred to P2 `derive_test_cases.md` per the harness policy recorded in `TEST_INVENTORY.yaml` | Agent A (Requirements Engineer, Round 1) |
| 2026-08-12 | Round 2 — B-2 review fixes: (1) FR-01 row now references `fr_tests.FR-01` block (1 unit + 6 integration) instead of placeholder names; (2) §2 Notes line 51 now points to `TEST_INVENTORY.yaml` at repo root (was `01-requirements/`); (3) §3 table now carries a NFR-99-09 deferred-decision footnote for FR-09 metrics.py split; (4) §5 test-inventory row updated from `1/10 (FR-01 only)` to `76/76 (all 10 FRs + 12 NFRs)` per `coverage_summary.by_fr`; (5) §5 closing remark rewritten to reflect 76-entry coverage instead of "FR-01 only" seed claim | Agent A (Requirements Engineer, Round 2) |
