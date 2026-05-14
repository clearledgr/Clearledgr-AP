"""``solden audit`` — export audit events for a tenant + window.

Sole subcommand for v0.1:

* ``export <org_id> [--since DATE] [--until DATE] [--limit N]`` —
  emits audit events as JSON or CSV. Honors ``audit_events.chain_seq``
  ordering so the output is reproducible (snapshot-pinned reads in
  ModernRelay vocab — every event has a deterministic position in
  the per-org append-only chain).
"""
from __future__ import annotations

import argparse
import csv
import sys
from typing import Any, Dict, List, Optional

from . import _common


def add_subparsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "audit",
        help="Export audit events from a tenant's append-only chain",
    )
    group = parser.add_subparsers(dest="audit_cmd", required=True)

    p_export = group.add_parser("export", help="Export audit events for an org + window")
    p_export.add_argument("org_id", help="Organization id (required)")
    p_export.add_argument("--since", help="ISO timestamp lower bound (inclusive)")
    p_export.add_argument("--until", help="ISO timestamp upper bound (inclusive)")
    p_export.add_argument("--limit", type=int, default=10_000,
                          help="Max rows (default 10000; raise carefully — full chain dumps can be huge)")
    p_export.add_argument("--csv", action="store_true",
                          help="Emit CSV to stdout instead of the default JSON")
    p_export.add_argument("--ap-item-id",
                          help="Filter to events for one ap_item_id (audit-trail walk)")
    p_export.set_defaults(func=_cmd_export)


def _cmd_export(args: argparse.Namespace) -> int:
    # ``--csv`` and ``--json`` are mutually exclusive in spirit but
    # we don't error on both — JSON is the silent default for the
    # audit subcommand because audit events have nested metadata that
    # tables truncate. ``--csv`` is for spreadsheet handoff.
    db = _common.get_db()
    since = _common.parse_iso_window(args.since)
    until = _common.parse_iso_window(args.until)

    rows: List[Dict[str, Any]] = db.list_ap_audit_events(
        organization_id=args.org_id,
        since=since.isoformat() if since else None,
        until=until.isoformat() if until else None,
        ap_item_id=args.ap_item_id,
        limit=args.limit,
        order="asc",
    )

    if args.csv:
        _emit_csv(rows)
    else:
        _common.print_json(rows)
    return 0


def _emit_csv(rows: List[Dict[str, Any]]) -> None:
    """CSV emission with a stable column order. Audit events have a
    bag of optional fields; we project a flat view that's
    spreadsheet-friendly. Nested metadata becomes a JSON string in
    the metadata column.
    """
    columns = [
        "id", "organization_id", "ap_item_id", "event_type",
        "actor_type", "actor_id", "ts", "chain_seq", "content_hash",
        "idempotency_key", "correlation_id", "reason", "metadata",
    ]
    writer = csv.DictWriter(sys.stdout, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        out: Dict[str, Any] = {c: row.get(c, "") for c in columns}
        # ``metadata`` and ``reason`` may be dicts; JSON-encode them
        # so the CSV cell stays parseable.
        meta = row.get("metadata")
        if isinstance(meta, (dict, list)):
            import json
            out["metadata"] = json.dumps(meta, default=str)
        writer.writerow(out)
