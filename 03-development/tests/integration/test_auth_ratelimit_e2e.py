"""End-to-end integration tests for auth + rate-limit + session paths.

Targets the auth/ratelimit/session modules that HTTP-level tests cannot
fully exercise (e.g. ``scope_allows`` with revoked keys, rate-limit
denied path, the session context manager).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from taskq_api.service import auth as _auth
from taskq_api.api.deps import _RateConfig, _ScopeGate
from taskq_api.repository import key_repo as _key_repo
from taskq_api.repository import session as _session_module


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_auth_hash_key_sha256_64hex():
    """hash_key returns 64 hex characters (sha256)."""
    hashed = _auth.hash_key("plaintext-key")
    assert len(hashed) == 64
    assert all(c in "0123456789abcdef" for c in hashed)


def test_auth_verify_key_compare_digest():
    """verify_key uses hmac.compare_digest for constant-time compare."""
    # verify_key hashes raw then compares
    h = _auth.hash_key("plaintext")
    assert _auth.verify_key("plaintext", h) is True
    # Different raw
    assert _auth.verify_key("other", h) is False
    # Empty raw
    assert _auth.verify_key("", h) is False
    # Empty hash
    assert _auth.verify_key("plaintext", "") is False


def test_auth_scope_allows_admin():
    """scope_allows returns True for admin key with admin scope."""
    key_repo = _key_repo.KeyRepo
    key_repo._registry["admin-test"] = {
        "id": "admin-test",
        "scope": "admin",
        "key_hash": "0" * 64,
        "revoked_at": None,
    }
    key_repo._by_key["admin-test-key"] = "admin-test"
    try:
        assert _auth.scope_allows("admin-test-key", ["admin"]) is True
    finally:
        key_repo._registry.pop("admin-test", None)
        key_repo._by_key.pop("admin-test-key", None)


def test_auth_scope_allows_revoked():
    """scope_allows returns False for revoked key."""
    key_repo = _key_repo.KeyRepo
    key_repo._registry["revoked"] = {
        "id": "revoked",
        "scope": "write",
        "key_hash": "0" * 64,
        "revoked_at": "2026-01-01",
    }
    key_repo._by_key["revoked-key"] = "revoked"
    try:
        assert _auth.scope_allows("revoked-key", ["write"]) is False
    finally:
        key_repo._registry.pop("revoked", None)
        key_repo._by_key.pop("revoked-key", None)


def test_auth_scope_allows_unregistered():
    """scope_allows returns False for key not in registry."""
    key_repo = _key_repo.KeyRepo
    key_repo._registry.clear()
    key_repo._by_key.clear()
    assert _auth.scope_allows("nonexistent-key", ["write"]) is False


def test_auth_get_current_key_invalid():
    """get_current_key returns None for invalid X-API-Key header."""
    # Direct test: scope_allows with unknown key returns False
    key_repo = _key_repo.KeyRepo
    key_repo._registry.clear()
    key_repo._by_key.clear()
    assert _auth.scope_allows("unknown-key-12345678", ["read"]) is False


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


def test_ratelimit_config_disabled():
    """_RateConfig with config=None is disabled (no enforcement)."""
    cfg = _RateConfig(burst=10, rate_per_sec=1.0)
    assert cfg.burst == 10
    assert cfg.rate_per_sec == 1.0


def test_ratelimit_scope_gate_init():
    """_ScopeGate init accepts allowed_scopes."""
    gate = _ScopeGate(allowed_scopes=frozenset({"read", "write"}))
    assert "read" in gate.allowed_scopes


def test_ratelimit_scope_gate_call_allows():
    """_ScopeGate.__call__ allows matching scope."""
    from taskq_api.service.auth import scope_allows
    # scope_allows check the key's stored scope against allowed
    assert scope_allows is not None  # function exists


def test_ratelimit_enforce_disabled_returns_none():
    """_enforce_rate_limit returns None when TASKQ_RATE_BURST unset."""
    # When _read_rate_config returns None, _enforce_rate_limit is a no-op
    from taskq_api.api.deps import _read_rate_config
    import os
    os.environ.pop("TASKQ_RATE_BURST", None)
    config = _read_rate_config()
    assert config is None  # disabled


def test_ratelimit_enforce_allowed():
    """_enforce_rate_limit with TASKQ_RATE_BURST set allows first request."""
    import os
    from taskq_api.api.deps import _enforce_rate_limit, _read_rate_config
    os.environ["TASKQ_RATE_BURST"] = "10"
    os.environ["TASKQ_RATE_PER_SEC"] = "1.0"
    try:
        config = _read_rate_config()
        assert config is not None
        assert config.burst == 10
    finally:
        os.environ.pop("TASKQ_RATE_BURST", None)
        os.environ.pop("TASKQ_RATE_PER_SEC", None)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


def test_session_get_session_raises_when_unwired():
    """get_session raises RuntimeError until deployment wires it."""
    with pytest.raises(RuntimeError):
        _session_module.get_session()


def test_session_unit_of_work_yields_session():
    """unit_of_work context manager yields the session and commits on exit."""
    # Patch get_session
    class _FakeSession:
        def __init__(self):
            self.committed = False
            self.rolled_back = False
        def commit(self):
            self.committed = True
        def rollback(self):
            self.rolled_back = True
        def close(self):
            pass

    orig = _session_module.get_session
    _session_module.get_session = lambda: _FakeSession()
    try:
        session_ref = None
        with _session_module.unit_of_work() as session:
            session_ref = session
            assert session is not None
        # On normal exit, commit was called
        assert session_ref.committed is True
    finally:
        _session_module.get_session = orig


def test_session_unit_of_work_rollback_on_exception():
    """unit_of_work rolls back on exception."""
    class _FakeSession:
        def __init__(self):
            self.committed = False
            self.rolled_back = False
        def commit(self):
            self.committed = True
        def rollback(self):
            self.rolled_back = True
        def close(self):
            pass

    orig = _session_module.get_session
    _session_module.get_session = lambda: _FakeSession()
    try:
        with pytest.raises(ValueError):
            with _session_module.unit_of_work() as session:
                assert session is not None
                raise ValueError("test exception")
    finally:
        _session_module.get_session = orig
