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
import logging
import re


# ---------------------------------------------------------------------------
# Log redaction — scrub the TASKQ_DB_URL password fragment (FR-03 / NFR-04)
# ---------------------------------------------------------------------------
# Pattern matches the password component of a typical
# ``postgres://user:password@host:port/db`` URL — the substring between
# the first ``:`` after the userinfo and the ``@`` is replaced with
# ``***``, leaving scheme/user/host intact so logs stay diagnosable.
_DB_URL_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://[^:@\s]+:)(?P<pwd>[^@\s]+)(?P<at>@)"
)


def _redact_db_url(text: str) -> str:
    """[FR-03] Redact the password fragment of any DB URL in ``text``.

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
    return _redact_db_url(value) if isinstance(value, str) else value


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
        record.msg = _redact_db_url(record.msg)
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


def redact_db_url(text: str) -> str:
    """[FR-03] Public redaction helper — used by `/v1/metrics` too.

    Citations:
    - SPEC.md §3 FR-03 (NFR-04) — both logs and the metrics
      endpoint must scrub the DB URL password.
    """
    return _redact_db_url(text)


__all__ = ["verify_key", "redact_db_url", "install_redact_filter"]
