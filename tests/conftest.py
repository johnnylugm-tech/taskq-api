"""Shared pytest fixtures for the FR-01..FR-04 acceptance suite.

[FR-03] [FR-04]

The FR-04 GREEN wiring of ``require_api_key`` (see ``taskq_api.app``)
made the X-API-Key dependency actually run on every ``/v1/*`` request.
Earlier FR tests (``test_fr01``, ``test_fr02``, ``test_fr03``) carry
placeholder key strings (``fr01-fixture-placeholder``,
``fr02-fixture-placeholder``, ``sk-stub`` …) in their request
headers. Without a registration hook those placeholders now surface
as 403 "Insufficient scope" and break FR-01/FR-02 happy paths.

This conftest seeds the in-process ``_KEY_SCOPES`` registry in
``taskq_api.api.deps`` with the keys used by the existing tests so
the suite runs end-to-end without rewriting every prior FR's test
file. Each key is given an ``admin`` scope so scope-restricted
endpoints (``require_scope('write')``) also admit them.

``sk-stub`` is intentionally NOT registered — test_fr04 uses it to
probe the unregistered-key branch of ``require_api_key`` (AC-4.2
anti-enumeration guarantee), so it must continue to surface as 403.
"""

from __future__ import annotations

import pytest

from taskq_api.api.deps import register_key


# Placeholder API keys used by the per-FR test files. Each is registered
# with ``admin`` scope so the fixtures exercise both auth (FR-03) and
# the scope hierarchy (FR-04).
_FIXTURE_KEYS: tuple[str, ...] = (
    "fr01-fixture-placeholder",
    "fr02-fixture-placeholder",
    "sk-plaintext-never-stored",
    "sk-secret-plain",
    "sk-once-print",
    "sk-revoked-example",
    "sk-active-example",
    "sk-valid-plaintext",
)


@pytest.fixture(autouse=True)
def _register_fixture_api_keys() -> None:
    """Populate ``_KEY_SCOPES`` with the keys used by prior FR tests.

    Returns:
        None: side-effects only — registers admin-scoped keys for the
        placeholder plaintexts referenced across the suite.
    """
    for plaintext in _FIXTURE_KEYS:
        register_key(plaintext, "admin")
