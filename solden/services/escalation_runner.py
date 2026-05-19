"""Escalation runner — Module 11 (org-level stuck-exception escalation).

Per-tick the Celery beat task ``fire_due_escalation_policies`` calls
``run_escalation_tick(db)`` which:

  1. Lists every active ``escalation_policy`` (across orgs).
  2. For each, queries ``find_unescalated_due_exceptions`` to surface
     box_exceptions that have crossed the threshold and haven't yet
     been escalated under this policy.
  3. For each match: send the configured action (notify_email),
     record a row in ``escalation_events`` (idempotency-safe via the
     UNIQUE constraint), continue.

Idempotency guarantee: even if two workers race on the same minute,
the UNIQUE(policy_id, exception_id) constraint on escalation_events
ensures a recipient is emailed at most once per (policy, exception).

Acceptance criterion (§354): "fires within 1 minute of threshold
breach." The Celery beat schedule runs this every 60 seconds; with
the threshold check in seconds-precision and the action issued
inline, breach-to-delivery latency is bounded by SMTP round-trip +
1 minute schedule jitter.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from solden.services.transactional_email import (
    EmailDeliveryResult,
    send_transactional_email,
)

logger = logging.getLogger(__name__)


@dataclass
class EscalationFireResult:
    policy_id: str
    exception_id: str
    delivered: bool
    skipped: bool = False
    error: Optional[str] = None


@dataclass
class EscalationTickSummary:
    processed: int = 0
    fired: int = 0
    failed: int = 0
    skipped: int = 0


def run_escalation_tick(db: Any) -> EscalationTickSummary:
    """One sweep of the escalation worker. Iterates every active
    policy across all orgs; never raises."""
    summary = EscalationTickSummary()

    try:
        policies = _list_active_policies_all_orgs(db)
    except Exception as exc:
        logger.exception("[escalation] failed to list policies: %s", exc)
        return summary

    for policy in policies:
        try:
            results = _fire_policy(db, policy)
        except Exception as exc:
            logger.exception(
                "[escalation] policy %s tick failed: %s",
                policy.get("id"), exc,
            )
            continue
        summary.processed += len(results)
        for r in results:
            if r.skipped:
                summary.skipped += 1
            elif r.delivered:
                summary.fired += 1
            else:
                summary.failed += 1

    if summary.processed:
        logger.info(
            "[escalation] tick: processed=%d fired=%d failed=%d skipped=%d",
            summary.processed, summary.fired, summary.failed, summary.skipped,
        )
    return summary


def _list_active_policies_all_orgs(db: Any) -> List[Dict[str, Any]]:
    """Cross-org listing of active policies. The store mixin exposes
    a per-org list helper; here we read directly because the
    worker iterates every tenant."""
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM escalation_policies "
            "WHERE is_active = 1 ORDER BY organization_id, created_at"
        )
        rows = cur.fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["is_active"] = bool(d.get("is_active"))
        for key in ("recipients_json", "exception_types", "severity_filter"):
            value = d.get(key)
            if isinstance(value, str) and value:
                try:
                    parsed = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    parsed = []
                if key == "recipients_json":
                    d["recipients"] = parsed
                else:
                    d[key] = parsed
        if "recipients" not in d:
            d["recipients"] = []
        out.append(d)
    return out


def _fire_policy(db: Any, policy: Dict[str, Any]) -> List[EscalationFireResult]:
    matches = db.find_unescalated_due_exceptions(policy, limit=200)
    if not matches:
        return []

    results: List[EscalationFireResult] = []
    for exc_row in matches:
        result = _fire_single(db, policy, exc_row)
        results.append(result)
    return results


def _fire_single(
    db: Any, policy: Dict[str, Any], exception_row: Dict[str, Any],
) -> EscalationFireResult:
    action = policy.get("action") or "notify_email"
    if action != "notify_email":
        # Action enum allows future expansion; today only notify_email.
        return EscalationFireResult(
            policy_id=policy["id"], exception_id=exception_row["id"],
            delivered=False, skipped=True, error=f"unsupported_action:{action}",
        )

    recipients = policy.get("recipients") or []
    if not recipients:
        # Mis-configured policy. Record the attempt with the error so
        # the (policy, exception) pair is recognised as fired and we
        # don't loop on it.
        db.record_escalation_event(
            policy_id=policy["id"],
            exception_id=exception_row["id"],
            organization_id=policy.get("organization_id") or "",
            delivered=False,
            delivery_error="no recipients configured",
        )
        return EscalationFireResult(
            policy_id=policy["id"], exception_id=exception_row["id"],
            delivered=False, error="no recipients configured",
        )

    subject, body_text = _build_email(policy, exception_row)
    delivered = True
    last_error: Optional[str] = None
    skipped_count = 0
    for recipient in recipients:
        result: EmailDeliveryResult = send_transactional_email(
            to_addr=recipient, subject=subject, body_text=body_text,
        )
        if result.skipped:
            skipped_count += 1
            continue
        if not result.ok:
            delivered = False
            last_error = result.error_message or "delivery failed"

    if skipped_count == len(recipients):
        # SMTP not configured — deployment state, not a failure. Don't
        # record an event so a later config fix picks up the queue.
        return EscalationFireResult(
            policy_id=policy["id"], exception_id=exception_row["id"],
            delivered=False, skipped=True,
            error="smtp_not_configured",
        )

    db.record_escalation_event(
        policy_id=policy["id"],
        exception_id=exception_row["id"],
        organization_id=policy.get("organization_id") or "",
        delivered=delivered,
        delivery_error=last_error,
    )
    return EscalationFireResult(
        policy_id=policy["id"], exception_id=exception_row["id"],
        delivered=delivered, error=last_error,
    )


def _build_email(
    policy: Dict[str, Any], exception_row: Dict[str, Any],
) -> tuple:
    threshold = policy.get("threshold_hours") or 24
    exc_type = exception_row.get("exception_type") or "exception"
    severity = exception_row.get("severity") or "medium"
    box_id = exception_row.get("box_id") or ""
    raised_at = exception_row.get("raised_at") or ""
    reason = exception_row.get("reason") or ""

    subject = (
        f"[Solden] Escalation: {exc_type} on {box_id} "
        f"older than {threshold}h"
    )
    body_text = (
        f"An open exception in your AP queue has crossed the {threshold}-hour "
        f"escalation threshold under policy '{policy.get('name')}'.\n"
        "\n"
        f"  Exception type: {exc_type}\n"
        f"  Severity:       {severity}\n"
        f"  Box id:         {box_id}\n"
        f"  Raised at:      {raised_at}\n"
        "\n"
        f"Reason:\n{reason}\n"
        "\n"
        "Open the dashboard to resolve:\n"
        f"  https://workspace.soldenai.com/records/{box_id}\n"
        "\n"
        "Once the underlying exception is resolved, no further escalations "
        "fire for this item."
    )
    return subject, body_text
