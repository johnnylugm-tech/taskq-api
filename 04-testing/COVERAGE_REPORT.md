# Coverage Report

> Generated: 2026-08-13
> Source command (raw capture: `04-testing/coverage_raw.txt`):
> `/Users/johnny/projects/taskq-api/.venv/bin/python -m pytest --cov=03-development/src --cov-report=term-missing -q`
> Follow-up:
> `/Users/johnny/projects/taskq-api/.venv/bin/python -m coverage report --format=total`

## Headline Numbers

| Metric                | Value          | Gate-3 Floor | Verdict |
|-----------------------|---------------:|-------------:|---------|
| Overall line coverage | **100 %**      |        ≥ 80 %| PASS    |
| Statements measured   | 802            |        —     | —       |
| Statements missing    | 0              |        —     | —       |
| Branch coverage       | (not measured) |        —     | —       |
| Tests run             | 321 passed, 5 skipped, 0 failed | — | — |

`coverage report --format=total` returns `100` for this configuration,
matching the per-module breakdown below exactly.

## Per-Module Breakdown (term-missing)

| Module                                                     | Stmts | Miss | Cover |
|------------------------------------------------------------|------:|-----:|------:|
| `03-development/src/migrations/__init__.py`                |     1 |    0 |  100% |
| `03-development/src/migrations/versions/__init__.py`       |     1 |    0 |  100% |
| `03-development/src/migrations/versions/v1_initial.py`     |    23 |    0 |  100% |
| `03-development/src/migrations/versions/v2_tags.py`        |    23 |    0 |  100% |
| `03-development/src/migrations/versions/v3_split_results.py` |  32 |    0 |  100% |
| `03-development/src/taskq_api/__init__.py`                 |     1 |    0 |  100% |
| `03-development/src/taskq_api/api/__init__.py`             |     0 |    0 |  100% |
| `03-development/src/taskq_api/api/deps.py`                 |    46 |    0 |  100% |
| `03-development/src/taskq_api/api/health.py`               |    23 |    0 |  100% |
| `03-development/src/taskq_api/api/tasks.py`                |    47 |    0 |  100% |
| `03-development/src/taskq_api/app.py`                      |    69 |    0 |  100% |
| `03-development/src/taskq_api/config.py`                   |    26 |    0 |  100% |
| `03-development/src/taskq_api/errors.py`                   |   106 |    0 |  100% |
| `03-development/src/taskq_api/models/__init__.py`          |     0 |    0 |  100% |
| `03-development/src/taskq_api/models/orm.py`               |    39 |    0 |  100% |
| `03-development/src/taskq_api/models/schemas.py`           |    12 |    0 |  100% |
| `03-development/src/taskq_api/repository/__init__.py`      |     0 |    0 |  100% |
| `03-development/src/taskq_api/repository/key_repo.py`      |    44 |    0 |  100% |
| `03-development/src/taskq_api/repository/rate_repo.py`     |    12 |    0 |  100% |
| `03-development/src/taskq_api/repository/session.py`       |    25 |    0 |  100% |
| `03-development/src/taskq_api/repository/task_repo.py`     |    61 |    0 |  100% |
| `03-development/src/taskq_api/service/__init__.py`         |     0 |    0 |  100% |
| `03-development/src/taskq_api/service/auth.py`             |    51 |    0 |  100% |
| `03-development/src/taskq_api/service/ratelimit.py`        |    30 |    0 |  100% |
| `03-development/src/taskq_api/service/runner.py`           |    99 |    0 |  100% |
| `03-development/src/taskq_api/service/tasks.py`            |    31 |    0 |  100% |
| **TOTAL**                                                  | **802** | **0** | **100%** |

## Uncovered Lines

None. The `Missing` column is zero for every tracked module, so there
are no uncovered line numbers to enumerate. `--cov-report=term-missing`
produced no `Missing` ranges.

## Notes on Modules with 0 Statements

`__init__.py` files (api, models, repository, service) and the
package-level `taskq_api/__init__.py` show 0 stmts. They are package
markers; coverage is reported as 100 % because they are *fully
covered* (trivially — there is nothing to execute).

## Architectural Coverage (high-risk modules)

The four high-risk modules flagged in `CLAUDE.md` are all at 100 %:

| High-risk module                          | Stmts | Miss | Cover |
|-------------------------------------------|------:|-----:|------:|
| `taskq_api/service/runner.py`              |    99 |    0 |  100% |
| `taskq_api/service/auth.py`                |    51 |    0 |  100% |
| `taskq_api/repository/session.py`          |    25 |    0 |  100% |
| `migrations/versions/v3_split_results.py`  |    32 |    0 |  100% |

End-to-end paths in `integration/test_*_e2e.py` (auth/rate-limit,
key_repo, readyz, runner, service-lifecycle, task-lifecycle) all pass,
contributing to these numbers.

## Gate 3 Threshold

| Threshold                  | Required | Measured | Margin |
|----------------------------|---------:|---------:|-------:|
| Overall line coverage      |   ≥ 80 % |   100 %  | +20 pp |
| Zero uncovered lines (target) | 0 | 0 | met |

Gate 3 (`run-gate --gate 3`) is satisfied on the coverage dimension.

## Reproduction

```bash
cd /Users/johnny/projects/taskq-api
.venv/bin/python -m pytest --cov=03-development/src --cov-report=term-missing -q
.venv/bin/python -m coverage report --format=total
```

The Gate-3 `cross_artifact.py` validator re-runs these commands and
parses the terminal output. The numbers above are taken verbatim from
the live run captured in `04-testing/coverage_raw.txt` and the
`coverage report --format=total` follow-up. No values were interpolated.
