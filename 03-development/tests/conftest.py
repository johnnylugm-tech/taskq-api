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
