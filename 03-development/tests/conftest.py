"""Shared pytest configuration for FR-01 tests.

Adds 03-development/src to sys.path so test modules can import the
taskq_api package directly. Defines autouse fixtures that stub
external side-effects (HMAC verification, DB connection) so each test
fails because the FEATURE is missing, not because of bad signature
or a live DB round-trip.
"""
import sys
from pathlib import Path

import pytest

# Ensure 03-development/src is on sys.path so `from taskq_api.xxx import`
# resolves. Top-level imports in the test file will fail with Collection
# Error (Exit Code 2) when the package is not yet present — that is the
# expected RED state for TDD-RED.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def _reset_taskq_state():
    """Reset in-process task storage so each test starts isolated.

    The FR-01 GREEN step keeps task rows in a module-level registry on
    `TaskRepo` (SQLAlchemy session is replaced with a fresh per-call
    `_FakeSession` in `test_fr01.py::_stub_external_side_effects`).
    Without this reset hook the registry survives across tests and
    pollutes the duplicate-name assertion (`test_fr01_create_task_duplicate_409`).
    Production wiring moves state into the real DB session and this
    fixture becomes a no-op.
    """
    from taskq_api.repository.task_repo import TaskRepo

    TaskRepo._registry.clear()
    TaskRepo._by_name.clear()
    yield
    TaskRepo._registry.clear()
    TaskRepo._by_name.clear()


@pytest.fixture(autouse=True)
def _register_default_api_keys():
    """Pre-register the canonical test API keys so `/v1/*` routes pass the gate.

    FR-04 makes `service.auth.scope_allows` consult the in-process
    `KeyRepo._by_key` / `KeyRepo._registry` side-tables; without a
    registered row, the scope gate rejects every request with 403.
    The pre-existing test suites (FR-01 / FR-02 / FR-03) hand out
    static plaintext keys (e.g. ``test-write-key``) without registering
    them — this autouse fixture bridges the gap so the legacy suites
    continue to authenticate without each test having to wire up a
    `KeyRepo` row of its own. Production wiring moves the key store
    into the real DB and this fixture becomes a no-op.
    """
    from taskq_api.repository.key_repo import KeyRepo

    # Reset the api_keys side-tables so each test starts clean.
    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()

    for scope, raw_key in (
        ("write", "test-write-key"),
        ("read", "test-read-key"),
        ("admin", "test-admin-key"),
        ("write", "fr04-write-key"),
        ("admin", "fr04-admin-key"),
    ):
        key_id = f"key-{scope}-{raw_key}"
        KeyRepo._registry[key_id] = {
            "id": key_id,
            "scope": scope,
            "key_hash": "0" * 64,
            "revoked_at": None,
        }
        KeyRepo._by_key[raw_key] = key_id

    yield

    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()
