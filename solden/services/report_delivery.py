"""Scheduled report delivery — runs the report generator for a
subscription, renders the email, sends it, and records the outcome.

Called hourly by ``deliver_due_report_subscriptions`` in
``celery_tasks.py``. Designed so the worker handler stays a thin
shell: pop a subscription, hand it to ``deliver_subscription``, get
back a structured result.

Two layers of safety net:
  - Each subscription delivery is independent. One subscription's
    SMTP timeout doesn't block the rest of the queue.
  - Failures are recorded per-subscription via
    ``record_subscription_failure``; success advances ``next_due_at``.
  - Five consecutive failures auto-pauses the row so a misconfigured
    SMTP doesn't keep firing retries every hour indefinitely.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from solden.services import workspace_reports
from solden.services.transactional_email import (
    EmailAttachment,
    EmailDeliveryResult,
    send_transactional_email,
)

logger = logging.getLogger(__name__)


_REPORT_LABELS = {
    "volume": "Volume",
    "agent_performance": "Agent Performance",
    "cycle_time": "Cycle Time",
    "exception_breakdown": "Exception Breakdown",
    "vendor_quality": "Vendor Quality",
}


@dataclass
class SubscriptionDeliveryResult:
    subscription_id: str
    ok: bool
    skipped: bool = False
    error_message: Optional[str] = None


def deliver_subscription(db: Any, subscription: Dict[str, Any]) -> SubscriptionDeliveryResult:
    """Run the report, render the email, send it, and update the row.

    Returns a structured result. Never raises. Caller (the Celery task)
    can iterate over a list of subscriptions without try/except.
    """
    sub_id = subscription.get("id") or ""
    report_type = subscription.get("report_type", "")
    if report_type not in workspace_reports.VALID_REPORT_TYPES:
        msg = f"unknown report_type: {report_type!r}"
        db.record_subscription_failure(sub_id, error=msg)
        return SubscriptionDeliveryResult(
            subscription_id=sub_id, ok=False, error_message=msg,
        )

    generator = workspace_reports.REPORT_GENERATORS[report_type]
    params = subscription.get("params") or {}
    org_id = subscription.get("organization_id") or ""

    # Build the kwargs the generator accepts. The from/to params are
    # rolling — recomputed at delivery time so a "weekly volume report"
    # always covers the most recent window the operator chose.
    gen_kwargs: Dict[str, Any] = {}
    for key in ("period", "entity_id", "vendor_name", "min_invoices", "limit"):
        value = params.get(key)
        if value is not None:
            gen_kwargs[key] = value

    try:
        payload = generator(org_id, **gen_kwargs)
    except TypeError:
        # Some generators don't accept all kwargs (e.g. vendor_quality
        # has no period). Try again with only the kwargs they accept.
        import inspect
        sig = inspect.signature(generator)
        accepted = {
            k: v for k, v in gen_kwargs.items()
            if k in sig.parameters
        }
        try:
            payload = generator(org_id, **accepted)
        except Exception as exc:
            db.record_subscription_failure(sub_id, error=str(exc))
            return SubscriptionDeliveryResult(
                subscription_id=sub_id, ok=False, error_message=str(exc),
            )
    except Exception as exc:
        db.record_subscription_failure(sub_id, error=str(exc))
        return SubscriptionDeliveryResult(
            subscription_id=sub_id, ok=False, error_message=str(exc),
        )

    csv_text = workspace_reports.report_to_csv(payload)
    csv_filename = workspace_reports.csv_filename(report_type, payload.get("params", {}))
    label = _REPORT_LABELS.get(report_type, report_type)

    subject = f"[Solden] {label} report — {payload.get('params', {}).get('period') or 'snapshot'}"
    body_text = _build_email_body_text(label, payload, subscription)
    body_html = _build_email_body_html(label, payload, subscription)

    result: EmailDeliveryResult = send_transactional_email(
        to_addr=subscription.get("recipient_email", ""),
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attachments=[EmailAttachment(
            filename=csv_filename,
            content=csv_text.encode("utf-8"),
            content_type="text/csv",
        )],
    )

    if result.skipped:
        # SMTP not configured — this is a deployment state, not a
        # delivery miss. Don't increment failure_count or auto-pause.
        # We still leave next_due_at unchanged so a later config fix
        # picks up the queue.
        return SubscriptionDeliveryResult(
            subscription_id=sub_id, ok=False, skipped=True,
            error_message="smtp_not_configured",
        )

    if not result.ok:
        db.record_subscription_failure(
            sub_id, error=result.error_message or "delivery failed",
        )
        return SubscriptionDeliveryResult(
            subscription_id=sub_id, ok=False, error_message=result.error_message,
        )

    db.record_subscription_delivery(sub_id)
    return SubscriptionDeliveryResult(subscription_id=sub_id, ok=True)


def deliver_due_subscriptions(db: Any, *, limit: int = 100) -> List[SubscriptionDeliveryResult]:
    """The Celery-task entry point. Pops up to ``limit`` due rows and
    delivers them one at a time."""
    due = db.due_report_subscriptions(limit=limit) or []
    results: List[SubscriptionDeliveryResult] = []
    for sub in due:
        try:
            results.append(deliver_subscription(db, sub))
        except Exception as exc:
            # Belt-and-suspenders: deliver_subscription is supposed to
            # never raise, but if it does we don't want one bad row
            # taking the whole batch down.
            logger.exception(
                "[report_delivery] unexpected error delivering %s: %s",
                sub.get("id"), exc,
            )
            results.append(SubscriptionDeliveryResult(
                subscription_id=sub.get("id") or "",
                ok=False,
                error_message=f"unexpected_error: {exc}",
            ))
    return results


# ---------------------------------------------------------------------------
# Email body rendering — plain text + HTML, both built from the same payload.
# ---------------------------------------------------------------------------

def _build_email_body_text(
    label: str, payload: Dict[str, Any], subscription: Dict[str, Any],
) -> str:
    summary = payload.get("summary") or {}
    params = payload.get("params") or {}
    cadence = subscription.get("cadence", "")

    lines: List[str] = [
        f"Your {cadence} {label} report from Solden.",
        "",
        f"Window: {params.get('from', '')[:10]} to {params.get('to', '')[:10]}",
    ]
    if params.get("period"):
        lines.append(f"Period: {params['period']}")
    lines.append("")

    if summary:
        lines.append("Summary:")
        for key, value in summary.items():
            lines.append(f"  {key}: {value}")
        lines.append("")

    lines.append(
        "The full data is attached as a CSV. Open the dashboard for the "
        "interactive view: https://workspace.clearledgr.com/reports"
    )
    lines.append("")
    lines.append(
        "To unsubscribe from this email, open the Reports page and remove "
        "the subscription."
    )
    return "\n".join(lines)


def _build_email_body_html(
    label: str, payload: Dict[str, Any], subscription: Dict[str, Any],
) -> str:
    summary = payload.get("summary") or {}
    params = payload.get("params") or {}
    cadence = subscription.get("cadence", "")

    summary_rows = "".join(
        f"<tr><td style='padding:4px 12px 4px 0;color:#475569;font-size:13px'>{_h(key)}</td>"
        f"<td style='padding:4px 0;color:#0F172A;font-size:13px;font-family:monospace'>{_h(str(value))}</td></tr>"
        for key, value in summary.items()
    )
    return f"""<!doctype html>
<html><body style="font-family:-apple-system,system-ui,sans-serif;color:#0F172A;background:#FAFAF8;padding:24px">
  <div style="max-width:600px;margin:0 auto;background:#FFFFFF;border:1px solid #E2E8F0;border-radius:10px;padding:32px">
    <h1 style="font-size:18px;font-weight:600;margin:0 0 4px;color:#0A1628">
      {_h(label)} report
    </h1>
    <p style="margin:0 0 24px;color:#475569;font-size:13px">
      Your {_h(cadence)} Solden report. Window: {_h(params.get('from', '')[:10])} to {_h(params.get('to', '')[:10])}.
    </p>
    {f'<table style="border-collapse:collapse;margin:0 0 24px"><tbody>{summary_rows}</tbody></table>' if summary_rows else ''}
    <p style="margin:0 0 16px;color:#475569;font-size:13px">
      Full data is attached as a CSV.
      <a href="https://workspace.clearledgr.com/reports" style="color:#00B86B;text-decoration:none;font-weight:500">
        Open the dashboard
      </a>
      for the interactive view.
    </p>
    <p style="margin:24px 0 0;font-size:12px;color:#94A3B8">
      To unsubscribe, open the Reports page and remove this subscription.
    </p>
  </div>
</body></html>
"""


def _h(value: str) -> str:
    """Cheap HTML escape — avoids a templating dep for two strings."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
