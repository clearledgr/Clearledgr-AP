"""Approver workload — Module 1 (Live Operations).

Spec line 74: "Approver workload strip: who has what on their plate.
Surfaces logistics ('Tobi has 8 waiting, oldest 5 days, on PTO') so
the leader can re-route. Logistics, not scoring."

Read-only aggregation over ``approval_chains`` + ``approval_steps``:
for every pending step, decode the approver list and count items
per approver, plus the age of the oldest pending chain that names
that approver. Resolve the approver id → display name + email via
the users table where possible.

Why pull from approval_chains rather than ap_items: the chain row
is the canonical "this invoice is waiting on these specific people"
record; ap_items just carries the state. A single ap_item can have
multiple pending approvers (sequential dual approval, parallel
approver groups), so aggregating from chains is the only honest
shape.

Performance: with 5K invoices in flight (the §79 target) and a
typical 1-2 active steps per chain, this returns ~5-10K rows from
the join. Decoding + grouping in Python is microseconds; the bound
is the SQL fetch. No index changes needed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def get_approver_workload(
    db: Any, organization_id: str,
) -> List[Dict[str, Any]]:
    """Return per-approver pending counts + oldest-stuck age.

    Output shape: a list of approvers ranked by pending_count desc:
      [
        {
          "approver_id": "alice@orga.com",
          "name": "Alice",
          "email": "alice@orga.com",
          "pending_count": 8,
          "oldest_pending_at": "2026-04-22T09:00:00Z",
          "oldest_pending_age_days": 5,
        },
        ...
      ]
    """
    sql = (
        "SELECT s.approvers, c.created_at, c.id "
        "FROM approval_chains c "
        "JOIN approval_steps s ON s.chain_id = c.id "
        "WHERE c.organization_id = %s "
        "  AND c.status = 'pending' "
        "  AND s.status = 'pending'"
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning(
            "[approver_workload] query failed for org=%s: %s",
            organization_id, exc,
        )
        return []

    now = datetime.now(timezone.utc)
    per_approver: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        d = dict(row) if hasattr(row, "keys") else {
            "approvers": row[0], "created_at": row[1], "id": row[2],
        }
        approvers_raw = d.get("approvers") or "[]"
        if isinstance(approvers_raw, str):
            try:
                approver_ids = json.loads(approvers_raw)
            except (json.JSONDecodeError, TypeError):
                approver_ids = []
        elif isinstance(approvers_raw, list):
            approver_ids = approvers_raw
        else:
            approver_ids = []

        chain_created = _parse_iso(d.get("created_at"))

        for approver in approver_ids:
            key = str(approver or "").strip()
            if not key:
                continue
            slot = per_approver.setdefault(key, {
                "approver_id": key,
                "pending_count": 0,
                "oldest_pending_at": None,
            })
            slot["pending_count"] += 1
            if chain_created and (
                slot["oldest_pending_at"] is None
                or chain_created < _parse_iso(slot["oldest_pending_at"])
            ):
                slot["oldest_pending_at"] = chain_created.isoformat()

    if not per_approver:
        return []

    # Resolve display names + emails from the users table where the
    # approver key looks like a user_id or email. Best-effort; an
    # unrecognised approver still surfaces (just without a name).
    user_lookup = _build_user_lookup(db, organization_id, list(per_approver.keys()))

    results: List[Dict[str, Any]] = []
    for key, slot in per_approver.items():
        user = user_lookup.get(key) or user_lookup.get(key.lower()) or {}
        oldest_iso = slot["oldest_pending_at"]
        age_days = None
        if oldest_iso:
            try:
                age_days = max(0, (now - _parse_iso(oldest_iso)).days)
            except Exception:
                age_days = None
        results.append({
            "approver_id": key,
            "name": user.get("name") or _email_to_name(key),
            "email": user.get("email") or (key if "@" in key else None),
            "pending_count": slot["pending_count"],
            "oldest_pending_at": oldest_iso,
            "oldest_pending_age_days": age_days,
        })

    # Order: most-loaded first, then by oldest age desc, then alphabetical.
    results.sort(
        key=lambda r: (
            -int(r.get("pending_count") or 0),
            -int(r.get("oldest_pending_age_days") or 0),
            (r.get("name") or r.get("email") or "").lower(),
        ),
    )
    return results


def _build_user_lookup(
    db: Any, organization_id: str, keys: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Resolve approver keys to user rows for display. Keyed by both
    user_id and lower-cased email so variant casing doesn't miss."""
    lookup: Dict[str, Dict[str, Any]] = {}
    if not keys:
        return lookup
    try:
        if not hasattr(db, "get_users"):
            return lookup
        rows = db.get_users(organization_id, include_inactive=True) or []
        for row in rows:
            uid = str(row.get("id") or "").strip()
            email = str(row.get("email") or "").strip().lower()
            entry = {
                "id": uid,
                "name": (row.get("name") or "").strip() or email,
                "email": email,
                "is_active": bool(row.get("is_active", True)),
            }
            if uid:
                lookup[uid] = entry
            if email:
                lookup[email] = entry
    except Exception as exc:
        logger.debug(
            "[approver_workload] user lookup failed: %s", exc,
        )
    return lookup


def _parse_iso(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _email_to_name(value: str) -> str:
    """Human-readable fallback when we don't have a users-row name."""
    text = str(value or "").strip()
    if not text:
        return ""
    if "@" in text:
        local = text.split("@", 1)[0]
        return local.replace(".", " ").replace("_", " ").title()
    return text
