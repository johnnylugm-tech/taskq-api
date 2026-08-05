# Traceability Matrix — taskq-api

> Requirements Traceability Matrix
> Framework: harness-methodology
> Version: v1.0 · Phase 1 (Requirements)
> Requirement authority: `01-requirements/SRS.md` (approved) · Canonical spec: `SPEC.md` v1.0.0
> Test-name authority: `TEST_INVENTORY.yaml` (P1) → `TEST_SPEC.md` (P2, single source of truth)

---

## 1. Overview

Provides complete **FR/NFR -> Design Element -> Test Case** bidirectional
traceability supporting ASPICE SWE.3 / SYS.4 compliance.

### 1.1 Method

- **Forward direction** (§3, §4): every SRS acceptance criterion (AC) resolves to
  one or more design elements and at least one test case.
- **Backward direction** (§5): every test case and every SPEC §8 acceptance item
  resolves back to an FR/NFR; §5.3 is the exhaustive reverse index from design
  element to requirement.
- Design elements are cited as `` `module.path::symbol` ``. Module paths are taken
  verbatim from the canonical folder structure (`SPEC.md` §6); symbol names are
  **planned** and are re-confirmed against `02-architecture/SAD.md` in Phase 2.
- No requirement is invented here. Every AC row cites an SRS AC ID; the SRS in
  turn cites `SPEC.md`.

### 1.2 Status legend

| Status | Meaning |
|--------|---------|
| `SPECIFIED` | Requirement traced to a planned design element and a planned test case. No code exists yet (P1 entry state). |
| `IN_PROGRESS` | Code/module exists; test not yet green. Machine-set by `build_traceability`. |
| `VERIFIED` | Test actually ran and passed. Machine-set only — never hand-edited (SRS AC-09.6). |

At P1 every row is `SPECIFIED`. The Status column is machine-refreshed by
`advance-phase` from the live code/test scan; hand edits are overwritten.

### 1.3 Naming caveat (recorded, not resolved at P1)

`01-requirements/SPEC_TRACKING.md`'s Owner column writes the FR-01/FR-09 owner as
`taskq_api.api.routes.tasks` / `taskq_api.api.routes.health`, but the canonical
folder structure (`SPEC.md` §6) has no `routes` package — the files are
`api/tasks.py` and `api/health.py`. This matrix uses the canonical `SPEC.md` §6
paths (`taskq_api.api.tasks`, `taskq_api.api.health`). `SPEC_TRACKING.md` is
approved and is not modified by this document; the divergence is reconciled in
Phase 2 against `02-architecture/SAD.md`.

---

## 2. Forward Traceability Index

| Req | Title | SRS § | ACs | Primary design elements | Tests | Status |
|-----|-------|-------|-----|-------------------------|-------|--------|
| FR-01 | Task resource CRUD API | SRS §3 FR-01 | 4 | api.tasks, service.tasks, repository.task_repo, models.schemas | 6 | SPECIFIED |
| FR-02 | Task execution endpoint | SRS §3 FR-02 | 5 | api.tasks, service.runner, repository.task_repo | 5 | SPECIFIED |
| FR-03 | API Key authentication | SRS §3 FR-03 | 5 | api.deps, service.auth, repository.key_repo | 6 | SPECIFIED |
| FR-04 | Scope authorisation | SRS §3 FR-04 | 3 | api.deps, service.auth, app | 3 | SPECIFIED |
| FR-05 | Per-token rate limiting | SRS §3 FR-05 | 4 | service.ratelimit, repository.rate_repo, api.deps | 4 | SPECIFIED |
| FR-06 | Persistence + transaction boundaries | SRS §3 FR-06 | 5 | repository.session, repository.task_repo | 5 | SPECIFIED |
| FR-07 | Schema migration (three-step) | SRS §3 FR-07 | 5 | migrations.versions.*, migrations.env | 5 | SPECIFIED |
| FR-08 | Async executor | SRS §3 FR-08 | 4 | service.runner, app | 4 | SPECIFIED |
| FR-09 | Health checks + observability | SRS §3 FR-09 | 1 (+3 endpoints) | api.health, repository.session | 4 | SPECIFIED |
| FR-10 | Error contract (RFC 7807) | SRS §3 FR-10 | 5 | errors, app | 5 | SPECIFIED |
| NFR-01 | Performance / query efficiency | SRS §4 NFR-01 | 4 | api.tasks, repository.task_repo | 3 | SPECIFIED |
| NFR-02 | HTTP + data-layer security | SRS §4 NFR-02 | 7 | service.auth, api.deps, errors, app | 6 | SPECIFIED |
| NFR-03 | Error handling / txn / async | SRS §4 NFR-03 | 6 | repository.session, service.runner, api.health | 6 | SPECIFIED |
| NFR-04 | Sensitive-data redaction | SRS §4 NFR-04 | 3 | errors, config | 3 | SPECIFIED |
| NFR-05 | Documentation coverage | SRS §4 NFR-05 | 2 | app, api.tasks | 2 | SPECIFIED |
| NFR-06 | Layering contract | SRS §4 NFR-06 | 4 | repository.session, service.tasks, api.deps | 3 | SPECIFIED |
| NFR-07 | Dependency + licence compliance | SRS §4 NFR-07 | 4 | (config artifacts — no runtime module) | 4 | SPECIFIED |
| NFR-08 | Mutation testing | SRS §4 NFR-08 | 3 | service.*, repository.* | 2 | SPECIFIED |
| NFR-09 | Verification authenticity (zero-skip) | SRS §4 NFR-09 | 6 | migrations.versions.v3_split_results | 6 | SPECIFIED |
| NFR-10 | Integration coverage | SRS §4 NFR-10 | 3 | app | 2 | SPECIFIED |
| NFR-11 | Readability | SRS §4 NFR-11 | 3 | api.tasks | 4 | SPECIFIED |
| NFR-12 | System verification target | SRS §4 NFR-12 | 2 | (Makefile target — no runtime module) | 1 | SPECIFIED |

**Totals**: 10 FRs + 12 NFRs = 22 requirements · 88 acceptance criteria · 0 untraced.

---

## 3. FR Traceability

### 3.1 FR-01 — Task resource CRUD API

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-1.1 | Non-empty / ≤1000 chars / injection blacklist / unique name; violation → 422 + problem+json | `taskq_api.models.schemas::TaskCreate` | `test_fr01_create_rejects_invalid_command_with_422` | integration (SPEC §8 #4) | SPECIFIED |
| AC-1.1 | Name uniqueness → 409 | `taskq_api.service.tasks::create_task` | `test_fr01_duplicate_name_returns_409` | integration (SPEC §8 #8) | SPECIFIED |
| AC-1.2 | Unknown id → 404 + problem+json | `taskq_api.service.tasks::get_task` | `test_fr01_get_unknown_id_returns_404` | integration (SPEC §8 #7) | SPECIFIED |
| AC-1.3 | Cursor-based pagination; offset forbidden | `taskq_api.repository.task_repo::list_tasks_by_cursor` | `test_fr01_list_paginates_by_cursor_not_offset` | unit + integration | SPECIFIED |
| AC-1.4 | Default `limit` 50, max 200; over max → 422 | `taskq_api.models.schemas::TaskListQuery` | `test_fr01_limit_defaults_50_and_rejects_over_200` | unit | SPECIFIED |
| AC-1.1..1.4 | Endpoint surface `POST` / `GET` / `GET`-list / `DELETE` with scope guards | `taskq_api.api.tasks::create_task` | `test_fr01_crud_chain_end_to_end` | integration (SPEC §8 #4) | SPECIFIED |

**Linked Modules**: `taskq_api.api.deps` (per-endpoint scope guard), `taskq_api.errors` (422/404/409 problem+json rendering).

### 3.2 FR-02 — Task execution endpoint

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-2.1 | `POST /v1/tasks/{id}/run` (scope `write`) → 202 + `run_id` | `taskq_api.api.tasks::run_task` | `test_fr02_run_returns_202_with_run_id` | integration | SPECIFIED |
| AC-2.2 | `asyncio.create_subprocess_exec(*shlex.split(...))`; `shell=True` forbidden; timeout `TASKQ_TASK_TIMEOUT` | `taskq_api.service.runner::spawn_process` | `test_fr02_spawns_via_exec_not_shell` | unit | SPECIFIED |
| AC-2.3 | State machine `pending → running → done \| failed \| timeout` | `taskq_api.service.runner::transition_status` | `test_fr02_status_state_machine_transitions` | unit | SPECIFIED |
| AC-2.4 | Results written to `task_results` (`exit_code` / `stdout_tail` / `stderr_tail` / `duration_ms` / `finished_at`) | `taskq_api.repository.task_repo::insert_task_result` | `test_fr02_result_row_persists_all_columns` | integration | SPECIFIED |
| AC-2.5 | `GET /v1/tasks/{id}/runs` (scope `read`) newest-first history | `taskq_api.api.tasks::list_task_runs` | `test_fr02_runs_history_newest_first` | integration | SPECIFIED |

**Linked Modules**: `taskq_api.models.orm` (`task_results` table), `taskq_api.config` (`TASKQ_TASK_TIMEOUT`).

### 3.3 FR-03 — API Key authentication

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-3.1 | `X-API-Key` required on every `/v1/*`; missing/invalid → 401 + problem+json | `taskq_api.api.deps::require_api_key` | `test_fr03_missing_api_key_returns_401` | integration (SPEC §8 #5) | SPECIFIED |
| AC-3.2 | Keys stored as SHA-256 hashes; plaintext never stored | `taskq_api.repository.key_repo::get_by_key_hash` | `test_fr03_api_keys_table_stores_64_hex_hash_only` | integration (SPEC §8 #18) | SPECIFIED |
| AC-3.2 | Comparison via `hmac.compare_digest` (constant time) | `taskq_api.service.auth::verify_key` | `test_fr03_key_compare_is_constant_time` | unit | SPECIFIED |
| AC-3.3 | `python -m taskq_api key create --scope <scope>`; plaintext printed once | `taskq_api.__main__::key_create` | `test_fr03_key_create_prints_plaintext_once` | unit | SPECIFIED |
| AC-3.4 | Non-null `revoked_at` → key invalid | `taskq_api.service.auth::is_revoked` | `test_fr03_revoked_key_is_rejected` | unit | SPECIFIED |
| AC-3.5 | `/healthz` and `/readyz` need no authentication | `taskq_api.api.health::healthz` | `test_fr03_health_endpoints_skip_auth` | integration | SPECIFIED |

**Linked Modules**: `taskq_api.models.orm` (`api_keys` table), `taskq_api.errors` (401 problem+json).

### 3.4 FR-04 — Scope authorisation

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-4.1 | Strict inclusive hierarchy `read < write < admin` | `taskq_api.service.auth::scope_satisfies` | `test_fr04_scope_hierarchy_is_inclusive` | unit | SPECIFIED |
| AC-4.2 | Insufficient scope → 403 + problem+json; body must not leak resource existence | `taskq_api.api.deps::require_scope` | `test_fr04_403_body_does_not_reveal_resource_existence` | integration (SPEC §8 #6) | SPECIFIED |
| AC-4.3 | Single middleware / dependency; every `/v1` route passes through it | `taskq_api.app::register_v1_routes` | `test_fr04_every_v1_route_uses_same_auth_dependency` | integration | SPECIFIED |

**Linked Modules**: `taskq_api.errors` (403 problem+json, existence-neutral detail).

### 3.5 FR-05 — Per-token rate limiting

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-5.1 | Token bucket, capacity `TASKQ_RATE_BURST`, refill `TASKQ_RATE_PER_SEC` | `taskq_api.service.ratelimit::consume_token` | `test_fr05_bucket_capacity_and_refill_rate` | unit | SPECIFIED |
| AC-5.2 | Over limit → 429 + problem+json + `Retry-After` (seconds) | `taskq_api.errors::rate_limited` | `test_fr05_burst_over_limit_returns_429_with_retry_after` | integration (SPEC §8 #9) | SPECIFIED |
| AC-5.3 | Bucket state in DB; update in one transaction with a row-level lock | `taskq_api.repository.rate_repo::lock_bucket_for_update` | `test_fr05_bucket_update_holds_row_lock` | integration | SPECIFIED |
| AC-5.4 | `/healthz` and `/readyz` are not rate-limited | `taskq_api.api.deps::rate_limit_guard` | `test_fr05_health_endpoints_exempt_from_rate_limit` | integration | SPECIFIED |

**Linked Modules**: `taskq_api.models.orm` (`rate_buckets` table), `taskq_api.config` (burst / refill env vars).

### 3.6 FR-06 — Persistence layer and transaction boundaries

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-6.1 | All data access through `repository/`; business layer holds no `Session` | `taskq_api.repository.session::get_session` | `test_fr06_service_layer_holds_no_session` | unit | SPECIFIED |
| AC-6.2 | One `Session` per request; success commits, exception rolls back, via context manager | `taskq_api.repository.session::transaction` | `test_fr06_transaction_commits_on_success_rolls_back_on_error` | unit | SPECIFIED |
| AC-6.3 | String-concatenated SQL forbidden; ORM or parameterised only | `taskq_api.repository.task_repo::list_tasks_by_cursor` | `test_fr06_queries_are_orm_or_parameterised` | unit (SPEC §8 #17) | SPECIFIED |
| AC-6.4 | Explicit `selectinload` / `joinedload`; N+1 is an acceptance failure | `taskq_api.repository.task_repo::load_with_results` | `test_fr06_relationship_load_is_explicitly_eager` | integration (SPEC §8 #14) | SPECIFIED |
| AC-6.5 | `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True` | `taskq_api.repository.session::create_engine_from_config` | `test_fr06_engine_sets_pool_size_and_pre_ping` | unit | SPECIFIED |

**Linked Modules**: `taskq_api.models.orm` (declarative tables), `taskq_api.config` (pool settings).

### 3.7 FR-07 — Schema migration (Alembic three-step evolution)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-7.1 | `alembic upgrade head` and `alembic downgrade base` both succeed | `migrations.versions.v1_initial::upgrade` | `test_fr07_upgrade_head_then_downgrade_base_exit_zero` | integration (SPEC §8 #13) | SPECIFIED |
| AC-7.2 | Round trip `upgrade head` → write → `downgrade -1` → `upgrade head` leaves every column byte-identical | `migrations.versions.v3_split_results::downgrade` | `test_fr07_v3_round_trip_preserves_every_column` | integration (SPEC §8 #12) | SPECIFIED |
| AC-7.3 | Destructive `op.execute("DROP TABLE ...")` shortcuts forbidden in place of a real downgrade | `migrations.versions.v2_tags::downgrade` | `test_fr07_downgrade_has_no_destructive_shortcut` | unit | SPECIFIED |
| AC-7.4 | Migration files covered by tests (offline SQL generation + assertions) | `migrations.env::run_migrations_offline` | `test_fr07_offline_sql_generation_matches_expected` | unit | SPECIFIED |
| AC-7.5 | Tested against a real SQLite **file** (not in-memory mock); no skip downgrade | `migrations.versions.v3_split_results::upgrade` | `test_fr07_data_move_verified_on_real_sqlite_file` | integration (SPEC §8 #12) | SPECIFIED |

**Linked Modules**: `taskq_api.models.orm` (target schema, `SPEC.md` §5.2).

### 3.8 FR-08 — Async executor

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-8.1 | `asyncio.TaskGroup`; graceful drain up to `TASKQ_DRAIN_TIMEOUT`; over-budget tasks marked `interrupted` | `taskq_api.service.runner::drain_on_shutdown` | `test_fr08_shutdown_drains_then_marks_interrupted` | integration (SPEC §8 #25) | SPECIFIED |
| AC-8.2 | Concurrency cap `TASKQ_MAX_CONCURRENT`; over-cap requests queue, coroutines not spawned without limit | `taskq_api.service.runner::acquire_slot` | `test_fr08_concurrency_cap_queues_excess` | unit | SPECIFIED |
| AC-8.3 | Timeout via `asyncio.wait_for`; child killed (`process.kill()` then `await process.wait()`); no orphans | `taskq_api.service.runner::run_with_timeout` | `test_fr08_timeout_kills_child_leaving_no_orphan` | integration (SPEC §8 #25) | SPECIFIED |
| AC-8.4 | `asyncio.CancelledError` must propagate, never swallowed by `except Exception` | `taskq_api.service.runner::execute` | `test_fr08_cancelled_error_propagates` | unit | SPECIFIED |

**Linked Modules**: `taskq_api.app` (lifespan / shutdown hook), `taskq_api.config` (drain + concurrency env vars).

### 3.9 FR-09 — Health checks and observability

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| — (endpoint) | `GET /healthz`, no auth, always 200 `{"status":"ok"}` | `taskq_api.api.health::healthz` | `test_fr09_healthz_returns_200_ok` | integration | SPECIFIED |
| — (endpoint) | `GET /readyz`, DB reachable → 200, else 503 naming the failed check | `taskq_api.api.health::readyz` | `test_fr09_readyz_returns_503_when_db_unreachable` | integration (SPEC §8 #10) | SPECIFIED |
| AC-9.1 | `/readyz` fails closed when `alembic current` != head | `taskq_api.api.health::check_migration_at_head` | `test_fr09_readyz_fails_closed_when_migration_behind_head` | integration (SPEC §8 #11) | SPECIFIED |
| — (endpoint) | `GET /v1/metrics` (scope `admin`): task counts by status, latency percentiles, rate-limit rejections | `taskq_api.api.health::metrics` | `test_fr09_metrics_requires_admin_and_reports_counters` | integration | SPECIFIED |

**Linked Modules**: `taskq_api.repository.session` (DB reachability probe backing `/readyz`).

### 3.10 FR-10 — Error contract (RFC 7807)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-10.1 | Every non-2xx has `Content-Type: application/problem+json` | `taskq_api.errors::problem_response` | `test_fr10_all_non_2xx_use_problem_json_content_type` | integration | SPECIFIED |
| AC-10.2 | Body fields `type` / `title` / `status` / `detail` / `instance` / `correlation_id` | `taskq_api.errors::ProblemDetail` | `test_fr10_problem_body_has_all_six_fields` | unit | SPECIFIED |
| AC-10.3 | `detail` leaks no SQL, stack trace, file path, or schema description | `taskq_api.errors::sanitize_detail` | `test_fr10_error_body_contains_no_stack_or_sql_or_path` | integration (SPEC §8 #19) | SPECIFIED |
| AC-10.4 | `correlation_id` returned in `X-Correlation-Id` and logged with the same value | `taskq_api.app::correlation_id_middleware` | `test_fr10_correlation_id_round_trips_header_and_log` | integration | SPECIFIED |
| AC-10.5 | Status mapping 422 / 401 / 403 / 404 / 409 / 429 / 503 / 500 | `taskq_api.errors::STATUS_TITLE_MAP` | `test_fr10_status_code_mapping_matches_spec_section_7` | unit | SPECIFIED |

---

## 4. NFR Traceability

### 4.1 NFR-01 — Performance and query efficiency (`performance`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-01.1 | `GET /v1/tasks/{id}` p95 < 30 ms at 10 000 rows | `taskq_api.api.tasks::get_task` | `test_kpi_p95_get_by_id_under_30ms_at_10k` | benchmark (SPEC §8 #15) | SPECIFIED |
| AC-01.2 | `GET /v1/tasks?limit=50` p95 < 80 ms at 10 000 rows | `taskq_api.api.tasks::list_tasks` | `test_kpi_p95_list_under_80ms_at_10k` | benchmark | SPECIFIED |
| AC-01.3 | List-endpoint SQL statement count constant (no N+1), asserted via SQLAlchemy event listener | `taskq_api.repository.task_repo::load_with_results` | `test_n_plus_one_sql_count_constant_within_list_endpoint` | integration (SPEC §8 #14) | SPECIFIED |
| AC-01.4 | Measurement tool is `pytest-benchmark` | (tooling — `requirements-dev.txt`) | (covered by AC-01.1 / AC-01.2 cases) | tool config | SPECIFIED |

> Coverage note (from SRS §4 NFR-01): the `performance` dimension scores mean
> latency with a >1 s / >3 s penalty curve and does **not** verify the 30 ms /
> 80 ms p95 thresholds or the constant-statement-count rule. The three dedicated
> cases above are the P3+ closure for that gap.

### 4.2 NFR-02 — HTTP and data-layer security (`security`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-02.1 | Project-wide ban on `shell=True` / `eval(` / `exec(`; grep returns zero hits | `taskq_api.service.runner::spawn_process` | `test_grep_no_shell_true_or_eval_or_exec` | grep gate (SPEC §8 #16) | SPECIFIED |
| AC-02.2 | No f-string / `%` / `+` composed SQL; ORM or parameterised only | `taskq_api.repository.task_repo::list_tasks_by_cursor` | `test_grep_no_string_concatenated_sql` | grep gate (SPEC §8 #17) | SPECIFIED |
| AC-02.3 | Keys hash-stored and compared with `hmac.compare_digest` | `taskq_api.service.auth::verify_key` | `test_fr03_key_compare_is_constant_time` | unit (SPEC §8 #18) | SPECIFIED |
| AC-02.4 | 403 must not leak resource existence | `taskq_api.api.deps::require_scope` | `test_403_body_does_not_leak_resource_existence` | integration (SPEC §8 #6) | SPECIFIED |
| AC-02.5 | Error bodies carry no stack trace, SQL, or file path | `taskq_api.errors::sanitize_detail` | `test_500_body_contains_no_stack_or_sql_or_path` | integration (SPEC §8 #19) | SPECIFIED |
| AC-02.6 | CORS defaults to deny-all; allowlist from `TASKQ_CORS_ORIGINS` | `taskq_api.app::configure_cors` | `test_cors_default_deny_all_origins` | integration | SPECIFIED |
| AC-02.7 | `bandit -r 03-development/src/` reports 0 HIGH, 0 MEDIUM | (whole `src` tree) | (bandit run — no test function) | tool gate (SPEC §8 #23) | SPECIFIED |

> Coverage note (from SRS §4 NFR-02): the `security` dimension runs `bandit` only;
> AC-02.1 / 02.3 / 02.4 / 02.5 / 02.6 are closed by the five named cases above.

### 4.3 NFR-03 — Error handling, transactions, async correctness (`error_handling`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-03.1 | Explicit per-request transaction boundary via context manager | `taskq_api.repository.session::transaction` | `test_fr06_transaction_commits_on_success_rolls_back_on_error` | unit | SPECIFIED |
| AC-03.2 | Bare `except:` and `except Exception: pass` forbidden | `taskq_api.service.runner::execute` | `test_no_bare_except_or_silent_swallow_in_src` | AST scan | SPECIFIED |
| AC-03.3 | `asyncio.CancelledError` re-raised, never swallowed | `taskq_api.service.runner::run_with_timeout` | `test_cancelled_error_propagates_under_async_runner` | unit | SPECIFIED |
| AC-03.4 | DB connection failure → `/readyz` 503 with explicit detail; no silent infinite retry | `taskq_api.api.health::readyz` | `test_readyz_returns_503_when_db_unreachable` | integration (SPEC §8 #10) | SPECIFIED |
| AC-03.5 | Per-task timeout terminates the child; no orphan processes | `taskq_api.service.runner::drain_on_shutdown` | `test_fr08_timeout_kills_child_leaving_no_orphan` | integration (SPEC §8 #25) | SPECIFIED |
| AC-03.6 | Migration failure rolls back; DB stays at the previous revision | `migrations.env::run_migrations_online` | `test_failed_migration_rolls_back_to_previous_revision` | integration | SPECIFIED |

> Coverage note (from SRS §4 NFR-03): the `error_handling` dimension scores a
> try/except ratio minus anti-patterns and does not verify AC-03.3 / 03.4 / 03.6;
> the three named cases above are the P3+ closure.

### 4.4 NFR-04 — Sensitive-data redaction (`security`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-04.1 | Lines matching the SRS redaction pattern replaced wholesale with `[REDACTED]` before any tail/log/error write | `taskq_api.errors::redact` | `test_log_redacts_sk_token_bearer_dburl` | unit | SPECIFIED |
| AC-04.2 | DB connection string (with password) absent from logs, errors, and `/v1/metrics` | `taskq_api.config::safe_settings_repr` | `test_metrics_response_omits_db_url_password` | integration (SPEC §8 #20) | SPECIFIED |
| AC-04.3 | API-key plaintext emitted only at `key create`, never persisted | `taskq_api.__main__::key_create` | `test_fr03_key_create_prints_plaintext_once` | unit | SPECIFIED |

> Open item carried from SRS §7: each alternation branch of the redaction regex
> (`sk-…` / `token=` / `Bearer ` / `postgres(ql)://`) needs an independent case at
> P3; the module anchor regex and the SRS pattern must be kept in sync.

### 4.5 NFR-05 — Documentation coverage (`documentation`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-05.1 | 100 % of public functions/classes have a docstring citing `[FR-XX]` or `[NFR-XX]` | `taskq_api.api.tasks::create_task` | `test_docstrings_cite_fr_or_nfr_marker` | AST scan | SPECIFIED |
| AC-05.2 | Every endpoint has `summary` + `description` in `/openapi.json` | `taskq_api.app::build_app` | `test_openapi_schema_has_summary_and_description_per_endpoint` | integration | SPECIFIED |

### 4.6 NFR-06 — Layering contract (`architecture_constraints`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-06.1 | `.importlinter` declares layers `api > service > repository > models`; `config` / `errors` independent | (root config file) | `test_importlinter_declares_layer_order` | config assertion | SPECIFIED |
| AC-06.2 | Forbidden contract: only `repository` may import `sqlalchemy` | `taskq_api.repository.session::get_session` | `test_sqlalchemy_import_outside_repository_blocked` | lint-imports (SPEC §8 #21) | SPECIFIED |
| AC-06.3 | `lint-imports` exits 0 | (whole `src` tree) | (lint-imports run — no test function) | tool gate (SPEC §8 #21) | SPECIFIED |
| AC-06.4 | Deleting `.importlinter`, wildcard `ignore_imports`, or contract downgrade forbidden | (root config file) | `test_importlinter_has_no_wildcard_ignore` | config assertion | SPECIFIED |

**Linked Modules**: `taskq_api.service.tasks`, `taskq_api.api.deps` (upper layers that must stay ORM-free).

### 4.7 NFR-07 — Dependency and licence compliance (`license_compliance`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-07.1 | Runtime deps pinned with `==`; transitive deps pinned in the lock file | `requirements.txt` + `requirements.lock` | `test_requirements_txt_pins_with_double_equals` / `test_requirements_lock_present_and_pinned` | config assertion | SPECIFIED |
| AC-07.2 | Allowlist MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF | (dependency tree) | `test_all_runtime_dependencies_license_in_allowlist` | scan (SPEC §8 #22) | SPECIFIED |
| AC-07.3 | Whole-tree scan `pip-licenses --format=json --with-system` | (dependency tree) | (pip-licenses run — no test function) | tool gate (SPEC §8 #22) | SPECIFIED |
| AC-07.4 | SBOM at `08-config/SBOM.json`, each entry `name / version / license / direct\|transitive` | (build artifact) | `test_sbom_markdown_present_at_08_config_path` | artifact assertion | SPECIFIED |

> No runtime module owns NFR-07 — it is carried entirely by project-side config
> artifacts (`SPEC.md` §5.3), so §5.3 below has no ownership row for it.

### 4.8 NFR-08 — Mutation testing (`mutation_testing`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-08.1 | `.methodology/harness_config.json` sets `features.mutation_testing: true` | (harness config) | `test_harness_config_enables_mutation_testing` | config assertion | SPECIFIED |
| AC-08.2 | Mutation score ≥ 70 | (mutated scope below) | (mutmut run — no test function) | tool gate (SPEC §8 #24) | SPECIFIED |
| AC-08.3 | Scope limited to `service/` and `repository/`, rationale recorded in harness config | (mutated scope below) | `test_mutation_scope_matches_configured_layers` | config assertion | SPECIFIED |

**Linked Modules**: `taskq_api.service.tasks`, `taskq_api.service.runner`, `taskq_api.service.auth`, `taskq_api.service.ratelimit`, `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.repository.key_repo`, `taskq_api.repository.rate_repo`.

### 4.9 NFR-09 — Verification authenticity, zero-skip (`test_assertion_quality`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-09.1 | No FR/NFR test may be `pytest.skip` / `skipif` / `xfail` / assertion-free | (whole test tree) | `test_no_skip_or_xfail_markers_in_test_tree` | AST scan (SPEC §8 #1) | SPECIFIED |
| AC-09.2 | `pytest 03-development/tests -q` reports 0 skipped | (whole test tree) | `test_no_skipped_tests_in_pytest_q_output` | tool gate (SPEC §8 #1) | SPECIFIED |
| AC-09.3 | Every test function has at least one `assert` | (whole test tree) | `test_every_test_function_has_at_least_one_assert` | AST scan | SPECIFIED |
| AC-09.4 | No exclusion via `--ignore` / `-k` / `--deselect` / `collect_ignore` / `testpaths` pruning | (pytest config) | `test_no_tests_excluded_via_ignore_k_deselect` | config assertion | SPECIFIED |
| AC-09.5 | FR-07's three-step migration tested on a real SQLite file; per-column round-trip comparison; no skip downgrade | `migrations.versions.v3_split_results::upgrade` | `test_migration_round_trip_against_real_sqlite_file` | integration (SPEC §8 #12) | SPECIFIED |
| AC-09.6 | `VERIFIED` set only when the test actually ran and passed | (this matrix) | `test_traceability_verified_only_after_green_run` | matrix assertion | SPECIFIED |

### 4.10 NFR-10 — Integration coverage (`integration_coverage`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-10.1 | `03-development/tests/integration/` line coverage ≥ 80 % | (integration suite) | (coverage run — no test function) | tool gate (SPEC §8 #3) | SPECIFIED |
| AC-10.2 | Driven via `httpx.AsyncClient(transport=ASGITransport(app))`; no direct handler calls | `taskq_api.app::build_app` | `test_integration_client_uses_asgi_transport` | integration | SPECIFIED |
| AC-10.3 | Must include full CRUD chain, one each of 401/403/404/409/422/429/503, migration round trip, rate-limit trigger + recovery, graceful drain | (integration suite) | `test_integration_suite_covers_required_scenarios` | suite assertion | SPECIFIED |

### 4.11 NFR-11 — Readability (`readability`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-11.1 | Project MI (LLOC-weighted) ≥ 80; per-function CC ≤ 10 | (whole `src` tree) | `test_per_function_cc_le_10` | radon | SPECIFIED |
| AC-11.2 | Each file ≤ 400 lines; each directory ≤ 15 files | (whole `src` tree) | `test_per_file_lines_le_400` / `test_per_dir_files_le_15` | static scan | SPECIFIED |
| AC-11.3 | Each API handler ≤ 40 lines; business logic sinks into `service/` | `taskq_api.api.tasks::create_task` | `test_per_api_handler_lines_le_40` | static scan | SPECIFIED |

### 4.12 NFR-12 — System verification target (`execute_verification_target`)

| AC | Requirement (SRS) | Design Element | Test Case | Verification | Status |
|----|-------------------|----------------|-----------|--------------|--------|
| AC-12.1 | `verify-system` chains upgrade head → full suite → `/healthz` + `/readyz` smoke → downgrade base + upgrade head | (Makefile target) | `test_makefile_verify_system_chains_four_steps` | config assertion | SPECIFIED |
| AC-12.2 | `make verify-system` exits 0 and prints `verify-system: PASS` | (Makefile target) | (make run — no test function) | tool gate (SPEC §8 #27) | SPECIFIED |

---

## 5. Backward Traceability

### 5.1 Test -> Requirement reverse index (representative)

Every test named in §3/§4 resolves back to exactly one owning AC. The table below
lists the cases that carry more than one requirement, i.e. the ones where a
regression has multi-requirement blast radius.

| Test case | Owning AC | Also satisfies |
|-----------|-----------|----------------|
| `test_fr03_key_compare_is_constant_time` | AC-3.2 | AC-02.3 |
| `test_fr06_transaction_commits_on_success_rolls_back_on_error` | AC-6.2 | AC-03.1 |
| `test_fr08_timeout_kills_child_leaving_no_orphan` | AC-8.3 | AC-03.5 |
| `test_fr03_key_create_prints_plaintext_once` | AC-3.3 | AC-04.3 |
| `test_fr06_relationship_load_is_explicitly_eager` | AC-6.4 | AC-01.3 |
| `test_fr07_data_move_verified_on_real_sqlite_file` | AC-7.5 | AC-09.5 |
| `test_403_body_does_not_leak_resource_existence` | AC-02.4 | AC-4.2 |
| `test_500_body_contains_no_stack_or_sql_or_path` | AC-02.5 | AC-10.3 |
| `test_readyz_returns_503_when_db_unreachable` | AC-03.4 | FR-09 endpoint row |

### 5.2 Acceptance item -> Requirement (SPEC §8, 27 items)

| # | Acceptance item | Requirement |
|---|-----------------|-------------|
| 1 | pytest -q, 0 skipped | NFR-09 |
| 2 | total coverage 100 % | NFR-09, NFR-10 |
| 3 | integration coverage ≥ 80 % | NFR-10 |
| 4 | POST /v1/tasks → 201 | FR-01 |
| 5 | POST without key → 401 | FR-03 |
| 6 | DELETE with write key → 403, no existence leak | FR-04, NFR-02 |
| 7 | GET unknown id → 404 | FR-01 |
| 8 | duplicate name → 409 | FR-01 |
| 9 | burst over limit → 429 + Retry-After | FR-05 |
| 10 | /readyz with DB stopped → 503 | FR-09, NFR-03 |
| 11 | /readyz after downgrade -1 → 503 | FR-09 |
| 12 | v3 round trip byte-identical | FR-07, NFR-09 |
| 13 | downgrade base, no leftover tables | FR-07 |
| 14 | list SQL statement count constant | FR-06, NFR-01 |
| 15 | GET by id p95 < 30 ms | NFR-01 |
| 16 | grep shell=True / eval( / exec( → 0 hits | NFR-02 |
| 17 | SQL concatenation scan → 0 hits | NFR-02 |
| 18 | api_keys holds 64-hex hashes only | FR-03, NFR-02 |
| 19 | 500 body has no stack / SQL / path | FR-10, NFR-02 |
| 20 | logs + metrics omit DB password | NFR-04 |
| 21 | lint-imports exit 0 | NFR-06 |
| 22 | pip-licenses whole tree in allowlist | NFR-07 |
| 23 | bandit 0 HIGH / 0 MEDIUM | NFR-02 |
| 24 | mutation score ≥ 70 | NFR-08 |
| 25 | graceful drain, no orphans | FR-08 |
| 26 | .env.example declares 12 TASKQ_ vars | FR-05, FR-06, FR-08 (env surface) |
| 27 | make verify-system → PASS | NFR-12 |

All 27 acceptance items resolve to at least one requirement; no orphan item.

### 5.3 Design Element -> FR/NFR Coverage Matrix

Exhaustive reverse index. Each row lists every requirement the module is cited
under in §3/§4 above.

| Design element | Requirements | Layer | Risk |
|----------------|--------------|-------|------|
| `taskq_api.api.tasks` | FR-01, FR-02, NFR-01, NFR-05, NFR-11 | api (L4) | normal |
| `taskq_api.api.deps` | FR-01, FR-03, FR-04, FR-05, NFR-02, NFR-06 | api (L4) | normal |
| `taskq_api.api.health` | FR-03, FR-09, NFR-03 | api (L4) | normal |
| `taskq_api.service.tasks` | FR-01, NFR-06, NFR-08 | service (L3) | normal |
| `taskq_api.service.runner` | FR-02, FR-08, NFR-02, NFR-03, NFR-08 | service (L3) | high |
| `taskq_api.service.auth` | FR-03, FR-04, NFR-02, NFR-08 | service (L3) | high |
| `taskq_api.service.ratelimit` | FR-05, NFR-08 | service (L3) | normal |
| `taskq_api.repository.session` | FR-06, FR-09, NFR-03, NFR-06, NFR-08 | repository (L2) | high |
| `taskq_api.repository.task_repo` | FR-01, FR-02, FR-06, NFR-01, NFR-02, NFR-08 | repository (L2) | normal |
| `taskq_api.repository.key_repo` | FR-03, NFR-08 | repository (L2) | normal |
| `taskq_api.repository.rate_repo` | FR-05, NFR-08 | repository (L2) | normal |
| `taskq_api.models.orm` | FR-02, FR-03, FR-05, FR-06, FR-07 | models (L1) | normal |
| `taskq_api.models.schemas` | FR-01 | models (L1) | normal |
| `taskq_api.errors` | FR-01, FR-03, FR-04, FR-05, FR-10, NFR-02, NFR-04 | independent | normal |
| `taskq_api.config` | FR-02, FR-05, FR-06, FR-08, NFR-04 | independent | normal |
| `taskq_api.app` | FR-04, FR-08, FR-10, NFR-02, NFR-05, NFR-10 | composition root | normal |
| `taskq_api.__main__` | FR-03, NFR-04 | management entry | normal |
| `migrations.env` | FR-07, NFR-03 | migrations | normal |
| `migrations.versions.v1_initial` | FR-07 | migrations | normal |
| `migrations.versions.v2_tags` | FR-07 | migrations | normal |
| `migrations.versions.v3_split_results` | FR-07, NFR-09 | migrations | high |

Risk column reproduces the four high-risk modules from `SPEC.md` §10, which
require per-module TDD.

Requirements with no runtime-module owner — carried by project-side config
artifacts only: NFR-07 (pinning / licence / SBOM) and NFR-12 (Makefile target).

---

## 6. Completeness Verification

| Check | Target | Actual | Status |
|-------|--------|--------|--------|
| FR -> SRS mapping | 100 % | 10 / 10 | PASS |
| NFR -> SRS mapping | 100 % | 12 / 12 | PASS |
| AC -> design-element mapping | 100 % | 88 / 88 | PASS |
| AC -> verification method | 100 % | 88 / 88 | PASS |
| AC -> named test function | — | 81 / 88 | PASS (see note) |
| SPEC §8 acceptance item -> requirement | 100 % | 27 / 27 | PASS |
| Design element -> requirement reverse index | 100 % | 21 / 21 modules | PASS |
| Design -> code mapping | 100 % | 0 / 21 (no code at P1) | DEFERRED to P3 |
| Code -> test mapping | 100 % | 0 / 21 (no code at P1) | DEFERRED to P3 |
| Test coverage | ≥ 80 % (P3 interim: ≥ 70 %) | not measurable at P1 | DEFERRED to P4 |

The two `DEFERRED to P3` rows are expected at Phase 1: design elements are
planned symbols, not files on disk. `build_traceability` fills them from the live
code/test scan at `advance-phase`.

Seven ACs have no named test function because their verification is a command's
exit status or output rather than an assertion inside a test: AC-01.4 (tool
choice, exercised by AC-01.1 / AC-01.2), AC-02.7 (`bandit`), AC-06.3
(`lint-imports`), AC-07.3 (`pip-licenses`), AC-08.2 (`mutmut`), AC-10.1
(coverage total), AC-12.2 (`make verify-system`). Each still has a verification
method and a SPEC §8 acceptance item, so `AC -> verification method` remains
88 / 88. Note that NFR-09's zero-skip rule (AC-09.1 / AC-09.2) forbids turning
any of the 81 assertion-backed cases into a skip to reach a green run.

---

## 7. ASPICE Compliance

| ASPICE capability | Evidence | Status |
|-------------------|----------|--------|
| SWE.3.B.SP1 — task-to-work-product traceability | §2 index maps every requirement to design elements and tests | PARTIAL (design elements are planned, not implemented) |
| SWE.3.B.SP2 — bidirectional traceability | §3/§4 forward + §5.1/§5.2/§5.3 backward, mutually consistent | PASS |
| SWE.3.B.SP3 — traceability consistency | §5.3 is derived from and equal to the §3/§4 citation set; verified by the `module_fr_coverage` consistency gate | PASS |

SP1 stays PARTIAL until Phase 3 produces the code that the planned design
elements name.

---

## 8. Gaps and Open Items

| ID | Gap | Owner phase |
|----|-----|-------------|
| G-1 | Design-element symbol names are planned, not implemented; must be reconciled against `02-architecture/SAD.md` | P2 |
| G-2 | `SPEC_TRACKING.md` writes `api.routes.tasks` / `api.routes.health`; canonical `SPEC.md` §6 has no `routes` package (see §1.3) | P2 |
| G-3 | Test names in §3/§4 are proposals; `TEST_INVENTORY.yaml` then `TEST_SPEC.md` are the naming authority and win on conflict | P1 / P2 |
| G-4 | NFR-04 redaction regex needs one case per alternation branch (SRS §7 open item) | P3 |
| G-5 | Async `CancelledError` scanning gap in the framework AST checker (SRS §7 open item) | P4 |
| G-6 | Coverage / code / test columns in §6 cannot be filled before code exists | P3 |

---

## 9. Update Log

| Date | Change | By |
|------|--------|----|
| 2026-08-05 | Initial population from the empty framework template. Built forward traceability for FR-01..FR-10 and NFR-01..NFR-12 (88 ACs) from `01-requirements/SRS.md`, design elements from `SPEC.md` §6 folder structure, backward indices in §5, and the exhaustive design-element reverse index in §5.3. | Agent A (requirements-engineer) |
