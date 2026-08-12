# Phase 9 Full Execution Plan -- taskq-api

> **Version**: v2.12.0 (project plan)
> **Project**: taskq-api
> **Date**: 2026-08-12
> **Framework**: harness-methodology v2.12.0
> **Phase**: 9 - Maintenance
> **Status**: Full version (including Phase 9 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


> **Hard Rules in Force (this plan)** — explicit reminders:
> - HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews. Never role-play A or B yourself.
> - HR-05: harness-methodology wins all conflicts — if a project decision contradicts SKILL.md / INIT / this plan, the harness wins.
> - HR-16: Trace dimension = `min(4a, 4b, 4c)` — ALL THREE must pass (G2/G3/G4 only): 4a = 100% over IN_PROGRESS+VERIFIED FRs, 4b = TEST_SPEC→test coverage (60/80/90% at G2/G3/G4), 4c = NFR→test coverage (60/80/90% at G2/G3/G4, NFR-99 placeholder excluded). `gate_score_overrides` is a **threshold floor (raises, not lowers)** per `sab_parser.derive_gate_score_overrides` — cannot bypass a failing trace dim. Remediation: fix code/FRs/tests to pass, accept gate block, or escalate to human. No automated override.
> - HR-17: NEVER modify files inside `harness/` — debug the framework, never hot-patch the submodule.

---

## Phase 9 Tasks: Maintenance (Change Request loop)

### Phase 9 Overview
Phase 9 is a RE-ENTRANT STEADY STATE — it never exits (`advance-phase --completed 9` is always BLOCKED). All work is ticket-driven: CR-BUG (ASPICE SUP.9 problem resolution) and CR-FEAT (ASPICE SUP.10 change request management). Every change re-enters the existing traceability chain; nothing bypasses the phase folders.

### Entry Gate Verification

- **[ENTRY-CHECK]** Gate 4 PASS + P8 completed (Maintenance entry via advance-phase --completed 8):
  Proof: .methodology/quality_manifest.json records Gate 4 PASS from P6.
  If NOT confirmed: verify Phase 6 Gate PASS is recorded in quality_manifest.json and confirm all intervening phases (P7–P8) completed their tasks.

### Pre-Phase Preflight

- **[PREFLIGHT]** Run phase hooks (FSM, Kill-Switch, Drift):
  ```bash
  python3 harness_cli.py run-phase --phase 9 --project .
  ```
  If FAILED: fix FSM/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 9` full output.
  Human fix → re-run `run-phase --phase 9 --project .` → PASS required before continuing.
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

- **[V2.9.1-B.1-HANDOFF]** Cross-deliverable dependency check (P8 → P9) — v2.9.1 B.1. **Must PASS** before any Phase 9 work begins:
  ```bash
  python3 harness_cli.py validate-handoff --from-phase 8 --project .
  ```
  > Verifies P8 deliverables are present and well-formed (e.g. P1 TEST_INVENTORY.yaml non-empty + covers all FRs; P2 TEST_SPEC.md has parseable named test cases; P3 all FRs have per-FR Gate 1 sentinels; P4 TEST_RESULTS.md non-trivial; P5 VERIFICATION_REPORT.md non-trivial; P6 06-quality/QUALITY_REPORT.md + RELEASE_NOTES.md + FINAL_SIGN_OFF.md + .methodology/quality_manifest.json gate_results.gate4.quality_complete=true; P7 07-risk/RISK_REGISTER.md + RISK_MITIGATION_PLANS.md + RISK_STATUS_REPORT.md).
  > If exit 1: read the error list, fix the upstream deliverable, re-run until exit 0. Do NOT proceed with Phase 9 work on a BLOCKED handoff.

- **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 9 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 9 --project . --overwrite`

### CR-BUG workflow (SUP.9 — bug fix)

- **[CR-OPEN]** `python3 harness_cli.py cr-open --type bug --title '...' --severity high --project .`
- **[REPRO-FIRST]** Write a FAILING repro test BEFORE touching code; record it:
  `cr-update --cr CR-NN --set repro_test=tests/test_crNN_repro.py`
- **[ROOT-CAUSE]** Document root cause: `cr-update --cr CR-NN --set root_cause='...'`
  then advance: `--status ANALYZED` → `--status APPROVED` → `--status IN_PROGRESS`
- **[FIX]** Fix code (keep `[FR-XX]` annotations). If an SRS acceptance
  criterion was itself wrong, correct SRS.md and note it in impact_analysis.
- **[VERIFY]** Repro test green + full suite green; re-run Gate 1 on touched FRs:
  ```bash
  python3 harness_cli.py run-gate --gate 1 --fr-id FR-XX --phase 9 --project .
  python3 harness_cli.py finalize-gate --gate 1 --fr-id FR-XX --phase 9 --project .
  ```
  Untouched FRs: `run-gate --gate 1 --fr-id FR-YY --phase 9 --delta` (regression check)
- **[EVIDENCE]** `cr-update --cr CR-NN --set affected_frs=FR-XX --set resolution.fix_commit=<sha> --status VERIFIED`

### CR-FEAT workflow (SUP.10 — feature add/change)

- **[CR-OPEN]** `python3 harness_cli.py cr-open --type feat --title '...' --project .`
- **[IMPACT]** Record impact + FR IDs: `cr-update --cr CR-NN --set affected_frs=FR-XX
  --set impact_analysis.srs=true --set impact_analysis.sad=true --set impact_analysis.test_spec=true`
- **[APPROVAL]** SUP.10 decision: `cr-update --cr CR-NN --set approval.approved_by=<name>
  --set approval.justification='...'` then `--status ANALYZED` → `APPROVED` → `IN_PROGRESS`
- **[SPEC-WRITEBACK]** Update the FROZEN artifacts in place (never around them):
  1. `01-requirements/SRS.md` — add/update `### FR-XX:` section
  2. `02-architecture/SAD.md` — FR→module table row (new module → `amend-sab`)
  3. `02-architecture/TEST_SPEC.md` — FR test section; `TEST_INVENTORY.yaml` entry
- **[TDD]** Implement via run-fr-step (same discipline as P3):
  ```bash
  python3 harness_cli.py run-fr-step --step TDD-RED --fr-id FR-XX --phase 9 --project .
  # → TDD-GREEN → TDD-IMPROVE → GATE1
  ```
- **[EVIDENCE]** `cr-update --cr CR-NN --set resolution.fix_commit=<sha> --status VERIFIED`

### CR closure (both types — fail-closed re-entry checklist)

- **[ATTESTATION]** Rebuild the git-anchored trace attestation after artifact changes:
  ```bash
  python3 harness_cli.py build-trace-attestation --project . --write
  ```
- **[CR-CLOSE]** Full checklist (ticket evidence + Gate 1 per affected FR +
  attestation verify + spec/SAD drift). Any failure prints the missing items:
  ```bash
  python3 harness_cli.py cr-close --cr CR-NN --project .
  ```
- **[PUSH]** One milestone push per closed CR:
  ```bash
  python3 harness_cli.py push-milestone --type cr-close --cr CR-NN --project .
  ```

### Phase 9 Deliverables
- `09-maintenance/MAINTENANCE_LOG.md` — CR index (auto-appended by cr-close)
- `.methodology/change_requests/CR-NN.json` — ticket state (machine)
- Gate 1 PASS for every CR-touched FR; attestation clean after every close
