# Phase 4 Full Execution Plan -- taskq-api

> **Version**: v2.12.0 (project plan)
> **Project**: taskq-api
> **Date**: 2026-08-05
> **Framework**: harness-methodology v2.12.0
> **Phase**: 4 - Testing
> **Status**: Full version (including Phase 4 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


> **Hard Rules in Force (this plan)** — explicit reminders:
> - HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews. Never role-play A or B yourself.
> - HR-05: harness-methodology wins all conflicts — if a project decision contradicts SKILL.md / INIT / this plan, the harness wins.
> - HR-16: Trace dimension = `min(4a, 4b, 4c)` — ALL THREE must pass (G2/G3/G4 only): 4a = 100% over IN_PROGRESS+VERIFIED FRs, 4b = TEST_SPEC→test coverage (60/80/90% at G2/G3/G4), 4c = NFR→test coverage (60/80/90% at G2/G3/G4, NFR-99 placeholder excluded). `gate_score_overrides` is a **threshold floor (raises, not lowers)** per `sab_parser.derive_gate_score_overrides` — cannot bypass a failing trace dim. Remediation: fix code/FRs/tests to pass, accept gate block, or escalate to human. No automated override.
> - HR-17: NEVER modify files inside `harness/` — debug the framework, never hot-patch the submodule.

---

## Phase 4 Tasks: Test Planning & Execution

### Phase 4 Overview
Phase 4 formulates and executes a complete test plan based on Phase 3 code.
Each FR ends with a Gate 1 re-evaluation (CHECKPOINT). Phase exits via Gate 3 (16 dims).

> **Crash Recovery**: `python3 harness_cli.py resume-fr-phase --phase 4 --project .`
> prints the next pending step. Each `run-fr-step` auto-pushes to GitHub on completion.
> Per-FR GATE1-DELTA auto-pushes on completion; when code-change triggers full TDD, TDD-RED → GREEN → IMPROVE → GATE1 each push immediately (idempotent on re-run).
> At milestones, `HANDOVER.md` is written with phase/FR/status summary.

> **Checkpoint Index**:
> - CHECKPOINT-0: TEST_PLAN.md (generate before per-FR testing starts)
> - MILESTONE: P4-mid push (≥50% FRs Gate 1 PASS) → **HANDOVER.md**
> - MILESTONE: P4-pre-gate3 push (all FRs done, before Gate 3) → **HANDOVER.md**
> - CHECKPOINT-GATE-3: Gate 3 (Phase 4 Exit) → **push + HANDOVER.md**

### Entry Gate Verification

- **[ENTRY-CHECK]** Gate 2 PASS:
  Proof: .methodology/quality_manifest.json records Gate 2 PASS from P3.
  If NOT confirmed: verify Phase 3 Gate PASS is recorded in quality_manifest.json and confirm all intervening phases (P4–P3) completed their tasks.

### Pre-Phase Preflight

- **[PREFLIGHT]** Run phase hooks (FSM, Kill-Switch, Drift):
  ```bash
  python3 harness_cli.py run-phase --phase 4 --project .
  ```
  If FAILED: fix FSM/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 4` full output.
  Human fix → re-run `run-phase --phase 4 --project .` → PASS required before continuing.
  **Reliability lint fix** (P4+ blocking — if `preflight_reliability_lint` reports findings):
  Fix flagged patterns before continuing: `subprocess.run/Popen` without `timeout=`,
  `tempfile.mkstemp` outside try/finally, `os.path.exists` before open/unlink (TOCTOU),
  `time.sleep` inside async def. Re-run `run-phase` after each fix.
  **Config liveness fix** (P4+ blocking — if `preflight_config_liveness` reports orphans):
  Env keys read in code but absent from `.env.example`/`docker-compose*.yml`/`deployment/`.
  Add the key to the declaration source (or fix the typo). Re-run `run-phase` after each fix.

- **[V2.9.1-B.1-HANDOFF]** Cross-deliverable dependency check (P3 → P4) — v2.9.1 B.1. **Must PASS** before any Phase 4 work begins:
  ```bash
  python3 harness_cli.py validate-handoff --from-phase 3 --project .
  ```
  > Verifies P3 deliverables are present and well-formed (e.g. P1 TEST_INVENTORY.yaml non-empty + covers all FRs; P2 TEST_SPEC.md has parseable named test cases; P3 all FRs have per-FR Gate 1 sentinels; P4 TEST_RESULTS.md non-trivial; P5 VERIFICATION_REPORT.md non-trivial; P6 06-quality/QUALITY_REPORT.md + RELEASE_NOTES.md + FINAL_SIGN_OFF.md + .methodology/quality_manifest.json gate_results.gate4.quality_complete=true; P7 07-risk/RISK_REGISTER.md + RISK_MITIGATION_PLANS.md + RISK_STATUS_REPORT.md).
  > If exit 1: read the error list, fix the upstream deliverable, re-run until exit 0. Do NOT proceed with Phase 4 work on a BLOCKED handoff.

- **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 4 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 4 --project . --overwrite`

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 4 --project . --json \
  > .sessi-work/phase4_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules`, and `lessons` from current project state.
> **IMPORTANT (Direction C)**: Please carefully review the `lessons` (past failure modes) and DO NOT repeat them.
> All `{FR-ID}` references in tasks below come from this file.

### CHECKPOINT-0: Generate TEST_PLAN.md

> Generate `04-testing/TEST_PLAN.md` from SRS.md FR acceptance criteria.
> This step runs once before per-FR test execution.

**Generate TEST_PLAN.md** (orchestrator runs directly — not a sub-agent dispatch):
- Read SRS.md FR acceptance criteria → write TEST_PLAN.md with per-FR test cases
  - For each FR: test case ID, description, input, expected output, priority
  - Include positive, negative, boundary, and edge case categories
  - Output: `04-testing/TEST_PLAN.md`
- Verify TEST_PLAN.md covers all FRs from manifest/quality_manifest.json
- **[TP-DONE]** TEST_PLAN.md written: all FRs have ≥1 test case, NFRs addressed

### FR Tasks — Expanded at Execution Time

- **[ENV-CHECK]** Run ONCE before the FR loop — `GATE1`/`GATE1-DELTA` preflight requires `.sessi-work/env_check_result.json`:
  ```bash
  python3 harness_cli.py run-env-check --phase 4 --project .
  # evaluate inline → write .sessi-work/env_check_result.json →
  python3 harness_cli.py finalize-env-check --phase 4 --project .
  ```
  > Without this, every `run-fr-step --step GATE1-DELTA` blocks on 'env_check_result.json not found'.

> Read `fr_ids` from `.sessi-work/phase4_ctx.json`.
> For each `{FR-ID}` in the list, execute the template below:

---
**{FR-ID} — {FR-TITLE from fr_details}**

- **[ORCH-GATE1-DELTA]** `run-fr-step --phase 4 --fr-id {FR-ID} --step GATE1-DELTA --project .`
> Crash recovery: `resume-fr-phase` auto-detects code changes → switches to full TDD if needed.
> **Auto-skip**: if NO FR's code changed since its last Gate 1 PASS, `advance-phase --completed 4`
> treats this entire DELTA loop as satisfied automatically — you may skip the per-FR steps.
> Only FRs whose code actually changed need a re-evaluation.
>
> **GATE1-DELTA outcomes:**
> - CASE 1 PASS:    Gate 1 PASS → continue to next {FR-ID}
> - CASE 2 FAIL:    Gate 1 FAIL → full TDD auto-triggered by crash recovery:
>   `run-fr-step --phase 4 --fr-id {FR-ID} --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1
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

### P4 Milestone Pushes (10-Push Strategy ⑤⑥)

> Per-FR steps push automatically via `run-fr-step`. The milestone pushes below
> also write `HANDOVER.md` with phase/FR/status summary and push to origin.
> **Note**: A `[WARN] post-push dirty tree` message may appear if local files were updated. This is non-blocking; do NOT attempt to self-correct.
> All FR IDs in this project: <FR-01,FR-02,…>

- **PUSH ⑤ — P4-mid** (trigger when ≥<N//2>/N FRs have Gate 1 PASS):
  ```bash
  python3 harness_cli.py push-milestone --type p4-mid --project . \
    --fr-done <N//2> --fr-total N --fr-ids <comma-separated FR-IDs with Gate 1 PASS>
  ```
  > `--fr-ids` lists the FRs with Gate 1 PASS so far. Replace `<comma-separated FR-IDs with Gate 1 PASS>` with actual.
  > Writes HANDOVER.md + commits + pushes. Next session reads HANDOVER.md to resume.

- **PUSH ⑥ — P4-pre-gate3** (trigger when all N FRs Gate 1 PASS, before Gate 3):
  ```bash
  python3 harness_cli.py push-milestone --type p4-pre-gate3 --project . \
    --fr-ids <comma-separated FR-IDs with Gate 1 PASS>
  ```
  > Last stable snapshot before Gate 3 evaluation. HANDOVER.md + push.

### TEST_RESULTS.md Summary

- **[TEST-RESULTS-SUMMARY]** Finalize `04-testing/TEST_RESULTS.md` before milestone push:
  - Summarise test execution: test cases run, pass/fail outcome, any deferred issues
  - Real test execution is enforced by advance-phase TDD-PRECHECK (`pytest --cov-fail-under=100`), not by string-matching this document

### COVERAGE_REPORT.md — Coverage Summary

- **[COVERAGE-REPORT]** Generate `04-testing/COVERAGE_REPORT.md`:
  ```bash
  pytest --cov=03-development/src --cov-report=term-missing -q \
    | tee 04-testing/coverage_raw.txt
  python3 -m coverage report --format=total  # → overall %
  ```
  Write `04-testing/COVERAGE_REPORT.md` including:
  - Overall coverage % (must be ≥80% for Gate 3)
  - Per-module breakdown (from term-missing output)
  - Uncovered lines (if any)
  > cross_artifact.py validates this file's numbers against live `pytest --cov` at Gate 3.
  > Fabricated numbers will be caught by the cross-artifact check.


### 🔒 CHECKPOINT-GATE-3: Phase 4 Exit
> linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70) · integration_coverage(60) · architecture(80) · readability(80) · error_handling(80) · documentation(75) · test_assertion_quality(60) · performance(75) · traceability(100) · adversarial_review(100) · composite ≥ 80  [traceability: framework-owned, harness-computed · adversarial_review: framework-owned, requires .methodology/bug_hunt_report.json · CRG recon inside run-gate · D4 spec-coverage unified ≥80%]
> HR-08: Phase end requires Quality Gate pass — never advance past a failing gate (max 3 retry rounds, then escalate).
> _Design note_: HR-08 only appears in P3-P6 (Gate 2/3/4 exits). P5/P7/P8 have no gate-exit checkpoint so HR-08 is correctly absent from those plans.


### Step 4b — Adversarial Bug Hunt (v2.9, required before Gate 3)

> `adversarial_review` is a framework-owned Gate 3 dimension (threshold 100, weight 0).
> It blocks Gate 3 if `.methodology/bug_hunt_report.json` is absent or any confirmed
> critical/high finding is still `open`. Run the hunt BEFORE `G3a`.

- **[HUNT-TARGETS]** Generate targeting manifest (CRG hubs + mutation survivors + integration gaps + SAD §6 threat model):
  ```bash
  python3 harness_cli.py bug-hunt-targets --project .
  ```
  Output: `.methodology/bug_hunt_targets.json` (its `threat_model` entries — SAD.md §6
  declared threats — are forced high-risk attack-vector seeds, independent of CRG/mutation signals)

- **[HUNT-RUN]** Execute the adversarial bug hunt:
  - Protocol: `harness/harness/ssi/prompts/hunt_bugs.md` (4-phase: scout → lens hunters → verify → synthesize)
  - Reference workflow: `templates/workflows/hunt-bugs.js`
  - **Use a model DIFFERENT from the developer model** to minimise same-source bias
  - `threat_model` targets: verify the declared `mitigation` actually blocks the attack
    (not just that defensive-looking code exists)
  - Output: `.methodology/bug_hunt_report.json` + `.audit/*.md`

- **[HUNT-RESOLVE]** For each **confirmed critical/high** finding, set `resolution.status`:
  - `resolved`: must include `fix_commit` (commit SHA) or `repro_test` (path in `tests/`)
  - `refuted`: must include `refute_evidence` (explanation + line citation)
  - Medium/low findings: record only — not required to resolve before Gate 3

- **G3a** Prepare Gate 3:
  ```bash
  python3 harness_cli.py run-gate --gate 3 --phase 4 --project .
  ```
  Read the evaluation prompt printed above.
  (CRG recon triggered inside run-gate automatically — no separate action needed)

- **G3b** Evaluate all Gate 3 dimensions inline:
  - Follow `harness/harness/ssi/prompts/evaluate_dimension.md`
  - Write result to `.sessi-work/gate3_result.json`
  - Failing dim: fix code → re-evaluate → re-score
  > Failing dims: fix the root cause in code, then re-evaluate → re-score.
  > (Auto-fix engine is NOT wired — fixes require manual code changes or targeted tools.)
  > **architecture** is framework-owned: the harness runs an independent CRG build itself
  > (`harness/crg_independent.py`) and overrides any agent-recorded score with
  > `community_cohesion`. error_handling is tool-scored (`ast-error-handling`), not CRG.
  > If architecture = 0 due to Orchestrator/hub-and-spoke pattern: complete DA challenge
  > and set `devil_advocate` + `da_waiver` + `devil_advocate_evidence` in
  > `.sessi-work/gate{N}_result.json` (gate3_result.json at Gate 3, gate4_result.json at Gate 4)
  > to bypass the threshold — the harness reads the waiver from that file, NOT quality_manifest.json.
  > See `harness/harness/ssi/prompts/evaluate_dimension.md` §Orchestrator Pattern False Positive.
  > **traceability** is also framework-owned: the harness calls `compute_trace_dimension()`
  > inside `finalize-gate` and injects the score automatically. Do NOT report a traceability
  > score in gate_result.json. If the gate is blocked by traceability, fix the named
  > gaps and re-run finalize-gate — it refreshes a stale attestation itself before
  > committing (no manual build-trace-attestation + commit step needed).

- **G3c** Finalize Gate 3:
  ```bash
  python3 harness_cli.py finalize-gate --gate 3 --phase 4 --project .
  ```
  > **Note**: A `[WARN] post-push dirty tree` message may appear after finalizing. This is non-blocking; do NOT attempt to self-correct.
- **[D4]** D4 spec-coverage-check — unified v2.6 (Gate 3 threshold 80%):
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 80.0
  ```
  FAIL → fix missing test implementations → re-run until coverage meets threshold

  **Early-stop cases after G3c:**
  - CASE 1 PASS:     score ≥ score_gate AND all dims ≥ threshold → `quality_complete=True` → G3d
  - CASE 2 REJECT:   score ≥ score_gate BUT ≤2 dims below threshold → fix below → retry loop
  - CASE 3 BLOCKED:  score < score_gate OR >2 dims below threshold → fix below → retry loop
  - CASE 4 PLATEAU:  3 consecutive rounds, no score improvement → `deferred_fixes.md` → escalate to human
  - CASE 5 ABORT:    max_rounds exhausted → escalate to human

### 🔄 REJECT LOOP — Gate 3 dim(s) below threshold

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
| adversarial_review (G3 only) | `.methodology/bug_hunt_report.json` missing, or confirmed critical/high findings are still `open`. Fix: run the adversarial bug hunt (Step 4b above), resolve/refute all critical+high findings with evidence (`fix_commit` or `repro_test` for resolved; `refute_evidence` for refuted). |

**Retry workflow:**
1. Read the failing dims from `finalize-gate` output above
2. Fix the ROOT CAUSE in code (NOT by editing gate_result.json)
3. Re-run the tool for each fixed dim to confirm the score change
4. Update `.sessi-work/gate{gate_num}_result.json` with new scores
5. Re-run: `python3 harness_cli.py finalize-gate --gate 3 --phase 4 --project .`
6. Repeat until CASE 1 PASS or 3 fix rounds exhausted
7. If stuck after 3 rounds: write `.methodology/deferred_fixes.md` with each remaining dim as a checkbox item ('- [ ] <dim>: <reason>'); every item MUST be resolved and marked '- [x]' before advance-phase (hard-blocked, exit 17, otherwise), then escalate
8. **Scope Violations (Exit 21)**: If `advance-phase` blocks you with Exit 21 for modifying files outside the current phase scope, and the changes are necessary, request a `da_waiver` from the Human Developer. Do NOT try to bypass the scanner.


- **G3d** ✅ Verify checkpoint saved (finalize-gate above already pushed + wrote HANDOVER.md):
  ```bash
  # Confirm HANDOVER.md exists at project root (written by finalize-gate → commit_and_push_gate)
  ls -la HANDOVER.md
  git log --oneline -1
  ```
  > `finalize-gate --gate 3` (G3c) calls `commit_and_push_gate()` which writes
  > `HANDOVER.md` **before** committing + pushing. No separate push needed here.
  > If HANDOVER.md is missing, re-run `finalize-gate` (do **not** raw-push).

- **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase
  > **FAIL** → check `phase_truth_verifier` output in `.sessi-work/`
  >   → identify which phase link or gate artifact failed
  >   → fix artifacts → re-run `advance-phase`
  >   → If 3 consecutive failures: escalate to human with `phase_truth_verifier` log

### Phase 4 Deliverables
- `04-testing/TEST_PLAN.md` - Test plan
- `04-testing/TEST_RESULTS.md` - Test results (test execution summary)
- `04-testing/COVERAGE_REPORT.md` - Coverage report
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)
- Gate 1 PASS for every FR
- Gate 3 PASS (phase exit, composite ≥ 80, 16 dims)

### Phase 4 → Phase 5: Verification & Delivery

- **[TDD-PRECHECK]** Verify TDD checks pass — advance-phase enforces:
  - diagnostic script check: orphan diagnostic scripts (e.g. `_diag_xxx.py`) at repo root will BLOCK (exit 21)
  - secrets scanning: `gitleaks detect --source .` (exit 20) — whole-repo, runs before linting
  - linting: `ruff check .` (exit 18) — fix violations before advancing
  - type safety: `python3 -m mypy . --ignore-missing-imports` (exit 19)
    > Note: advance-phase uses mypy; Gate scoring uses pyright. Both must pass.
  - `pytest --tb=short -q --cov=03-development/src --cov-fail-under=100` (exit 9)
  - `python3 harness_cli.py spec-coverage-check --project . --threshold 80.0` (exit 10, D4 unified v2.6)
  > For genuinely untestable lines add: `# pragma: no cover` (requires justification comment).

- Advance FSM to Phase 5 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 4 --project .
  ```
  > **Note**: `advance-phase` will automatically check for harness submodule drift.
  > If it prints a warning that you are behind `origin/main`, it is non-blocking and for your information only.
  > **Sync**: `advance-phase` only commits the handover locally. The workflow orchestrator
  > for this phase runs a separate `git push origin main` immediately after to publish
  > that commit to origin.
- Confirm `HANDOVER.md` reflects Phase 5 entry (`P5-entry` checkpoint, correct plan path)
- Open `phase5_plan.md` and follow from the top.
- If session crashes during Phase 5: read `HANDOVER.md` or run `generate-next-plan`
