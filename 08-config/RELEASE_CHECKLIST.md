# RELEASE_CHECKLIST

## Pre-Release Checks
- [ ] All P1-P7 phases completed and artifacts generated.
- [ ] CI pipeline fully passed.
- [ ] Final Sign Off approved.
- [ ] Production environment provisioned.
- [ ] Rollback plan documented.

## Human Context (P8 append)

### Deployment Runbook
- **Primary runbook**: `https://runbooks.internal/taskq-api/release/v1` (versioned; pin to release tag)
- **Per-FR deployment notes**: linked from `.methodology/phase8_plan.md` (one section per FR with smoke-test commands)
- **Architecture-constraint guardrails** (must remain green post-deploy):
  - `no_circular_dependencies`
  - `sqlalchemy_only_in_repository`
  - `single_auth_dependency_at_api_layer`
  - `errors_and_config_are_independence_modules`
  - `fr07_round_trip_must_preserve_data`
  - `rate_limit_update_in_single_transaction_with_row_lock`
- **High-risk modules to monitor first hour post-deploy**: `taskq_api.service.runner`, `taskq_api.service.auth`, `taskq_api.repository.session`, `migrations.versions.v3_split_results`

### Rollback Owner & On-Call
- **Primary rollback owner**: Release captain (assigned per release in PagerDuty schedule `taskq-api-release`)
- **On-call escalation**: PagerDuty `taskq-api-oncall` → secondary `taskq-api-oncall-2`
- **Rollback decision authority**: Release captain + one approver from backend lead or SRE lead (no unilateral rollback in prod)
- **Rollback SLA**: decision within 15 min of incident declaration; execution target 30 min
- **Post-rollback review**: mandatory RCA filed in `.methodology/incidents/` within 48 h

### Post-Release Monitoring Dashboard
- **Primary dashboard**: `https://grafana.internal/d/taskq-api-release` (auto-provisioned per release tag)
- **Key SLOs to watch (first 4 h)**:
  - P95 latency for `POST /tasks` (handler hot path; NFR-01)
  - Auth failure rate (NFR-02, NFR-04)
  - 5xx rate per endpoint (NFR-03)
  - Rate-limit contention (row-lock waits) — NFR-12 / architecture constraint
- **Alert thresholds**: page on-call if any SLO breaches for >5 min sustained
- **Logs dashboard**: `https://logs.internal/d/taskq-api` (Loki; correlation ID `release=<tag>`)
- **Mutation-test score gate**: must remain at or above NFR-08 threshold post-deploy

### Customer Comms Template
```
Subject: [taskq-api] v<VERSION> deployed to production

Hi customers,

We deployed taskq-api v<VERSION> on <DATE>. Release notes: <RELEASE_NOTES_URL>
What's new:
- <FR-1 one-liner>
- <FR-2 one-liner>
What's unchanged:
- Public API surface
- Existing task data (verified by FR-07 round-trip invariant)
Action required:
- None, unless you self-host (see migration notes: <MIGRATION_URL>)
Need help?
- Support: support@taskq.example
- Status: https://status.taskq.example
```
- Comms owner: Product / Customer Success lead
- Send within 1 h of green post-release monitoring window (4 h)
- Status-page update on incident-free deploy; incident comms via `https://status.taskq.example`
