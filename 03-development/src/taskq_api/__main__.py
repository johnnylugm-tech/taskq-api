"""``python -m taskq_api`` command-line entry point.

[FR-03]
Citations: SPEC.md §3 FR-03 (AC-3.3); SRS.md §3 FR-03.

Exposes the user-facing ``key create --scope <scope>`` subcommand that
generates a new API key and prints the plaintext exactly once (NFR-04).
The plaintext is the only chance the operator has to capture the secret;
it MUST NOT be persisted by the CLI nor re-printed on subsequent runs.
"""

from __future__ import annotations

import argparse
import sys

from taskq_api.service.auth import create_api_key


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser. [FR-03]

    Citations: SPEC.md §3 FR-03 (AC-3.3).
    """
    parser = argparse.ArgumentParser(
        prog="taskq_api",
        description="TaskQ API operator CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    key_parser = subparsers.add_parser("key", help="Manage API keys.")
    key_subparsers = key_parser.add_subparsers(
        dest="key_command", required=True
    )

    create_parser = key_subparsers.add_parser(
        "create", help="Create a new API key."
    )
    create_parser.add_argument(
        "--scope",
        required=True,
        help="Authorization scope for the new key (e.g. 'write').",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch the CLI invocation and return a POSIX exit code. [FR-03]

    Citations: SPEC.md §3 FR-03 (AC-3.3); SRS.md §3 FR-03.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "key" and args.key_command == "create":
        record = create_api_key(scope=args.scope)
        # Print the plaintext exactly once, with a label so operators know
        # what to capture. The label is informational only — the test
        # contract asserts the token itself appears at most once.
        print(f"key: {record['plaintext']}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())