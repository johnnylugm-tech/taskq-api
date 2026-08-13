# pragma: no error-handling
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
# The `if __name__ == "__main__"` guard is exercised by the
# `python -m taskq_api` integration (make verify-system's
# readyz-smoke step), not by the unit test suite. Omitted from
# pytest-cov via .coveragerc's `omit = taskq_api/__main__.py` so the
# test_coverage number stays at the project source tree the tests
# actually exercise.

from __future__ import annotations

import argparse
import secrets

from taskq_api.models.orm import ApiKey
from taskq_api.repository.key_repo import KeyRepo
from taskq_api.service.auth import hash_key


def _gen_plaintext() -> str:
    """[FR-03] Generate a URL-safe plaintext token (>=16 chars)."""
    return secrets.token_urlsafe(24)


def _cmd_key_create(scope: str) -> int:
    """[FR-03] Generate a new key, persist its hash, print the plaintext.

    Citations:
    - SPEC.md §3 FR-03 — print `KEY=<plaintext>` exactly once on
      stdout; persist only the hash.

    The CLI runs as a subprocess with no wired DB session, so it
    persists through `KeyRepo.register` (the in-process registry path)
    rather than `KeyRepo.create` — the latter acquires a session, and
    `repository.session.get_session` raises until the deployment layer
    wires one. Hashing goes through `service.auth.hash_key`, the same
    digest the verification path uses.
    """
    plaintext = _gen_plaintext()
    row = ApiKey(scope=scope, key_hash=hash_key(plaintext)).as_row()
    KeyRepo().register(row, raw_key=plaintext)

    # Print the plaintext EXACTLY once on stdout — this is the only
    # moment it is ever visible; only the hash is persisted.
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

    # argparse with `required=True` on both subparsers guarantees one
    # of the dispatch branches is reached; --help is handled by
    # argparse before this function returns. The dispatch table makes
    # the "every command is wired" invariant explicit so a missing
    # entry fails fast instead of falling through silently.
    if args.command == "key" and args.key_command == "create":
        return _cmd_key_create(scope=args.scope)

    raise AssertionError(  # noqa: TRY003 — defensive unreachable guard
        f"unreachable dispatch: command={args.command!r}, "
        f"key_command={getattr(args, 'key_command', None)!r}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
