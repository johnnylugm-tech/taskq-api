# Software Architecture Document (SAD) — {Project Name}

<!-- harness:template-stub -->
<!-- Remove the sentinel line above once you start filling this SAD.
     While present, harness load-context emits a stub warning. -->

> On-demand Lazy Load template.

## 1. Architecture Overview
{High-level architecture description}

### 1.1 System Verification Target
> **Phase 3 Gate 2 Requirement**: The harness executes `make verify-system` at Gate 2.
> If it exits with a non-zero status Gate 2 fails. Add a `verify-system` target to your
> project `Makefile` that assembles and exercises the system end-to-end (e.g. runs your
> integration tests or smoke-test suite). The target name is fixed — the harness always
> calls `make verify-system`.
**Makefile target**: `verify-system`

## 2. Module Design

### 2.1 Directory Structure Design Principles

> **CRG Architecture Scoring**: Phase 3+ judges your code's community cohesion via
> the Code Review Graph (CRG).  CRG groups files by **directory** — each directory
> is one community.  The architecture score is the fraction of communities that are
> "healthy" (internal edge density ≥ 0.3 AND size ≤ 50 nodes).
>
> **CRG scoring formula**: Each community's cohesion = internal_edges / (internal_edges + external_edges).
> External edges = calls to libraries (stdlib, frameworks) + calls to other communities.
> Internal edge dilution is the primary risk — entry points (CLI, main.py) import many libraries,
> producing external edges with no offsetting internal edges unless they also call sibling modules.
> The fix is **not** to reduce library imports — it is to ensure every function body also calls at least one
> sibling within the same directory.
>
> **Required edge budget**: To reach cohesion ≥ 0.3 with E external edges, you need
> I ≥ ceil(0.4286 × E) internal edges. Each function-body call to a hub function = 1 internal edge.
> Module-level calls create 1 edge per file, but per-function-body calls multiply the count.
> Example: 48 external edges → need ≥21 internal edges. With 5 sibling files each having
> 4 function bodies calling 2 hub functions → 40 internal edges — safely above threshold.

**Design for high cohesion from the start — 6 Universal CRG Design Principles:**

**Principle 1 — Use subdirectories to control CRG community boundaries.** CRG assigns one community per directory. If you dump 10+ files into a flat `src/`, CRG's Leiden algorithm freely splits them into unpredictable communities — some will likely fall below the 0.3 cohesion threshold. Explicit subdirectories (`src/api/`, `src/core/`, `src/infrastructure/`) each become one predictable community. Aim for 3-6 source directories total (excluding tests). Fewer than 3 → oversized single community; more than 6 → too many communities to keep all above 0.3.

**Principle 2 — Every directory needs a hub module (≥2 functions for 4+ siblings).** Each directory with ≥2 files must have a shared module (`utils.py`, `common.py`, `helpers.py`) that ≥70% of sibling files import and call via standalone function calls: `result = hub.fn(...)`. This creates cross-file internal edges. Pure library-utility files that no sibling calls produce zero internal edges — they only dilute the community.

For directories with ≥4 sibling files, **one hub function is rarely enough** — a single function called from 5 files produces ~5 edges, which may not offset ~40+ external edges. Use **≥2 hub functions** so each sibling can call both from multiple function bodies, multiplying internal edge count. The tts-new infrastructure directory (5 siblings, 48 external edges) required 2 hub functions (`validate_config` + `get_config_snapshot`) called from every function body to reach ~32 internal edges and pass 0.3.

Exception: directories that form a linear processing pipeline (A→B→C) where each file calls the next in chain.

**Principle 3 — Entry points must live inside a hub directory.** Entry-point modules (CLI, `main.py`, `app.py`, daemon) unavoidably import many external libraries — httpx, FastAPI, argparse, asyncio, etc. Each external import adds an external edge. If the entry point sits alone at the project root (e.g. `src/cli.py`), those external edges dominate and cohesion drops below 0.3. Place entry points inside a directory that also contains a hub module — the entry point calls the hub (internal edges) to compensate for its external edges.

**Principle 4 — Every function body must call a hub function (not just module-level).** A file that is never imported or called by any other file in its directory contributes only external edges (its own imports) and zero internal edges — pure dilution. For each file in your design, verify it is either: (a) the hub module itself, (b) called by the hub, or (c) calls the hub. Files that fail this check should be merged into another file or directory.

Critically, **module-level calls alone are insufficient**. A module-level `_ = validate_config()` creates 1 internal edge per file regardless of how many functions it has. CRG counts edges per (caller_node, callee_node) pair — each function body that calls the hub creates a separate edge. To accumulate enough internal edges (see edge budget above), the hub function must be called **from every accessible function body** in each sibling file, not just at module level. Example: a 5-sibling directory needs ~21 internal edges; 5 module-level calls + 5×4 function-body calls = 25 edges.

**Principle 5 — Respect CRG edge-detection limits.** CRG uses Tree-sitter AST parsing and detects cross-file function calls resolved through imports. These limitations are cross-language:
- Calls between functions in the **same** file — NOT detected (zero cohesion contribution)
- `self.method()` calls inside a class — DETECTED (class hierarchy contributes edges)
- `import sibling` → `sibling.fn()` — DETECTED (cross-file import resolved)
- `result = hub.fn(...)` then `log.info(..., extra=result)` — DETECTED (standalone assignment)
- `log.info(..., extra=hub.fn(...))` — INCONSISTENTLY detected (nested arg position)
- Calls through imports at runtime (lazy imports in `__getattr__`, `__init__.py` re-exports) — may be missed if not statically resolvable

**Principle 6 — Size cap: communities stay under 50 nodes.** CRG marks any community with >50 nodes as unhealthy regardless of cohesion. A node ≈ one function or class in a file. If your directory design would produce >50 nodes (roughly 4-6 modules with 8-12 functions each), split into subdirectories. Unlike Principles 1-5, this can be relaxed slightly — the cap is 50, not 30 — so this is rarely the binding constraint unless you have large god-modules.

| Quick reference | check |
|----------------|-------|
| Source directories count? | 3-6 |
| Each dir has a hub file? | Yes |
| Hub has ≥2 functions if ≥4 sibling files? | Yes |
| Entry points inside a hub dir? | Yes |
| Each function body calls a hub function? | Yes (not just module-level) |
| Cross-file calls use standalone assignment? | Yes |
| Community size ≤ 50 nodes? | Yes |
| Edge budget: I ≥ 0.4286 × E? | Yes |

**Anti-patterns that produce low scores:**

```
❌ src/__init__.py, src/main.py, src/models.py, src/cli.py, src/audio.py
   → 5 isolated files in flat src/, zero cross-imports → cohesion=0.0

❌ src/cli.py  (imports httpx, argparse, asyncio — all external, no internal sibling calls)
   → pure external edges, no compensation → cohesion near 0

❌ tests/test_fr01.py, tests/test_fr02.py, ... tests/test_fr08.py
   → 80 nodes in one dir, no internal edges → oversized + zero cohesion

✅ src/api/{cli,main,speech,utils}.py with utils imported by all siblings → hub-and-spoke
✅ src/engines/{synthesis,splitter,parser}.py with synthesis calling both → pipeline chain
✅ src/infrastructure/{circuit,health,config,models}.py → shared domain layer
```

### 2.2 {Module Name}

| Attribute | Value |
|-----------|-------|
| Responsibility | {responsibility} |
| External Interface | {API} |
| Dependencies | {dependency modules} |

#### Logical Constraints
- {constraint 1}
- {constraint 2}

## 3. Error Handling
| Level | Handling Strategy |
|-------|------------------|
| Level 1 | Immediate return |
| Level 2 | Retry 3 times |
| Level 3 | Graceful degradation |

## 4. Technology Choices
| Technology | Rationale |
|------------|----------|
| {technology} | {reason} |

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as int must
> match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`.
> Do NOT hand-write the YAML — paste from the canonical template and replace
> EXAMPLE values with your project's real values.
> Validate before committing: `python3 scripts/generate_sab.py --validate --project .`

<!-- SAB:START -->
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
    - name: service
      modules: ["app.service.handlers"]
      allowed_dependencies: []

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
<!-- SAB:END -->

Note: Fill in the YAML above — it is used for Drift Detection and gate scoring.
Generate: `python3 scripts/generate_sab.py --project . [--overwrite]`

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

> **CONTRACT**: Field names and the `security_design:` root key are parsed
> by `core/quality_gate/security_design.py:extract_security_block()`.
> Do NOT hand-write the YAML — paste from the canonical template and
> replace EXAMPLE values with your project's real values.
> Validate: `python3 harness_cli.py check-artifact-consistency --project .`
>
> `applicability: none` is a fully valid, honest declaration for a project
> with no real attack surface (e.g. a pure CLI formatting tool) — it
> requires a `justification` (>=20 chars) and skips the rest of this
> block. This is a decidable structural check, not a keyword scorer: an
> honest `none` always passes.

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full   # full | none — none REQUIRES justification and skips the rest
  justification: ""     # required (>=20 chars) when applicability: none
  trust_boundaries:     # EXAMPLE — replace with your project's real boundaries
    - id: TB-01
      name: "external HTTP input"
      description: "requests crossing from unauthenticated clients into the API layer"
  threats:              # STRIDE-lite — every boundary needs >=1 threat
    - id: T-01
      boundary: TB-01
      category: tampering   # spoofing|tampering|repudiation|information_disclosure|denial_of_service|elevation_of_privilege
      description: "malformed payload mutates task state without validation"
      mitigation: "schema validation + reject on unknown fields"
      owner_module: "app.api.webhooks"   # MUST be a module declared in the SAB block (§5)
      nfr: NFR-02                        # optional — MUST exist in SRS when present
      verified_by: "test_sec_t01_malformed_payload_rejected"   # single test name only — NOT "test_a, test_b"; split multi-test threats into separate T-NN entries
```
<!-- SEC:END -->

Note: `owner_module` must name a module declared in the §5 SAB block;
`nfr` (optional) must exist in SRS.md; `verified_by` names the test that
proves the mitigation — from Phase 5 onward, `check-artifact-consistency`
blocks if that test doesn't exist yet. Threats also seed
`bug-hunt-targets`' adversarial-review targeting and force NFR-pattern
test cases in `derive_test_cases.md` Step 1c regardless of SRS keywords.
