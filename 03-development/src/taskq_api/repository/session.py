"""[FR-01, FR-06] Session lifecycle.

Citations:
- SPEC.md §3 FR-06: repository owns Session lifecycle; transaction
  boundary via context manager.
- SAD.md §2.5 — `session.py` exports `unit_of_work()`; every
  commit/rollback boundary goes through it.

GREEN step keeps the surface minimal: `get_session()` returns a fresh
session per request. Tests stub this with a `_FakeSession` (see
`03-development/tests/test_fr01.py::_stub_external_side_effects`).
"""
from __future__ import annotations


def get_session() -> "object":
    """Return a new Session for the request unit-of-work.

    Citations: SAD.md §2.5 — `Session` lifecycle owned by repository;
    every commit/rollback boundary goes through it.
    """
    # Production would instantiate a SQLAlchemy `Session(bind=engine)`
    # wrapped in a context manager. The GREEN step exercises only the
    # contract — tests patch this function via `monkeypatch.setattr`.
    raise RuntimeError(
        "taskq_api.repository.session.get_session must be wired by the "
        "deployment layer (Phase 4) or stubbed in tests."
    )
