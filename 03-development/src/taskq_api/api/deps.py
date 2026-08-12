"""[FR-03, FR-04] Auth dependency wiring — single dependency point.

Citations:
- SPEC.md §3 FR-03 — every `/v1/*` route requires `X-API-Key`; missing
  or invalid key returns 401 + `application/problem+json`.
- SPEC.md §3 FR-04 — scope gate lives here as a single dependency
  point; the rest of the codebase only depends on `api.deps`.
- SAD.md §2.7 — `api.deps` is the hub for auth/scope/rate-limit; it
  is the only place that reads `service.auth` directly.
- SAD.md §3.1 — request lifecycle: handler → `deps.get_current_key`
  → `deps.require_scope(...)` → handler body.

This module re-exports the historical implementation from
`api.tasks` so the SAB declares `taskq_api.api.deps` as the public
import path (per Architecture Amendment Protocol). The
implementation lives in `api.tasks` because the router factory and
its dependencies live together.
"""
from __future__ import annotations

from taskq_api.api.tasks import get_current_key, require_scope

__all__ = ["get_current_key", "require_scope"]
