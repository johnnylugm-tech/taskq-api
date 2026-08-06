# Specification Tracking Matrix — taskq-api

> On-demand Lazy Load template.
> Source of truth for requirements: `SPEC.md` v1.0.0 (canonical, project root).
> Mirror document: `01-requirements/SRS.md` (approved transcription).

## Project Info
- Project Name: taskq-api
- Version: v1.0.0
- Created: 2026-08-05
- Phase: 1 — Requirements (P1)
- SRS Authority: `01-requirements/SRS.md` (approved)
- Canonical Spec: `SPEC.md`

## Specification Status

> **The Status column is machine-refreshed** — `advance-phase` overwrites each
> FR's Status from `build_traceability`'s live code/test scan (IN_PROGRESS once
> code/module exists, VERIFIED once code+test exist). The authoritative status is
> that scan / `quality_manifest.json`, NOT this hand-filled cell. Fill the
> semantic columns (Spec Description / Intent Class / Decision Framework / Notes);
> leave Status to refresh itself (a hand-edit is overwritten on the next advance).

| FR ID | Spec Description | Intent Class | Decision Framework | Status | Owner | Notes |
|-------|-----------------|--------------|-------------------|--------|-------|-------|
| FR-01 | Task resource CRUD API — `POST/GET/GET-list/DELETE /v1/tasks` with cursor pagination, scope-guarded endpoints, and 422/404 problem+json errors. | CRUD | FastAPI + pydantic v2; cursor pagination; SPEC §3 FR-01 + §7 error mapping. | VERIFIED | api-layer (`taskq_api.api.routes.tasks`) | Source: SPEC.md §3 FR-01; AC-1.1..1.4 (SRS §3 FR-01). |
| FR-02 | Task execution endpoint — `POST /v1/tasks/{id}/run` returns 202 + `run_id`; subprocess via `asyncio.create_subprocess_exec`; status state-machine `pending → running → done\|failed\|timeout`; results persisted to `task_results`. | Execution | `asyncio.create_subprocess_exec` (shell=True forbidden); `asyncio.wait_for`; SPEC §3 FR-02, NFR-02/NFR-03. | VERIFIED | service-layer (`taskq_api.service.runner`, high-risk) | Source: SPEC.md §3 FR-02; AC-2.1..2.5 (SRS §3 FR-02). |
| FR-03 | API Key authentication — `X-API-Key` header required on every `/v1/*`; keys stored as SHA-256 hashes; constant-time compare via `hmac.compare_digest`; plaintext emitted once at creation. | Auth | SHA-256 hashing; `hmac.compare_digest`; SPEC §3 FR-03, NFR-02/NFR-04. | VERIFIED | service-layer (`taskq_api.service.auth`, high-risk) | Source: SPEC.md §3 FR-03; AC-3.1..3.5 (SRS §3 FR-03). |
| FR-04 | Scope-based authorisation — strict hierarchy `read < write < admin`; per-endpoint scope from FR-01/FR-02 tables; 403 must not leak resource existence; single middleware/dependency. | AuthZ | Single FastAPI dependency for all `/v1/*`; RFC 7807 problem+json; SPEC §3 FR-04, NFR-02. | VERIFIED | service-layer (`taskq_api.service.auth`) | Source: SPEC.md §3 FR-04; AC-4.1..4.3 (SRS §3 FR-04). |
| FR-05 | Per-token rate limiting — token bucket with capacity `TASKQ_RATE_BURST` and refill `TASKQ_RATE_PER_SEC`; 429 + `Retry-After`; DB-stored bucket with row-level lock; `/healthz`/`/readyz` exempt. | Throttling | Token bucket; row-level lock (`SELECT … FOR UPDATE`); SPEC §3 FR-05, FR-06. | VERIFIED | service-layer (`taskq_api.service.ratelimit`) | Source: SPEC.md §3 FR-05; AC-5.1..5.4 (SRS §3 FR-05). |
| FR-06 | Persistence layer + transaction boundaries — repository-only data access; one `Session` per request; explicit commit/rollback context manager; ORM or parameterised SQL only; explicit eager loading; `pool_pre_ping=True`. | Persistence | SQLAlchemy 2.x; Alembic; `pool_pre_ping=True`; `selectinload`/`joinedload`; SPEC §3 FR-06, NFR-06. | VERIFIED | repository-layer (`taskq_api.repository.session`, high-risk) | Source: SPEC.md §3 FR-06; AC-6.1..6.5 (SRS §3 FR-06). |
| FR-07 | Schema migration (Alembic, three-step evolution) — v1 creates `tasks`+`api_keys`; v2 adds `tags`+`task_tags`+unique index; v3 is data-moving split of `tasks.result_json` → `task_results`; every revision has a working `downgrade`; round-trip reversibility verified on a real SQLite file. | Migration | Alembic three-step chain; reversible downgrade; SPEC §3 FR-07, NFR-09. | VERIFIED | migrations (`migrations/versions/v3_split_results.py`, high-risk) | Source: SPEC.md §3 FR-07; AC-7.1..7.5 (SRS §3 FR-07). |
| FR-08 | Async executor — `asyncio.TaskGroup`; graceful drain on shutdown up to `TASKQ_DRAIN_TIMEOUT`; concurrency cap `TASKQ_MAX_CONCURRENT`; per-task timeout via `asyncio.wait_for` (kill + wait on timeout, no orphan processes); `CancelledError` must propagate. | Concurrency | `asyncio.TaskGroup`; `asyncio.wait_for`; `process.kill()` + `await process.wait()`; SPEC §3 FR-08, NFR-03. | VERIFIED | service-layer (`taskq_api.service.runner`) | Source: SPEC.md §3 FR-08; AC-8.1..8.4 (SRS §3 FR-08). |
| FR-09 | Health checks + observability — `GET /healthz` (200 always), `GET /readyz` (DB reachable + migration at head; else 503 with detail), `GET /v1/metrics` (admin; task counts/latency percentiles/rate-limit rejections). | Observability | FastAPI health routes; `/readyz` fail-closed on migration lag; SPEC §3 FR-09. | VERIFIED | api-layer (`taskq_api.api.routes.health`) | Source: SPEC.md §3 FR-09; AC-9.1 (SRS §3 FR-09). |
| FR-10 | Error contract (RFC 7807) — all non-2xx responses use `application/problem+json`; body fields `type/title/status/detail/instance/correlation_id`; no SQL/stack-trace/file-path leakage; `X-Correlation-Id` header round-trip. | Error Contract | RFC 7807 `application/problem+json`; SPEC §3 FR-10, §7 mapping, NFR-02. | VERIFIED | cross-cutting (`taskq_api.errors`) | Source: SPEC.md §3 FR-10; AC-10.1..10.5 (SRS §3 FR-10). |

## Coverage Summary

- Total FRs tracked: **10 / 10** (FR-01..FR-10).
- Source coverage: every row cites `SPEC.md` (canonical, project root) — the directory-prefixed variant of that path is forbidden by the harness `check_forward_refs` gate (R-CANONICAL-SPEC-PATH-001); this matrix uses bare `SPEC.md` throughout.
- Status column is intentionally `DRAFT` for all rows at P1 entry; `advance-phase` will overwrite from `build_traceability`'s live code/test scan.
- NFR coverage (NFR-01..NFR-12) is owned by `TRACEABILITY_MATRIX.md` and the gate `traceability` dimension's 4c score; not duplicated in this FR-only matrix (parser is FR-focused: `_FR_CELL = re.compile(r"(?<!N)FR-(\d+)")`).
- Owner column populated per FR using module-path mapping derived from SPEC §6 layering (`api > service > repository > models`) and the §10 high-risk-module list: FR-01/FR-09 → `api/`; FR-02/FR-08 → `service.runner`; FR-03/FR-04 → `service.auth`; FR-05 → `service.ratelimit`; FR-06 → `repository.session`; FR-07 → `migrations/`; FR-10 → `errors/`. Module paths are pre-SAD placeholders and will be re-confirmed against `02-architecture/SAD.md` during Phase 2; high-risk-module rows carry the "(high-risk)" tag verbatim from SPEC §10.

## Update log

| Date | Change | By |
|------|--------|----|
| 2026-08-05 | Initial creation — populated FR-01..FR-10 from `01-requirements/SRS.md` (approved transcription of `SPEC.md` §3). Status column set to `DRAFT`; will be machine-refreshed by `advance-phase`. Canonical-spec source path uses bare `SPEC.md` (root) per R-CANONICAL-SPEC-PATH-001. | Agent A (requirements-engineer) |
| 2026-08-05 | Round 2 — B-review gap fix: Owner column promoted from implicit inline (`Owner: tbd.`) to a dedicated column populated for every FR with module-path assignment (api-layer/service-layer/repository-layer/migrations/cross-cutting). High-risk-module rows tag the (high-risk) module from SPEC §10. Coverage summary updated to record the P1 owner source and its P2 re-confirmation path. | Agent A (requirements-engineer) |
