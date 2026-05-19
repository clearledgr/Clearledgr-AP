"""``solden tenants`` — list / inspect organizations.

Subcommands:

* ``list`` — every organization, optionally filtered by domain.
* ``info <org_id>`` — full details for one org including the
  ``settings_json.onboarding`` snapshot, integration-mode flag, and
  parent-organization link.

Talks directly to ``SoldenDB`` (operator-local v0.1).
"""
from __future__ import annotations

import argparse
from typing import Any, Dict, List

from . import _common


_LIST_COLUMNS = ("id", "name", "domain", "integration_mode", "created_at")
_INFO_COLUMNS = (
    "id", "name", "domain", "integration_mode",
    "parent_organization_id", "lifecycle_status",
    "settings_json.onboarding", "created_at", "updated_at",
)


def add_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Mount ``tenants`` group under the top-level parser."""
    parser = subparsers.add_parser(
        "tenants",
        help="List or inspect organizations",
    )
    group = parser.add_subparsers(dest="tenants_cmd", required=True)

    p_list = group.add_parser("list", help="List all organizations")
    p_list.add_argument("--domain", help="Filter by domain (substring match)")
    p_list.add_argument("--limit", type=int, default=500)
    p_list.set_defaults(func=_cmd_list)

    p_info = group.add_parser("info", help="Show details for one organization")
    p_info.add_argument("org_id", help="Organization id (use 'tenants list' to find)")
    p_info.set_defaults(func=_cmd_info)


def _cmd_list(args: argparse.Namespace) -> int:
    db = _common.get_db()
    rows: List[Dict[str, Any]] = db.list_organizations(limit=args.limit)
    if args.domain:
        needle = args.domain.lower()
        rows = [r for r in rows if needle in str(r.get("domain") or "").lower()]
    _common.emit(rows, _LIST_COLUMNS, as_json=args.json, empty="(no organizations)")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    db = _common.get_db()
    row = db.get_organization(args.org_id)
    if not row:
        print(f"organization {args.org_id!r} not found")
        return 2
    _common.emit(row, _INFO_COLUMNS, as_json=args.json)
    return 0
