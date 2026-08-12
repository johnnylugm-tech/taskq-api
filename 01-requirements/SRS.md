# Software Requirements Specification (SRS) — {Project Name}

<!-- harness:template-stub -->
<!-- Remove the sentinel line above once you start filling this SRS.
     While present, harness load-context emits a stub warning. -->

> On-demand Lazy Load template.

## 1. Requirements Overview
{Brief description of project goals}

## 2. Functional Requirements

| ID | Requirement Description | Implementation Function (est.) | Verification Method |
|----|------------------------|-------------------------------|--------------------|
| FR-01 | {requirement} | {function_name} | {verification} |
| FR-02 | ... | ... | ... |

## 3. Non-Functional Requirements (NFR)

| ID | Type | Requirement | Test Method |
|----|------|-------------|-------------|
| NFR-01 | Performance | {requirement} | {test method} |
| NFR-02 | Security | {requirement} | {test method} |

## 4. Constraints
- {constraint 1}
- {constraint 2}

## 5. Glossary
| Term | Definition |
|------|------------|
| {term} | {definition} |

## 6. Cross-Cutting Test Requirements

> 此章節由 harness P1 模板自動注入，開發者必須填入具體測試名稱後才可進入 P2。
> `verify-spec` 檢查的是 FR→module 追溯完整性（實作是否存在），並非本節
> 的 placeholder 是否已填——填寫本節由人工 review + Agent B P2 審查把關。

### API Completeness（每個端點必須有以下四類測試）
- 正常流程 (2xx)
- 認證失敗 (401)
- 速率限制 (429)
- 驗證錯誤 (400/422)

**待填清單**（開發者補充）：
- [ ] `test_<endpoint>_<scenario>_returns_<status>`
- [ ] ...

### Security Red Team
- [ ] `test_redteam_prompt_injection_direct_<entrypoint>_payload`
- [ ] `test_redteam_rate_limit_burst_attack_blocked`
- [ ] `test_redteam_pii_mixed_<type>_leak_detected`

> These are examples, not an enforced list — the enforced mechanism is
> `02-architecture/SAD.md` §6's STRIDE-lite threat model: every declared
> `threats[]` entry names a `verified_by` test that `check-artifact-
> consistency` requires to exist on disk from Phase 5 onward, and each
> threat forces its matching NFR test pattern in `derive_test_cases.md`
> Step 1c regardless of SRS keywords. Use this section for narrative
> intent; SAD.md §6 is where red-team coverage becomes binding.

### KPI Gates（對應 ODD SQL + k6）
- [ ] `test_kpi_p95_latency_phase<N>_under_<X>s`
- [ ] `test_kpi_fcr_phase<N>_target_<X>_percent`

### Deployment Smoke
- [ ] `test_deploy_docker_compose_all_services_healthy`
- [ ] `test_deploy_health_endpoint_returns_200_after_startup`
- [ ] `test_backup_pg_basebackup_and_restore` (Phase 3+)

### Version Consistency（Phase 2+ 必填）
- [ ] `test_backward_compat_phase<N-1>_tests_pass_in_phase<N>_env`

---

## 7. FR Block (machine-readable)

<!-- FR:START -->
```json
{
  "version": "1.0",
  "created_at": "{YYYY-MM-DD}",
  "phase": 1,
  "project": "{project_name}",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "{requirement description}",
      "implementation_functions": ["{function_name}"],
      "verification_method": "{verification}"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "documentation|integration|layering|licensing|maintainability|mutation|performance|reliability|security|testability|verifiability|deployability|scalability|usability",
      "description": "{requirement description}",
      "test_method": "{test method}"
    }
  ]
}
```
<!-- FR:END -->

Note: `type:` must be one of the values above — this list mirrors
`harness/core/quality_gate/sab_parser.ALL_NFR_TYPES` (the vocabulary Phase 2's
`generate_sab.py --validate` enforces) and is pinned by
`tests/test_sab_parser.py::TestCanonicalTemplate::test_srs_template_nfr_type_example_matches_vocabulary`;
if it ever falls out of sync that test fails.

Note: Fill in the JSON above - used for downstream requirements traceability.
Ingestion mode (PROJECT_BRIEF.md declares a `canonical_spec`): every `### FR-NN`
in the canonical source MUST appear here, and every FR here MUST trace back to a
canonical clause — `harness_cli.py check-spec-alignment` blocks on a dropped or
invented requirement. Defer a canonical FR you cannot yet transcribe as
`FR-NN-deferred` / NFR-99 rather than omitting it.
