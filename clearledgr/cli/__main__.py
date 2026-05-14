"""Solden CLI entry point.

Usage:

  python -m clearledgr.cli <command> [...]
  solden <command> [...]    # via the scripts/solden shim

Top-level options:

  --db-url URL    override DATABASE_URL for this invocation
  --json          emit JSON instead of the default human-readable
                  table format (also overrides per-subcommand
                  defaults like ``audit export`` which is JSON
                  even without the flag)

See subcommand --help for the rest. v0.1 talks directly to the DB
through ``ClearledgrDB``; remote/API mode is a later sprint.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import _common, audit, health, migrations_cmd, policy, tenants


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="solden",
        description="Solden CLI — operator + ops-engineering tooling",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Override DATABASE_URL for this invocation",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON instead of human-readable tables",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    tenants.add_subparsers(subparsers)
    audit.add_subparsers(subparsers)
    policy.add_subparsers(subparsers)
    migrations_cmd.add_subparsers(subparsers)
    health.add_subparsers(subparsers)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    _common.apply_db_url_override(args.db_url)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        return int(func(args) or 0)
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        return 130
    except Exception as exc:
        # Print the error rather than a traceback for ops legibility.
        # Set ``SOLDEN_CLI_DEBUG=1`` to force a traceback when
        # debugging.
        import os
        if os.environ.get("SOLDEN_CLI_DEBUG"):
            raise
        sys.stderr.write(f"error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
