# Risk Mitigation Plans — taskq-api (Phase 7)

> **Project**: taskq-api
> **Phase**: 7 — Risk Management
> **Generated**: 2026-08-13
> **Scope**: HIGH-band risks (Likelihood × Impact ≥ 9) drawn from `RISK_REGISTER.md`.
> **Companion doc**: `RISK_REGISTER.md` is the canonical ID/owner ledger; this file is the **action plan** for the HIGH band plus the two still-OPEN MED risks that the spec wants formally tracked.

---

## 1. Scope Rule

This plan covers:
- **R1** (CRITICAL — resolved but plan retained as historical record).
- **R3, R4, R5, R6, R7, R8, R9, R10, R11** — HIGH band (active mitigations).
- **R13, R14** — MED band but **OPEN**; formally scheduled below to ensure they don't drift past Phase 9.

LOW/MED-already-mitigated (R2, R12) and ACCEPTED (R15) are tracked in `RISK_REGISTER.md` only.

---

## 2. Mitigation Plans

### R1 — v3 Alembic migration data loss (CRITICAL, RESOLVED, plan retained)

| Field | Value |
|-------|-------|
| **Owner** | Backend — Migrations |
| **Deadline** | DELIVERED (commits `a76c50f`, `78c3cbb`) |
| **Status** | RESOLVED 2026-08-12 |
| **Verification** | `pytest tests/test_bug_hunt_v3_downgrade.py tests/test_bug_hunt_v3_upgrade_safety.py` both PASS. Round-trip test exercises 5-run dataset through upgrade→downgrade and asserts every run id, exit_code, stdout_tail, stderr_tail, duration_ms, finished_at round-trips losslessly. |

**Steps (already executed)**

1. **TDD-RED**: write regression test that creates 5 task_results rows and asserts every row survives the v3→v2 downgrade.
2. **TDD-GREEN**:
   - Change downgrade `_REPOPULATE_RESULT_JSON` from correlated scalar `LIMIT 1` to `json_group_array(json_object(...))` grouped by `task_id`.
   - In upgrade, replace bare `except SQLAlchemyError: pass` with rowcount gate + reraise on backfill failure; only `drop_column` after backfill rowcount matches expected rows.
3. **TDD-IMPROVE**: assert `op.batch_alter_table` is wrapped in `with` context so the transaction boundary encloses the drop.
4. **Verify**: full alembic upgrade→downgrade→upgrade cycle on seeded SQLite + PostgreSQL fixtures; `test_bug_hunt_*` passes.

**Residual / monitor**

- Re-run on every schema change touching `tasks.result_json` or `task_results`.
- Add `--strict-roundtrip` flag to migration test runner for CI gate.

---

### R3 — API key leakage (HIGH, MITIGATED with residual R14)

| Field | Value |
|-------|-------|
| **Owner** | Auth |
| **Deadline** | 2026-08-30 (close residual R14) |
| **Status** | MITIGATED; residual risk = R14 (regex gap) |

**Mitigations already in place**

- Keys stored as SHA-256 hash (`taskq_api.service.auth.hash_key`).
- Constant-time comparison via `hmac.compare_digest`.
- `X-API-Key` header never appears in `/v1/metrics` or structured logs.

**Steps to close residual**

1. Add regression test `test_auth_redact_handles_at_in_password` with `TASKQ_DB_URL=postgres://app:p@ssw0rd@db/app`.
2. Patch `_DB_URL_RE` to anchor on the **last** `@` before path: `r'(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://[^:@\s]+:)(?P<pwd>.*)@(?P<rest>[^/\s]+)'` greedy-to-last.
3. Verify by `bandit` and `pytest tests/test_auth_redact_handles_at_in_password.py`.

---

### R4 — 403 leaks resource existence (HIGH, MITIGATED)

| Field | Value |
|-------|-------|
| **Owner** | Auth + API layer |
| **Deadline** | DELIVERED |
| **Status** | MITIGATED 2026-08-12 |

**Steps (delivered)**

- `verify_key` then `enforce_scope` then `service.get` ordered: scope check returns 403 **before** repo lookup.
- Regression tests: `test_fr04_403_does_not_leak` exercises `GET /v1/tasks/{id}` with `read`-only token on a non-existent id; asserts 403 not 404.
- `test_fr04_404_with_admin_on_missing_id` asserts 404 for admin on missing id and 403 for `read` token on existing id (different shapes from outside).

**Monitoring**

- Add `mutmut` survivor coverage on `deps.py:enforce_scope` so any future reordering mutation is killed.

---

### R5 — N+1 query collapse (CRITICAL, MITIGATED with residual)

| Field | Value |
|-------|-------|
| **Owner** | Backend — Repository |
| **Deadline** | 2026-08-30 (close residual cursor-pagination finding) |
| **Status** | MITIGATED; residual = bug-hunt `task_repo#1` (cursor parameter accepted but not applied) |

**Mitigations in place**

- `TaskRepo.list` uses `selectinload("*")`; Gate 4 performance=100 with `list_tasks mean=933us`.
- pytest-benchmark includes a 200-row list scenario; SQLAlchemy statement-count assertion (`<= 3 statements`) embedded in integration test.

**Steps to close residual**

1. Apply cursor as keyset filter: `if cursor is not None: stmt = stmt.where(_task_table.c.id > cursor)` with `ORDER BY id ASC`.
2. Add regression test `test_bug_hunt_cursor_pagination.py` (bug-hunt fix commit `6078cba`) — confirm `limit=50` on a 1000-row dataset advances strictly forward across 20 calls.

---

### R6 — Error body leaks internals (HIGH, MITIGATED)

| Field | Value |
|-------|-------|
| **Owner** | API layer |
| **Deadline** | DELIVERED |
| **Status** | MITIGATED 2026-08-12 |

**Mitigations in place**

- All handlers wrap business logic in `try` → raise domain exception → `app.add_exception_handler` maps to `application/problem+json`.
- `type` is one of an enum whitelist (`about:blank`, `tag:taskq/api/task-not-found`, etc.).
- `detail` is composed from a whitelisted template per exception class; never echoes request payload.
- Tests assert: no `Traceback`, no `sqlalchemy.exc.`, no `sqlite3.`, no file paths, no line numbers in response body for 500-class errors.

**Monitoring**

- CI gate: regex over `/v1/*` 5xx response fixtures.

---

### R7 — `CancelledError` swallowed (HIGH, MITIGATED)

| Field | Value |
|-------|-------|
| **Owner** | Async runtime + harness lint |
| **Deadline** | DELIVERED |
| **Status** | MITIGATED 2026-08-12 |

**Mitigations in place**

- `ast-error-handling` harness dimension enforces: every `except` clause that catches `Exception` is paired with a re-raise of `asyncio.CancelledError`; Gate 4 error_handling=100, anti_patterns=0.
- Test `test_fr08_cancelled_propagates` cancels a task mid-`communicate()` and asserts the cancellation surfaces to the API handler (returns 499, not 200).

---

### R8 — Subprocess timeout orphan (HIGH, RESOLVED)

| Field | Value |
|-------|-------|
| **Owner** | Backend — Runner |
| **Deadline** | DELIVERED (commit `3b43920`) |
| **Status** | RESOLVED 2026-08-12 |

**Steps (delivered)**

- `api/tasks.py:run_task` now reads `TASKQ_TASK_TIMEOUT` from env at request time and forwards to `TaskRunner().run(..., timeout_seconds=...)`.
- Regression test `tests/test_bug_hunt_run_timeout.py` issues `POST /v1/tasks/{id}/run` with `command='sleep 60'` and `TASKQ_TASK_TIMEOUT=0.1`; asserts HTTP returns within 1 second with status `failed` and exit code indicates timeout.

**Residual**

- Subprocess kill is best-effort; on POSIX we use SIGKILL after grace; on Windows we'd need `CREATE_NEW_PROCESS_GROUP`. Production target is Linux-only — acceptable.

---

### R9 — Deployment runs without migration (CRITICAL, MITIGATED)

| Field | Value |
|-------|-------|
| **Owner** | Ops + Release |
| **Deadline** | DELIVERED |
| **Status** | MITIGATED 2026-08-12 |

**Mitigations in place**

- `/readyz` returns 503 if `SELECT version_num FROM alembic_version` ≠ expected head. k8s readiness probe blocks traffic. (FR-09 + §8 #11.)
- `python -m taskq_api migrate` subcommand runs `alembic upgrade head`; container entrypoint is `migrate && uvicorn`.
- CI gate: deploy pipeline fails if `taskq_api/_expected_head.py` drift from `alembic_version` on a fresh DB.

---

### R10 — Connection pool exhaustion (HIGH, RESOLVED — engine cache fix)

| Field | Value |
|-------|-------|
| **Owner** | Backend — Infra |
| **Deadline** | DELIVERED (commit `c9de95b`) |
| **Status** | RESOLVED 2026-08-12 |

**Steps (delivered)**

- `_check_migration_state` now uses a module-scope lazy-initialised engine with `functools.lru_cache(maxsize=1)`.
- Lifespan teardown calls `engine.dispose()` on shutdown.
- Regression test `tests/test_bug_hunt_readyz_engine_cache.py` issues 1000 `/readyz` calls and asserts process holds exactly 1 engine (via `gc.get_objects()` filtered to `sqlalchemy.engine.Engine` instances).

**Residual**

- Production pool tuning (`pool_size`, `max_overflow`, `pool_timeout`) remains env-driven; values are conservative defaults. Owner monitors `pg_stat_activity` for production tuning decisions.

---

### R11 — Transitive dependency license (HIGH, MITIGATED)

| Field | Value |
|-------|-------|
| **Owner** | Release |
| **Deadline** | DELIVERED |
| **Status** | MITIGATED 2026-08-12 |

**Mitigations in place**

- `scancode-toolkit` runs on `requirements.txt` lockfile + installed transitive set; Gate 4 license_compliance=100, 71 files, no unknown.
- CI gate blocks any PR introducing GPL/AGPL/SSPL/Commons-Clause/etc.
- `.gitleaksignore` mirrors allowed exceptions.

---

### R13 — Rate-limit bucket never refills (MED, OPEN)

| Field | Value |
|-------|-------|
| **Owner** | Backend — Rate-limit |
| **Deadline** | 2026-08-25 |
| **Status** | OPEN |
| **Severity justification** | MED (L=4 × I=2): legitimate clients hit permanent 429 after burst; over-protective but not security-critical. |

**Plan**

1. **TDD-RED**: write test that sets `TASKQ_RATE_BURST=5`, `TASKQ_RATE_PER_SEC=10`; consume 5 tokens, sleep 1 second, consume 1 token — assert success.
2. **TDD-GREEN** in `taskq_api/service/ratelimit.py`:
   ```python
   bucket = repo.get_bucket(...)
   now = time.monotonic()
   last = bucket.get('last_refill_at', now)
   elapsed = max(0.0, now - last)
   tokens = min(burst, bucket.get('tokens', burst) + elapsed * rate_per_sec)
   ```
   Persist post-consume `tokens` and `now`.
3. **TDD-IMPROVE**: deterministic-clock test using `monkeypatch.setattr(ratelimit, 'time', fake_clock)`.
4. **Verify**: full pytest + Gate 1 mutation re-run on `ratelimit.py` to drive its 6 survivors down.
5. **Gate**: Gate 5 re-check.

---

### R14 — `_DB_URL_RE` regex gap (MED, OPEN)

| Field | Value |
|-------|-------|
| **Owner** | Auth |
| **Deadline** | 2026-08-30 |
| **Status** | OPEN |
| **Severity justification** | MED (L=2 × I=4): requires a non-RFC-3986 URL form; redacts valid URLs but lets password leak in pathological cases. |

**Plan**

1. **TDD-RED**: `test_auth_redact_handles_at_in_password` with `TASKQ_DB_URL=postgres://app:p@ssw0rd@db.internal:5432/app` — assert no `p@ssw0rd` substring in any log/metric line.
2. **TDD-GREEN**: replace regex with anchored-last-`@` form:
   ```python
   _DB_URL_RE = re.compile(
       r'(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://[^:@\s]+:)(?P<pwd>.*)@(?P<rest>[^/\s]+)'
   )
   ```
   This makes the password group greedy up to the LAST `@` before path/host boundary.
3. **Verify**: `bandit` + new regression test pass; no other tests regress.
4. **Gate**: Gate 5 re-check.

---

## 3. Plan Verification Checklist

Before declaring a mitigation delivered, all of the following must hold:

- [ ] Regression test exists and was failing on the unfixed code.
- [ ] Regression test passes after the fix.
- [ ] No new mutation survivors introduced (re-run mutmut baseline).
- [ ] Owner + deadline recorded in this doc.
- [ ] Traceability matrix row updated (`01-requirements/TRACEABILITY_MATRIX.md`).
- [ ] `degradations.jsonl` does NOT contain an entry for the risk (only acceptable entry is "deferred to P8/P9 with reason").

---

## 4. Calendar

| Risk | Deadline | Owner | Open Work |
|------|----------|-------|-----------|
| R1   | DONE | Backend — Migrations | monitoring only |
| R3   | 2026-08-30 | Auth | close residual R14 |
| R4   | DONE | Auth + API | monitoring only |
| R5   | 2026-08-30 | Backend — Repository | close residual cursor |
| R6   | DONE | API layer | monitoring only |
| R7   | DONE | Async runtime | monitoring only |
| R8   | DONE | Backend — Runner | monitoring only |
| R9   | DONE | Ops + Release | monitoring only |
| R10  | DONE | Backend — Infra | monitoring only |
| R11  | DONE | Release | monitoring only |
| R13  | 2026-08-25 | Backend — Rate-limit | full TDD cycle |
| R14  | 2026-08-30 | Auth | full TDD cycle |

---

## 5. Self-Review

- **Possible error**: I declared R8 RESOLVED based on the bug-hunt `resolution.fix_commit: 3b43920` field. The fix is referenced in the bug-hunt report — confidence High, but I have not run `git show 3b43920` to inspect the diff. If the commit message or content differs from the description, the residual might be larger.
- **Possible error**: I closed R4 as DONE; if any future change adds a code path that does scope check AFTER resource lookup, the bug returns. Owner (Auth + API layer) should add a static check in `ast-error-handling` or a custom arch rule. Marked as "monitoring only".
- **Unverified assumption**: deadlines are reasonable for the team; no scheduling constraints have been gathered from outside this doc.
- **Confidence**: High on plan structure; Medium on calendar feasibility.