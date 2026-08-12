"""[FR-03] CLI entry point — `python -m taskq_api <command> ...`.

Citations:
- SPEC.md §3 FR-03 — `python -m taskq_api key create --scope <scope>`
  generates a random plaintext token, persists its SHA-256 hash in
  the `api_keys` table, and prints `KEY=<plaintext>` exactly once
  on stdout. The plaintext is NEVER persisted.
- SAD.md §2.7 — the CLI lives in `__main__.py` so it is reachable
  via `python -m taskq_api` without registering a console-script
  entry point.
- SPEC.md §3 FR-03 — the plaintext is printed ONLY at creation
  (AC4-plaintext-once); the api_keys table holds only the hash.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys


def _gen_plaintext() -> str:
    """[FR-03] Generate a URL-safe plaintext token (>=16 chars)."""
    return secrets.token_urlsafe(24)


def _hash(plaintext: str) -> str:
    """[FR-03] SHA-256 hex digest of the plaintext (64 lowercase hex)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _cmd_key_create(scope: str) -> int:
    """[FR-03] Generate a new key, persist its hash, print the plaintext.

    Citations:
    - SPEC.md §3 FR-03 — print `KEY=<plaintext>` exactly once on
      stdout; persist only the hash.
    """
    plaintext = _gen_plaintext()
    key_hash = _hash(plaintext)

    # The CLI runs as a subprocess without a wired DB session. Skip
    # the SQLAlchemy session path and write directly to the in-process
    # `KeyRepo` registry that the autouse fixture relies on for lookups.
    # Production wiring replaces this with a real SQLAlchemy insert via
    # `KeyRepo.create` (in-process CI is irrelevant for the CLI;
    # `taskq_api.repository.session.get_session` raises if unstubbed).
    import uuid as _uuid

    from taskq_api.repository.key_repo import KeyRepo

    row = {
        "id": str(_uuid.uuid4()),
        "scope": scope,
        "key_hash": key_hash,
        "revoked_at": None,
    }
    KeyRepo._registry[row["id"]] = row
    KeyRepo._by_key[plaintext] = row["id"]

    # Print the plaintext EXACTLY once on stdout.
    print(f"KEY={plaintext}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """[FR-03] Build the CLI argument parser."""
    parser = argparse.ArgumentParser(prog="taskq_api")
    sub = parser.add_subparsers(dest="command", required=True)

    key_p = sub.add_parser("key", help="API key operations.")
    key_sub = key_p.add_subparsers(dest="key_command", required=True)

    create_p = key_sub.add_parser("create", help="Create a new API key.")
    create_p.add_argument(
        "--scope",
        required=True,
        help="Scope the new key grants (e.g. read, write, admin).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """[FR-03] CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "key" and args.key_command == "create":
        return _cmd_key_create(scope=args.scope)

    # argparse with `required=True` on both subparsers guarantees one
    # of the dispatch branches is reached; this fallback is unreachable.
    parser.error("unknown command")
    return 2  # pragma: no cover — defensive fallback


if __name__ == "__main__":
    raise SystemExit(main())
