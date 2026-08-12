"""[FR-01, FR-02, FR-06] Session lifecycle.

Citations:
- SPEC.md §3 FR-06 — repository owns Session lifecycle; transaction
  boundary via context manager.
- SAD.md §2.5 — `session.py` exports `unit_of_work()`; every
  commit/rollback boundary goes through it.

GREEN step keeps the surface minimal: `get_session()` returns a fresh
session per request. Tests stub this with a `_FakeSession` (see
`03-development/tests/test_fr01.py::_stub_external_side_effects`).
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator


def get_session() -> Any:
    """[FR-06] Return a new Session for the request unit-of-work.

    Citations:
    - SPEC.md §3 FR-06 — repository owns the Session; the business /
      API layers never see one.
    - SAD.md §2.5 — `get_session()` is the single acquisition point;
      every commit/rollback boundary goes through `unit_of_work()`.
    """
    # Production would instantiate a SQLAlchemy `Session(bind=engine)`
    # wrapped in a context manager. The GREEN step exercises only the
    # contract — tests patch this function via `monkeypatch.setattr`.
    raise RuntimeError(
        "taskq_api.repository.session.get_session must be wired by the "
        "deployment layer (Phase 4) or stubbed in tests."
    )


@contextlib.contextmanager
def unit_of_work() -> Iterator[Any]:
    """[FR-06] Context manager wrapping a single repository transaction.

    Acquires a Session through :func:`get_session`, yields it for the
    caller, and — on normal exit — calls ``session.commit()``. On ANY
    exception raised inside the ``with`` block, the manager invokes
    ``session.rollback()`` and re-raises the original error. There is
    no other commit/rollback path in the repository; this is the
    SPEC §3 FR-06 transaction boundary.

    Citations:
    - SPEC.md §3 FR-06 — every API request uses exactly one Session;
      success commits, exception rolls back, boundary owned by the
      repository layer.
    - SPEC.md §4 NFR-03 — exceptions roll back instead of silently
      committing partial state.
    - SAD.md §2.5 — ``unit_of_work()`` is the canonical boundary.
    """
    session = get_session()
    try:
        yield session
    except BaseException:
        # Any exception (including ``BaseException`` subclasses such as
        # ``asyncio.CancelledError`` per NFR-03) triggers a rollback so
        # no partial state escapes the unit-of-work. ``rollback()`` is
        # best-effort: if it raises we still re-raise the original
        # exception so the caller's error handling is not masked.
        _safe_rollback(session)
        raise
    else:
        # Normal exit — commit. If commit itself raises, roll back so
        # we never leave the session in an inconsistent state.
        try:
            session.commit()
        except Exception:
            _safe_rollback(session)
            raise


__all__ = ["get_session", "unit_of_work"]


def _safe_rollback(session: Any) -> None:
    """Best-effort rollback that swallows secondary exceptions.

    Rollback failures must not mask the original error from the caller
    (NFR-03). The original exception is re-raised by the caller of this
    helper, so silently dropping the secondary ``rollback()`` exception
    is the correct behaviour here.
    """
    try:
        session.rollback()
    except Exception:  # noqa: BLE001 — best-effort cleanup; caller re-raises
        return None
    return None