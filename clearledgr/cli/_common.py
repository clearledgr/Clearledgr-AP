"""Shared helpers for the Solden CLI.

* ``get_db()`` returns the singleton ``ClearledgrDB`` instance after
  honoring an optional ``--db-url`` override (set via the top-level
  parser before any subcommand runs).
* ``print_table()`` renders a list of dicts as a fixed-width table.
* ``print_json()`` emits compact JSON for scripting consumers.
* ``parse_iso_window()`` accepts ``--since`` / ``--until`` strings
  and returns timezone-aware datetimes (or None if not supplied).

Kept dependency-free on purpose — argparse + stdlib only. The CLI
must run on a vanilla Python install with the project package
already on ``PYTHONPATH``.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, List, Mapping, Optional, Sequence


def apply_db_url_override(db_url: Optional[str]) -> None:
    """Apply ``--db-url`` before the DB singleton is constructed.

    ``ClearledgrDB.__init__`` reads ``DATABASE_URL`` from the env at
    construction time, so we set it here before the first call to
    ``get_db()``. Idempotent — calling with ``None`` leaves the env
    alone.
    """
    if db_url:
        os.environ["DATABASE_URL"] = db_url


def get_db():
    """Return the canonical ``ClearledgrDB`` singleton.

    Lazy import keeps ``--help`` fast: the DB layer pulls in
    ``psycopg`` + connection-pool init, which is ~150ms of startup
    we don't want on every CLI invocation.
    """
    from clearledgr.core.database import get_db as _get_db
    return _get_db()


def parse_iso_window(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp.

    Accepts ``2026-05-10``, ``2026-05-10T12:34:56Z``, or
    ``2026-05-10T12:34:56+00:00``. Returns a timezone-aware
    ``datetime`` (UTC if no offset). Returns ``None`` for ``None`` /
    empty input so callers can pass through unfiltered windows.
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # ``fromisoformat`` doesn't accept the trailing ``Z`` until
    # 3.11+; normalize for older Python and consistent behavior.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def print_json(payload: Any) -> None:
    """Compact JSON to stdout. Default for scripted use."""
    json.dump(payload, sys.stdout, default=_json_default, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _json_default(obj: Any) -> Any:
    """Make datetimes + sets JSON-encodable. Tenant data rarely has
    other awkward types, so this is a thin layer.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


def print_table(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
    *,
    empty_message: str = "(no rows)",
) -> None:
    """Render a list of dicts as a fixed-width ASCII table.

    Columns are explicit so the order is stable and ``--json`` and
    ``--table`` output have the same surface contract.

    Long values are truncated at 60 chars with an ellipsis — keeps
    the table readable on a standard 120-col terminal even when
    payloads contain JSON blobs (audit-event metadata, policy
    descriptions, etc.).
    """
    if not rows:
        sys.stdout.write(f"{empty_message}\n")
        return

    str_rows: List[List[str]] = []
    for row in rows:
        str_rows.append([_truncate(str(_safe_get(row, c))) for c in columns])

    widths = [len(c) for c in columns]
    for r in str_rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))

    sep = "  "  # two spaces; cleaner than pipes for ops eyes
    sys.stdout.write(sep.join(c.ljust(widths[i]) for i, c in enumerate(columns)) + "\n")
    sys.stdout.write(sep.join("-" * widths[i] for i in range(len(columns))) + "\n")
    for r in str_rows:
        sys.stdout.write(sep.join(cell.ljust(widths[i]) for i, cell in enumerate(r)) + "\n")


def _safe_get(row: Mapping[str, Any], key: str) -> Any:
    """Tolerate keys missing from a row (sparse columns) and dict-
    nested values (``settings_json.onboarding`` -> dotted lookup).
    """
    if "." not in key:
        return row.get(key, "")
    cur: Any = row
    for part in key.split("."):
        if not isinstance(cur, Mapping):
            return ""
        cur = cur.get(part, "")
    return cur


def _truncate(value: str, *, max_len: int = 60) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


def emit(rows: Iterable[Mapping[str, Any]] | Mapping[str, Any], columns: Sequence[str], *, as_json: bool, empty: str = "(no rows)") -> None:
    """Output dispatcher — table for humans, JSON for scripts."""
    if as_json:
        # Materialize iterables so json doesn't get a generator.
        if isinstance(rows, Mapping):
            print_json(dict(rows))
        else:
            print_json(list(rows))
        return
    if isinstance(rows, Mapping):
        # Single record — render as a vertical key/value list, more
        # readable than a one-row wide table for ``info`` commands.
        for col in columns:
            sys.stdout.write(f"{col}: {_safe_get(rows, col)}\n")
        return
    print_table(list(rows), columns, empty_message=empty)
