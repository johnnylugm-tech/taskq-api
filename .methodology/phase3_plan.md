# Phase 3 Full Execution Plan -- taskq-api

> **Version**: v2.12.0 (project plan)
> **Project**: taskq-api
> **Date**: 2026-08-12
> **Framework**: harness-methodology v2.12.0
> **Phase**: 3 - Implementation
> **Status**: Full version (including Phase 3 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


> **Hard Rules in Force (this plan)** — explicit reminders:
> - HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews. Never role-play A or B yourself.
> - HR-05: harness-methodology wins all conflicts — if a project decision contradicts SKILL.md / INIT / this plan, the harness wins.
> - HR-16: Trace dimension = `min(4a, 4b, 4c)` — ALL THREE must pass (G2/G3/G4 only): 4a = 100% over IN_PROGRESS+VERIFIED FRs, 4b = TEST_SPEC→test coverage (60/80/90% at G2/G3/G4), 4c = NFR→test coverage (60/80/90% at G2/G3/G4, NFR-99 placeholder excluded). `gate_score_overrides` is a **threshold floor (raises, not lowers)** per `sab_parser.derive_gate_score_overrides` — cannot bypass a failing trace dim. Remediation: fix code/FRs/tests to pass, accept gate block, or escalate to human. No automated override.
> - HR-17: NEVER modify files inside `harness/` — debug the framework, never hot-patch the submodule.

---

## Phase 3 Tasks: Implementation

### Phase 3 Overview
Phase 3 implements all FR modules according to SAD, including unit tests.
Each FR ends with a Gate 1 quality evaluation (CHECKPOINT). Phase exits via Gate 2.

> **Crash Recovery**: `python3 harness_cli.py resume-fr-phase --phase 3 --project .`
> prints the next pending step. Each `run-fr-step` auto-pushes to GitHub on completion.
> Per-FR TDD-RED/GREEN/IMPROVE/GATE1 each push immediately (idempotent on re-run).
> At milestones, `HANDOVER.md` is written with phase/FR/status summary.

> **Checkpoint Index**:
> - MILESTONE: P3-mid push (≥50% FRs Gate 1 PASS) → **HANDOVER.md**
> - MILESTONE: P3-pre-gate2 push (all FRs done) → **HANDOVER.md**
> - CHECKPOINT-GATE-2: Gate 2 (Phase 3 Exit) → **push + HANDOVER.md**

### Entry Gate Verification

- **[ENTRY-CHECK]** P2 review-complete:
  Proof: git log contains commit 'phase2(review-complete): Phase 2 deliverables APPROVED'.
  If NOT confirmed: return to Phase 2 and complete exit gate first.

- **[P2-ARTIFACTS]** Verify Phase 2 output artifacts exist:
  ```bash
  ls -la 02-architecture/SAD.md 02-architecture/adr/ADR.md 02-architecture/TEST_SPEC.md \
     .methodology/quality_manifest.json .methodology/SAB.json
  git log --oneline --grep="APPROVE" -1
  ```
  If any file missing: return to Phase 2 and complete missing deliverables.

### Pre-Phase Preflight

- **[PREFLIGHT]** Run phase hooks (FSM, Kill-Switch, Drift):
  ```bash
  python3 harness_cli.py run-phase --phase 3 --project .
  ```
  If FAILED: fix FSM/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 3` full output.
  Human fix → re-run `run-phase --phase 3 --project .` → PASS required before continuing.

- **[V2.9.1-B.1-HANDOFF]** Cross-deliverable dependency check (P2 → P3) — v2.9.1 B.1. **Must PASS** before any Phase 3 work begins:
  ```bash
  python3 harness_cli.py validate-handoff --from-phase 2 --project .
  ```
  > Verifies P2 deliverables are present and well-formed (e.g. P1 TEST_INVENTORY.yaml non-empty + covers all FRs; P2 TEST_SPEC.md has parseable named test cases; P3 all FRs have per-FR Gate 1 sentinels; P4 TEST_RESULTS.md non-trivial; P5 VERIFICATION_REPORT.md non-trivial; P6 06-quality/QUALITY_REPORT.md + RELEASE_NOTES.md + FINAL_SIGN_OFF.md + .methodology/quality_manifest.json gate_results.gate4.quality_complete=true; P7 07-risk/RISK_REGISTER.md + RISK_MITIGATION_PLANS.md + RISK_STATUS_REPORT.md).
  > If exit 1: read the error list, fix the upstream deliverable, re-run until exit 0. Do NOT proceed with Phase 3 work on a BLOCKED handoff.

- **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 3 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 3 --project . --overwrite`

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 3 --project . --json \
  > .sessi-work/phase3_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules`, and `lessons` from current project state.
> **IMPORTANT (Direction C)**: Please carefully review the `lessons` (past failure modes) and DO NOT repeat them.
> All `{FR-ID}` references in tasks below come from this file.

### FR Tasks — Expanded at Execution Time

- **[ENV-CHECK]** Run ONCE before the FR loop — `GATE1`/`GATE1-DELTA` preflight requires `.sessi-work/env_check_result.json`:
  ```bash
  python3 harness_cli.py run-env-check --phase 3 --project .
  # evaluate inline → write .sessi-work/env_check_result.json →
  python3 harness_cli.py finalize-env-check --phase 3 --project .
  ```
  > Without this, every `run-fr-step --step GATE1` blocks on 'env_check_result.json not found'.

> Read `fr_ids` from `.sessi-work/phase3_ctx.json`.
> For each `{FR-ID}` in the list, execute the template below:

---
**{FR-ID} — {FR-TITLE from fr_details}**

- **[ORCH-RED]**     `run-fr-step --phase 3 --fr-id {FR-ID} --step TDD-RED --project . --srs 01-requirements/SRS.md`
> **NFR annotation (4c gate dim — F-2.3)**: the new test file MUST include `# NFR-XX` annotations for every NFR associated with {FR-ID} in `01-requirements/SRS.md §2` `NFR Association` column (e.g. `# NFR-01 perf: submit+status p95 < 50ms`). Without these, compute_trace_dimension 4c = 0% and Gate 2 blocks. NFR-99 placeholder is excluded.
- **[P3-MIRROR]**    `python3 harness_cli.py check-test-mirrors-spec --fr-id {FR-ID} --test-file 03-development/tests/test_*.py --project .`
- **[ORCH-GREEN]**   `run-fr-step --phase 3 --fr-id {FR-ID} --step TDD-GREEN --project . --srs 01-requirements/SRS.md`
- **[ORCH-IMPROVE]** `run-fr-step --phase 3 --fr-id {FR-ID} --step TDD-IMPROVE --project .`
- **[ORCH-GATE1]**   `run-fr-step --phase 3 --fr-id {FR-ID} --step GATE1 --project .`
> Gate 1 thresholds: linting(100) · type_safety(100) · test_coverage(80) · architecture_constraints(100)
> Crash recovery: `resume-fr-phase --phase 3 --project .`
>
> **Gate 1 outcomes:**
> - CASE 1 PASS:    Gate 1 PASS → continue to next {FR-ID}
> - CASE 2 FAIL:    Fix failing dims → re-run `run-fr-step --step GATE1`
>   (linting: `ruff check . --fix`; coverage: add tests; type_safety: fix pyright errors)
> - CASE 3 BLOCKED: 3 rounds still failing → escalate to human.
>   Provide: Gate 1 output + failing dimension details.

- **[ORCH-POST]** After GATE1 PASS — orchestrator runs directly:
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 40.0 --fr-id {FR-ID}
  python3 harness_cli.py amend-sab --project .
  ```

---

### P3 Milestone Pushes (10-Push Strategy ③④⑤)

> Per-FR steps push automatically via `run-fr-step`. The milestone pushes below
> also write `HANDOVER.md` with phase/FR/status summary and push to origin.
> **Note**: A `[WARN] post-push dirty tree` message may appear if local files were updated. This is non-blocking; do NOT attempt to self-correct.
> All FR IDs in this project: <FR-01,FR-02,…>

- **PUSH ③ — P3-mid** (trigger when ≥<N//2>/N FRs have Gate 1 PASS):
  ```bash
  python3 harness_cli.py push-milestone --type p3-mid --project . \
    --fr-done <N//2> --fr-total N --fr-ids <comma-separated FR-IDs with Gate 1 PASS>
  ```
  > `--fr-ids` lists the FRs with Gate 1 PASS so far. Replace `<comma-separated FR-IDs with Gate 1 PASS>` with actual.
  > Writes HANDOVER.md + commits + pushes. Next session reads HANDOVER.md to resume.

- **PUSH ④ — P3-pre-gate2** (trigger when all N FRs Gate 1 PASS, before Gate 2):
  ```bash
  python3 harness_cli.py push-milestone --type p3-pre-gate2 --project . \
    --fr-ids <comma-separated FR-IDs with Gate 1 PASS>
  ```
  > Last stable snapshot before Gate 2 evaluation. HANDOVER.md + push.

- **PUSH ⑤ — P3-post-gate2** (trigger when Gate 2 PASSes, all N FRs Gate 1 PASS — formal P3 exit):
  ```bash
  python3 harness_cli.py push-milestone --type p3-post-gate2 --project . \
    --fr-ids <comma-separated FR-IDs with Gate 1 PASS>
  ```
  > **v2.9.1 B.2** -- replaces label-only `chore(P3-exit): ...` commits.
  > Pre-flight (enforced) checks:
  >   1. `.methodology/gate2_result.json` composite ≥ phase threshold
  >   2. Per-FR Gate 1 sentinel `.sessi-work/sentinels/g1_p3_<fr>.flag` exists for every FR in `--fr-ids`
  > If either fails the push is BLOCKED with a clear error list (exit 1).
  > On success: writes HANDOVER.md with `resume_phase=4` + commits + pushes.


### 🔒 CHECKPOINT-GATE-2: Phase 3 Exit
> linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70) · integration_coverage(60) · test_assertion_quality(60) · execute_verification_target(100) · traceability(100) · architecture(80) · composite ≥ 75  [traceability: framework-owned, harness-computed · CRG recon inside run-gate · D4 spec-coverage unified ≥60%]
> HR-08: Phase end requires Quality Gate pass — never advance past a failing gate (max 3 retry rounds, then escalate).
> _Design note_: HR-08 only appears in P3-P6 (Gate 2/3/4 exits). P5/P7/P8 have no gate-exit checkpoint so HR-08 is correctly absent from those plans.

- **G2a** Prepare Gate 2:
  ```bash
  python3 harness_cli.py run-gate --gate 2 --phase 3 --project .
  ```
  Read the evaluation prompt printed above.

- **G2b** Evaluate all Gate 2 dimensions inline:
  - Follow `harness/harness/ssi/prompts/evaluate_dimension.md`
  - Write result to `.sessi-work/gate2_result.json`
  - Failing dim: fix code → re-evaluate → re-score
  > Failing dims: fix the root cause in code, then re-evaluate → re-score.
  > (Auto-fix engine is NOT wired — fixes require manual code changes or targeted tools.)
  > **traceability** is framework-owned: the harness calls `compute_trace_dimension()`
  > inside `finalize-gate` and injects the score automatically. Do NOT report a traceability
  > score in gate_result.json. If the gate is blocked by traceability, fix the named
  > gaps and re-run finalize-gate — it refreshes a stale attestation itself before
  > committing (no manual build-trace-attestation + commit step needed).

- **G2c** Finalize Gate 2:
  ```bash
  python3 harness_cli.py finalize-gate --gate 2 --phase 3 --project .
  ```
  > **Note**: A `[WARN] post-push dirty tree` message may appear after finalizing. This is non-blocking; do NOT attempt to self-correct.
- **[D4]** D4 spec-coverage-check — unified v2.6 (Gate 2 threshold 60%):
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 60.0
  ```
  FAIL → fix missing test implementations → re-run until coverage meets threshold

  **Early-stop cases after G2c:**
  - CASE 1 PASS:     score ≥ score_gate AND all dims ≥ threshold → `quality_complete=True` → G2d
  - CASE 2 REJECT:   score ≥ score_gate BUT ≤2 dims below threshold → fix below → retry loop
  - CASE 3 BLOCKED:  score < score_gate OR >2 dims below threshold → fix below → retry loop
  - CASE 4 PLATEAU:  3 consecutive rounds, no score improvement → `deferred_fixes.md` → escalate to human
  - CASE 5 ABORT:    max_rounds exhausted → escalate to human

### 🔄 REJECT LOOP — Gate 2 dim(s) below threshold

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
5. Re-run: `python3 harness_cli.py finalize-gate --gate 2 --phase 3 --project .`
6. Repeat until CASE 1 PASS or 3 fix rounds exhausted
7. If stuck after 3 rounds: write `.methodology/deferred_fixes.md` with each remaining dim as a checkbox item ('- [ ] <dim>: <reason>'); every item MUST be resolved and marked '- [x]' before advance-phase (hard-blocked, exit 17, otherwise), then escalate
8. **Scope Violations (Exit 21)**: If `advance-phase` blocks you with Exit 21 for modifying files outside the current phase scope, and the changes are necessary, request a scope exception from the Human Developer. Do NOT try to bypass the scanner. (This is a human decision about which files a phase may touch — unrelated to gate dimension thresholds, which nothing waives.)


- **G2d** ✅ Verify checkpoint saved (finalize-gate above already pushed + wrote HANDOVER.md):
  ```bash
  # Confirm HANDOVER.md exists at project root (written by finalize-gate → commit_and_push_gate)
  ls -la HANDOVER.md
  git log --oneline -1
  ```
  > `finalize-gate --gate 2` (G2c) calls `commit_and_push_gate()` which writes
  > `HANDOVER.md` **before** committing + pushing. No separate push needed here.
  > If HANDOVER.md is missing, re-run `finalize-gate` (do **not** raw-push).

- **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase
  > **FAIL** → check `phase_truth_verifier` output in `.sessi-work/`
  >   → identify which phase link or gate artifact failed
  >   → fix artifacts → re-run `advance-phase`
  >   → If 3 consecutive failures: escalate to human with `phase_truth_verifier` log

### Phase 3 Deliverables
- `03-development/src/` - All FR modules implemented
- `tests/` - Unit tests (≥80% coverage per FR)
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)
- Gate 1 PASS for every FR
- Gate 2 PASS (phase exit, composite ≥ 75)

### Phase 3 → Phase 4: Testing

- **[TDD-PRECHECK]** Verify TDD checks pass — advance-phase enforces:
  - diagnostic script check: orphan diagnostic scripts (e.g. `_diag_xxx.py`) at repo root will BLOCK (exit 21)
  - secrets scanning: `gitleaks detect --source .` (exit 20) — whole-repo, runs before linting
  - linting: `ruff check .` (exit 18) — fix violations before advancing
  - type safety: `python3 -m mypy . --ignore-missing-imports` (exit 19)
    > Note: advance-phase uses mypy; Gate scoring uses pyright. Both must pass.
  - `pytest --tb=short -q --cov=03-development/src --cov-fail-under=100` (exit 9)
  - `python3 harness_cli.py spec-coverage-check --project . --threshold 60.0` (exit 10, D4 unified v2.6)
  > For genuinely untestable lines add: `# pragma: no cover` (requires justification comment).

- Advance FSM to Phase 4 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 3 --project .
  ```
  > **Note**: `advance-phase` will automatically check for harness submodule drift.
  > If it prints a warning that you are behind `origin/main`, it is non-blocking and for your information only.
  > **Sync**: `advance-phase` only commits the handover locally. The workflow orchestrator
  > for this phase runs a separate `git push origin main` immediately after to publish
  > that commit to origin.
- Confirm `HANDOVER.md` reflects Phase 4 entry (`P4-entry` checkpoint, correct plan path)
- Open `phase4_plan.md` and follow from the top.
- If session crashes during Phase 4: read `HANDOVER.md` or run `generate-next-plan`
