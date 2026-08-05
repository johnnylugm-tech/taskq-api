# Phase 2 Full Execution Plan -- taskq-api

> **Version**: v2.12.0 (project plan)
> **Project**: taskq-api
> **Date**: 2026-08-05
> **Framework**: harness-methodology v2.12.0
> **Phase**: 2 - Architecture Design
> **Status**: Full version (including Phase 2 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


> **Hard Rules in Force (this plan)** — explicit reminders:
> - HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews. Never role-play A or B yourself.
> - HR-05: harness-methodology wins all conflicts — if a project decision contradicts SKILL.md / INIT / this plan, the harness wins.
> - HR-16: Trace dimension = `min(4a, 4b, 4c)` — ALL THREE must pass (G2/G3/G4 only): 4a = 100% over IN_PROGRESS+VERIFIED FRs, 4b = TEST_SPEC→test coverage (60/80/90% at G2/G3/G4), 4c = NFR→test coverage (60/80/90% at G2/G3/G4, NFR-99 placeholder excluded). `gate_score_overrides` is a **threshold floor (raises, not lowers)** per `sab_parser.derive_gate_score_overrides` — cannot bypass a failing trace dim. Remediation: fix code/FRs/tests to pass, accept gate block, or escalate to human. No automated override.
> - HR-17: NEVER modify files inside `harness/` — debug the framework, never hot-patch the submodule.

---

## Phase 2 Tasks: Architecture Design

### Phase 2 Overview
Phase 2 designs the system architecture based on SRS, producing SAD and ADR.
**Exit gate = Agent B peer review of deliverables** (not `harness run-gate --gate 1`).

> **Crash Recovery**: after each push, `HANDOVER.md` is written to project root.
> If context is lost, read `HANDOVER.md` first — it contains phase, status, and next steps.

> **Checkpoint Index** (push to GitHub = checkpoint + HANDOVER.md saved):
> - CHECKPOINT-PEER-REVIEW: Agent B Peer Review (Phase 2 Exit) → `push-checkpoint --phase 2`

### Entry Gate Verification

- **[ENTRY-CHECK]** P1 review-complete:
  Proof: git log contains commit 'phase1(review-complete): Phase 1 deliverables APPROVED'.
  If NOT confirmed: return to Phase 1 and complete exit gate first.

- **[P1-ARTIFACTS]** Verify all 4 Phase 1 deliverables exist (CONSTITUTION.md §2.3 P2 entry requirement):
  ```bash
  ls 01-requirements/SRS.md \
     01-requirements/SPEC_TRACKING.md \
     01-requirements/TRACEABILITY_MATRIX.md \
     TEST_INVENTORY.yaml
  ```
  All 4 files must exist. If any is missing → return to Phase 1 to complete them before entering Phase 2.

### Pre-Phase Preflight

- **[PREFLIGHT]** Run phase hooks (FSM, Kill-Switch, Drift):
  ```bash
  python3 harness_cli.py run-phase --phase 2 --project .
  ```
  If FAILED: fix FSM/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 2` full output.
  Human fix → re-run `run-phase --phase 2 --project .` → PASS required before continuing.

- **[V2.9.1-B.1-HANDOFF]** Cross-deliverable dependency check (P1 → P2) — v2.9.1 B.1. **Must PASS** before any Phase 2 work begins:
  ```bash
  python3 harness_cli.py validate-handoff --from-phase 1 --project .
  ```
  > Verifies P1 deliverables are present and well-formed (e.g. P1 TEST_INVENTORY.yaml non-empty + covers all FRs; P2 TEST_SPEC.md has parseable named test cases; P3 all FRs have per-FR Gate 1 sentinels; P4 TEST_RESULTS.md non-trivial; P5 VERIFICATION_REPORT.md non-trivial; P6 06-quality/QUALITY_REPORT.md + RELEASE_NOTES.md + FINAL_SIGN_OFF.md + .methodology/quality_manifest.json gate_results.gate4.quality_complete=true; P7 07-risk/RISK_REGISTER.md + RISK_MITIGATION_PLANS.md + RISK_STATUS_REPORT.md).
  > If exit 1: read the error list, fix the upstream deliverable, re-run until exit 0. Do NOT proceed with Phase 2 work on a BLOCKED handoff.

- **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 2 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 2 --project . --overwrite`

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 2 --project . --json \
  > .sessi-work/phase2_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules`, and `lessons` from current project state.
> **IMPORTANT (Direction C)**: Please carefully review the `lessons` (past failure modes) and DO NOT repeat them.

### Task Decomposition (Dependency Analysis)

**Phase 2 has 3 deliverables with sequential dependencies:**

| Order | Deliverable | Depends On | Agent A | Agent B |
|-------|------------|------------|---------|---------|
| 1 | `SAD.md` | (none — starting point) | ARCHITECT | TECH_LEAD |
| 2 | `ADR.md` | SAD.md | ARCHITECT | TECH_LEAD |
| 3 | `TEST_SPEC.md` | ADR.md | ARCHITECT | TECH_LEAD |

**Execution rule**: Each deliverable must pass Agent B review BEFORE starting the next.
If a deliverable is REJECTED, fix only that deliverable — earlier APPROVED deliverables
are not re-opened. This bounds backtracking to a single step.

### Architecture Design (Serial A/B per Deliverable)

### Sub-Task 1/3: SAD.md — Software Architecture Document — components, interfaces, FR→module mapping, data flows

**Depends on**: none — starting point
**Agent A**: ARCHITECT
**Agent B**: TECH_LEAD

**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):
- **[A-1]** Agent A (ARCHITECT): Design system architecture → write SAD.md → validate every FR has a module mapping
  - FORBIDDEN: vague/non-testable acceptance criteria
- **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`
- **[B-1]** Agent B (TECH_LEAD) — dispatch as separate subagent:
  > **3-layer B-review defense** (T1-B, 2026-07-14):
  > Layer 1 — Agent B gets a `makeDocSummary()` orientation summary; B must Bash-cat
  >   the full file for any citation file:line (playbook §8.2: Bash cat is reliable).
  > Layer 2 — `structured_b_review.py --doc-content` (harness) deterministically
  >   verifies each gap's claims against actual file content (Python open(), not LLM).
  > Layer 3 — `enforce_escalation` computes the round-loop verdict AFTER Layer 2 has
  >   corrected severities. No LLM-verifying-LLM; no hallucinated gaps escaping.

  **Documents for B review** (embedded as `makeDocSummary()` — B must Bash-cat full file for any citation, per playbook §8.2):
  - `01-requirements/SRS.md (full)`
  - `draft 02-architecture/SAD.md (full)`
  - `harness/templates/SAD.md §2.1 (Directory Structure Design Principles)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are TECH_LEAD. Your task: review the following deliverable (SAD.md).
  DOC blocks below are a SUMMARY for orientation — for any citation file:line,
  you MUST re-read the full file via Bash cat first (playbook §8.2).

  === [DOC 1: 01-requirements/SRS.md (full)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 2: draft 02-architecture/SAD.md (full)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 3: harness/templates/SAD.md §2.1 (Directory Structure Design Principles)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  Review checklist:
  - Every FR maps to ≥1 module?
  - NFRs addressed (latency/security/cost)?
  - No circular dependencies?
  - Data flow diagrams consistent?
  - SAB block present in §5 (<!-- SAB:START --> marker exists)?
  - `phase` is a bare int (not quoted string)? e.g. `phase: 2` not `phase: "2"`
  - All NFR `type` values from legal values (documentation/integration/layering/licensing/maintainability/mutation/performance/reliability/security/testability/verifiability/deployability/scalability/usability)?
  - Directory structure follows CRG cohesion principles (SAD.md §2.1)?  Hub coverage per dir, per-function-body calls, entry point placement.  See embedded DOC 3 for the full 6 universal principles.
  - No flat dumps or god-modules? (≤15 files per dir, no single dir with all source)
  - SEC block complete in §6 (<!-- SEC:START --> marker exists; boundaries + threats + verified_by, or an honest applicability: none + justification)?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "docs_embedded":["SRS.md", "SAD.md", "SAD.md \u00a72.1"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  > gaps schema: `[{"severity": "low|medium|high", "message": "...", "fr_id": "FR-XX or null"}]`
  - `APPROVE` + all gaps are `low` → continue to Sub-Task 2/3
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above, replacing `SAD.md` with its updated content)
    → continue to Sub-Task 2/3 only after round-2 APPROVE
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current
  > round is fully APPROVED (including any required round 2).
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

  > fr_id uses P2 as phase-level placeholder; replace with FR-XX for FR-specific plans.

### Sub-Task 2/3: ADR.md — Architecture Decision Records — document key design decisions (tech stack, patterns, interfaces, trade-offs) with context and consequences

**Depends on**: SAD.md (+ Sub-Task 1/3 review: previous review gaps carry forward)
**Agent A**: ARCHITECT
**Agent B**: TECH_LEAD

**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):
- **[A-1]** Agent A (ARCHITECT): Extract key architecture decisions from SAD.md → write individual ADR entries → validate rationale and consequences are recorded
  - FORBIDDEN: vague/non-testable acceptance criteria
- **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`
- **[B-1]** Agent B (TECH_LEAD) — dispatch as separate subagent:
  > **3-layer B-review defense** (T1-B, 2026-07-14):
  > Layer 1 — Agent B gets a `makeDocSummary()` orientation summary; B must Bash-cat
  >   the full file for any citation file:line (playbook §8.2: Bash cat is reliable).
  > Layer 2 — `structured_b_review.py --doc-content` (harness) deterministically
  >   verifies each gap's claims against actual file content (Python open(), not LLM).
  > Layer 3 — `enforce_escalation` computes the round-loop verdict AFTER Layer 2 has
  >   corrected severities. No LLM-verifying-LLM; no hallucinated gaps escaping.

  **Documents for B review** (embedded as `makeDocSummary()` — B must Bash-cat full file for any citation, per playbook §8.2):
  - `Previous Sub-Task B-2 review JSON — SAD.md (Sub-Task 1/3, gaps field may contain non-blocking caveats)`
  - `02-architecture/SAD.md (APPROVED — full content)`
  - `draft 02-architecture/adr/ADR.md (full content)`
  - `harness/templates/ADR.md (template format)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are TECH_LEAD. Your task: review the following deliverable (ADR.md).
  DOC blocks below are a SUMMARY for orientation — for any citation file:line,
  you MUST re-read the full file via Bash cat first (playbook §8.2).

  === [DOC 1: Previous Sub-Task B-2 review JSON — SAD.md (Sub-Task 1/3, gaps field may contain non-blocking caveats)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 2: 02-architecture/SAD.md (APPROVED — full content)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 3: draft 02-architecture/adr/ADR.md (full content)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 4: harness/templates/ADR.md (template format)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  Review checklist:
  - Upstream deliverable review caveats addressed? (check previous B-2 gaps field)
  - All major decisions documented (tech stack, patterns, interfaces)?
  - Each ADR has clear context, decision, and consequences?
  - Alternatives considered documented?
  - Decision aligns with SAD.md architecture?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "docs_embedded":["Previous Sub-Task B-2 review JSON \u2014 SAD.md", "SAD.md", "ADR.md"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  > gaps schema: `[{"severity": "low|medium|high", "message": "...", "fr_id": "FR-XX or null"}]`
  - `APPROVE` + all gaps are `low` → continue to Sub-Task 3/3
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above, replacing `ADR.md` with its updated content)
    → continue to Sub-Task 3/3 only after round-2 APPROVE
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current
  > round is fully APPROVED (including any required round 2).
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

  > fr_id uses P2 as phase-level placeholder; replace with FR-XX for FR-specific plans.

### 📋 Constitution Quality Self-Check — ADR.md

> **Scoped to the ADR file you just wrote.**
> Catches stub-style or low-density ADRs *before* TEST_SPEC.md depends on them.

- **[CONSTITUTION-CHECK-ADR]** Run single-file constitution check:
  ```bash
  python3 harness_cli.py check-constitution \
      --phase 2 \
      --project . \
      --file 02-architecture/adr/ADR.md
  ```
  - PASS → continue to Sub-Task 3/3 (TEST_SPEC.md)
  - FAIL → fix ADR.md (remove `<!-- harness:template-stub -->` if still present; expand decision/rationale/consequences) and re-run until PASS
  - File missing → `[SKIP]` (exit 0) is reported when ADR.md has not been written yet; in that case **escalate** — Sub-Task 2/3 should have produced this file

### Sub-Task 3/3: TEST_SPEC.md — Test Specification Catalog — named test cases from SRS (single source of truth, D4 unified check)

**Depends on**: ADR.md (+ Sub-Task 2/3 review: previous review gaps carry forward)
**Agent A**: ARCHITECT
**Agent B**: TECH_LEAD

**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):
- **[A-1]** Agent A (ARCHITECT): Generate TEST_SPEC.md via derive_test_cases.md skill → preserve TEST_INVENTORY.yaml names where specified → apply Step 1b Architecture-Risk Triggers FIRST (scan SAD modules: shared mutable state → force NP-13; external process → force NP-15; network client/cache → force NP-07; forced cases go in tests/integration/ and are tagged SAD: in Pattern Activation table) → apply 8-Question Protocol per FR (Q1-Q8 + Step 2.5 Interface Contracts + Step 4 Infrastructure Wiring) → fill concrete Inputs + a Sub-assertion predicate table per FR → run check-test-spec-consistency → populate cross-cutting section. **v2.9.1 B.3**: parser expects `### FR-XX: ...` followed by table rows. A prose strategy doc with no table rows will FAIL the D4 spec-coverage check (no vacuous pass when FRs are defined) — re-run this skill if TEST_SPEC.md is wrong shape. **Direction B (Properties)**: If an FR has algebraic invariants, declare a `**Properties**` table for it.
  - FORBIDDEN: vague/non-testable acceptance criteria
- **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`
- **[B-1]** Agent B (TECH_LEAD) — dispatch as separate subagent:
  > **3-layer B-review defense** (T1-B, 2026-07-14):
  > Layer 1 — Agent B gets a `makeDocSummary()` orientation summary; B must Bash-cat
  >   the full file for any citation file:line (playbook §8.2: Bash cat is reliable).
  > Layer 2 — `structured_b_review.py --doc-content` (harness) deterministically
  >   verifies each gap's claims against actual file content (Python open(), not LLM).
  > Layer 3 — `enforce_escalation` computes the round-loop verdict AFTER Layer 2 has
  >   corrected severities. No LLM-verifying-LLM; no hallucinated gaps escaping.

  **Documents for B review** (embedded as `makeDocSummary()` — B must Bash-cat full file for any citation, per playbook §8.2):
  - `Previous Sub-Task B-2 review JSON — ADR.md (Sub-Task 2/3, gaps field may contain non-blocking caveats)`
  - `01-requirements/SRS.md (APPROVED — full content)`
  - `02-architecture/SAD.md (APPROVED — full content)`
  - `02-architecture/adr/ADR.md (APPROVED — full content)`
  - `draft 02-architecture/TEST_SPEC.md (full content)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are TECH_LEAD. Your task: review the following deliverable (TEST_SPEC.md).
  DOC blocks below are a SUMMARY for orientation — for any citation file:line,
  you MUST re-read the full file via Bash cat first (playbook §8.2).

  === [DOC 1: Previous Sub-Task B-2 review JSON — ADR.md (Sub-Task 2/3, gaps field may contain non-blocking caveats)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 2: 01-requirements/SRS.md (APPROVED — full content)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 3: 02-architecture/SAD.md (APPROVED — full content)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 4: 02-architecture/adr/ADR.md (APPROVED — full content)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 5: draft 02-architecture/TEST_SPEC.md (full content)] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  Review checklist:
  - Upstream deliverable review caveats addressed? (check previous B-2 gaps field)
  - Every FR has ≥1 named test case (happy_path + validation mandatory)?
  - 8-Question Protocol applied per FR (Q1-Q8 as applicable by classification, YAML names do NOT exempt missing categories)?
  - Classification assigned per FR (API_ENDPOINT|DATA_ENTITY|ALGORITHM|STATE_MACHINE|INTEGRATION|SECURITY_CONTROL|INFRASTRUCTURE)?
  - NFR Pattern Activation table filled (Step 1 of derive_test_cases.md)?
  - Architecture-risk triggers applied (Step 1b)? SAD modules with shared mutable state → NP-13 forced; external process → NP-15; network client/cache → NP-07. Forced cases recorded in tests/integration/ with SAD: source tag.
  - Every case has concrete Inputs in TRUE form (key="value"), NOT pytest-id form (underscore-replaced)?
  - Sub-assertions table populated per FR (rule_id + predicate + applies_to referencing real case #s)?
  - Self-consistency gate passes? (python3 harness_cli.py check-test-spec-consistency --project .)
  - Direction B property gate passes? (python3 harness_cli.py check-property-spec --project . --no-require-execution)
  - Cross-cutting sections complete (NFR Integration + Deployment Smoke + Backward Compatibility if multi-phase)?
  - Summary table populated with counts per type?
  - All upstream deliverables consistent with each other? No contradictory decisions?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "docs_embedded":["Previous Sub-Task B-2 review JSON \u2014 ADR.md", "SRS.md", "SAD.md", "ADR.md", "TEST_SPEC.md"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  > gaps schema: `[{"severity": "low|medium|high", "message": "...", "fr_id": "FR-XX or null"}]`
  - `APPROVE` + all gaps are `low` → all deliverables complete; proceed to Agent B Peer Review
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above, replacing `TEST_SPEC.md` with its updated content)
    → all deliverables complete; proceed to Agent B Peer Review only after round-2 APPROVE
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current
  > round is fully APPROVED (including any required round 2).
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

  > fr_id uses P2 as phase-level placeholder; replace with FR-XX for FR-specific plans.

### SAB Generation (Machine-Readable Architecture Baseline)

> **CONTRACT**: The SAB block in SAD.md §5 is parsed by
> `core/quality_gate/sab_parser.py:extract_sab_from_sad()`.
> Field names, `sab:` root key, `phase` as **int** (not string), and
> NFR `type` values must match `render_canonical_sab_template()` exactly.
> Do NOT hand-write the YAML — paste from the template below.

- **[SAB-WRITE]** Write the SAB block into `02-architecture/SAD.md` §5
  using the canonical template (replace EXAMPLE values with real project values):
  ```yaml
  sab:
    version: "1.0"
    created_at: "{YYYY-MM-DD}"
    phase: 2  # MUST be int, NOT a string — parser raises on 'phase: "2"'
    project: "{project_name}"
  
    layers:  # EXAMPLE — replace with your project's layers
      - name: api
        modules:
          - name: "app.api.webhooks"
            implemented_in: "app.main"  # OPTIONAL — Use if consolidated into another file
        allowed_dependencies: ["service"]
  
    allowed_dependencies:
      - from: api
        to: service
  
    quality_targets:
      max_complexity: 15
      min_coverage: 80
      max_coupling: 0.3
  
    nfr_dimension_mapping: {}  # OPTIONAL — auto-derived from nfr_traceability.type
  
    nfr_traceability:
      NFR-01:
        # type MUST be one of 14 legal values listed below:
        # Enforceable (mapped to gate dim):
        #   documentation, integration, layering, licensing, maintainability, mutation, performance, reliability, security, testability, verifiability
        # Advisory (no scoring tool, auto-added to advisory_only):
        #   deployability, scalability, usability
        type: performance
        # dimension: OPTIONAL and PREFERRED — the gate dimension this NFR
        #   is scored by, copied verbatim from SPEC.md's own `dimension:`
        #   for this NFR. Outranks the type guess above. `none` = no
        #   automated scorer. A name no gate scores is REFUSED (the error
        #   lists the legal names), never silently dropped.
        target: "p95 < 200ms"  # use ">=N" or "≥N" to raise the gate floor
        module: app.processing.pipeline
  
    advisory_only: []  # AUTO-FILLED by parser — omit or leave []
  
    gate_score_overrides: {}  # AUTO-DERIVED by parser — omit or leave {}
  
    fr_module_traceability:  # EXAMPLE — one entry per FR
      # If an FR owns MULTIPLE modules, use a YAML list instead of a single
      # string, e.g. FR-02: ["app.a", "app.b"] — both forms are supported.
      FR-01: "app.api.webhooks"
  
    architecture_constraints:
      - "no_circular_dependencies"
  
    high_risk_modules:
      - "app.api.webhooks"
  ```

- **[SAB-VALIDATE]** Validate the SAB block before committing:
  ```bash
  python3 harness/scripts/generate_sab.py --validate --project .
  ```
  - MUST exit 0. On failure the message lists the exact problem
    (e.g. unknown NFR type, `phase` as string).
  - Fix and re-run until PASS.

- **[SAB-GENERATE]** Generate `.methodology/SAB.json` from the validated SAB block:
  ```bash
  python3 harness/scripts/generate_sab.py --project .
  ```
  > **Note**: If `SAB.json` already exists and needs regeneration, pass `--overwrite`.
  - SAB.json contains all 14 fields from `SABSpec`:
    version, created_at, phase, project, layers, allowed_dependencies,
    quality_targets, nfr_dimension_mapping, nfr_traceability, advisory_only,
    gate_score_overrides, fr_module_traceability, architecture_constraints,
    high_risk_modules.
  - Used by: drift detector (M2), gate architecture dimension, constitution check
  - Also embedded inline in `quality_manifest.json` via `harness_bridge`

### Security Design (STRIDE-lite Threat Model, Round 10)

> **CONTRACT**: The SEC block in SAD.md §6 is parsed by
> `core/quality_gate/security_design.py:extract_security_block()`.
> Do NOT hand-write the YAML — paste from the canonical template below.
> `applicability: none` + a justification (>=20 chars) is a fully valid,
> honest declaration for a project with no real attack surface.

- **[SEC-WRITE]** Write the SEC block into `02-architecture/SAD.md` §6
  using the canonical template (replace EXAMPLE values with real project values):
  ```python
  from core.quality_gate.security_design import render_canonical_security_template
  print(render_canonical_security_template())
  ```
  `applicability: full` requires >=1 `trust_boundaries` and >=1 `threats` per
  boundary; each threat's `owner_module` must be declared in the §5 SAB block,
  `nfr` (optional) must exist in SRS.md, and `verified_by` names the test that
  proves the mitigation (Step 1c of `derive_test_cases.md` forces this test
  into TEST_SPEC.md; `check-artifact-consistency` requires it exist from Phase 5).

- **[SEC-VALIDATE]** Validate before committing:
  ```bash
  python3 harness_cli.py check-artifact-consistency --project .
  ```
  - MUST exit 0. On failure the message lists the exact rule violated
    (missing block, bad STRIDE category, unregistered owner_module, ...).
  - Fix and re-run until PASS.

### Phase 2 Deliverables
- `SAD.md` — Software Architecture Document (every FR has module mapping)
- `ADR.md` — Architecture Decision Records (tech stack, patterns, interfaces)
- `TEST_SPEC.md` — Test specification catalog (named test cases from SRS, single source of truth — D4 unified check)
- `.methodology/quality_manifest.json` — Quality manifest (FR list + SAB data)
- `.methodology/SAB.json` — Machine-readable architecture baseline
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)

### 📋 Constitution Quality Self-Check

> **Verify document quality meets constitution standards BEFORE peer review.**
> Run this check, fix gaps, and re-run until PASS. This avoids cascading rewrites after Agent B review.

- **[CONSTITUTION-CHECK]** Run constitution self-check:
  ```bash
  python3 harness_cli.py check-constitution --phase 2 --project .
  ```
  - Score must be ≥ constitution composite threshold
  - If **FAIL**: fix documents (add missing keywords), then **re-run until PASS**
  - If **PASS**: proceed to CHECKPOINT-PEER-REVIEW


### 🔒 CHECKPOINT-PEER-REVIEW: Agent B Peer Review — Phase 2 Exit
> Phase 1/2 exit gate = Agent B document review (NOT `harness run-gate --gate 1`).
> APPROVE criteria: all FRs addressed, no critical gaps, terminology consistent.

- **[B-1]** Agent B (TECH_LEAD) — dispatch as separate subagent (holistic review of all deliverables; 3-layer defense, T1-B):
  > **3-layer B-review defense** (T1-B, 2026-07-14):
  > Layer 1 — Agent B gets a `makeDocSummary()` orientation summary; B must Bash-cat
  >   the full file for any citation file:line (playbook §8.2: Bash cat is reliable).
  > Layer 2 — `structured_b_review.py --doc-content` (harness) deterministically
  >   verifies each gap's claims against actual file content (Python open(), not LLM).
  > Layer 3 — `enforce_escalation` computes the round-loop verdict AFTER Layer 2 has
  >   corrected severities. No LLM-verifying-LLM; no hallucinated gaps escaping.

  **Embed ALL deliverables in full** (copy content, not paths):
  > Note: `quality_manifest.json` and `SAB.json` are machine-generated by `generate_sab.py`
  > and are NOT embedded for manual review. Agent B reviews the human-authored documents only.
  - `02-architecture/SAD.md (full content)`
  - `02-architecture/adr/ADR.md (full content)`
  - `02-architecture/TEST_SPEC.md (full content)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are TECH_LEAD. Your task: holistic review of ALL Phase 2 deliverables.
  DOC blocks below are a SUMMARY for orientation — for any citation file:line,
  you MUST re-read the full file via Bash cat first (playbook §8.2).

  === [DOC 1: 02-architecture/SAD.md] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 2: 02-architecture/adr/ADR.md] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  === [DOC 3: 02-architecture/TEST_SPEC.md] ===
  <<embedded as makeDocSummary() — Bash-cat full file for any citation>>

  Review checklist:
  - All FRs covered across all deliverables?
  - No contradictions between deliverables?
  - Each item testable/traceable?
  - All gaps from sub-task reviews addressed?
  - Terminology consistent across all documents?
  - SAB block layers / NFR targets semantically match the module design in SAD §2?
  - Every `fr_module_traceability` entry points to a real module defined in SAD §2?
  - NFR `target` fields contain measurable values (not 'N/A' or empty placeholders)?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "docs_embedded":["SAD.md", "ADR.md", "TEST_SPEC.md"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  - `APPROVE` + all gaps are `low` → proceed to push (CHECKPOINT saved)
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above with updated content) → push only after round-2 APPROVE
  - `REJECT` → fix all gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

- **[B-APPROVAL]** ✅ Persist Agent B approval JSONs for each deliverable to `.methodology/agent_b_approvals/<id>.json`
  > Required by `harness_cli.py advance-phase` via `_verify_agent_b_approvals_core`.
  > Each file MUST contain: `{"fr": "<id>", "review_status": "APPROVE", "reason": "<≥40 chars>", "citations": ["file:line"], "docs_embedded": ["<basename of each source doc>"]}`
  > Phase 2 deliverable IDs = phase deliverables (see `harness_cli.py _PHASE_DELIVERABLES[2]`, e.g., for Phase 1: SRS.md, SPEC_TRACKING.md, TRACEABILITY_MATRIX.md, TEST_INVENTORY.yaml).
  > `<id>` MUST match the full _PHASE_DELIVERABLES[N] entry EXACTLY, including file extension (e.g. `SRS.md` → file `SRS.md.json`). Harness matches `approvals_dir / f"{did}.json"` directly without stem-stripping.
  > Use Bash + Python (harness_cli.py write-approval subcommand if available, else direct Write tool) — do NOT use Edit (whole-file write only).
  > **Retry pattern (orchestrator-level, MAX_PERSIST_ATTEMPTS=3)**: `write-approval` already
  >   self-verifies (write + size + exists check) server-side before printing `[write-approval] OK`,
  >   so retries live at the orchestrator level, not inside a single Bash call: up to 3 independent
  >   dispatches, each running `write-approval` once and checking its own exit code / stdout for the
  >   OK marker. After 3 failed attempts: fail loudly (throw) rather than silently lose the approval.
  > ```bash
  > python harness_cli.py write-approval --fr-id <id> --json '<json>'
  > # exit 0 + `[write-approval] OK` on stdout = success; anything else = this attempt failed,
  > # the orchestrator re-dispatches (up to 3 attempts total) before giving up.
  > ```
  > Rationale: workflow JS sandbox (playbook §3-§4) forbids native fs / child_process; each `await agent()`
  >   call is one LLM-as-shell-wrapper invocation with ~5% random-failure rate. Retrying at the
  >   orchestrator level (independent dispatches) proved more reliable in practice than wrapping the
  >   retry loop inside a single Bash call, since a dispatch that itself failed can't reliably retry itself.

- **[B-PUSH]** ✅ PUSH ② — Push to GitHub + HANDOVER.md — retry until success (CHECKPOINT-PEER-REVIEW saved):
  > Run `push-checkpoint` → if blocked, read the error → fix → re-run until green.
  > Do NOT use `--no-verify` to bypass.
  ```bash
  python3 harness_cli.py push-checkpoint --phase 2 --project .
  ```
  > **Note**: A `[WARN] post-push dirty tree` message may appear if local files were updated. This is non-blocking; do NOT attempt to self-correct.
  > This writes `HANDOVER.md` (crash-recovery checkpoint) to project root,
  > then commits + pushes all changes to origin.
  > After a crash, read HANDOVER.md first — it tells you where you were.

### Phase 2 → Phase 3: Implementation

- Advance FSM to Phase 3 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 2 --project .
  ```
  > **Note**: `advance-phase` will automatically check for harness submodule drift.
  > If it prints a warning that you are behind `origin/main`, it is non-blocking and for your information only.
  > **Sync**: `advance-phase` only commits the handover locally. The workflow orchestrator
  > for this phase runs a separate `git push origin main` immediately after to publish
  > that commit to origin.
- Confirm `HANDOVER.md` reflects Phase 3 entry (`P3-entry` checkpoint, correct plan path)
- Open `phase3_plan.md` and follow from the top.
- If session crashes during Phase 3: read `HANDOVER.md` or run `generate-next-plan`
