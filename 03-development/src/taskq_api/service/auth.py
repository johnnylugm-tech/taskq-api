"""[FR-03, FR-04] API key verification.

Citations:
- SPEC.md §3 FR-03 — keys stored as SHA-256 hash; comparison via
  `hmac.compare_digest` (constant-time).
- SAD.md §2.6 — `auth.py` is the hub for the service community;
  scope gate lives in `api.deps.require_scope`.

The GREEN test suite patches `verify_key` via `monkeypatch.setattr`,
so this signature MUST exist verbatim:

    verify_key(raw: str, hashed: str) -> bool
"""
from __future__ import annotations

import hashlib
import hmac


def verify_key(raw: str, hashed: str) -> bool:
    """[FR-03] Constant-time comparison of `raw` against `hashed`.

    Citations:
    - SPEC.md §3 FR-03 — `hmac.compare_digest` over SHA-256.
    - SAD.md §2.6 — production wiring hashes `raw` then compares.

    The GREEN test (`test_fr01.py::_stub_external_side_effects`) stubs
    this with `lambda raw, hashed: bool(raw) and bool(hashed)`. The
    real implementation must exist so the autouse patch resolves.
    """
    if not raw or not hashed:
        return False
    candidate = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate, hashed)


__all__ = ["verify_key"]
