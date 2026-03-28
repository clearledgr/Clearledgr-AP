#!/usr/bin/env python3
"""Clear stale planner-failure metadata after a successful extraction refresh.

Targets AP items where:
- metadata.exception_code == "planner_failed"
- metadata.processing_status == "extraction_refreshed"

Those rows were repaired successfully but still carry the old planner failure
marker in metadata, which makes live worklists and vendor issue rollups noisy.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SELECT_SQL = """
SELECT
    id,
    organization_id,
    thread_id,
    vendor_name,
    invoice_number,
    state,
    updated_at,
    json_extract(metadata, '$.exception_code') AS metadata_exception_code,
    json_extract(metadata, '$.processing_status') AS metadata_processing_status
FROM ap_items
WHERE json_extract(metadata, '$.exception_code') = 'planner_failed'
  AND json_extract(metadata, '$.processing_status') = 'extraction_refreshed'
  AND (? IS NULL OR organization_id = ?)
ORDER BY updated_at DESC, id ASC
"""


UPDATE_SQL = """
UPDATE ap_items
SET
    metadata = json_set(
        CASE
            WHEN json_valid(COALESCE(metadata, '')) THEN COALESCE(NULLIF(metadata, ''), '{}')
            ELSE '{}'
        END,
        '$.exception_code', NULL,
        '$.exception_severity', NULL,
        '$.planner_error', NULL,
        '$.planner_failure_repaired_at', ?,
        '$.planner_failure_repair_reason', 'stale_extraction_refresh'
    ),
    exception_code = CASE
        WHEN lower(COALESCE(exception_code, '')) = 'planner_failed' THEN NULL
        ELSE exception_code
    END,
    exception_severity = CASE
        WHEN lower(COALESCE(exception_code, '')) = 'planner_failed' THEN NULL
        ELSE exception_severity
    END,
    last_error = NULL
WHERE id = ?
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default="clearledgr.db",
        help="Path to the SQLite database. Default: clearledgr.db",
    )
    parser.add_argument(
        "--organization-id",
        default="default",
        help="Organization to repair. Use an empty string to target all orgs.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the repair. Without this flag the script only prints a dry-run summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    organization_id = (args.organization_id or "").strip() or None

    with _connect(db_path) as conn:
        rows = conn.execute(SELECT_SQL, (organization_id, organization_id)).fetchall()
        print(f"db_path={db_path}")
        print(f"organization_id={organization_id or '*'}")
        print(f"stale_planner_failures={len(rows)}")
        for row in rows[:20]:
            print(
                f"- {row['id']} | {row['vendor_name'] or 'Unknown vendor'}"
                f" | invoice={row['invoice_number'] or 'n/a'}"
                f" | state={row['state']}"
                f" | updated_at={row['updated_at']}"
            )

        if not args.apply:
            print("dry_run=true")
            return 0

        repaired_at = datetime.now(timezone.utc).isoformat()
        with conn:
            for row in rows:
                conn.execute(UPDATE_SQL, (repaired_at, row["id"]))
        print(f"dry_run=false")
        print(f"repaired_rows={len(rows)}")
        print(f"repaired_at={repaired_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
