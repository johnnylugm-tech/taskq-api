# Phase 8 Full Execution Plan -- taskq-api

> **Version**: v2.12.0 (project plan)
> **Project**: taskq-api
> **Date**: 2026-08-12
> **Framework**: harness-methodology v2.12.0
> **Phase**: 8 - Configuration Management
> **Status**: Full version (including Phase 8 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


> **Hard Rules in Force (this plan)** — explicit reminders:
> - HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews. Never role-play A or B yourself.
> - HR-05: harness-methodology wins all conflicts — if a project decision contradicts SKILL.md / INIT / this plan, the harness wins.
> - HR-16: Trace dimension = `min(4a, 4b, 4c)` — ALL THREE must pass (G2/G3/G4 only): 4a = 100% over IN_PROGRESS+VERIFIED FRs, 4b = TEST_SPEC→test coverage (60/80/90% at G2/G3/G4), 4c = NFR→test coverage (60/80/90% at G2/G3/G4, NFR-99 placeholder excluded). `gate_score_overrides` is a **threshold floor (raises, not lowers)** per `sab_parser.derive_gate_score_overrides` — cannot bypass a failing trace dim. Remediation: fix code/FRs/tests to pass, accept gate block, or escalate to human. No automated override.
> - HR-17: NEVER modify files inside `harness/` — debug the framework, never hot-patch the submodule.

---

## Phase 8 Tasks: Configuration Management

### Phase 8 Overview
Phase 8 establishes a complete configuration management system ensuring traceability.
Each FR gets a Gate 1 config-aware re-evaluation (CHECKPOINT). No harness run-gate — P8 cleared by Gate 4. However, advance-phase still enforces TDD-PRECHECK (gitleaks + ruff + mypy + pytest 100% + D4 spec-coverage ≥90%) before FSM transition. Mutation testing is gated per-FR at Gate 1, not re-verified here.

> If configuration changes require code modifications to any FR, run full TDD: `run-fr-step --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1. Crash recovery (`resume-fr-phase`) auto-detects code changes and switches from GATE1-DELTA to full TDD when needed.

> **Crash Recovery**: `python3 harness_cli.py resume-fr-phase --phase 8 --project .`
> prints the next pending step. Each `run-fr-step` auto-pushes to GitHub on completion.
> Per-FR GATE1-DELTA auto-pushes on completion; when code-change triggers full TDD, TDD-RED → GREEN → IMPROVE → GATE1 each push immediately (idempotent on re-run).
> At milestones, `HANDOVER.md` is written with phase/FR/status summary.

> **Checkpoint Index**:
> - MILESTONE: P8 exit push (config records complete) → **HANDOVER.md**

### Entry Gate Verification

- **[ENTRY-CHECK]** Gate 4 PASS (P6 exit — P7 has no exit gate, P7 completed stands between):
  Proof: .methodology/quality_manifest.json records Gate 4 PASS from P6.
  If NOT confirmed: verify Phase 6 Gate PASS is recorded in quality_manifest.json and confirm all intervening phases (P7–P7) completed their tasks.

### Pre-Phase Preflight

- **[PREFLIGHT]** Run phase hooks (FSM, Kill-Switch, Drift):
  ```bash
  python3 harness_cli.py run-phase --phase 8 --project .
  ```
  If FAILED: fix FSM/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 8` full output.
  Human fix → re-run `run-phase --phase 8 --project .` → PASS required before continuing.
  **Reliability lint fix** (P4+ blocking — if `preflight_reliability_lint` reports findings):
  Fix flagged patterns before continuing: `subprocess.run/Popen` without `timeout=`,
  `tempfile.mkstemp` outside try/finally, `os.path.exists` before open/unlink (TOCTOU),
  `time.sleep` inside async def. Re-run `run-phase` after each fix.
  **Config liveness fix** (P4+ blocking — if `preflight_config_liveness` reports orphans):
  Env keys read in code but absent from `.env.example`/`docker-compose*.yml`/`deployment/`.
  Add the key to the declaration source (or fix the typo). Re-run `run-phase` after each fix.
  **Attestation fix** (P5+ — if ASPICE Traceability preflight shows `attestation: missing` or `mismatch`):
  ```bash
  python3 harness_cli.py build-trace-attestation --project . --write
  git add .methodology/trace/attestation.json
  git commit -m 'trace: regenerate attestation'
  ```
  Re-run `run-phase` to confirm `Attestation: clean` before continuing.

- **[V2.9.1-B.1-HANDOFF]** Cross-deliverable dependency check (P7 → P8) — v2.9.1 B.1. **Must PASS** before any Phase 8 work begins:
  ```bash
  python3 harness_cli.py validate-handoff --from-phase 7 --project .
  ```
  > Verifies P7 deliverables are present and well-formed (e.g. P1 TEST_INVENTORY.yaml non-empty + covers all FRs; P2 TEST_SPEC.md has parseable named test cases; P3 all FRs have per-FR Gate 1 sentinels; P4 TEST_RESULTS.md non-trivial; P5 VERIFICATION_REPORT.md non-trivial; P6 06-quality/QUALITY_REPORT.md + RELEASE_NOTES.md + FINAL_SIGN_OFF.md + .methodology/quality_manifest.json gate_results.gate4.quality_complete=true; P7 07-risk/RISK_REGISTER.md + RISK_MITIGATION_PLANS.md + RISK_STATUS_REPORT.md).
  > If exit 1: read the error list, fix the upstream deliverable, re-run until exit 0. Do NOT proceed with Phase 8 work on a BLOCKED handoff.

- **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 8 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 8 --project . --overwrite`

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 8 --project . --json \
  > .sessi-work/phase8_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules`, and `lessons` from current project state.
> **IMPORTANT (Direction C)**: Please carefully review the `lessons` (past failure modes) and DO NOT repeat them.
> All `{FR-ID}` references in tasks below come from this file.

### FR Tasks — Expanded at Execution Time

- **[ENV-CHECK]** Run ONCE before the FR loop — `GATE1`/`GATE1-DELTA` preflight requires `.sessi-work/env_check_result.json`:
  ```bash
  python3 harness_cli.py run-env-check --phase 8 --project .
  # evaluate inline → write .sessi-work/env_check_result.json →
  python3 harness_cli.py finalize-env-check --phase 8 --project .
  ```
  > Without this, every `run-fr-step --step GATE1-DELTA` blocks on 'env_check_result.json not found'.

> Read `fr_ids` from `.sessi-work/phase8_ctx.json`.
> For each `{FR-ID}` in the list, execute the template below:

---
**{FR-ID} — {FR-TITLE from fr_details}**

- **[ORCH-GATE1-DELTA]** `run-fr-step --phase 8 --fr-id {FR-ID} --step GATE1-DELTA --project .`
> Crash recovery: `resume-fr-phase` auto-detects code changes → switches to full TDD if needed.
> **Auto-skip**: if NO FR's code changed since its last Gate 1 PASS, `advance-phase --completed 8`
> treats this entire DELTA loop as satisfied automatically — you may skip the per-FR steps.
> Only FRs whose code actually changed need a re-evaluation.
>
> **GATE1-DELTA outcomes:**
> - CASE 1 PASS:    Gate 1 PASS → continue to next {FR-ID}
> - CASE 2 FAIL:    Gate 1 FAIL → full TDD auto-triggered by crash recovery:
>   `run-fr-step --phase 8 --fr-id {FR-ID} --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1
> - CASE 3 BLOCKED: 3 TDD rounds still failing → escalate to human.
>   Provide: last Gate 1 output + pytest failure log.

- **[ORCH-POST]** After GATE1-DELTA PASS — orchestrator runs directly:
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 40.0 --fr-id {FR-ID}
  ```

---

- **[ORCH-POST-ONCE]** After the FR loop completes — project-wide, runs ONCE
  (takes no `--fr-id`; re-running adds nothing):
  ```bash
  python3 harness_cli.py amend-sab --project .
  ```

### P8 Configuration Records Generation

> Generate config deliverables ONCE before push-milestone (orchestrator runs directly).

> **Baseline source** (harness commits `4738542` + `51bd4a8`):
> `CONFIG_RECORDS.md` and `RELEASE_CHECKLIST.md` are deterministically generated
> by `scripts/phase8_doc_gen.py` during the P7→P8 advance-phase hook (see
> `phase7_plan.md` §Auto-trigger on P7→P8 advance). P8 phase work is therefore
> **review and append**, not regenerate:
>
> 1. Read the framework-generated baseline in `08-config/`
> 2. Flag any missing sections the generator could not derive
> 3. Append human-only context (ownership, on-call rotation, runbook links,
>    anything not derivable from `state.json` / `quality_manifest.json` / git)
> 4. Do NOT overwrite the framework-generated version — that would break
>    determinism (byte-equal across runs) for downstream consumers

- **[CONFIG-RECORDS]** Review + append `08-config/CONFIG_RECORDS.md`:
  - Framework baseline already contains: env var inventory, source-of-truth
    module references, feature flags derived from `harness_config.json`
  - Human-only additions: ownership per config item, secret rotation cadence,
    access audit log reference
  - Reference: `03-development/src/` module configs + any `.env.example` or `settings.py`
- **[RELEASE-CHECKLIST]** Review + append `08-config/RELEASE_CHECKLIST.md`:
  - Framework baseline already contains: Gate 4 PASS proof, quality_manifest
    composite score, FR coverage summary, git tag/hash
  - Human-only additions: deployment runbook URL, rollback owner + on-call,
    post-release monitoring dashboard, customer comms template

### P8 Archive — REQUIRED before push-milestone (CI p8-archive-check)

- **[P8-ARCHIVE]** Create `.methodology-archive/` directory (required for CI `p8-archive-check`):
  ```bash
  mkdir -p .methodology-archive
  cp -r .methodology/ .methodology-archive/
  ```
  > Must run BEFORE `push-milestone --type p8`; `_validate_p8_completion()` in push-milestone auto-verifies.
  > CI job `p8-archive-check` also validates this directory on push to main.

### P8 Milestone Push (10-Push Strategy ⑩)

- **PUSH ⑩ — P8 exit** (after config records are complete):
  ```bash
  python3 harness_cli.py push-milestone --type p8 --project .
  ```
  > Writes HANDOVER.md + commits + pushes. Development pipeline complete.

- **[P8→P9]** Enter maintenance mode (steady state — bug fixes and
  feature changes continue as Change Requests):
  ```bash
  python3 harness_cli.py advance-phase --completed 8 --project .
  ```
  > Phase 9 is re-entrant and never exits; work is ticket-driven
  > (`cr-open` / `cr-close`, see phase9_plan.md).
  > **Sync**: `advance-phase` only commits the handover locally. The workflow
  > orchestrator for this phase runs a separate `git push origin main` immediately
  > after to publish that commit to origin, and also confirms any pending Gate 4
  > `harness-v*` tag has been pushed.

### Phase 8 Deliverables
- `CONFIG_RECORDS.md` - Configuration records
- `RELEASE_CHECKLIST.md` - Release checklist
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)
- Gate 1 PASS for every FR

- **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase

- **[TDD-PRECHECK]** P8 completion checklist (final quality gate before archive):
  - diagnostic script check: orphan diagnostic scripts (e.g. `_diag_xxx.py`) at repo root will BLOCK (exit 21)
  - secrets scanning: `gitleaks detect --source .` (exit 20) — whole-repo, runs before linting
  - linting: `ruff check .` (exit 18) — fix violations before advancing
  - type safety: `python3 -m mypy . --ignore-missing-imports` (exit 19)
  - `pytest --tb=short -q --cov=03-development/src --cov-fail-under=100` (exit 9)
  - `python3 harness_cli.py spec-coverage-check --project . --threshold 90.0` (exit 10, D4 unified v2.6)
  > For genuinely untestable lines add: `# pragma: no cover` (requires justification comment).

### 🎉 Pipeline Complete

- All 8 phases complete. Archive `.methodology/` for the audit trail.
