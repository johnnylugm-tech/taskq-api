"""[FR-03, FR-04] API key hashing, verification, and log redaction.

Citations:
- SPEC.md §3 FR-03 — keys stored as SHA-256 hash; comparison via
  `hmac.compare_digest` (constant-time).
- SPEC.md L211 (§4 NFR-04) — the DB connection string (with its
  password) must not appear in any log, error message, or
  `/v1/metrics` response.
- SAD.md §2.6 — `auth.py` is the hub for the service community;
  the scope gate lives in `api.deps.require_scope`.

The test suites patch `verify_key` via `monkeypatch.setattr` on this
MODULE, so this signature MUST exist verbatim and callers MUST reach
it through a module attribute lookup (see `api.deps`):

    verify_key(raw: str, hashed: str) -> bool
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any, Iterable

from taskq_api.repository.key_repo import KeyRepo


# ---------------------------------------------------------------------------
# Key hashing / verification (FR-03)
# ---------------------------------------------------------------------------
def hash_key(raw: str) -> str:
    """[FR-03] SHA-256 hex digest of ``raw`` — 64 lowercase hex chars.

    The single definition of how a plaintext key becomes a stored
    hash. The CLI write path (`python -m taskq_api key create`) and the
    `verify_key` read path both route through here, so the two can
    never drift into computing different digests for the same key.

    Citations:
    - SPEC.md §3 FR-03 — `api_keys.key_hash` is the SHA-256 of the
      plaintext, stored as 64 lowercase hex chars; the plaintext is
      never persisted.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_key(raw: str, hashed: str) -> bool:
    """[FR-03] Constant-time comparison of ``raw`` against ``hashed``.

    Citations:
    - SPEC.md §3 FR-03 — `hmac.compare_digest` over SHA-256; a plain
      `==` short-circuits on the first differing byte and leaks the
      digest through response timing.
    - SAD.md §2.6 — production wiring hashes `raw` then compares.

    The test suites stub this via `monkeypatch.setattr` on the module
    object; the real implementation must exist so the patch target
    resolves.
    """
    if not raw or not hashed:
        return False
    return hmac.compare_digest(hash_key(raw), hashed)


# ---------------------------------------------------------------------------
# Scope authorisation (FR-04)
# ---------------------------------------------------------------------------
def scope_allows(raw: str, allowed_scopes: Iterable[str]) -> bool:
    """[FR-04] True iff the stored scope for ``raw`` is in ``allowed_scopes``.

    Citations:
    - SPEC.md §3 FR-04 — scope check (`read` < `write` < `admin`) is
      decided in `api.deps.require_scope`; this function is the
      primitive the gate calls to read the key's stored scope from
      `api_keys` and compare it against the gate's allowed set.
    - SAD.md §2.6 — `service.auth` is the only place that consults the
      `api_keys` row directly; `api.deps` delegates here so the
      layering stays one-directional (`api` → `service` → `repository`).

    Production wiring hashes ``raw`` and looks the row up by hash; the
    GREEN step consults the in-process `KeyRepo._by_key` /
    `KeyRepo._registry` side-tables the test suite pre-populates. A
    revoked row (non-null `revoked_at`) is rejected here so a stale
    key cannot bypass the gate even though `get_current_key` already
    returned the raw key.
    """
    if not raw:
        return False
    allowed = set(allowed_scopes)
    key_id = KeyRepo._by_key.get(raw)
    if key_id is None:
        return False
    row = KeyRepo._registry.get(key_id)
    if row is None:
        return False
    if row.get("revoked_at") is not None:
        return False
    return row.get("scope") in allowed


# ---------------------------------------------------------------------------
# Log redaction — scrub the TASKQ_DB_URL password fragment (FR-03 / NFR-04)
# ---------------------------------------------------------------------------
# Matches the password component of a typical
# ``postgres://user:password@host:port/db`` URL — the substring between
# the first ``:`` after the userinfo and the ``@`` is replaced with
# ``***``, leaving scheme/user/host intact so logs stay diagnosable.
_DB_URL_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://[^:@\s]+:)(?P<pwd>[^@\s]+)(?P<at>@)"
)


def redact_db_url(text: str) -> str:
    """[FR-03] Redact the password fragment of any DB URL in ``text``.

    The one redaction implementation: the logging record factory below
    and `/v1/metrics` (`app._build_metrics_body`) both call it, so the
    two exits a password could escape through are scrubbed by the same
    pattern.

    Citations:
    - SPEC.md L211 (§4 NFR-04) — the DB connection string (with its
      password) must not appear in any log, error message, or
      `/v1/metrics` response.
    - SPEC.md L430 (§8 #20) — logs and `/v1/metrics` are grepped for
      the `TASKQ_DB_URL` password fragment; expected 0 hits.
    """
    return _DB_URL_RE.sub(r"\g<scheme>***\g<at>", text)


def _scrub(value: Any) -> Any:
    """[FR-03] Redact a single log-record argument when it is a string.

    Citations:
    - SPEC.md L211 (§4 NFR-04) — the password fragment is scrubbed
      wherever it enters the logging pipeline.
    """
    return redact_db_url(value) if isinstance(value, str) else value


# Captured at import time so the installer can delegate to whatever
# factory was already in place (pytest, structlog, ...) instead of
# replacing it outright.
_BASE_RECORD_FACTORY = logging.getLogRecordFactory()


def _redacting_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    """[FR-03] Build a LogRecord with DB-URL passwords already redacted.

    Redaction happens at record CONSTRUCTION, not in a `logging.Filter`
    on the root logger: a logger's filters run only for records emitted
    through that logger itself. A record logged on `taskq_api.db`
    propagates to the root logger's HANDLERS without ever consulting the
    root logger's filters, so a root filter would let the password reach
    the handler unredacted. The record factory is the one hook every
    record passes through, whatever logger created it.

    Both `msg` and `args` are scrubbed so the password is gone before
    `%`-formatting happens, covering `logger.info("%s", url)` (tuple
    args) and `logger.info("%(u)s", {"u": url})` (mapping args) alike.

    Citations:
    - SPEC.md L211 (§4 NFR-04) — no DB connection string in any log.
    - SPEC.md L430 (§8 #20) — logs are grepped for the password
      fragment; expected 0 hits.
    """
    record = _BASE_RECORD_FACTORY(*args, **kwargs)
    if isinstance(record.msg, str):
        record.msg = redact_db_url(record.msg)
    if isinstance(record.args, dict):
        record.args = {key: _scrub(val) for key, val in record.args.items()}
    elif isinstance(record.args, tuple):
        record.args = tuple(_scrub(val) for val in record.args)
    return record


def install_log_redaction() -> None:
    """[FR-03] Install the DB-URL-password redaction record factory.

    Idempotent: re-installing would otherwise chain the factory onto
    itself and redact each record twice.

    Citations:
    - SPEC.md L211 (§4 NFR-04) — logs must never carry the DB password.
    """
    if logging.getLogRecordFactory() is _redacting_record_factory:
        return
    logging.setLogRecordFactory(_redacting_record_factory)


# Install at import time so every record built after `taskq_api` is
# imported is scrubbed, regardless of which logger or handler is used.
install_log_redaction()


__all__ = [
    "hash_key",
    "verify_key",
    "scope_allows",
    "redact_db_url",
    "install_log_redaction",
]
