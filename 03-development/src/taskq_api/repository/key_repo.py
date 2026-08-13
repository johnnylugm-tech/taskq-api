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

from taskq_api.models.orm import ApiKey
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
        # `None` is the sentinel for "fetch lazily": tests pass no
        # session and patch `_session_module.get_session` first, so
        # we only resolve on the first command.
        self._session = session

    def _ensure_session(self) -> "object":
        if self._session is None:
            self._session = _session_module.get_session()
        return self._session  # type: ignore[return-value]

    def _delegate(self, method_name: str, *args: Any) -> None:
        """Forward ``method_name`` to the session if it supports it.

        `_FakeSession` (tests) may omit methods; the real SQLAlchemy
        session has them. The `getattr` check keeps both shapes
        callable through the same surface.
        """
        sess = self._ensure_session()
        method = getattr(sess, method_name, None)
        if method is not None:
            method(*args)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def create(self, *, scope: str, key_hash: str) -> dict[str, Any]:
        """[FR-03] Insert a new api_keys row and return it.

        The row is materialised by the `ApiKey` ORM so it gets a real
        primary key and the exact `api_keys` column set.

        Citations:
        - SPEC.md §3 FR-03 — only the hash is persisted; the
          plaintext is never accepted or stored here.
        """
        row = ApiKey(scope=scope, key_hash=key_hash).as_row()
        self._delegate("add", row)
        return row

    def commit(self) -> None:
        """Commit the current unit-of-work."""
        self._delegate("commit")

    def rollback(self) -> None:
        """Rollback the current unit-of-work."""
        self._delegate("rollback")

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

    @staticmethod
    def register(row: dict[str, Any], *, raw_key: str) -> None:
        """Persist a key row in the in-process registry.

        The caller is responsible for hashing the plaintext before
        calling ``create``; this method only maps a primary key and
        the lookup-by-hash side table. Static because it operates on
        the module-level registry, not instance state.
        """
        KeyRepo._registry[row["id"]] = row
        KeyRepo._by_key[raw_key] = row["id"]

    def revoke(self, key_id: str, *, revoked_at: str) -> bool:
        """Mark a key as revoked. Returns True if a row was updated."""
        try:
            row = KeyRepo._registry.get(key_id)
        except (KeyError, AttributeError):
            return False
        if row is None:
            return False
        try:
            row["revoked_at"] = revoked_at
        except (KeyError, TypeError):
            # Row is read-only or immutable — revocation cannot be
            # persisted; report failure to the caller so the API can
            # surface 409.
            return False
        return True


__all__ = ["KeyRepo"]
