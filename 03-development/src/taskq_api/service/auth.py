"""API Key authentication service.

[FR-03]
Citations: SPEC.md §3 FR-03 (AC-3.1..AC-3.5); SRS.md §3 FR-03; SAD.md §2.

The plaintext secret is never persisted: only its SHA-256 hex digest is
written to the ``api_keys`` table. Comparison uses ``hmac.compare_digest``
to keep the boundary resistant to timing attacks (NFR-02 / NP-01).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PLAINTEXT_PREFIX = "sk-"
_PLAINTEXT_RANDOM_BYTES = 32


def hash_key(plaintext: str) -> str:
    """Return the 64-character lowercase hex SHA-256 digest of the API key. [FR-03]

    Citations: SPEC.md AC-3.2; SRS.md §3 FR-03; NFR-02.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def verify_key(candidate: str, stored_hash: str) -> bool:
    """Compare a candidate plaintext against a stored hash in constant time. [FR-03]

    Citations: SPEC.md AC-3.2 / NFR-02 / NP-01.

    ``hmac.compare_digest`` performs a length-equalized, time-equalized
    comparison that does not short-circuit on the first byte mismatch,
    keeping the auth boundary resistant to timing attacks. The hex
    digests passed in are ASCII, so the str-accepting overload is safe.
    """
    candidate_hash = hash_key(candidate)
    return hmac.compare_digest(candidate_hash, stored_hash)


def create_api_key(scope: str) -> dict[str, object]:
    """Generate a new API key and return its plaintext and hash exactly once. [FR-03]

    Citations: SPEC.md AC-3.3 / NFR-04.

    The plaintext is included in the returned mapping so the caller can
    surface it to the user at creation time. It MUST NOT be persisted by
    callers; only ``key_hash`` is meant for storage.
    """
    plaintext = f"{_PLAINTEXT_PREFIX}{secrets.token_urlsafe(_PLAINTEXT_RANDOM_BYTES)}"
    return {
        "plaintext": plaintext,
        "key_hash": hash_key(plaintext),
        "scope": scope,
        "revoked_at": None,
    }


def is_key_revoked(record: dict[str, object]) -> bool:
    """Return True when the record's ``revoked_at`` is non-null. [FR-03]

    Citations: SPEC.md AC-3.4 / NFR-02.
    """
    return record.get("revoked_at") is not None
