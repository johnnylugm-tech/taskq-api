"""[FR-09] Health and readiness probe endpoints.

Citations:
- SPEC.md:156 §3 FR-09 — ``GET /healthz`` returns 200 ``{"status": "ok"}``
  without auth.
- SPEC.md:157 §3 FR-09 — ``GET /readyz`` returns 200 when the DB is
  reachable AND ``alembic current`` == head; otherwise 503 with a body
  stating WHICH check failed ("在 body 說明哪一項失敗").
- SPEC.md:160 §3 FR-09 — the behind-head verdict MUST fail closed so a
  process deployed without its migrations never receives traffic.
- SPEC.md:107 §3 FR-03 — the FR-09 exception carves ``/healthz`` and
  ``/readyz`` out of the auth chain (no ``WWW-Authenticate`` challenge).
- SPEC.md:120 §3 FR-05 — both probes are exempt from the rate limiter.
- SPEC.md:420 §8 #10 — DB down → 503, detail identifies the DB.
- SPEC.md:421 §8 #11 — alembic behind head → 503, detail identifies the
  migration.
- SPEC.md:164 §3 FR-10 — non-2xx responses use ``application/problem+json``.
- SPEC.md:362 §7 — ``api/health.py`` is the declared home for FR-09.
- SAD.md §2.8 — this module is the hub; ``app.py`` mounts the router via
  ``_flat_include_router``.
- SAD.md §3.2 — the ``api`` layer never imports SQLAlchemy directly, so
  the readiness check is injected as a ``probe`` callable by the
  composition root (``app._check_migration_state``), which owns the
  ``create_engine`` binding the TDD suite monkey-patches.
"""
from __future__ import annotations

import json
from typing import Callable, Tuple

from fastapi import APIRouter, Response


def _unwired_probe() -> Tuple[bool, str]:
    """[FR-09] Default readiness probe — reports NOT ready.

    Fails closed (SPEC.md:160): a process whose composition root never
    called ``create_health_router`` cannot know its migration state, so
    ``/readyz`` must not claim readiness. ``create_app`` always installs
    the real probe, so this default only answers a direct import of
    ``readyz`` that bypassed the composition root.
    """
    return False, "migration state unknown (readyz probe not wired; db unchecked)"


# [FR-09] Holds the readiness probe installed by ``create_health_router``.
# Resolved at request time so each ``create_app()`` wires its own probe —
# the test suite monkey-patches ``taskq_api.app.create_engine`` to
# exercise the DB-down branch.
_probe: Callable[[], Tuple[bool, str]] = _unwired_probe


def set_probe(probe: Callable[[], Tuple[bool, str]]) -> None:
    """[FR-09] Install the readiness probe the router's ``/readyz`` will call."""
    global _probe
    _probe = probe


async def healthz() -> dict:
    """[FR-09] Liveness probe — returns 200 ``{"status": "ok"}``.

    Citations:
    - SPEC.md:156 §3 FR-09 — process-alive ping returning
      ``{"status": "ok"}``.
    - SPEC.md:107 §3 FR-03 — the FR-09 carve-out keeps ``/healthz`` out
      of the auth dependency chain, so this handler raises no 401 and
      emits no ``WWW-Authenticate`` challenge.
    - SPEC.md:120 §3 FR-05 — exempt from the rate limiter.
    """
    return {"status": "ok"}


async def readyz():
    """[FR-09] Readiness probe — 200 when ready, else 503 problem+json.

    Delegates the decision to the injected ``probe`` (the composition
    root's ``app._check_migration_state``) and surfaces its detail
    verbatim, so the 503 body states WHICH check failed rather than a
    generic "not ready" (SPEC.md:157).

    Citations:
    - SPEC.md:157 §3 FR-09 — 200 only when the DB is reachable AND
      alembic is at head; otherwise 503 naming the failed check.
    - SPEC.md:160 §3 FR-09 — behind-head MUST fail closed.
    - SPEC.md:420 §8 #10 — DB down → detail identifies the db.
    - SPEC.md:421 §8 #11 — behind head → detail identifies the migration.
    - SPEC.md:164 §3 FR-10 — non-2xx uses ``application/problem+json``.
    """
    is_at_head, detail_str = _probe()
    if not is_at_head:
        return Response(
            content=json.dumps({
                "type": "/errors/readyz-failed",
                "title": "Readiness Check Failed",
                "status": 503,
                "detail": detail_str,
                "instance": "/readyz",
            }),
            status_code=503,
            media_type="application/problem+json",
        )
    return {"status": "ready", "migration": detail_str}


def create_health_router(probe: Callable[[], Tuple[bool, str]]) -> APIRouter:
    """[FR-09] Build the ``/healthz`` + ``/readyz`` router.

    The ``probe`` callable is invoked on every ``/readyz`` request and
    must return ``(is_at_head, detail)``. When ``is_at_head`` is
    ``False`` the router emits a 503 ``application/problem+json``
    envelope whose ``detail`` field is the probe's detail string —
    letting operators grep for ``db`` (DB down) or ``migration``
    (behind head) (SPEC §8 #10, #11).

    Citations: SPEC.md §3 FR-09; SAD.md §2.8 (``api/health.py`` hub).
    """
    set_probe(probe)
    router = APIRouter()
    router.add_api_route(
        "/healthz",
        healthz,
        methods=["GET"],
        summary="[FR-09] Liveness probe.",
        description=(
            "GET /healthz — process-alive ping. No auth, no rate limit "
            "(SPEC §3 FR-09 / FR-03 exception)."
        ),
    )
    router.add_api_route(
        "/readyz",
        readyz,
        methods=["GET"],
        summary="[FR-09] Readiness probe.",
        description=(
            "GET /readyz — 200 when DB up AND alembic at head; 503 "
            "application/problem+json otherwise (SPEC §3 FR-09 / "
            "SPEC §8 #10, #11)."
        ),
    )
    return router


__all__ = ["healthz", "readyz", "create_health_router", "set_probe"]