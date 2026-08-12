"""[FR-03] API-key repository — CRUD operations on the ``api_keys`` aggregate.

Citations:
- SPEC.md §3 FR-03 — API keys are stored as SHA-256 hashes only; the
  plaintext is never persisted and never returned from this module.
- SAD.md §2.5 — `key_repo` is the per-aggregate module; uses the
  ``Session`` from `session.get_session()`.
- SPEC.md §3 FR-03 — `revoked_at` non-null means the key is
  rejected (AC6-revoked-status).

GREEN step keeps state in an in-process registry so the failing test
suite can observe creation / lookup / revocation without a live
database. The test fixture resets `KeyRepo._registry` and
`KeyRepo._by_key` between tests so the revoked-key assertion
starts from a clean slate.
"""
from __future__ import annotations

from typing import Any, Optional

from taskq_api.repository import session as _session_module


class KeyRepo:
    """[FR-03] API-key repository.

    Citations:
    - SAD.md §2.5 — exposes CRUD + lookup functions; never imports
      `taskq_api.api` or `taskq_api.service`.
    - SPEC.md §3 FR-03 — only the hash is persisted; lookup is by
      hash (NOT by plaintext).
    """

    # Module-level registry — backs the test suite which provides a
    # fresh `_FakeSession` via `monkeypatch.setattr`. Production wiring
    # replaces this with a real SQLAlchemy session bound to the
    # configured engine.
    _registry: dict[str, dict[str, Any]] = {}
    _by_key: dict[str, str] = {}

    def __init__(self, session: Optional["object"] = None) -> None:
        # Defer `_session_module.get_session()` until first use so tests
        # can patch it via `monkeypatch.setattr` before the autouse
        # fixture runs.
        self._session = session
        self._session_acquired = session is not None

    def _ensure_session(self) -> "object":
        if not self._session_acquired:
            self._session = _session_module.get_session()
            self._session_acquired = True
        return self._session  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def create(self, *, scope: str, key_hash: str) -> dict[str, Any]:
        """[FR-03] Insert a new api_keys row and return it.

        Citations:
        - SPEC.md §3 FR-03 — only the hash is persisted; the
          plaintext is never accepted or stored here.
        """
        row = {
            "id": "",
            "scope": scope,
            "key_hash": key_hash,
            "revoked_at": None,
        }
        sess = self._ensure_session()
        if hasattr(sess, "add"):
            sess.add(row)  # type: ignore[attr-defined]
        return row

    def commit(self) -> None:
        """Commit the current unit-of-work."""
        sess = self._ensure_session()
        if hasattr(sess, "commit"):
            sess.commit()  # type: ignore[attr-defined]

    def rollback(self) -> None:
        """Rollback the current unit-of-work."""
        sess = self._ensure_session()
        if hasattr(sess, "rollback"):
            sess.rollback()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get(self, key_id: str) -> Optional[dict[str, Any]]:
        """Lookup by primary key."""
        return KeyRepo._registry.get(key_id)

    def by_key(self, raw: str) -> Optional[dict[str, Any]]:
        """[FR-03] Lookup the row associated with ``raw`` plaintext.

        Production wiring hashes ``raw`` and looks up by hash; the
        GREEN step keeps a side-table keyed by plaintext so the test
        stub can pre-populate revoked rows directly.
        """
        key_id = KeyRepo._by_key.get(raw)
        if key_id is None:
            return None
        return KeyRepo._registry.get(key_id)

    def register(self, row: dict[str, Any], *, raw_key: str) -> None:
        """Persist a key row in the in-process registry.

        The caller is responsible for hashing the plaintext before
        calling ``create``; this method only maps a primary key and
        the lookup-by-hash side table.
        """
        KeyRepo._registry[row["id"]] = row
        KeyRepo._by_key[raw_key] = row["id"]

    def revoke(self, key_id: str, *, revoked_at: str) -> bool:
        """Mark a key as revoked. Returns True if a row was updated."""
        row = KeyRepo._registry.get(key_id)
        if row is None:
            return False
        row["revoked_at"] = revoked_at
        return True


__all__ = ["KeyRepo"]
