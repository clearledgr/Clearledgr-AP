"""
Conditional Digest — DESIGN_THESIS.md §6.8

"On any working day where the previous 24 hours produced no exceptions,
no pending approvals, and no onboarding blockers, no digest is sent.
Silence is the signal that everything ran correctly."

Digest sections when sent:
1. What the agent handled (count + total of invoices processed without human)
2. What needs you today (exceptions + pending approvals, max 5)
3. Due for payment this week (approved invoices next 5 working days)
4. Vendor onboarding status (vendors stuck 48h+)
5. Agent confidence note (if accuracy below baseline)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _is_working_day(dt: datetime) -> bool:
    """Monday=0 .. Friday=4 are working days."""
    return dt.weekday() < 5


async def build_digest(
    org_id: str,
    *,
    db=None,
) -> Optional[Dict[str, Any]]:
    """Build the conditional digest payload. Returns None if silence is appropriate.

    Returns a dict with sections and block kit blocks if there's something to report.
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    # Resolve org timezone — thesis says digest should respect local working days
    org_tz = timezone.utc
    try:
        org_data = db.get_organization(org_id)
        tz_name = ((org_data or {}).get("settings_json") or {})
        if isinstance(tz_name, str):
            import json as _json
            tz_name = _json.loads(tz_name)
        tz_str = (tz_name or {}).get("timezone", "")
        if tz_str:
            try:
                from zoneinfo import ZoneInfo
            except ImportError:
                from backports.zoneinfo import ZoneInfo
            org_tz = ZoneInfo(tz_str)
    except Exception:
        pass

    now = datetime.now(org_tz)
    if not _is_working_day(now):
        return None

    yesterday = now - timedelta(hours=24)

    # Gather data
    try:
        all_items = db.list_ap_items(organization_id=org_id, limit=1000)
    except Exception:
        all_items = []

    # Section 1: What the agent handled (autonomous actions in last 24h)
    autonomous_states = {"posted_to_erp", "closed", "approved", "ready_to_post"}
    agent_handled = [
        item for item in all_items
        if item.get("state") in autonomous_states
        and _updated_within(item, yesterday)
    ]
    agent_handled_count = len(agent_handled)
    agent_handled_total = sum(float(item.get("amount") or 0) for item in agent_handled)
    currency = _dominant_currency(agent_handled) or "USD"

    # Section 2: What needs you today (exceptions + pending approvals)
    exception_states = {"needs_info", "failed_post", "reversed"}
    approval_states = {"needs_approval", "pending_approval"}
    exceptions = [item for item in all_items if item.get("state") in exception_states]
    pending_approvals = [item for item in all_items if item.get("state") in approval_states]
    needs_action = sorted(
        exceptions + pending_approvals,
        key=lambda i: i.get("due_date") or "9999",
    )[:5]
    total_needs_action = len(exceptions) + len(pending_approvals)

    # Section 3: Due for payment this week
    week_ahead = now + timedelta(days=5)
    due_this_week = [
        item for item in all_items
        if item.get("state") in {"approved", "ready_to_post", "posted_to_erp"}
        and item.get("due_date")
        and _is_before(item["due_date"], week_ahead)
    ]

    # Section 4: Vendor onboarding blockers (48h+)
    try:
        from clearledgr.services.vendor_onboarding_lifecycle import _list_all_active_sessions
        sessions = await _list_all_active_sessions(db, org_id)
    except Exception:
        sessions = []
    blocked_states = {"invited", "awaiting_kyc", "awaiting_bank", "microdeposit_pending", "escalated"}
    onboarding_blockers = [
        s for s in sessions
        if s.get("state") in blocked_states
        and _hours_since(s.get("invited_at")) >= 48
    ]

    # Section 5: Agent confidence (check if accuracy below customer's baseline)
    # Thesis: "If the agent's match accuracy for the previous week is below
    # the customer's established baseline, it flags this."
    confidence_note = None
    try:
        kpis = db.get_org_kpis(org_id) if hasattr(db, "get_org_kpis") else {}
        accuracy = kpis.get("match_accuracy_pct") or kpis.get("touchless_rate_pct")
        # Customer baseline from trust arc state or org settings
        customer_baseline = 97.3  # Default industry baseline
        try:
            org_data = db.get_organization(org_id)
            org_s = (org_data or {}).get("settings_json")
            if isinstance(org_s, str):
                import json as _j2
                org_s = _j2.loads(org_s)
            trust_arc = (org_s or {}).get("trust_arc") or {}
            if trust_arc.get("established_baseline"):
                customer_baseline = float(trust_arc["established_baseline"])
        except Exception:
            pass
        if accuracy is not None and float(accuracy) < customer_baseline:
            cases_reviewed = kpis.get("overrides_this_week") or kpis.get("corrections_this_week") or 0
            confidence_note = (
                f"Match accuracy {float(accuracy):.1f}% this week vs "
                f"{customer_baseline:.1f}% baseline."
                + (f" {cases_reviewed} cases reviewed." if cases_reviewed else "")
            )
    except Exception:
        pass

    # Conditional silence: if nothing to report, return None
    if (
        total_needs_action == 0
        and len(onboarding_blockers) == 0
        and confidence_note is None
    ):
        logger.info("[digest] org=%s: silence — no exceptions, no pending, no blockers", org_id)
        return None

    # Build the digest payload
    blocks = _build_digest_blocks(
        agent_handled_count=agent_handled_count,
        agent_handled_total=agent_handled_total,
        currency=currency,
        needs_action=needs_action,
        total_needs_action=total_needs_action,
        due_this_week=due_this_week,
        onboarding_blockers=onboarding_blockers,
        confidence_note=confidence_note,
    )

    return {
        "org_id": org_id,
        "blocks": blocks,
        "summary": {
            "agent_handled": agent_handled_count,
            "needs_action": total_needs_action,
            "due_this_week": len(due_this_week),
            "onboarding_blockers": len(onboarding_blockers),
            "confidence_note": confidence_note,
        },
    }


async def send_digest(org_id: str) -> bool:
    """Build and send the conditional digest to the org's Slack channel."""
    digest = await build_digest(org_id)
    if digest is None:
        return False  # Silence is the signal

    try:
        from clearledgr.services.slack_api import resolve_slack_runtime
        runtime = resolve_slack_runtime(org_id)
        if not runtime or not runtime.get("channel"):
            logger.warning("[digest] org=%s: no Slack channel configured", org_id)
            return False

        import httpx
        headers = {"Authorization": f"Bearer {runtime['token']}", "Content-Type": "application/json"}
        payload = {
            "channel": runtime["channel"],
            "text": "Clearledgr Daily Digest",
            "blocks": digest["blocks"],
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://slack.com/api/chat.postMessage", json=payload, headers=headers)
            data = resp.json()
            if not data.get("ok"):
                logger.warning("[digest] Slack post failed: %s", data.get("error"))
                return False
        return True
    except Exception as exc:
        logger.warning("[digest] send failed for org=%s: %s", org_id, exc)
        return False


# ==================== HELPERS ====================


def _updated_within(item: Dict, since: datetime) -> bool:
    updated = item.get("updated_at") or item.get("created_at")
    if not updated:
        return False
    try:
        dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        return dt >= since
    except (ValueError, TypeError):
        return False


def _is_before(date_str: str, cutoff: datetime) -> bool:
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        return dt <= cutoff
    except (ValueError, TypeError):
        return False


def _hours_since(date_str) -> float:
    if not date_str:
        return 0
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except (ValueError, TypeError):
        return 0


def _dominant_currency(items: List[Dict]) -> str:
    currencies = [str(i.get("currency") or "").upper() for i in items if i.get("currency")]
    if not currencies:
        return "USD"
    from collections import Counter
    return Counter(currencies).most_common(1)[0][0]


def _build_digest_blocks(
    *,
    agent_handled_count: int,
    agent_handled_total: float,
    currency: str,
    needs_action: List[Dict],
    total_needs_action: int,
    due_this_week: List[Dict],
    onboarding_blockers: List[Dict],
    confidence_note: Optional[str],
) -> List[Dict[str, Any]]:
    """Build Slack Block Kit blocks for the digest."""
    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Clearledgr Daily Digest"}},
    ]

    # Section 1: What the agent handled — thesis: adapts if zero or exceptions
    if agent_handled_count > 0 and total_needs_action == 0:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*What the agent handled*\n{agent_handled_count} invoices processed automatically — {currency} {agent_handled_total:,.0f} total. No exceptions.",
            },
        })
    elif agent_handled_count > 0:
        # Agent handled some but there were also exceptions
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*What the agent handled*\n{agent_handled_count} invoices processed — {currency} {agent_handled_total:,.0f} total. {total_needs_action} exception(s) need attention below.",
            },
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*What the agent handled*\nNo invoices processed in the last 24 hours."},
        })

    blocks.append({"type": "divider"})

    # Section 2: What needs you today
    if needs_action:
        lines = [f"*What needs you today* ({total_needs_action} total)"]
        for item in needs_action:
            vendor = item.get("vendor_name") or item.get("vendor") or "Unknown"
            amount = float(item.get("amount") or 0)
            state = (item.get("state") or "").replace("_", " ")
            lines.append(f"• {vendor} — {currency} {amount:,.0f} — {state}")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
        if total_needs_action > 5:
            blocks.append({
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": f"See all {total_needs_action}"},
                    "action_id": "digest_open_exceptions",
                    "url": "https://mail.google.com/mail/u/0/#clearledgr/invoices",
                }],
            })
        blocks.append({"type": "divider"})

    # Section 3: Due for payment this week
    if due_this_week:
        lines = [f"*Due for payment this week* ({len(due_this_week)} invoices)"]
        for item in due_this_week[:5]:
            vendor = item.get("vendor_name") or item.get("vendor") or "Unknown"
            amount = float(item.get("amount") or 0)
            due = item.get("due_date", "")[:10]
            lines.append(f"• {vendor} — {currency} {amount:,.0f} — due {due}")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
        blocks.append({"type": "divider"})

    # Section 4: Vendor onboarding blockers
    if onboarding_blockers:
        lines = [f"*Vendor onboarding blockers* ({len(onboarding_blockers)} vendors stuck 48h+)"]
        for s in onboarding_blockers[:3]:
            vendor = s.get("vendor_name") or "Unknown"
            state = (s.get("state") or "").replace("_", " ")
            hours = int(_hours_since(s.get("invited_at")))
            lines.append(f"• {vendor} — {state} — {hours // 24}d elapsed")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
        blocks.append({"type": "divider"})

    # Section 5: Agent confidence note
    if confidence_note:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"⚡ {confidence_note}"}],
        })

    return blocks
