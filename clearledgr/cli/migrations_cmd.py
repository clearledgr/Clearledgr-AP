"""``solden migrations`` — schema version + migration sanity.

Subcommands:

* ``status`` — current schema version, latest migration registered
  in code, and a count of unapplied migrations (delta indicates a
  deploy that hasn't run init yet).
* ``list`` — every migration registered in
  ``clearledgr/core/migrations.py`` with its applied/unapplied flag.

Useful for ops verifying that a freshly-deployed pod has run init,
and for CI guards that block ``solden`` deploys when pending
migrations would surprise the operator.
"""
from __future__ import annotations

import argparse
from typing import Any, Dict, List

from . import _common


_LIST_COLUMNS = ("version", "description", "applied")


def add_subparsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "migrations",
        help="Show schema version + migration status",
    )
    group = parser.add_subparsers(dest="migrations_cmd", required=True)

    p_status = group.add_parser("status", help="Current schema version + pending count")
    p_status.set_defaults(func=_cmd_status)

    p_list = group.add_parser("list", help="Every registered migration with applied flag")
    p_list.set_defaults(func=_cmd_list)


def _cmd_status(args: argparse.Namespace) -> int:
    from clearledgr.core import migrations as _migrations

    db = _common.get_db()
    current = _migrations.get_schema_version(db)
    registered = max((v for v, _, _ in _migrations._MIGRATIONS), default=0)
    pending = max(0, registered - current)

    payload = {
        "schema_version": current,
        "latest_registered": registered,
        "pending": pending,
        "in_sync": pending == 0,
    }
    _common.emit(payload,
                 ("schema_version", "latest_registered", "pending", "in_sync"),
                 as_json=args.json)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    from clearledgr.core import migrations as _migrations

    db = _common.get_db()
    current = _migrations.get_schema_version(db)
    rows: List[Dict[str, Any]] = [
        {
            "version": version,
            "description": desc,
            "applied": version <= current,
        }
        for version, desc, _fn in sorted(_migrations._MIGRATIONS, key=lambda t: t[0])
    ]
    _common.emit(rows, _LIST_COLUMNS, as_json=args.json,
                 empty="(no migrations registered)")
    return 0
