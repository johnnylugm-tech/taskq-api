# Risk Register — taskq-api (Phase 7)

> **Project**: taskq-api
> **Phase**: 7 — Risk Management
> **Author**: P7 Risk Author (orchestrator agent)
> **Generated**: 2026-08-13
> **Status source**: SPEC.md §9 risk matrix, Gate 3/4 results
> (`.methodology/gate3_result.json`, `.methodology/gate4_result.json`), bug-hunt
> report (`.methodology/bug_hunt_report.json`), mutation-test score
> (`.methodology/mutation_score.json`, `mutation_survivors.json`).
> **Note**: `.methodology/deferred_fixes.md` and `.sessi-work/issue_registry.json`
> do not exist in this repo; the inputs above are the canonical risk sources
> (verified by `ls`).

---

## 1. Scoring Convention

| Field | Range | Definition |
|-------|-------|------------|
| **Likelihood (L)** | 1–5 | Probability that the risk materialises in production within 12 months (1=remote, 5=almost certain). |
| **Impact (I)** | 1–5 | Severity when it does materialise (1=cosmetic, 5=catastrophic data loss / availability). |
| **Score (L×I)** | 1–25 | Composite; banded as `LOW (≤4)`, `MED (5–8)`, `HIGH (9–15)`, `CRITICAL (16–25)`. |
| **Status** | open / mitigated / accepted / resolved | Current disposition. |
| **Category** | security / data-integrity / availability / correctness / observability / compliance / architecture | Grouping for mitigation plans. |

Each row carries: **ID · Name · L · I · Score · Category · Mitigation Approach · Owner · Status · Source**.

---

## 2. Register (15 risks)

### 2.1 Seeded from SPEC.md §9 (R1–R12)

| ID | Name | L | I | Score | Category | Mitigation Approach | Owner | Status | Source |
|----|------|---|---|-------|----------|---------------------|-------|--------|--------|
| **R1** | v3 Alembic migration data loss on downgrade (silent `LIMIT 1` collapse) | 4 | 5 | **20 CRITICAL** | data-integrity | Use `json_group_array(...)` so every run survives round-trip; gate `drop_column` behind rowcount; reraise on backfill error. Fixed in commits `a76c50f` + `78c3cbb`; regression test `tests/test_bug_hunt_v3_downgrade.py` + `test_bug_hunt_v3_upgrade_safety.py`. | Backend (migrations) | **RESOLVED** | SPEC §9 R1 + bug-hunt `migrations.versions.v3_split_results#1/#2` |
| **R2** | SQL injection via raw SQL or string concatenation | 1 | 5 | **5 MED** | security | ORM only at repository layer (NFR-06 import-linter contract) + `bandit` gate (NFR-02). No raw user input enters a query. | Security review | MITIGATED | SPEC §9 R2; Gate 4 security=100, bandit 0 HIGH/MED/LOW |
| **R3** | API key plaintext leakage (logs / metrics / DB) | 3 | 5 | **15 HIGH** | security | SHA-256 hash + constant-time compare (`hmac.compare_digest`); redact-on-write in `/v1/metrics` via `_DB_URL_RE`. The redact regex still has a known gap (R14). | Auth | MITIGATED (residual: R14) | SPEC §9 R3; FR-03 |
| **R4** | 403 response leaks resource existence | 3 | 3 | **9 HIGH** | security | Authorisation check precedes resource lookup in `deps.py` and `service/auth.py`. Verified by `test_fr04_403_does_not_leak`. | Auth | MITIGATED | SPEC §9 R4; FR-04 |
| **R5** | N+1 query collapse on large `tasks` table | 4 | 5 | **20 CRITICAL** | performance | `selectinload("*")` on `TaskRepo.list`; SQLAlchemy statement-count assertion in tests; cursor-based pagination (still guarded by R13-adjacent finding). | Backend (repo) | MITIGATED (residual: cursor handling) | SPEC §9 R5; NFR-01; bug-hunt `task_repo#1` |
| **R6** | Error body leaks internal structure / stack traces | 5 | 3 | **15 HIGH** | observability | RFC 7807 `application/problem+json` with whitelisted `type`/`detail` (FR-10). Tests assert no `traceback`, no SQL fragments in response body. | API layer | MITIGATED | SPEC §9 R6; Gate 4 error_handling=100 |
| **R7** | `CancelledError` swallowed → shutdown deadlock | 3 | 3 | **9 HIGH** | availability | Explicit ban + lint (`ast-error-handling` anti-pattern check) + test `test_fr08_cancelled_propagates`. | Async runtime | MITIGATED | SPEC §9 R7; NFR-03 |
| **R8** | Subprocess timeout leaves orphan child process | 3 | 3 | **9 HIGH** | availability | `proc.kill()` + `await proc.wait()` (FR-08). **New risk**: HTTP path was bypassing the timeout machinery entirely — fixed in commit `3b43920` (runner now reads `TASKQ_TASK_TIMEOUT`). Regression test `tests/test_bug_hunt_run_timeout.py`. | Backend (runner) | **RESOLVED** | SPEC §9 R8; bug-hunt `taskq_api.api.tasks#1` |
| **R9** | Deployment runs without `alembic upgrade head` | 4 | 5 | **20 CRITICAL** | availability | `/readyz` fails closed if `alembic_version.version_num` ≠ head (FR-09, §8 #11). Plus `migrate` subcommand in `__main__`. | Ops + release | MITIGATED | SPEC §9 R9; Gate 4 health=100 |
| **R10** | Connection pool exhaustion under sustained load | 3 | 3 | **9 HIGH** | availability | `pool_pre_ping=True`, `pool_size` + `max_overflow` configured; `pool_timeout` enforced. **New risk**: `/readyz` was creating a fresh engine per request — fixed in commit `c9de95b` (module-scope cached engine with lifespan teardown). Regression test `tests/test_bug_hunt_readyz_engine_cache.py`. | Backend (infra) | **RESOLVED** | SPEC §9 R10 + bug-hunt `taskq_api.app#1` |
| **R11** | Transitive dependency introduces incompatible license | 3 | 3 | **9 HIGH** | compliance | Full-tree `scancode` scan in CI; lockfile pinned (`requirements.txt`); Gate 4 license_compliance=100, 71 files, no unknown. | Release | MITIGATED | SPEC §9 R11; NFR-07 |
| **R12** | Rate-limit bucket race allows over-allowance | 2 | 3 | **6 MED** | security | Per-bucket row-level lock + single transaction in rate-repo (FR-05). **New risk**: an unknown-token bucket-growth DoS — fixed in commit `745a23e` (LRU cap on `_buckets`). Regression test `tests/test_bug_hunt_rate_bounded.py`. | Backend (ratelimit) | **RESOLVED** (residual: R13 refill) | SPEC §9 R12 + bug-hunt `taskq_api.service.ratelimit#1` |

### 2.2 Carried from Bug-Hunt Round 1 (open or advisory)

| ID | Name | L | I | Score | Category | Mitigation Approach | Owner | Status | Source |
|----|------|---|---|-------|----------|---------------------|-------|--------|--------|
| **R13** | Rate-limit bucket never refills — clients hit permanent 429 after burst | 4 | 2 | **8 MED** | correctness | Compute tokens on read using `(now − last_refill_at) × rate_per_sec`, clamped at `burst`. Persist new `tokens` + `now`. Add test that waits > 1/rate and asserts refill. | Backend (ratelimit) | **OPEN** | bug-hunt `taskq_api.service.ratelimit#2` (severity: medium; refuted as Gate-3 blocking, recorded only) |
| **R14** | `_DB_URL_RE` regex misses passwords containing unescaped `@` | 2 | 4 | **8 MED** | security | Anchor regex to the LAST `@` before the path (minimal patch: `r'\:([^@\s]*)@'` greedy to final `@`); or use `sqlalchemy.engine.url.make_url(url).password` and substitute `***`. Add regression test for `p@ssword`-style passwords. | Auth | **OPEN** | bug-hunt `taskq_api.service.auth#1` (severity: medium; recorded only) |

### 2.3 Carried from Gate 4 / Mutation Score (informational)

| ID | Name | L | I | Score | Category | Mitigation Approach | Owner | Status | Source |
|----|------|---|---|-------|----------|---------------------|-------|--------|--------|
| **R15** | Mutation score = 79.0 (162 survivors); concentration in service/runner layer | 3 | 2 | **6 MED** | test-coverage | 162 survivors dominated by `tasks.py` (53), `runner.py` (25), `health.py` (22), `env.py` (13), `__main__.py` (11), `errors.py` (11), `deps.py` (7), `ratelimit.py` (6), `rate_repo.py` (5). Threshold 70 met; gate passes. Track survivors in `.methodology/mutation_survivors.json`; targeted assertion additions (boundary, error-path) on hot files in P8/P9. | QA | ACCEPTED | mutation_score.json; Gate 4 mutation_testing=79 |

---

## 3. Risk Heat Map

```
            Impact →
            1     2     3     4     5
Likelihood
   5                                  [R6]
   4                [R13]            [R1,R5,R9]
   3                       [R4,R7,R8, [R3,R11]
                           R10,R12]
   2          [R15]   [R12]  [R14]   [R2]
   1                                  [R2]
```

**HIGH or CRITICAL** (require formal mitigation plan in companion doc): **R1, R3, R4, R5, R6, R7, R8, R9, R10, R11**.

**MED or LOW** (advisory, tracked here): **R2, R12, R13, R14, R15**.

---

## 4. Status Summary

| Band | Count | Open | Mitigated | Resolved | Accepted |
|------|-------|------|-----------|----------|----------|
| CRITICAL (16–25) | 3 (R1, R5, R9) | 0 | 1 (R5) | 1 (R1) | 0 |
| HIGH (9–15) | 7 (R3,R4,R6,R7,R8,R10,R11) | 0 | 4 (R3,R4,R6,R7,R11) | 2 (R8,R10) | 0 |
| MED (5–8) | 4 (R2,R12,R13,R14) | 2 (R13,R14) | 1 (R2) | 1 (R12) | 0 |
| LOW (≤4) | 1 (R15) | 0 | 0 | 0 | 1 (R15) |
| **TOTAL** | **15** | **2** | **6** | **4** | **1** |

**Headline**: All CRITICAL and HIGH risks have at least one mitigation in place; **0 unresolved HIGH/CRITICAL**. Two MED-severity risks (R13 rate-limit refill, R14 redact-regex gap) are still OPEN and are scheduled in the companion mitigation plan.

---

## 5. Cross-References

- Risk mitigations trace to SPEC clauses: see `SPEC.md` §3 (FR-01..FR-10), §4 (NFR-01..NFR-12), §8 acceptance, §9 risk matrix, §10 framework alignment.
- High-risk modules per SPEC §10: `taskq_api.service.runner` (R8), `taskq_api.service.auth` (R3, R14), `taskq_api.repository.session` (R10), `migrations.versions.v3_split_results` (R1).
- Bug-hunt source of truth: `.methodology/bug_hunt_report.json` (8 findings, 6 resolved, 2 open).
- Mutation-test source of truth: `.methodology/mutation_score.json` (score 79.0, 162 survivors).

---

## 6. Self-Review

- **Possible error**: I assigned CRITICAL (20) to R1/R5/R9. R5 (N+1) is described in SPEC §9 as "high × high" which is 5×5=25; I used 4×5=20 because the explicit preload + SQL-count assertion already catches the regression in tests. Verify against SPEC: SPEC says "高 × 高" which in the §9 row reads literally "高" twice; convention is 5×5 for CRITICAL. If strict, R5 is **25 CRITICAL** (already required to be HIGH or above — band unchanged).
- **Possible error**: R8 status — I marked it RESOLVED based on bug-hunt commit `3b43920`. Verified by reading the bug-hunt `resolution.fix_commit` field. Confidence: High.
- **Unverified assumptions**:
  - That `.methodology/deferred_fixes.md` and `.sessi-work/issue_registry.json` do not exist (verified by `ls`, both return ENOENT).
  - That mutation-score 79 is "ACCEPTED" rather than "OPEN" — accepted because Gate 4 threshold (70) is met and the gate passed; the 162 survivors are concentrated in the modules already covered by per-FR Gate 1 mutation runs.
  - That R13 / R14 do not block Gate 3/4 — verified by `gate3_result.json` and `gate4_result.json` having `failing_dimensions: []` and `passed: true`.
- **Confidence**: High (multi-source verification).