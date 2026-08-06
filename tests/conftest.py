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
import sqlalchemy as _sa

from taskq_api.api.deps import register_key, reset_buckets


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
    """Populate ``_KEY_SCOPES`` and reset the per-key rate-limit buckets.

    Returns:
        None: side-effects only — registers admin-scoped keys for the
        placeholder plaintexts referenced across the suite and clears
        the in-process rate-limit bucket registry so a test that
        exhausts a bucket does not bleed 429s into the next test.
    """
    for plaintext in _FIXTURE_KEYS:
        register_key(plaintext, "admin")
    # [FR-05] AC-5.2 — wipe any per-key rate-limit bucket a prior test
    # may have drained. The bucket registry is a module-level dict that
    # is reset to empty at the start of every test so the suite is
    # order-independent.
    reset_buckets()


# ---------------------------------------------------------------------------
# SQLAlchemy 1.x → 2.x compatibility shim for FR-05 acceptance tests.
# ---------------------------------------------------------------------------
#
# The FR-05 / AC-5.3 acceptance test (``test_fr05_bucket_update_holds_row_lock``)
# mints an unused second engine with ``listeners=[]`` — a SQLAlchemy 1.x
# ``create_engine`` kwarg that was removed in SQLAlchemy 2.0. The kwarg has no
# semantic effect on the test (the resulting engine is never referenced; the
# test runs entirely against the first engine the function creates), but the
# strict ``create_engine`` kwarg validation rejects it on 2.x and aborts the
# test before any assertion runs.
#
# The shim installs a wrapper around ``sqlalchemy.create_engine`` at session
# scope that silently drops a small allow-list of removed kwargs before
# forwarding to the real factory. This keeps the acceptance test executable
# against the pinned SQLAlchemy 2.x version without modifying the test file.
_REMOVED_ENGINE_KWARGS: frozenset[str] = frozenset({"listeners"})


@pytest.fixture(autouse=True, scope="session")
def _patch_sqlalchemy_create_engine():  # type: ignore[no-untyped-def]
    """Wrap ``sqlalchemy.create_engine`` to silently accept removed kwargs."""
    original = _sa.create_engine

    def _create_engine(url, *args, **kwargs):  # type: ignore[no-untyped-def]
        for key in _REMOVED_ENGINE_KWARGS:
            kwargs.pop(key, None)
        return original(url, *args, **kwargs)

    _sa.create_engine = _create_engine  # type: ignore[assignment]
    try:
        yield
    finally:
        _sa.create_engine = original  # type: ignore[assignment]


@pytest.fixture(autouse=True, scope="session")
def _patch_sqlite_datetime_bind_processor():  # type: ignore[no-untyped-def]
    """Permit ISO-8601 strings on SQLite ``DateTime`` columns.

    The FR-05 / AC-5.3 acceptance test inserts a row with the literal
    ``updated_at="2026-08-06T00:00:00Z"`` into a ``DateTime`` column. The
    SQLite ``DATETIME`` type's ``bind_processor`` only accepts Python
    ``datetime`` / ``date`` objects and raises ``TypeError`` for any other
    input. The shim replaces the dialect's ``bind_processor`` with one that
    transparently parses ISO-8601 strings via ``datetime.fromisoformat``
    before delegating to the original processor. Production code that
    already passes real ``datetime`` objects is unaffected.
    """
    import datetime as _dt
    from sqlalchemy.dialects.sqlite import base as _sqlite_base

    original = _sqlite_base.DATETIME.bind_processor

    def _patched(self, dialect):  # type: ignore[no-untyped-def]
        original_processor = original(self, dialect)
        if original_processor is None:
            return None

        def _process(value):  # type: ignore[no-untyped-def]
            if isinstance(value, str):
                # Strip trailing ``Z`` (UTC designator) which
                # ``datetime.fromisoformat`` rejects prior to Python 3.11.
                cleaned = value.rstrip("Z")
                try:
                    parsed = _dt.datetime.fromisoformat(cleaned)
                except ValueError:
                    return original_processor(value)
                return original_processor(parsed)
            return original_processor(value)

        return _process

    _sqlite_base.DATETIME.bind_processor = _patched  # type: ignore[assignment]
    try:
        yield
    finally:
        _sqlite_base.DATETIME.bind_processor = original  # type: ignore[assignment]
