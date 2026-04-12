"""SLA Latency Tracker — Agent Design Specification §11.

Tracks latency per processing step for SLA compliance monitoring.
Each step is timed with a context manager and logged to the
``ap_sla_metrics`` table.

SLA targets (§11):
  classification:    <5s  (Starter) / <3s  (Enterprise)
  extraction:        <10s (Starter) / <6s  (Enterprise)
  guardrails:        <500ms
  erp_lookup:        <3s  (Starter) / <2s  (Enterprise)
  three_way_match:   <100ms
  erp_post:          <5s
  slack_delivery:    <3s
  total_to_approval: <5min (Starter) / <2min (Enterprise)

Usage:
    from clearledgr.core.sla_tracker import track_step, get_sla_tracker

    with track_step("classification", ap_item_id=item_id, org_id=org_id):
        result = classify_email(...)

    # Or explicitly:
    tracker = get_sla_tracker()
    tracker.record("extraction", latency_ms=1234, ap_item_id=item_id, org_id=org_id)
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# §11: SLA targets in milliseconds
SLA_TARGETS_MS = {
    "email_receipt_to_queue":  {"starter": 30000,  "enterprise": 30000},
    "queue_to_planning":       {"starter": 60000,  "enterprise": 15000},
    "classification":          {"starter": 5000,   "enterprise": 3000},
    "extraction":              {"starter": 10000,  "enterprise": 6000},
    "guardrails":              {"starter": 500,    "enterprise": 500},
    "erp_lookup":              {"starter": 3000,   "enterprise": 2000},
    "three_way_match":         {"starter": 100,    "enterprise": 100},
    "erp_post":                {"starter": 5000,   "enterprise": 5000},
    "slack_delivery":          {"starter": 3000,   "enterprise": 3000},
    "total_to_approval":       {"starter": 300000, "enterprise": 120000},
}


class SLATracker:
    """Records per-step latency metrics to the database."""

    def __init__(self, db: Any = None):
        self._db = db

    def _get_db(self) -> Any:
        if self._db is not None:
            return self._db
        try:
            from clearledgr.core.database import get_db
            self._db = get_db()
            return self._db
        except Exception:
            return None

    def _resolve_tier(self, organization_id: str, db: Any) -> str:
        """Resolve workspace tier (starter/enterprise) for SLA checking.

        Returns 'starter' (most permissive) when tier cannot be determined.
        """
        try:
            sub = db.get_subscription_record(organization_id) if hasattr(db, "get_subscription_record") else None
            if sub:
                plan = str(sub.get("plan") or "").lower()
                # Enterprise tier uses tighter SLAs
                if plan in ("enterprise", "enterprise_annual"):
                    return "enterprise"
        except Exception:
            pass
        return "starter"

    def record(
        self,
        step_name: str,
        latency_ms: int,
        *,
        ap_item_id: Optional[str] = None,
        organization_id: str = "default",
        breached: Optional[bool] = None,
    ) -> None:
        """Record a latency measurement for an SLA step.

        §11: Breach detection uses the workspace's tier (starter/enterprise).
        Enterprise workspaces have tighter targets than Starter.
        """
        db = self._get_db()
        if not db:
            return

        # Check if SLA was breached against THIS workspace's tier
        if breached is None:
            targets = SLA_TARGETS_MS.get(step_name)
            if targets:
                tier = self._resolve_tier(organization_id, db)
                target_ms = targets.get(tier, targets.get("starter", 999999))
                breached = latency_ms > target_ms

        try:
            db.initialize()
            now = datetime.now(timezone.utc).isoformat()
            metric_id = f"SLA-{uuid.uuid4().hex[:12]}"
            sql = db._prepare_sql(
                "INSERT INTO ap_sla_metrics "
                "(id, ap_item_id, organization_id, step_name, latency_ms, breached, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            )
            with db.connect() as conn:
                conn.execute(sql, (
                    metric_id, ap_item_id, organization_id,
                    step_name, latency_ms, 1 if breached else 0, now,
                ))
                conn.commit()

            if breached:
                logger.warning(
                    "[SLA] %s breached: %dms (target: %s)",
                    step_name, latency_ms, SLA_TARGETS_MS.get(step_name),
                )
        except Exception as exc:
            logger.debug("[SLA] Failed to record metric: %s", exc)

    def get_summary(
        self, organization_id: str, hours: int = 24,
    ) -> Dict[str, Any]:
        """Get SLA compliance summary for the last N hours."""
        db = self._get_db()
        if not db:
            return {}

        try:
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            sql = db._prepare_sql(
                "SELECT step_name, COUNT(*) as total, "
                "AVG(latency_ms) as avg_ms, MAX(latency_ms) as max_ms, "
                "SUM(CASE WHEN breached = 1 THEN 1 ELSE 0 END) as breached_count "
                "FROM ap_sla_metrics "
                "WHERE organization_id = ? AND created_at >= ? "
                "GROUP BY step_name"
            )
            with db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, cutoff))
                rows = cur.fetchall()
            return {
                "organization_id": organization_id,
                "period_hours": hours,
                "steps": [
                    {
                        "step": dict(r)["step_name"],
                        "total": dict(r)["total"],
                        "avg_ms": round(dict(r)["avg_ms"] or 0),
                        "max_ms": dict(r)["max_ms"] or 0,
                        "breached": dict(r)["breached_count"] or 0,
                        "compliance_pct": round(
                            (1 - (dict(r)["breached_count"] or 0) / max(dict(r)["total"], 1)) * 100, 1
                        ),
                    }
                    for r in rows
                ],
            }
        except Exception as exc:
            logger.debug("[SLA] Summary query failed: %s", exc)
            return {}


# Singleton
_tracker: Optional[SLATracker] = None


def get_sla_tracker() -> SLATracker:
    global _tracker
    if _tracker is None:
        _tracker = SLATracker()
    return _tracker


@contextmanager
def track_step(
    step_name: str,
    *,
    ap_item_id: Optional[str] = None,
    organization_id: str = "default",
):
    """Context manager to time an SLA step.

    Usage:
        with track_step("classification", ap_item_id="AP-123", organization_id="org-1"):
            result = classify_email(...)
    """
    start = time.monotonic()
    try:
        yield
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        get_sla_tracker().record(
            step_name, latency_ms,
            ap_item_id=ap_item_id,
            organization_id=organization_id,
        )
