# Phase 5 Full Execution Plan -- taskq-api

> **Version**: v2.12.0 (project plan)
> **Project**: taskq-api
> **Date**: 2026-08-05
> **Framework**: harness-methodology v2.12.0
> **Phase**: 5 - Verification & Delivery
> **Status**: Full version (including Phase 5 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


> **Hard Rules in Force (this plan)** — explicit reminders:
> - HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews. Never role-play A or B yourself.
> - HR-05: harness-methodology wins all conflicts — if a project decision contradicts SKILL.md / INIT / this plan, the harness wins.
> - HR-16: Trace dimension = `min(4a, 4b, 4c)` — ALL THREE must pass (G2/G3/G4 only): 4a = 100% over IN_PROGRESS+VERIFIED FRs, 4b = TEST_SPEC→test coverage (60/80/90% at G2/G3/G4), 4c = NFR→test coverage (60/80/90% at G2/G3/G4, NFR-99 placeholder excluded). `gate_score_overrides` is a **threshold floor (raises, not lowers)** per `sab_parser.derive_gate_score_overrides` — cannot bypass a failing trace dim. Remediation: fix code/FRs/tests to pass, accept gate block, or escalate to human. No automated override.
> - HR-17: NEVER modify files inside `harness/` — debug the framework, never hot-patch the submodule.

---

## Phase 5 Tasks: Verification & Delivery

### Phase 5 Overview
Phase 5 verifies the system against test results, ensuring all FRs meet acceptance criteria.
Each FR ends with a Gate 1 re-evaluation (CHECKPOINT). No harness run-gate — P5 was cleared by Gate 3 at P4 exit. However, advance-phase still enforces TDD-PRECHECK (gitleaks + ruff + mypy + pytest 100% + D4 spec-coverage ≥80%) before FSM transition. Mutation testing is gated per-FR at Gate 1, not re-verified here.

> If code changes are needed for any FR (e.g., bug fixes found during verification), run full TDD: `run-fr-step --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1. Crash recovery (`resume-fr-phase`) auto-detects code changes and switches from GATE1-DELTA to full TDD when needed.

> **Crash Recovery**: `python3 harness_cli.py resume-fr-phase --phase 5 --project .`
> prints the next pending step. Each `run-fr-step` auto-pushes to GitHub on completion.
> Per-FR GATE1-DELTA auto-pushes on completion; when code-change triggers full TDD, TDD-RED → GREEN → IMPROVE → GATE1 each push immediately (idempotent on re-run).
> At milestones, `HANDOVER.md` is written with phase/FR/status summary.

> **Checkpoint Index**:
> - MILESTONE: P5-baseline push (VERIFICATION_REPORT.md generated) → **HANDOVER.md**

### Entry Gate Verification

- **[ENTRY-CHECK]** Gate 3 PASS:
  Proof: .methodology/quality_manifest.json records Gate 3 PASS from P4.
  If NOT confirmed: verify Phase 4 Gate PASS is recorded in quality_manifest.json and confirm all intervening phases (P5–P4) completed their tasks.

### Pre-Phase Preflight

- **[PREFLIGHT]** Run phase hooks (FSM, Kill-Switch, Drift):
  ```bash
  python3 harness_cli.py run-phase --phase 5 --project .
  ```
  If FAILED: fix FSM/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 5` full output.
  Human fix → re-run `run-phase --phase 5 --project .` → PASS required before continuing.
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

- **[V2.9.1-B.1-HANDOFF]** Cross-deliverable dependency check (P4 → P5) — v2.9.1 B.1. **Must PASS** before any Phase 5 work begins:
  ```bash
  python3 harness_cli.py validate-handoff --from-phase 4 --project .
  ```
  > Verifies P4 deliverables are present and well-formed (e.g. P1 TEST_INVENTORY.yaml non-empty + covers all FRs; P2 TEST_SPEC.md has parseable named test cases; P3 all FRs have per-FR Gate 1 sentinels; P4 TEST_RESULTS.md non-trivial; P5 VERIFICATION_REPORT.md non-trivial; P6 06-quality/QUALITY_REPORT.md + RELEASE_NOTES.md + FINAL_SIGN_OFF.md + .methodology/quality_manifest.json gate_results.gate4.quality_complete=true; P7 07-risk/RISK_REGISTER.md + RISK_MITIGATION_PLANS.md + RISK_STATUS_REPORT.md).
  > If exit 1: read the error list, fix the upstream deliverable, re-run until exit 0. Do NOT proceed with Phase 5 work on a BLOCKED handoff.

- **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 5 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 5 --project . --overwrite`

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 5 --project . --json \
  > .sessi-work/phase5_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules`, and `lessons` from current project state.
> **IMPORTANT (Direction C)**: Please carefully review the `lessons` (past failure modes) and DO NOT repeat them.
> All `{FR-ID}` references in tasks below come from this file.

### FR Tasks — Expanded at Execution Time

- **[ENV-CHECK]** Run ONCE before the FR loop — `GATE1`/`GATE1-DELTA` preflight requires `.sessi-work/env_check_result.json`:
  ```bash
  python3 harness_cli.py run-env-check --phase 5 --project .
  # evaluate inline → write .sessi-work/env_check_result.json →
  python3 harness_cli.py finalize-env-check --phase 5 --project .
  ```
  > Without this, every `run-fr-step --step GATE1-DELTA` blocks on 'env_check_result.json not found'.

> Read `fr_ids` from `.sessi-work/phase5_ctx.json`.
> For each `{FR-ID}` in the list, execute the template below:

---
**{FR-ID} — {FR-TITLE from fr_details}**

- **[ORCH-GATE1-DELTA]** `run-fr-step --phase 5 --fr-id {FR-ID} --step GATE1-DELTA --project .`
> Crash recovery: `resume-fr-phase` auto-detects code changes → switches to full TDD if needed.
> **Auto-skip**: if NO FR's code changed since its last Gate 1 PASS, `advance-phase --completed 5`
> treats this entire DELTA loop as satisfied automatically — you may skip the per-FR steps.
> Only FRs whose code actually changed need a re-evaluation.
>
> **GATE1-DELTA outcomes:**
> - CASE 1 PASS:    Gate 1 PASS → continue to next {FR-ID}
> - CASE 2 FAIL:    Gate 1 FAIL → full TDD auto-triggered by crash recovery:
>   `run-fr-step --phase 5 --fr-id {FR-ID} --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1
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

### P5 System Verification

- **[BASELINE]** Generate `05-verification/BASELINE.md` (system state snapshot):
  - Document: current version, test results summary, coverage %, Gate 3 composite score
  - Reference: `04-testing/TEST_RESULTS.md` and `03-development/src/` module list
  - Structure: 7 `##` sections per `templates/BASELINE.md` (Overview, Functional,
    Quality, Performance, Issue Log, Change Log, Acceptance Sign-off) — audit-phase
    C5 counts H2 headings and warns below 7
- **[VERIFY-REPORT]** Generate `05-verification/VERIFICATION_REPORT.md`:
  - For each FR: verification status, acceptance criteria result (PASS/FAIL), evidence
  - Include: test coverage %, mutation score, deferred issues from Gate 3
  - Certify: all Gate 3 open issues addressed or deferred with justification
- Re-run integration tests: `pytest tests/integration/ -q` (or equivalent per NFRs)
- Confirm performance NFRs met: review benchmark entries in `04-testing/TEST_RESULTS.md`
- Re-run security scan clean: `bandit -r 03-development/src/ -ll` + `gitleaks detect`

### P5 Milestone Push (10-Push Strategy ⑦)

- **PUSH ⑦ — P5-baseline** (after VERIFICATION_REPORT.md is generated):
  ```bash
  python3 harness_cli.py push-milestone --type p5-baseline --project .
  ```
  > Writes HANDOVER.md + commits + pushes.

### Phase 5 Deliverables
- `05-verification/BASELINE.md` - System baseline
- `05-verification/VERIFICATION_REPORT.md` - Verification report
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)
- Gate 1 PASS for every FR

### Phase 5 → Phase 6: Quality Assurance

- **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase
  > **FAIL** → check `phase_truth_verifier` output in `.sessi-work/`
  >   → identify which phase link or gate artifact failed
  >   → fix artifacts → re-run `advance-phase`
  >   → If 3 consecutive failures: escalate to human with `phase_truth_verifier` log

- **[D4-GAP WARNING]** Gate 4 (next phase) requires spec-coverage ≥ 90% but current advance threshold is 80%.
  > Close this gap NOW to avoid a surprise Gate 4 D4 block.
  > Check: `python3 harness_cli.py spec-coverage-check --project . --threshold 90.0`
  > If below 90%: add missing test implementations before advancing to Phase 6.

- **[TDD-PRECHECK]** Verify TDD checks pass — advance-phase enforces:
  - diagnostic script check: orphan diagnostic scripts (e.g. `_diag_xxx.py`) at repo root will BLOCK (exit 21)
  - secrets scanning: `gitleaks detect --source .` (exit 20) — whole-repo, runs before linting
  - linting: `ruff check .` (exit 18) — fix violations before advancing
  - type safety: `python3 -m mypy . --ignore-missing-imports` (exit 19)
    > Note: advance-phase uses mypy; Gate scoring uses pyright. Both must pass.
  - `pytest --tb=short -q --cov=03-development/src --cov-fail-under=100` (exit 9)
  - `python3 harness_cli.py spec-coverage-check --project . --threshold 80.0` (exit 10, D4 unified v2.6)
  > For genuinely untestable lines add: `# pragma: no cover` (requires justification comment).

- Advance FSM to Phase 6 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 5 --project .
  ```
  > **Note**: `advance-phase` will automatically check for harness submodule drift.
  > If it prints a warning that you are behind `origin/main`, it is non-blocking and for your information only.
  > **Sync**: `advance-phase` only commits the handover locally. The workflow orchestrator
  > for this phase runs a separate `git push origin main` immediately after to publish
  > that commit to origin.
- Confirm `HANDOVER.md` reflects Phase 6 entry (`P6-entry` checkpoint, correct plan path)
- Open `phase6_plan.md` and follow from the top.
- If session crashes during Phase 6: read `HANDOVER.md` or run `generate-next-plan`
