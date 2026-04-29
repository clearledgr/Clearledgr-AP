"""Sanctions screening persistence (Wave 3 / E1).

Records every sanctions/PEP/adverse-media screen the workspace runs
against a vendor — provider's raw payload preserved verbatim for
SOC 2 + 6AMLD audit. The rolled-up disposition lives on
``vendor_profiles.sanctions_status`` (clear / review / blocked /
unscreened) and is what the pre-payment gate reads.

Idempotency: callers don't need to dedup screens — every call gets
its own row so the timeline of "screened on date X, hit on date Y,
cleared by operator on date Z" is fully reconstructable.

Operator override flow:
  * status='hit' + review_status='open'  → vendor_profiles.sanctions_status='review'
  * operator clears as false positive    → review_status='cleared',
                                            sanctions_status set per latest screen
  * operator confirms hit                → review_status='confirmed',
                                            sanctions_status='blocked'
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_VALID_REVIEW_STATUSES = frozenset({"open", "cleared", "confirmed"})


class SanctionsStore:
    """Mixin: CRUD over ``vendor_sanctions_checks``."""

    # ── Writes ─────────────────────────────────────────────────────

    def record_sanctions_check(
        self,
        *,
        organization_id: str,
        vendor_name: str,
        check_type: str,
        provider: str,
        status: str,
        provider_reference: Optional[str] = None,
        matches: Optional[List[Dict[str, Any]]] = None,
        evidence: Optional[Dict[str, Any]] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
        checked_at: Optional[str] = None,
        checked_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.initialize()
        check_id = f"SC-{uuid.uuid4().hex[:24]}"
        now_iso = checked_at or datetime.now(timezone.utc).isoformat()
        sql = (
            "INSERT INTO vendor_sanctions_checks "
            "(id, organization_id, vendor_name, check_type, provider, "
            " provider_reference, status, matches_json, evidence_json, "
            " raw_payload_json, checked_at, checked_by, review_status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')"
        )
        params = (
            check_id, organization_id, vendor_name, check_type, provider,
            provider_reference, status,
            (json.dumps(matches) if matches else None),
            (json.dumps(evidence) if evidence else None),
            (json.dumps(raw_payload) if raw_payload else None),
            now_iso, checked_by,
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
        return self.get_sanctions_check(check_id) or {
            "id": check_id,
            "organization_id": organization_id,
            "vendor_name": vendor_name,
            "check_type": check_type,
            "provider": provider,
            "status": status,
            "checked_at": now_iso,
            "review_status": "open",
        }

    def update_sanctions_check_review(
        self,
        check_id: str,
        *,
        review_status: str,
        cleared_by: Optional[str] = None,
        cleared_reason: Optional[str] = None,
    ) -> None:
        self.initialize()
        if review_status not in _VALID_REVIEW_STATUSES:
            raise ValueError(
                f"invalid review_status: {review_status!r}"
            )
        cleared_at = (
            datetime.now(timezone.utc).isoformat()
            if review_status != "open" else None
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE vendor_sanctions_checks "
                "SET review_status = %s, cleared_at = %s, "
                "    cleared_by = %s, cleared_reason = %s "
                "WHERE id = %s",
                (
                    review_status, cleared_at, cleared_by,
                    cleared_reason, check_id,
                ),
            )
            conn.commit()

    # ── Reads ──────────────────────────────────────────────────────

    def get_sanctions_check(self, check_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM vendor_sanctions_checks WHERE id = %s",
                (check_id,),
            )
            row = cur.fetchone()
        return self._decode_sanctions_row(row)

    def get_latest_sanctions_check(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        check_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Latest check for the (org, vendor[, check_type]) tuple.

        Used by the pre-payment gate (latest sanctions screen) and by
        the re-screen scheduler (latest of any type)."""
        self.initialize()
        clauses = ["organization_id = %s", "vendor_name = %s"]
        params: List[Any] = [organization_id, vendor_name]
        if check_type:
            clauses.append("check_type = %s")
            params.append(check_type)
        sql = (
            "SELECT * FROM vendor_sanctions_checks "
            "WHERE " + " AND ".join(clauses) + " "
            "ORDER BY checked_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        return self._decode_sanctions_row(row)

    def list_sanctions_checks(
        self,
        organization_id: str,
        *,
        vendor_name: Optional[str] = None,
        status: Optional[str] = None,
        review_status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        clauses = ["organization_id = %s"]
        params: List[Any] = [organization_id]
        if vendor_name:
            clauses.append("vendor_name = %s")
            params.append(vendor_name)
        if status:
            clauses.append("status = %s")
            params.append(status)
        if review_status:
            if review_status not in _VALID_REVIEW_STATUSES:
                raise ValueError(
                    f"invalid review_status filter: {review_status!r}"
                )
            clauses.append("review_status = %s")
            params.append(review_status)
        safe_limit = max(1, min(int(limit or 100), 1000))
        params.append(safe_limit)
        sql = (
            "SELECT * FROM vendor_sanctions_checks "
            "WHERE " + " AND ".join(clauses) + " "
            "ORDER BY checked_at DESC LIMIT %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [
            d for d in (self._decode_sanctions_row(r) for r in rows)
            if d is not None
        ]

    # ── Helpers ────────────────────────────────────────────────────

    def _decode_sanctions_row(self, row) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        out = dict(row)
        for col in ("matches_json", "evidence_json", "raw_payload_json"):
            raw = out.pop(col, None)
            target = col.removesuffix("_json")
            if raw:
                try:
                    out[target] = (
                        json.loads(raw) if isinstance(raw, str) else raw
                    )
                except Exception:
                    out[target] = None
            else:
                out[target] = None
        return out
