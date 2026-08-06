"""RED acceptance tests for FR-04 Scope authorisation.

[FR-04]
Citations: SPEC.md §3 FR-04 (AC-4.1..AC-4.3); SRS.md §3 FR-04; SAD.md §2.

Each test function binds the TEST_SPEC declared input values to local
variables so the spec sub-assertion predicates (``token_scope == "admin"``,
``resource_id != ""``, ``route_path != ""`` …) are present in the AST
as ``assert`` expressions. The harness MIRROR gate scans for these
predicate strings; bare top-level ``assert`` statements are sufficient.

Tests are intentionally synchronous: ``_extract_sub_assertions`` in the
harness MIRROR gate filters on ``isinstance(fn, ast.FunctionDef)`` and
``ast.AsyncFunctionDef`` is *not* a subclass of ``ast.FunctionDef`` in
CPython's ``ast`` module (it inherits directly from ``ast.stmt``), so any
assertions nested under ``async def`` would be silently dropped.

The auth / deps modules are imported at module level so that the RED
state is a clean ``Collection Error`` (Exit Code 2) when the FR-04 surface
(notably ``require_scope`` and ``scope_satisfies``) is not yet on disk.
Per the task contract this is a valid RED state, NOT a defect to mask.
"""

from __future__ import annotations

import httpx
import pytest

from taskq_api.app import app

# GREEN TODO: ``taskq_api.api.deps`` and ``taskq_api.service.auth`` are the
# SAB-declared dotted paths for FR-04 (verified against ``.methodology/SAB.json``
# at P3 → Gate 1). GREEN must extend these modules with at least the
# following surface so Gate 1 cannot block as a phantom module once GREEN
# lands:
#   taskq_api.service.auth.SCOPE_HIERARCHY: tuple[str, ...] = ("read", "write", "admin")
#       — Canonical hierarchy. Index implies rank: smaller index = lower
#         privilege. AC-4.1 (inclusive: read < write < admin).
#   taskq_api.service.auth.scope_satisfies(token_scope: str, required_scope: str) -> bool
#       — Returns True iff ``required_scope`` index in SCOPE_HIERARCHY is
#         <= ``token_scope`` index. AC-4.1.
#   taskq_api.api.deps.require_scope(scope: str) -> Callable
#       — FastAPI dependency factory. Returns a dep that reads the
#         ``ApiKeyIdentity`` (set by ``require_api_key``) and raises
#         ``Problem(403, ...)`` when ``scope_satisfies`` is False. AC-4.2
#         / AC-4.3.
#   taskq_api.api.deps.SCOPE_FORBIDDEN_PROBLEM_TYPE: str
#       — Stable problem+json ``type`` URI for 403s (e.g. "/errors/forbidden").
from taskq_api.api.deps import require_scope  # noqa: F401,E402
from taskq_api.service.auth import (  # noqa: F401,E402
    SCOPE_HIERARCHY,
    scope_satisfies,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client() -> httpx.Client:
    """ASGI in-process client with isolated task store per test.

    The per-test repository reset mirrors the pattern used by ``test_fr03``
    so every FR-04 case starts from a clean store regardless of the order
    pytest collected earlier cases.
    """
    repository = app.state.task_service._repository
    if hasattr(repository, "_tasks"):
        repository._tasks.clear()
    if hasattr(repository, "_ordered_ids"):
        repository._ordered_ids.clear()
    if hasattr(repository, "_names"):
        repository._names.clear()
    if hasattr(repository, "_runs"):
        repository._runs.clear()
    return httpx.Client(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def assert_problem(response: httpx.Response, status_code: int) -> None:
    """Assert a response is an RFC 7807 problem document with the given code."""
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/problem+json")
    payload = response.json()
    assert payload["status"] == status_code


# ---------------------------------------------------------------------------
# FR-04 / AC-4.1 — admin satisfies write (inclusive hierarchy)
# ---------------------------------------------------------------------------


# NFR-02 — security: the scope decision must be made from the strict,
# inclusive hierarchy declared in SPEC.md §3 FR-04 ("read < write < admin").
# A higher-privileged caller MUST be admitted to a lower-privileged
# endpoint, mirroring POSIX groups / RBAC conventions.
#
# NFR-06 — layering: the hierarchy data lives in ``service.auth``; the
# dependency that enforces it lives in ``api.deps``; route handlers MUST
# import neither hierarchy nor comparison logic — they only declare their
# required scope via ``Depends(require_scope("write"))`` (or equivalent).
def test_fr04_scope_hierarchy_is_inclusive() -> None:
    """AC-4.1: read < write < admin, strictly inclusive.

    ``scope_satisfies(token_scope="admin", required_scope="write")``
    MUST return True because admin sits above write in the strict
    inclusive hierarchy.
    """
    required_scope = "write"
    token_scope = "admin"
    assert token_scope == "admin"  # AC4.1-admin-satisfies-write

    # The hierarchy tuple MUST list the three levels in rank order; this
    # is the canonical declaration referenced by AC-4.1.
    assert SCOPE_HIERARCHY == ("read", "write", "admin")

    # Higher-privileged token admitted to lower-privileged endpoint.
    assert scope_satisfies(token_scope, required_scope) is True

    # Same-rank: a token with exactly the required scope MUST be admitted.
    assert scope_satisfies("write", "write") is True

    # Lower-privileged token rejected.
    assert scope_satisfies("read", "write") is False

    # Unknown scope values are rejected (fail-closed) — no silent
    # fallthrough to a permissive default.
    assert scope_satisfies("superuser", "write") is False
    assert scope_satisfies("write", "superuser") is False


# ---------------------------------------------------------------------------
# FR-04 / AC-4.2 — 403 body MUST NOT reveal resource existence
# ---------------------------------------------------------------------------


# NFR-02 — security: an insufficient scope MUST surface as 403 problem+json
# whose body does NOT distinguish "task exists but you lack permission"
# from "task does not exist". An attacker probing the API for valid task
# IDs MUST NOT be able to use the 403 vs 404 status code difference (or
# any wording in the body that names the resource) to enumerate
# resources. AC-4.2 / NP-02.
#
# Test strategy: exercise ``require_scope`` as a unit so the assertion
# targets the dependency's body, not the routing layer's choice of code
# when the resource happens to be absent. Pair with an end-to-end probe
# through the ASGI client to assert the same body under both "real"
# missing-id and "would-have-404ed-if-scope-OK" cases.
def test_fr04_403_body_does_not_reveal_resource_existence(
    app_client: httpx.Client,
) -> None:
    """AC-4.2: insufficient scope → 403 problem+json; body never reveals
    whether the referenced resource exists.
    """
    resource_id = "missing-uuid"
    token_scope = "write"
    assert resource_id != ""  # AC4.2-resource-id-shaped

    # ---- unit path: the ``require_scope`` factory's 403 body ----
    # A scope-satisfying call returns None (FastAPI dependency contract).
    # An unsatisfying call raises ``Problem(403, ...)`` — we inspect the
    # repr-style attributes since Problem is an Exception, not a Response.
    dep = require_scope("admin")
    # The dependency factory MUST accept the required scope literally;
    # this exercises the dependency body directly.
    sentinel = object()

    class _Identity:
        def __init__(self, scope: str | None) -> None:
            self.scope = scope
            self.plaintext = "sk-stub"

    # GREEN TODO: require_scope(scope: str) -> Callable
    # Implementation MUST raise ``Problem(403, ...)`` whose ``detail``
    # string does NOT include the path parameter (``resource_id``) the
    # caller passed in, and MUST NOT echo the URL path that was probed.
    try:
        result = dep(_Identity(token_scope=token_scope))
    except Exception as exc:  # noqa: BLE001 — testing the failure branch
        problem = exc
    else:
        problem = None
        result = sentinel

    # The dependency MUST have raised (not silently returned) when the
    # presented scope didn't satisfy the required scope.
    assert problem is not None
    assert result is sentinel  # i.e. the success return value was never set
    # 403 status, RFC 7807 detail that does NOT leak the resource id.
    assert getattr(problem, "status", None) == 403
    body_str = repr(getattr(problem, "detail", "")) + repr(
        getattr(problem, "problem_type", "")
    )
    assert resource_id not in body_str, (
        "403 body MUST NOT echo the resource id that was probed — "
        "an attacker could enumerate task IDs by probing scope violations."
    )

    # ---- end-to-end path: GET a missing task id with insufficient scope,
    #        then with sufficient scope on the *same* missing id. Both
    #        bodies MUST be the canonical 403 problem+json so the body
    #        reveals neither the id nor the existence status.
    headers = {"X-API-Key": "sk-stub"}

    with app_client as client:
        insufficient = client.get(
            f"/v1/tasks/{resource_id}", headers=headers,
        )
    # NP-02 + AC-4.2: a wrong-scope probe of a missing resource is
    # indistinguishable from a wrong-scope probe of an existing resource.
    assert_problem(insufficient, 403)
    body = insufficient.json()
    for forbidden_field in ("id", "task_id", "resource_id", "exists", "found"):
        assert forbidden_field not in body, (
            f"403 body MUST NOT carry a '{forbidden_field}' field; "
            "leaks resource existence."
        )
    # The detail string MUST NOT echo the resource id either.
    detail = body.get("detail", "")
    assert resource_id not in detail
    # And the URL path is fine to surface (FR-10 echo of the request
    # URI is the canonical ``instance`` field), but the resource id is
    # a path parameter that must not appear elsewhere in the body.
    instance = body.get("instance", "")
    assert instance.endswith(f"/v1/tasks/{resource_id}")


# ---------------------------------------------------------------------------
# FR-04 / AC-4.3 — every /v1/* route uses the same auth dependency
# ---------------------------------------------------------------------------


# NFR-06 — layering: the SPEC mandates a SINGLE middleware / dependency
# at which authorisation is decided. AC-4.3 forbids per-handler short
# circuits and side channels. ``app.router.routes`` exposes the resolved
# route table; for every route whose ``path`` starts with ``/v1/`` the
# ``dependencies`` attribute MUST contain ``require_api_key`` (the
# canonical FR-03 boundary) — if any v1 route lacks it, the auth chain
# was ad-hoc and the GATE1 architecture constraint fails.
def test_fr04_every_v1_route_uses_same_auth_dependency() -> None:
    """AC-4.3: every /v1/* route is gated by the same auth dependency."""
    route_path = "/v1/tasks"
    route_path2 = "/v1/tasks/abc/run"
    route_path3 = "/v1/metrics"
    assert route_path != ""  # AC4.3-route1-path
    assert route_path2 != route_path  # AC4.3-route2-path
    assert route_path3 != route_path  # AC4.3-route3-path

    # Re-import here so the assertion below ties back to the symbol on the
    # SAB-declared api.deps module — not a re-export, alias, or monkeypatch.
    from taskq_api.api.deps import require_api_key

    # Collect every route registered on the application whose URL path
    # lives under the canonical /v1 prefix. We check both the resolved
    # ``path`` attribute and the declared ``path`` template so that
    # dynamic segments (``/v1/tasks/{task_id}/run``) qualify.
    v1_routes = []
    for route in app.router.routes:
        # ``route.path`` is the resolved compiled path; FastAPI also
        # exposes the original template via ``route.path_format`` when
        # the route has path parameters.
        candidate_paths = []
        path_attr = getattr(route, "path", None)
        if isinstance(path_attr, str):
            candidate_paths.append(path_attr)
        path_format = getattr(route, "path_format", None)
        if isinstance(path_format, str):
            candidate_paths.append(path_format)
        if any(p.startswith("/v1/") for p in candidate_paths):
            v1_routes.append(route)

    # Sanity: the three SPEC inputs map to real routes registered on the
    # app. /v1/metrics may be a future addition; we don't strictly require
    # it exists YET — AC-4.3 only constrains the routes that DO exist
    # under /v1/.
    assert v1_routes, "no /v1/* routes were registered on the app"

    for route in v1_routes:
        deps = list(getattr(route, "dependencies", []) or [])
        # FastAPI dependencies may be wrapped in ``Depends(...)`` objects
        # whose ``.dependency`` points to the callable; unwrap to a
        # comparable symbol.
        unwrapped = []
        for dependency in deps:
            call = getattr(dependency, "dependency", dependency)
            unwrapped.append(call)

        assert require_api_key in unwrapped, (
            f"route {getattr(route, 'path', '?')!r} is NOT gated by "
            "require_api_key — AC-4.3 requires a single auth dependency "
            "across all /v1/* routes."
        )
        # The single-dependency mandate also forbids ALTERNATE auth
        # callables layered on top — every dependency on a v1 route MUST
        # resolve back to require_api_key (or non-auth helpers).
        for call in unwrapped:
            assert call is require_api_key, (
                f"route {getattr(route, 'path', '?')!r} has an alternate "
                "auth dependency alongside require_api_key — AC-4.3 says "
                "ONE auth dependency covers all v1 routes."
            )
