# Release Notes — taskq-api

> **Version**: 1.0.0
> **Release Date**: 2026-08-13
> **Gate**: 4 (P6 full quality gate)
> **Gate 4 Composite Score**: **95.28 / 100** (per `.methodology/quality_manifest.json` → `gate_results.gate4.score`; `06-quality/QUALITY_REPORT.md` reports 95.2776 rounded to 95.3)
> **Tag**: `gate4-20260813-score95`

---

## 1. Summary

This is the first full release of `taskq-api`, a task-queue HTTP service that delivers an authenticated, scope-controlled, rate-limited task lifecycle on top of SQLAlchemy/SQLite, with an in-process asyncio runner and Alembic-managed migrations. The release represents completion of the P1–P6 harness pipeline and a clean Gate 4 (P6) sign-off.

## 2. Gate Progression (since prior release)

There is no prior tagged release. Pipeline gate progression on `main`:

| Gate | Score | Status |
|------|------:|--------|
| Gate 1 (P3/P5/P7/P8 per-FR) | 100.0 / FR (10 / 10 FRs at 100.0) | PASS |
| Gate 2 (P3 exit)          | 93.76     | PASS |
| Gate 3 (P4 exit)          | 94.34     | PASS |
| **Gate 4 (P6 full, this release)** | **95.28** | **PASS** |

Source: `.methodology/quality_manifest.json` → `gate_results.gate1/2/3/4`.

## 3. Functional Requirements Delivered (10 / 10)

All ten functional requirements shipped at Gate 1 score **100.0**, per `.methodology/quality_manifest.json` → `gate_results.gate1`:

| FR ID | Title | Gate 1 Score | Provenance (commit subject verified against `git log`) |
|-------|-------|-------------:|------------------------------------------------------|
| FR-01 | 任務資源 CRUD API (Task resource CRUD)               | 100.0 | `feat(FR-01): Gate1 PASS — score=100.0` (`b889ce9`) |
| FR-02 | 任務執行端點 (`POST /v1/tasks/{id}/run`, 202)         | 100.0 | `feat(FR-02): Gate1 PASS — score=100.0` (`ae5d014`) |
| FR-03 | API Key 認證 (`X-API-Key` header, SHA-256 hash)      | 100.0 | `feat(FR-03): Gate1 PASS — score=100.0` (`62357bc`) |
| FR-04 | Scope 授權 (`read` < `write` < `admin`)               | 100.0 | `feat(FR-04): Gate1 PASS — score=100.0` (`28e222d`) |
| FR-05 | 流量控制 (per-token token bucket)                     | 100.0 | `feat(FR-05): Gate1 PASS — score=100.0` (`cfe8256`) |
| FR-06 | 持久化層與交易邊界 (repository only)                 | 100.0 | `feat(FR-06): Gate1 PASS — score=100.0` (`d3edcb4`) |
| FR-07 | Schema Migration (Alembic v1 → v2 → v3 round-trip)   | 100.0 | `feat(FR-07): Gate1 PASS — score=100.0` (`d13d01e`) |
| FR-08 | 非同步執行器 (asyncio runner + drain)                 | 100.0 | `feat(FR-08): Gate1 PASS — score=100.0` (`7596020`) |
| FR-09 | 健康檢查與可觀測性 (`/healthz`, `/readyz`, `/metrics`)| 100.0 | `feat(FR-09): Gate1 PASS — score=100.0` (`270a714`) |
| FR-10 | 錯誤契約 (RFC 7807 problem+json)                      | 100.0 | `feat(FR-10): Gate1 PASS — score=100.0` (`2b2cbb4`) |

Release commit (P6): `eb2d5bd release(P6): Gate4 PASS score=95.3 — pipeline complete` (subject verified against `git log`).

## 4. Gate 4 Quality Dimensions (per `06-quality/QUALITY_REPORT.md`)

| Dimension             | Score   | Status |
|-----------------------|--------:|--------|
| Linting               | 100.0   | PASS   |
| Type Safety           | 100.0   | PASS   |
| Test Coverage         | 100.0   | PASS   |
| Security              | 100.0   | PASS   |
| Secrets Scanning      | 100.0   | PASS   |
| License Compliance    | 100.0   | PASS   |
| Mutation Testing      | 79.0    | PASS   |
| Architecture          | 90.0    | PASS   |
| Readability           | 95.3    | PASS   |
| Error Handling        | 100.0   | PASS   |
| Documentation         | 100.0   | PASS   |
| Performance           | 100.0   | PASS   |
| Integration Coverage  | 80.0    | PASS   |
| Test Assertion Quality| 96.3    | PASS   |
| Execute Verification Target | 100.0 | PASS |
| Traceability          | 91.67   | PASS   |
| **Composite (Gate 4)** | **95.2776** | **PASS** |

Mutation-testing source: `.methodology/mutation_score.json` records `score=79.0`, `killed=609`, `survived=162` over `03-development/src/taskq_api/{repository,service}` (NFR-08 target ≥ 70).

## 5. Verification Provenance

- 10 / 10 FRs certified **PASS** at Gate 1 (`quality_manifest.json` gate1 entries all `quality_complete=true`, `open_critical=0`, `open_high=0`).
- Per-FR PASS verdicts generated against `.methodology/quality_manifest.json`; full evidence narrative in `05-verification/VERIFICATION_REPORT.md`.
- System baseline (test counts, coverage, performance, known issues) in `05-verification/BASELINE.md`.
- Line coverage **100 %** (802 stmts, 0 missing) per `04-testing/COVERAGE_REPORT.md`.
- `bandit -r 03-development/src/ -ll` → 0 HIGH / 0 MEDIUM (NFR-02, NFR-04).
- `gitleaks detect` → no leaks found.
- 5 / 5 architecture constraints in `CLAUDE.md` honored at HEAD (NFR-06).

## 6. Known Limitations

Carried forward from `05-verification/BASELINE.md` (HIGH severity count = 0):

- **LOW (a)** — `test_runner_shutdown_kwargs_translation` skipped: `TaskRunner.shutdown()` does not accept a legacy `wait` kwarg; the translation guard is not implemented (out-of-scope of the current FR set).
- **LOW (b)** — `test_key_repo_ensure_session_create_get` skipped: `create()` requires a real DB; `ensure_session` cannot stand up a session in the unit harness. Sibling tests prove the repository/runner paths end-to-end.
- **INFO (c)** — 3 NFR tooling-dependent tests skip because `pip-licenses`, `radon`, and the SBOM generator are not installed in the local venv; their `pytest.skip(...)` branches are themselves tested (NFR-07, NFR-11).
- **Advisory** — `06-quality/QUALITY_REPORT.md` lists 16 dead-code candidates under CRG; framework callbacks and entrypoints (`readyz`, `metrics`, `_lifespan`, problem-class subclasses, `_reset_taskq_state`, etc.) are intentional and **must not** be removed without verification.

## 7. References

- `06-quality/QUALITY_REPORT.md` — auto-generated Gate 4 quality report (per-dimension scores).
- `05-verification/VERIFICATION_REPORT.md` — P5 verification provenance and per-FR certification.
- `05-verification/BASELINE.md` — P5 system baseline (functional + quality + performance + known issues).
- `.methodology/quality_manifest.json` — persistent source of truth for FR scores and gate results.

---

_Generated by the P6 Release Author (gate4-verify-r1). Do not edit by hand — re-generate after Gate 4 re-run._