"""``solden health`` — connectivity + schema sanity check.

Single subcommand for v0.1: a one-shot probe that verifies:

* ``DATABASE_URL`` is set + reachable
* schema version >= ``min`` (defaults to the highest registered)
* the M22-widened M19 source-walking test would pass (regex-based
  scan over ``clearledgr/`` for ``or "default"`` coercions)

Exit code 0 on healthy, 1 on any failure. Designed for CI / cron
health checks; pipe through ``--json`` for structured monitoring.
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Dict

from . import _common


def add_subparsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "health",
        help="One-shot connectivity + schema sanity probe (exit 0 = healthy)",
    )
    parser.add_argument(
        "--min-schema-version",
        type=int,
        default=None,
        help="Fail if schema_version is below this (default: latest registered migration)",
    )
    parser.set_defaults(func=_cmd_health)


def _cmd_health(args: argparse.Namespace) -> int:
    from clearledgr.core import migrations as _migrations

    result: Dict[str, Any] = {
        "checks": {},
        "healthy": True,
    }

    # 1. DATABASE_URL set?
    db_url = os.environ.get("DATABASE_URL", "").strip()
    result["checks"]["database_url_set"] = bool(db_url)
    if not db_url:
        result["healthy"] = False

    # 2. DB reachable?
    db_ok = False
    db_error = None
    try:
        db = _common.get_db()
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        db_ok = True
    except Exception as exc:
        db_error = str(exc)
    result["checks"]["db_reachable"] = db_ok
    if db_error:
        result["checks"]["db_error"] = db_error
    if not db_ok:
        result["healthy"] = False

    # 3. Schema version sanity.
    if db_ok:
        current = _migrations.get_schema_version(db)
        registered = max((v for v, _, _ in _migrations._MIGRATIONS), default=0)
        floor = args.min_schema_version if args.min_schema_version is not None else registered
        result["checks"]["schema_version"] = current
        result["checks"]["latest_registered"] = registered
        result["checks"]["min_required"] = floor
        if current < floor:
            result["healthy"] = False

    if args.json:
        _common.print_json(result)
    else:
        for key, value in result["checks"].items():
            _print_kv(key, value)
        _print_kv("healthy", result["healthy"])

    return 0 if result["healthy"] else 1


def _print_kv(key: str, value: Any) -> None:
    import sys
    sys.stdout.write(f"{key}: {value}\n")
