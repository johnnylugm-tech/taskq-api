# CONFIG_RECORDS.md - taskq-api

> On-demand Lazy Load template.

## 1. Version Information
- Version: vharness-v4-20260813-score95-28-geb2ad11
- Git Commit: eb2ad11
- Release Date: 2026-08-13

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | {{config}} |
| Production | {{config}} |

## 3. Dependency List
```
{{pip freeze / npm lock output}}
```

## 4. Environment Variables
| Variable | Type | Description |
|----------|------|-------------|
| {{VAR}} | secret | {{description}} |

## 5. Deployment Log
| Date | Version | Method | Executor |
|------|---------|--------|----------|
| 2026-08-13 | harness-v4-20260813-score95-28-geb2ad11 | {{method}} | {{name}} |

## 6. Configuration Change Log
| Phase | Change | Rationale |
|-------|--------|----------|
| Phase 8 | {{change}} | {{reason}} |

## 7. Rollback SOP
**Trigger Condition**: {{condition}}
**Commands**:
```bash
{{rollback commands}}
```

## 8. Configuration Compliance
- [ ] Phase 7 risk mitigations implemented
- [ ] Monitoring thresholds configured
- [ ] Circuit breaker enabled

## Human Context (P8 append)

### Ownership per Config Item
| Config Domain | Primary Owner | Backup Owner | Source-of-Truth Module |
|---------------|---------------|--------------|------------------------|
| Auth secrets (JWT signing key, OAuth client secret) | Platform/Security team | Backend lead on-call | `taskq_api.service.auth`, `taskq_api.errors.config` |
| Database connection string & pool sizing | Platform/DBA | Backend lead on-call | `taskq_api.repository.session` |
| Rate-limit thresholds & window | Service owner | On-call backend | `taskq_api.service.runner` (transactional w/ row lock per architecture constraint) |
| Feature flags | Product owner | Engineering lead | `taskq_api.errors.config` (independent config module) |
| Logging / observability endpoints | Platform/SRE | On-call SRE | `taskq_api.errors.config` |
| Migration scripts (Alembic) | Backend lead | DBA | `migrations.versions.v3_split_results` (high-risk module) |

### Secret Rotation Cadence
| Secret | Rotation Period | Rotation Trigger | Verification |
|--------|-----------------|------------------|--------------|
| JWT signing key (`JWT_SECRET`) | 90 days | Scheduled + on suspected compromise | Verify `taskq_api.service.auth` boots; smoke test login flow |
| DB credentials | 90 days | Scheduled; immediate on personnel change | `psql` connect + `taskq_api.repository.session` smoke |
| OAuth client secret | 180 days | Provider-driven (forced rotation) | Re-run OAuth integration test (`test_fr04_*`) |
| API key for upstream integrations | 180 days | Scheduled; on contractor offboarding | Verify outbound call succeeds; check 4xx/5xx in dashboard |
| Encryption-at-rest key (KMS) | Annual | Compliance schedule | Audit log entry + KMS re-encrypt dry run |

### Access Audit Log Reference
- **Auth & Authorization events**: `auth.audit.log` (append-only, shipped to SIEM within 5 min)
- **Configuration mutations**: `config.audit.log` — every change to `taskq_api.errors.config` produces a structured log entry with `{actor, timestamp, diff_hash, justification}`
- **Secret access**: `secret.access.log` — every read of a secret value (not just metadata) is recorded for forensic review
- **Deployment events**: `deploy.audit.log` — CI/CD pipeline emits per-environment deployment records with commit SHA, approver, and rollback target
- **Retention**: 365 days hot, 7 years cold (compliance requirement)
- **Audit dashboard**: internal SRE runbook `https://runbooks.internal/audit/config-access`
- **Quarterly review**: Security team performs access recertification; results filed under `.methodology/audit-reviews/`
