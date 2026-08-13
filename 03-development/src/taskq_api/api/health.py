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
from collections.abc import Callable

from fastapi import APIRouter, Response


def _unwired_probe() -> tuple[bool, str]:
    """[FR-09] Default readiness probe — reports NOT ready.

    Fails closed (SPEC.md:160): a process whose composition root never
    called ``create_health_router`` cannot know its migration state, so
    a direct call to ``readyz`` that bypassed the router MUST NOT
    claim readiness. ``create_app`` always installs the real probe via
    the per-app ``readyz_endpoint`` closure, so this default only
    answers an import-only path.
    """
    return False, "migration state unknown (readyz probe not wired; db unchecked)"


def _readyz_response(
    probe: Callable[[], tuple[bool, str]],
):
    """[FR-09] Materialise the ``/readyz`` response from the probe's verdict.

    ``True`` → ``{"status": "ready", ...}``; ``False`` → 503
    ``application/problem+json`` whose ``detail`` names which check
    failed so the operator can grep ``db`` or ``migration``
    (SPEC §8 #10, #11).

    Any exception raised by the probe (DB connection failure, alembic
    subprocess error, etc.) is materialised as a 503 with the
    exception's name in the detail so the operator can distinguish
    a down DB from a behind-head migration.
    """
    try:
        is_at_head, detail_str = probe()
    except (OSError, RuntimeError, ConnectionError) as exc:
        return Response(
            content=json.dumps({
                "type": "/errors/readyz-failed",
                "title": "Readiness Check Failed",
                "status": 503,
                "detail": f"probe raised {type(exc).__name__}: {exc}",
                "instance": "/readyz",
            }),
            status_code=503,
            media_type="application/problem+json",
        )
    if is_at_head:
        return {"status": "ready", "migration": detail_str}
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
    """[FR-09] Readiness probe — module-level fail-closed default.

    Always reports NOT ready via ``_unwired_probe``. The composition
    root wires the real probe by mounting ``create_health_router``'s
    per-app ``readyz_endpoint`` closure as the actual ``/readyz``
    route handler; this module-level symbol is kept for tests that
    import the handler shape.

    Citations:
    - SPEC.md:157 §3 FR-09 — 200 only when the DB is reachable AND
      alembic is at head; otherwise 503 naming the failed check.
    - SPEC.md:160 §3 FR-09 — behind-head MUST fail closed.
    - SPEC.md:420 §8 #10 — DB down → detail identifies the db.
    - SPEC.md:421 §8 #11 — behind head → detail identifies the migration.
    - SPEC.md:164 §3 FR-10 — non-2xx uses ``application/problem+json``.
    """
    return _readyz_response(_unwired_probe)


def create_health_router(probe: Callable[[], tuple[bool, str]]) -> APIRouter:
    """[FR-09] Build the ``/healthz`` + ``/readyz`` router.

    The ``probe`` callable is invoked on every ``/readyz`` request and
    must return ``(is_at_head, detail)``. Captured in a per-app
    closure (``readyz_endpoint``) so each ``create_app()`` instance
    owns its probe and the test suite's monkey-patch of
    ``taskq_api.app.create_engine`` exercises the DB-down branch
    without leaking across app instances.

    Citations: SPEC.md §3 FR-09; SAD.md §2.8 (``api/health.py`` hub).
    """
    async def readyz_endpoint():
        return _readyz_response(probe)

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
        readyz_endpoint,
        methods=["GET"],
        summary="[FR-09] Readiness probe.",
        description=(
            "GET /readyz — 200 when DB up AND alembic at head; 503 "
            "application/problem+json otherwise (SPEC §3 FR-09 / "
            "SPEC §8 #10, #11)."
        ),
    )
    return router


__all__ = ["healthz", "readyz", "create_health_router"]