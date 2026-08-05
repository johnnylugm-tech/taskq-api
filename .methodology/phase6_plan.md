# Phase 6 Full Execution Plan -- taskq-api

> **Version**: v2.12.0 (project plan)
> **Project**: taskq-api
> **Date**: 2026-08-05
> **Framework**: harness-methodology v2.12.0
> **Phase**: 6 - Quality Assurance
> **Status**: Full version (including Phase 6 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


> **Hard Rules in Force (this plan)** — explicit reminders:
> - HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews. Never role-play A or B yourself.
> - HR-05: harness-methodology wins all conflicts — if a project decision contradicts SKILL.md / INIT / this plan, the harness wins.
> - HR-16: Trace dimension = `min(4a, 4b, 4c)` — ALL THREE must pass (G2/G3/G4 only): 4a = 100% over IN_PROGRESS+VERIFIED FRs, 4b = TEST_SPEC→test coverage (60/80/90% at G2/G3/G4), 4c = NFR→test coverage (60/80/90% at G2/G3/G4, NFR-99 placeholder excluded). `gate_score_overrides` is a **threshold floor (raises, not lowers)** per `sab_parser.derive_gate_score_overrides` — cannot bypass a failing trace dim. Remediation: fix code/FRs/tests to pass, accept gate block, or escalate to human. No automated override.
> - HR-17: NEVER modify files inside `harness/` — debug the framework, never hot-patch the submodule.

---

## Phase 6 Tasks: Quality Assurance

### Phase 6 Overview
Phase 6 centres on Gate 4 — the full-project quality evaluation.
No FR loop. Gate 4 = tool-scored automated evaluation (15 dims incl. traceability, CRG recon) PLUS
Agent B peer review of the QA deliverables (HR-01) — both are required to exit.

> **Checkpoint Index** (push to GitHub = checkpoint saved):
> - CHECKPOINT-GATE-4: Gate 4 (Full Project — 15 dims) + Agent B peer review

### Entry Gate Verification

- **[ENTRY-CHECK]** Gate 3 PASS (P4 exit — P5 has no exit gate, P5 completed stands between):
  Verify P5 output artifacts exist: `05-verification/VERIFICATION_REPORT.md`
  Proof: .methodology/quality_manifest.json records Gate 3 PASS from P4.
  If NOT confirmed: verify Phase 4 Gate PASS is recorded in quality_manifest.json and confirm all intervening phases (P5–P5) completed their tasks.

- **[D4-PRECHECK]** Verify spec-coverage meets Gate 4 threshold BEFORE starting P6 (avoid late surprise):
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 90.0
  ```
  FAIL → add missing test implementations now (Gate 4 blocks at 90%, not 80%).
  Do NOT proceed to G4a until this passes.

### Pre-Phase Preflight

- **[PREFLIGHT]** Run phase hooks (FSM, Kill-Switch, Drift):
  ```bash
  python3 harness_cli.py run-phase --phase 6 --project .
  ```
  If FAILED: fix FSM/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 6` full output.
  Human fix → re-run `run-phase --phase 6 --project .` → PASS required before continuing.
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

- **[V2.9.1-B.1-HANDOFF]** Cross-deliverable dependency check (P5 → P6) — v2.9.1 B.1. **Must PASS** before any Phase 6 work begins:
  ```bash
  python3 harness_cli.py validate-handoff --from-phase 5 --project .
  ```
  > Verifies P5 deliverables are present and well-formed (e.g. P1 TEST_INVENTORY.yaml non-empty + covers all FRs; P2 TEST_SPEC.md has parseable named test cases; P3 all FRs have per-FR Gate 1 sentinels; P4 TEST_RESULTS.md non-trivial; P5 VERIFICATION_REPORT.md non-trivial; P6 06-quality/QUALITY_REPORT.md + RELEASE_NOTES.md + FINAL_SIGN_OFF.md + .methodology/quality_manifest.json gate_results.gate4.quality_complete=true; P7 07-risk/RISK_REGISTER.md + RISK_MITIGATION_PLANS.md + RISK_STATUS_REPORT.md).
  > If exit 1: read the error list, fix the upstream deliverable, re-run until exit 0. Do NOT proceed with Phase 6 work on a BLOCKED handoff.

- **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 6 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 6 --project . --overwrite`

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 6 --project . --json \
  > .sessi-work/phase6_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules`, and `lessons` from current project state.
> **IMPORTANT (Direction C)**: Please carefully review the `lessons` (past failure modes) and DO NOT repeat them.

### P6 Phase End Audit (+ A/B Review)

> A/B collaboration is active for Phase 6 deliverables (HR-01).
> Agent A generates QUALITY_REPORT.md and RELEASE_NOTES.md.
> Agent B (reviewer) reviews the deliverables and verifies Gate 4 score (3-layer defense, T1-B).

### Pre-Gate Preparation
- Confirm all FRs are merged to main branch
- Confirm no open critical or high issues from Gate 3

### Gate 4 Result JSON — Required Fields

> `finalize-gate --gate 4` validates A3 **before** scoring. Missing/insufficient → `[BLOCKED]`.

- **[A3] `devil_advocate`** + **`devil_advocate_evidence`** — artifact-backed DA challenge for all Tier 3 dims:
  ```json
  "devil_advocate": {
    "architecture": true, "readability": true, "error_handling": true,
    "documentation": true, "performance": true
  },
  "devil_advocate_evidence": {
    "architecture": {
      "challenger_model": "claude",
      "challenge": "<≥120 chars: the challenger persona's actual critique of the design/score>",
      "response": "<≥120 chars: the defence / justification>"
    }
  }
  ```
  > A bare boolean is **not** accepted (A3 is artifact-backed): for each Tier 3 dim, dispatch a
  > Claude sub-agent with a challenger persona, then record its `challenge` + `response` text.
  > **Orchestrator Pattern** (architecture/error_handling score = 0 due to hub-and-spoke):
  > complete the DA challenge AND add `"da_waiver": {"architecture": true}` to bypass the
  > score threshold — the waiver also requires the `devil_advocate_evidence.architecture` artifact.
  > The same waiver mechanism is honored at Gate 3 (read from `.sessi-work/gate3_result.json`).
  > See `harness/harness/ssi/prompts/evaluate_dimension.md` §Orchestrator.

  > _Optional (not a gate step)_ — **[A5]** `issue_registry`: for a useful audit
  > trail, populate `.sessi-work/issue_registry.json` via `issue_tracker.py add`
  > during G4b. Advisory only — agent-written, so it never blocks or verifies anything.


### 🔒 CHECKPOINT-GATE-4: Phase 6 Exit
> linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70) · architecture(80) · readability(80) · error_handling(80) · documentation(75) · performance(75) · integration_coverage(75) · test_assertion_quality(70) · traceability(100) · composite ≥ 85  [traceability: framework-owned, harness-computed · CRG recon inside run-gate · D4 spec-coverage unified ≥90%]
> HR-08: Phase end requires Quality Gate pass — never advance past a failing gate (max 3 retry rounds, then escalate).
> _Design note_: HR-08 only appears in P3-P6 (Gate 2/3/4 exits). P5/P7/P8 have no gate-exit checkpoint so HR-08 is correctly absent from those plans.

- **G4a** Prepare Gate 4:
  ```bash
  python3 harness_cli.py run-gate --gate 4 --phase 6 --project .
  ```
  Read the evaluation prompt printed above.
  (CRG recon triggered inside run-gate automatically — no separate action needed)

- **G4b** Evaluate all Gate 4 dimensions inline:
  - Follow `harness/harness/ssi/prompts/evaluate_dimension.md`
  - Write result to `.sessi-work/gate4_result.json`
  - Failing dim: fix code → re-evaluate → re-score
  > Failing dims: fix the root cause in code, then re-evaluate → re-score.
  > (Auto-fix engine is NOT wired — fixes require manual code changes or targeted tools.)
  > **architecture** is framework-owned: the harness runs an independent CRG build itself
  > (`harness/crg_independent.py`) and overrides any agent-recorded score with
  > `community_cohesion`. error_handling is tool-scored (`ast-error-handling`), not CRG.
  > If architecture = 0 due to Orchestrator/hub-and-spoke pattern: complete DA challenge (A3 above)
  > and set `devil_advocate` + `da_waiver` + `devil_advocate_evidence` in
  > `.sessi-work/gate{N}_result.json` (gate3_result.json at Gate 3, gate4_result.json at Gate 4)
  > to bypass the threshold — the harness reads the waiver from that file, NOT quality_manifest.json.
  > See `harness/harness/ssi/prompts/evaluate_dimension.md` §Orchestrator Pattern False Positive.
  > **traceability** is also framework-owned: the harness calls `compute_trace_dimension()`
  > inside `finalize-gate` and injects the score automatically. Do NOT report a traceability
  > score in gate_result.json. If the gate is blocked by traceability, fix the named
  > gaps and re-run finalize-gate — it refreshes a stale attestation itself before
  > committing (no manual build-trace-attestation + commit step needed).

- **G4c** Finalize Gate 4:
  ```bash
  python3 harness_cli.py finalize-gate --gate 4 --phase 6 --project .
  ```
  > **Note**: A `[WARN] post-push dirty tree` message may appear after finalizing. This is non-blocking; do NOT attempt to self-correct.
  > **PUSH ⑧ in the 10-Push Strategy**: `finalize-gate --gate 4` writes HANDOVER.md + commits + pushes.
- **[D4]** D4 spec-coverage-check — unified v2.6 (Gate 4 threshold 90%):
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 90.0
  ```
  FAIL → fix missing test implementations → re-run until coverage meets threshold

  **Early-stop cases after G4c:**
  - CASE 1 PASS:     score ≥ score_gate AND all dims ≥ threshold → `quality_complete=True` → G4d
  - CASE 2 REJECT:   score ≥ score_gate BUT ≤2 dims below threshold → fix below → retry loop
  - CASE 3 BLOCKED:  score < score_gate OR >2 dims below threshold → fix below → retry loop
  - CASE 4 PLATEAU:  3 consecutive rounds, no score improvement → `deferred_fixes.md` → escalate to human
  - CASE 5 ABORT:    max_rounds exhausted → escalate to human

### 🔄 REJECT LOOP — Gate 4 dim(s) below threshold

> `finalize-gate` prints the failing dims with their scores and gaps.
> Read the output CAREFULLY — it tells you exactly what to fix.

**General fix strategies by dimension:**
| Dimension | Fix |
|-----------|-----|
| mutation_testing | Framework-owned score: `python3 harness_cli.py mutation-test-score --project .` runs `compute_mutation_score()` (harness-managed workdir + setup.cfg rewrite + sqlite cache parse). To investigate surviving mutants manually: `mutmut results` (legacy). Exclude data-only files (constants, dicts, Pydantic models) via `paths_to_exclude` in setup.cfg. Target: kill rate ≥ threshold. |
| architecture (G3/G4 only) | Community cohesion low → add cross-module integration tests, break hub-and-spoke coupling, or file an artifact-backed DA waiver in `.sessi-work/gate{N}_result.json` if the pattern is intentional (Orchestrator); calibrate `crg_excludes` / `crg_cohesion_healthy` in `.methodology/harness_config.json` for cohesion-scorer false positives (tooling counted as product, small-package over-fragmentation). |
| error_handling | (1) **Presence**: add try/except blocks. `grep -r 'try:' 03-development/src/` to see coverage. (2) **Anti-patterns** (v2.9 A1, −5 each): remove `except BaseException:` (flagged even with re-raise), bare `except:` without re-raise, `except Exception: pass`. Run `python3 harness_cli.py run-tool ast-error-handling --project .` to see exact deductions. |
| documentation | Add docstrings to public functions/classes. `python3 -m ast_docstrings` or manual: every `def`/`class` in `03-development/src/` needs a docstring. |
| readability | Refactor complex functions (readability_v2 < 65). Run `python3 -m harness.toolchains.readability_v2 03-development/src/` to see scores per file. |
| performance | Add pytest-benchmark tests. Create `tests/test_perf.py` with `def test_latency(benchmark): ...` |
| test_assertion_quality | Add `assert` statements to test functions. Every test must have ≥1 substantive assertion. |
| integration_coverage | Add integration tests in `03-development/tests/integration/` that exercise end-to-end flows. |
| security | Fix bandit HIGH/MEDIUM issues. Run `bandit -r 03-development/src/ -f json` to see them. |
| linting | Run `ruff check .` — fix violations. |
| type_safety | Run `pyright . --outputjson` — fix errorCount > 0. |
| test_coverage | Add tests to cover uncovered lines. Run `pytest --cov=03-development/src --cov-report=term-missing` |
| secrets_scanning | Remove committed secrets. Run `gitleaks detect --source .` |
| license_compliance | Replace non-MIT dependencies. Run `pip-licenses` to audit. |

**Retry workflow:**
1. Read the failing dims from `finalize-gate` output above
2. Fix the ROOT CAUSE in code (NOT by editing gate_result.json)
3. Re-run the tool for each fixed dim to confirm the score change
4. Update `.sessi-work/gate{gate_num}_result.json` with new scores
5. Re-run: `python3 harness_cli.py finalize-gate --gate 4 --phase 6 --project .`
6. Repeat until CASE 1 PASS or 3 fix rounds exhausted
7. If stuck after 3 rounds: write `.methodology/deferred_fixes.md` with each remaining dim as a checkbox item ('- [ ] <dim>: <reason>'); every item MUST be resolved and marked '- [x]' before advance-phase (hard-blocked, exit 17, otherwise), then escalate
8. **Scope Violations (Exit 21)**: If `advance-phase` blocks you with Exit 21 for modifying files outside the current phase scope, and the changes are necessary, request a `da_waiver` from the Human Developer. Do NOT try to bypass the scanner.


- **G4d** ✅ Verify checkpoint saved (finalize-gate above already pushed + wrote HANDOVER.md):
  ```bash
  # Confirm HANDOVER.md exists at project root (written by finalize-gate → commit_and_push_gate)
  ls -la HANDOVER.md
  git log --oneline -1
  ```
  > `finalize-gate --gate 4` (G4c) calls `commit_and_push_gate()` which writes
  > `HANDOVER.md` **before** committing + pushing. No separate push needed here.
  > If HANDOVER.md is missing, re-run `finalize-gate` (do **not** raw-push).

- **G4e** Generate Release Notes:
  Create `RELEASE_NOTES.md` at project root summarizing changes since Gate 3.
  Include: version, date, FR list, Gate 4 composite score, known limitations.
  Reference: `06-quality/QUALITY_REPORT.md` (auto-generated by G4c finalize-gate).

- **G4f** Generate Final Sign-Off:
  Create `FINAL_SIGN_OFF.md` at project root.
  Include: project name, completion date, Gate 4 composite score, sign-off statement.
  Must reference `VERIFICATION_REPORT.md` (verification provenance).

- **G4g** Agent B Peer Review (HR-01):
  Agent B (reviewer) explicitly reviews ALL deliverables. B gets makeDocSummary() orientation + must Bash-cat full files for citations (3-layer defense, T1-B).
  1. Review `06-quality/QUALITY_REPORT.md`, `RELEASE_NOTES.md`, and `FINAL_SIGN_OFF.md`.
  2. Cross-check `.methodology/quality_manifest.json` Gate 4 scoring logic.
  3. Reference `05-verification/VERIFICATION_REPORT.md` for historical traceability.
  4. Generate approval JSON files in `.methodology/agent_b_approvals/` with these exact filenames:
     `QUALITY_REPORT.md.json`, `RELEASE_NOTES.md.json`, `FINAL_SIGN_OFF.md.json`, `quality_manifest.json`.
     **Note:** Agent B must write these 4 files using file-write tools inside the session.
     The `dispatch` auto-persist keyed by `--fr-id` creates `HR-01.json` only — it does NOT
     produce the per-deliverable approval files that `advance-phase` checks.
  - **[B-DISPATCH]** Dispatch Agent B:
    ```bash
    # Bug #114: --fr-id must be a valid P6 deliverable name (not HR-01,
    # which is a Hard Rule and rejected by the dispatch CLI's deliverable
    # validator). Pick one of: QUALITY_REPORT.md, RELEASE_NOTES.md,
    # FINAL_SIGN_OFF.md, quality_manifest
    python3 harness_cli.py dispatch --role reviewer --fr-id QUALITY_REPORT.md \
      --prompt "Review Phase 6 Gate 4 deliverables" --phase 6 --project . --max-turns 30
    ```
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

- **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase
  > **FAIL** → check `phase_truth_verifier` output in `.sessi-work/`
  >   → identify which phase link or gate artifact failed
  >   → fix artifacts → re-run `advance-phase`
  >   → If 3 consecutive failures: escalate to human with `phase_truth_verifier` log

### Post-Gate 4 Git Tagging
- After Gate 4 PASS, generate the annotated git tag with composite scores:
  ```bash
  python3 harness_cli.py gate4-tag --project .
  ```
  → Verify: `git tag -l -n9` shows the new `harness-v4-*` tag.

### Phase 6 Deliverables
- Gate 4 PASS (composite ≥ 85, all 15 dims, CRG recon done)
- `06-quality/QUALITY_REPORT.md` - Quality report (auto-generated by Gate 4)
- `RELEASE_NOTES.md` - Release notes
- `FINAL_SIGN_OFF.md` - Final sign-off
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)

### Phase 6 → Phase 7: Risk Management

- **[TDD-PRECHECK]** Verify TDD checks pass — advance-phase enforces:
  - diagnostic script check: orphan diagnostic scripts (e.g. `_diag_xxx.py`) at repo root will BLOCK (exit 21)
  - secrets scanning: `gitleaks detect --source .` (exit 20) — whole-repo, runs before linting
  - linting: `ruff check .` (exit 18) — fix violations before advancing
  - type safety: `python3 -m mypy . --ignore-missing-imports` (exit 19)
    > Note: advance-phase uses mypy; Gate scoring uses pyright. Both must pass.
  - `pytest --tb=short -q --cov=03-development/src --cov-fail-under=100` (exit 9)
  - `python3 harness_cli.py spec-coverage-check --project . --threshold 90.0` (exit 10, D4 unified v2.6)
  > For genuinely untestable lines add: `# pragma: no cover` (requires justification comment).

- Advance FSM to Phase 7 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 6 --project .
  ```
  > **Note**: `advance-phase` will automatically check for harness submodule drift.
  > If it prints a warning that you are behind `origin/main`, it is non-blocking and for your information only.
  > **Sync**: `advance-phase` only commits the handover locally. The workflow orchestrator
  > for this phase runs a separate `git push origin main` immediately after to publish
  > that commit to origin.
- Confirm `HANDOVER.md` reflects Phase 7 entry (`P7-entry` checkpoint, correct plan path)
- Open `phase7_plan.md` and follow from the top.
- If session crashes during Phase 7: read `HANDOVER.md` or run `generate-next-plan`
