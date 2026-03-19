"""
Clearledgr Slack App - Thin Client

This Slack app connects to the central Clearledgr backend.
All intelligence lives in the backend. Slack is just an interface.

Architecture:
  Gmail Extension → Backend ← Slack App
                       ↑
                   Sheets Add-on

Features:
- /clearledgr slash commands
- Interactive approval buttons
- Real-time exception notifications
- Vita AI chat via @clearledgr mentions
"""

import logging
import os
import json
import hmac
import hashlib
import time
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from clearledgr.services.slack_api import resolve_slack_runtime

router = APIRouter(prefix="/slack", tags=["slack"])

# Configuration
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
DEFAULT_ORG_ID = os.getenv("DEFAULT_ORGANIZATION_ID", "default")


# ==================== BACKEND API CLIENT ====================

async def api(method: str, endpoint: str, body: Optional[Dict] = None) -> Optional[Dict]:
    """Call the Clearledgr backend API."""
    url = f"{API_BASE_URL}{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "X-Organization-ID": DEFAULT_ORG_ID,
    }
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.post(url, json=body, headers=headers)
            
            if resp.status_code < 400:
                return resp.json()
            else:
                print(f"[Slack] API error: {resp.status_code} - {resp.text}")
                return None
    except Exception as e:
        print(f"[Slack] API call failed: {e}")
        return None


# ==================== SLACK API ====================

def _resolve_org_id_for_team(team_id: Optional[str]) -> str:
    if not team_id:
        return DEFAULT_ORG_ID
    try:
        from clearledgr.core.database import get_db

        install = get_db().get_slack_installation_by_team(team_id)
        if install and install.get("organization_id"):
            return str(install["organization_id"])
    except Exception:
        pass
    return DEFAULT_ORG_ID


async def slack_api(method: str, payload: Dict, organization_id: Optional[str] = None) -> Optional[Dict]:
    """Call Slack API."""
    runtime = resolve_slack_runtime(organization_id or DEFAULT_ORG_ID)
    bot_token = runtime.get("bot_token") or SLACK_BOT_TOKEN
    if not bot_token:
        print("[Slack] No bot token configured")
        return None
    
    url = f"https://slack.com/api/{method}"
    headers = {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json",
    }
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
            return resp.json()
    except Exception as e:
        print(f"[Slack] Slack API error: {e}")
        return None


async def send_message(
    channel: str,
    text: str,
    blocks: Optional[List] = None,
    thread_ts: Optional[str] = None,
    organization_id: Optional[str] = None,
):
    """Send a message to a Slack channel."""
    payload = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return await slack_api("chat.postMessage", payload, organization_id=organization_id)


async def update_message(
    channel: str,
    ts: str,
    text: str,
    blocks: Optional[List] = None,
    organization_id: Optional[str] = None,
):
    """Update an existing Slack message."""
    payload = {"channel": channel, "ts": ts, "text": text}
    if blocks:
        payload["blocks"] = blocks
    return await slack_api("chat.update", payload, organization_id=organization_id)


# ==================== VERIFICATION ====================

def verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify request came from Slack."""
    if not SLACK_SIGNING_SECRET:
        return True  # Skip in dev
    
    if abs(time.time() - int(timestamp)) > 300:
        return False
    
    sig_base = f"v0:{timestamp}:{body.decode()}"
    computed = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        sig_base.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(computed, signature)


# ==================== ENDPOINTS ====================

@router.post("/events")
async def slack_events(request: Request):
    """Handle Slack events (messages, app mentions, etc.)."""
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    
    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    data = json.loads(body)
    
    # URL verification challenge
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}
    
    event = data.get("event", {})
    event_type = event.get("type")
    
    # Handle app mentions (@clearledgr)
    if event_type == "app_mention":
        await handle_mention(event)
    
    # Handle direct messages
    elif event_type == "message" and event.get("channel_type") == "im":
        if not event.get("bot_id"):  # Ignore bot messages
            await handle_dm(event)
    
    # Handle messages in channels (for expense detection)
    elif event_type == "message" and not event.get("bot_id"):
        # Check if this is an expense-related message
        await check_for_expense(event)
    
    return {"ok": True}


@router.post("/commands")
async def slack_commands(request: Request):
    """Handle /clearledgr slash commands."""
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    
    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    form = dict(x.split("=") for x in body.decode().split("&"))
    command = form.get("command", "")
    text = form.get("text", "").replace("+", " ")
    user_id = form.get("user_id", "")
    channel_id = form.get("channel_id", "")
    
    # Parse command
    parts = text.strip().split(maxsplit=1)
    action = parts[0].lower() if parts else "help"
    args = parts[1] if len(parts) > 1 else ""
    
    response_text = await handle_command(action, args, user_id, channel_id)
    
    return JSONResponse({"response_type": "in_channel", "text": response_text})


@router.post("/interactions")
async def slack_interactions(request: Request):
    """Handle interactive components (buttons, modals, etc.)."""
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    
    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse payload
    form = dict(x.split("=") for x in body.decode().split("&"))
    payload = json.loads(form.get("payload", "{}").replace("%22", '"').replace("%7B", "{").replace("%7D", "}"))
    
    payload_type = payload.get("type", "")
    trigger_id = payload.get("trigger_id", "")

    # Handle modal submissions
    if payload_type == "view_submission":
        callback_id = payload.get("view", {}).get("callback_id", "")
        if callback_id.startswith("expense_reject_modal_"):
            expense_id = callback_id.replace("expense_reject_modal_", "")
            submitting_user = payload.get("user", {}).get("id", "")
            values = payload.get("view", {}).get("state", {}).get("values", {})
            reason = (
                values.get("reason_block", {})
                .get("rejection_reason", {})
                .get("value", "No reason provided")
            )
            # Retrieve channel/ts from private_metadata
            import urllib.parse as _urlparse
            meta = dict(_urlparse.parse_qsl(payload.get("view", {}).get("private_metadata", "")))
            meta_channel = meta.get("channel", "")
            meta_ts = meta.get("ts", "")
            if meta_channel and meta_ts:
                await update_message(
                    meta_channel, meta_ts,
                    f"*Expense Rejected*\n\nRejected by <@{submitting_user}>\n*Reason:* {reason}",
                )
        return {"response_action": "clear"}

    action_id = ""
    if payload.get("actions"):
        action_id = payload["actions"][0].get("action_id", "")

    user_id = payload.get("user", {}).get("id", "")
    channel = payload.get("channel", {}).get("id", "")
    message_ts = payload.get("message", {}).get("ts", "")
    message_payload = payload.get("message", {})
    team_id = payload.get("team", {}).get("id", "")
    org_id = _resolve_org_id_for_team(team_id)

    # Handle actions
    if action_id.startswith("approve_invoice_"):
        gmail_id = action_id.replace("approve_invoice_", "")
        await handle_invoice_approve(gmail_id, user_id, channel, message_ts, organization_id=org_id)
    
    elif action_id.startswith("reject_invoice_"):
        gmail_id = action_id.replace("reject_invoice_", "")
        await handle_invoice_reject(gmail_id, user_id, channel, message_ts, organization_id=org_id)

    elif action_id.startswith("approve_budget_override_"):
        gmail_id = action_id.replace("approve_budget_override_", "")
        await handle_budget_override(gmail_id, user_id, channel, message_ts, organization_id=org_id)

    elif action_id.startswith("request_budget_adjustment_"):
        gmail_id = action_id.replace("request_budget_adjustment_", "")
        await handle_budget_adjustment(gmail_id, user_id, channel, message_ts, organization_id=org_id)

    elif action_id.startswith("reject_budget_"):
        gmail_id = action_id.replace("reject_budget_", "")
        await handle_budget_reject(gmail_id, user_id, channel, message_ts, organization_id=org_id)

    elif action_id.startswith("flag_invoice_"):
        gmail_id = action_id.replace("flag_invoice_", "")
        await handle_invoice_flag(gmail_id, user_id, channel, message_ts, organization_id=org_id)
    
    elif action_id.startswith("approve_"):
        item_id = action_id.replace("approve_", "")
        await handle_approve(item_id, user_id, channel, message_ts)
    
    elif action_id.startswith("reject_"):
        item_id = action_id.replace("reject_", "")
        await handle_reject(item_id, user_id, channel, message_ts)
    
    elif action_id.startswith("resolve_"):
        exc_id = action_id.replace("resolve_", "")
        await handle_resolve(exc_id, user_id, channel, message_ts)
    
    elif action_id.startswith("review_exception_"):
        exc_id = action_id.replace("review_exception_", "")
        await handle_review_exception(exc_id, user_id, channel, message_ts)
    
    elif action_id.startswith("dismiss_exception_"):
        exc_id = action_id.replace("dismiss_exception_", "")
        await handle_dismiss_exception(exc_id, user_id, channel, message_ts)
    
    # Expense actions
    elif action_id.startswith("approve_expense_"):
        expense_id = action_id.replace("approve_expense_", "")
        await handle_expense_approve(expense_id, user_id, channel, message_ts)
    
    elif action_id.startswith("reject_expense_"):
        expense_id = action_id.replace("reject_expense_", "")
        await handle_expense_reject(expense_id, user_id, channel, message_ts, trigger_id=trigger_id)

    elif action_id.startswith("need_receipt_"):
        expense_id = action_id.replace("need_receipt_", "")
        await handle_need_receipt(expense_id, user_id, channel, message_ts, message=message_payload)
    
    # Clarifying question responses (2026-01-23)
    elif action_id.startswith("clarify_"):
        # Format: clarify_{question_id}_{response_value}
        parts = action_id.split("_", 2)
        if len(parts) >= 3:
            question_id = parts[1]
            # Get response value from the action value
            action_value = payload["actions"][0].get("value", "")
            # value format: question_id:response_value
            response_value = action_value.split(":", 1)[1] if ":" in action_value else parts[2]
            await handle_clarifying_response(question_id, response_value, user_id, channel, message_ts)
    
    return {"ok": True}


# ==================== COMMAND HANDLERS ====================

async def handle_command(action: str, args: str, user_id: str, channel_id: str) -> str:
    """Handle /clearledgr commands."""
    
    if action == "help":
        return """*Clearledgr Commands:*

*Setup & Config:*
- `/clearledgr setup` - Onboarding checklist and integration status
- `/clearledgr config channel #channel` - Set the approval notification channel
- `/clearledgr setup erp [quickbooks|xero|netsuite]` - Connect your ERP
- `/clearledgr invite user@email.com [admin|member]` - Invite a team member

*Daily Use:*
- `/clearledgr status` - View current status
- `/clearledgr queue` - View invoice priority queue
- `/clearledgr approve [id]` - Approve a draft entry
- `/clearledgr exceptions` - View open exceptions
- `/clearledgr drafts` - View pending draft entries
- `/clearledgr reconcile` - Run reconciliation
- `/clearledgr ask [question]` - Ask Vita AI a question
- `/clearledgr forecast` - View cash flow forecast
- `/clearledgr budget` - View budget status

*Natural Language (just type):*
- "Approve all AWS invoices under $500"
- "Show pending invoices from Stripe"
- "How much did we pay Acme last month?"
- "Flag anything over $10,000 for review\""""
    
    elif action == "status":
        return await get_status()
    
    elif action == "reconcile":
        return await trigger_reconciliation(user_id)
    
    elif action == "exceptions":
        return await list_exceptions()
    
    elif action == "drafts":
        return await list_drafts()
    
    elif action == "approve" and args:
        return await approve_draft(args, user_id)
    
    elif action == "ask" and args:
        return await ask_vita(args, user_id)
    
    elif action == "forecast":
        return await get_forecast()
    
    elif action == "budget":
        return await get_budget_status()
    
    elif action == "queue":
        return await get_priority_queue()

    elif action == "setup":
        if args and args.startswith("erp"):
            erp_type = args.split(None, 1)[1].strip().lower() if len(args.split()) > 1 else ""
            return await handle_setup_erp(erp_type, user_id)
        return await handle_setup(user_id)

    elif action == "config":
        if args and args.startswith("channel"):
            channel_arg = args.split(None, 1)[1].strip() if len(args.split()) > 1 else ""
            return await handle_config_channel(channel_arg, user_id)
        return "Usage: `/clearledgr config channel #channel-name`"

    elif action == "invite" and args:
        parts = args.split()
        email = parts[0]
        role = parts[1] if len(parts) > 1 else "member"
        return await handle_invite(email, role, user_id)

    else:
        # Try natural language processing
        full_text = f"{action} {args}".strip() if args else action
        return await process_natural_language(full_text, user_id, channel_id)


async def get_status() -> str:
    """Get dashboard status from backend."""
    result = await api("GET", f"/engine/dashboard?organization_id={DEFAULT_ORG_ID}")
    
    if not result:
        return "Could not fetch status from Clearledgr backend."
    
    stats = result.get("stats", {})
    
    return f"""*Clearledgr Status*
Finance Emails: {stats.get('email_count', 0)}
Matched Transactions: {stats.get('matched_transactions', 0)}
Open Exceptions: {stats.get('open_exceptions', 0)}
Pending Drafts: {stats.get('pending_drafts', 0)}
Match Rate: {stats.get('match_rate', 0):.1f}%"""


async def trigger_reconciliation(user_id: str) -> str:
    """Trigger reconciliation from Slack."""
    # Note: In production, this would fetch data from connected sheets
    # For now, we just trigger the backend
    
    result = await api("POST", "/engine/reconcile", {
        "organization_id": DEFAULT_ORG_ID,
        "gateway_transactions": [],  # Would come from connected data source
        "bank_transactions": [],
    })
    
    if not result:
        return "Failed to trigger reconciliation. Check backend connection."
    
    res = result.get("result", {})
    return f"""*Reconciliation Complete*
Matches: {res.get('matches', 0)}
Exceptions: {res.get('exceptions', 0)}
Match Rate: {res.get('match_rate', 0):.1f}%"""


async def list_exceptions() -> str:
    """List open exceptions."""
    result = await api("GET", f"/engine/exceptions?organization_id={DEFAULT_ORG_ID}&status=open&limit=10")
    
    if not result or not result.get("exceptions"):
        return "No open exceptions."
    
    exceptions = result["exceptions"]
    lines = ["*Open Exceptions:*"]
    
    for exc in exceptions[:10]:
        priority = exc.get("priority", "").upper()
        amount = exc.get("amount", 0)
        vendor = exc.get("vendor", "Unknown")
        lines.append(f"[{priority}] {vendor}: EUR {amount:,.2f}")
    
    if len(exceptions) > 10:
        lines.append(f"...and {len(exceptions) - 10} more")
    
    return "\n".join(lines)


async def list_drafts() -> str:
    """List pending draft entries."""
    result = await api("GET", f"/engine/drafts?organization_id={DEFAULT_ORG_ID}&status=pending&limit=10")
    
    if not result or not result.get("drafts"):
        return "No pending draft entries."
    
    drafts = result["drafts"]
    lines = ["*Pending Draft Entries:*"]
    
    for draft in drafts[:10]:
        amount = draft.get("amount", 0)
        desc = draft.get("description", "")[:30]
        conf = draft.get("confidence", 0) * 100
        lines.append(f"- {desc}: EUR {amount:,.2f} ({conf:.0f}% confidence)")
    
    return "\n".join(lines)


async def approve_draft(draft_id: str, user_id: str) -> str:
    """Approve a draft entry."""
    result = await api("POST", "/engine/drafts/approve", {
        "draft_id": draft_id,
        "organization_id": DEFAULT_ORG_ID,
        "user_id": user_id,
    })
    
    if result and result.get("status") == "success":
        return f"Draft {draft_id} approved."
    return f"Failed to approve draft {draft_id}."


async def ask_vita(question: str, user_id: str) -> str:
    """Ask Vita AI a question."""
    result = await api("POST", "/chat/message", {
        "text": question,
        "user_id": user_id,
        "channel": "slack",
        "metadata": {"organization_id": DEFAULT_ORG_ID},
    })
    
    if result and result.get("text"):
        return f"*Vita:* {result['text']}"
    return "Vita could not process that request."


async def get_forecast() -> str:
    """Get cash flow forecast."""
    from clearledgr.services.cashflow_prediction import get_cashflow_predictor
    
    try:
        predictor = get_cashflow_predictor(DEFAULT_ORG_ID)
        forecast = predictor.forecast(days=30)
        
        lines = [
            f"*AP Forecast (Next 30 Days)*",
            f"Total Expected: *${forecast.total_predicted:,.2f}*",
            f"Confidence: {forecast.confidence*100:.0f}%",
            "",
            "*By Week:*"
        ]
        
        for week, amount in list(forecast.breakdown_by_week.items())[:4]:
            lines.append(f"• {week}: ${amount:,.2f}")
        
        if forecast.breakdown_by_vendor:
            lines.append("")
            lines.append("*Top Vendors:*")
            sorted_vendors = sorted(forecast.breakdown_by_vendor.items(), key=lambda x: x[1], reverse=True)[:5]
            for vendor, amount in sorted_vendors:
                lines.append(f"• {vendor}: ${amount:,.2f}")
        
        return "\n".join(lines)
        
    except Exception as e:
        return f"Could not generate forecast: {str(e)}"


async def get_budget_status() -> str:
    """Get budget status."""
    from clearledgr.services.budget_awareness import get_budget_awareness
    
    try:
        budget_service = get_budget_awareness(DEFAULT_ORG_ID)
        report = budget_service.get_report()
        
        lines = [
            f"*Budget Status ({report.period.capitalize()})*",
            f"Overall: {report.overall_status.value.capitalize()}",
            f"Total Budgeted: ${report.total_budgeted:,.0f}",
            f"Total Spent: ${report.total_spent:,.0f} ({report.total_spent/report.total_budgeted*100:.0f}%)" if report.total_budgeted > 0 else "",
            ""
        ]
        
        for check in report.budgets:
            bar = "█" * int(check.percent_used / 10) + "░" * (10 - int(check.percent_used / 10))
            status_label = check.status.value.upper()
            lines.append(f"{status_label} *{check.budget.name}*: ${check.spent:,.0f} / ${check.budget.amount:,.0f} `[{bar}]` {check.percent_used:.0f}%")
        
        if report.alerts:
            lines.append("")
            lines.append("*Alerts:*")
            for alert in report.alerts[:3]:
                lines.append(f"• {alert}")
        
        return "\n".join(lines)
        
    except Exception as e:
        return f"Could not get budget status: {str(e)}"


async def get_priority_queue() -> str:
    """Get invoice priority queue."""
    from clearledgr.services.priority_detection import get_priority_detection
    from clearledgr.core.database import get_db
    
    try:
        db = get_db()
        
        # Get pending invoices
        pipeline = db.get_invoice_pipeline(DEFAULT_ORG_ID)
        pending = pipeline.get("pending_approval", []) + pipeline.get("new", [])
        
        if not pending:
            return "No pending invoices in queue."
        
        priority_service = get_priority_detection(DEFAULT_ORG_ID)
        prioritized = priority_service.prioritize_queue(pending)
        
        lines = ["*Invoice Priority Queue*", ""]
        
        # Group by priority
        by_priority = {}
        for inv in prioritized:
            p = inv.get("priority", "medium")
            if p not in by_priority:
                by_priority[p] = []
            by_priority[p].append(inv)
        
        for priority in ["critical", "high", "medium", "low"]:
            if priority not in by_priority:
                continue
            
            items = by_priority[priority]
            lines.append(f"*{priority.upper()}* ({len(items)})")
            
            for inv in items[:3]:
                vendor = inv.get("vendor", "Unknown")
                amount = inv.get("amount", 0)
                days = inv.get("days_until_due")
                due_text = f" (due {days}d)" if days is not None else ""
                lines.append(f"  • {vendor}: ${amount:,.2f}{due_text}")
            
            if len(items) > 3:
                lines.append(f"  _...and {len(items) - 3} more_")
            lines.append("")
        
        return "\n".join(lines)
        
    except Exception as e:
        return f"Could not get priority queue: {str(e)}"


# ==================== SETUP & CONFIG HANDLERS ====================


async def handle_setup(user_id: str) -> str:
    """Show onboarding checklist with integration statuses."""
    from clearledgr.core.database import get_db

    db = get_db()
    org_id = DEFAULT_ORG_ID

    # Check Gmail (extension-based — always "ready" if AP items exist)
    ap_count = 0
    try:
        pipeline = db.get_invoice_pipeline(org_id)
        ap_count = sum(len(v) for v in pipeline.values() if isinstance(v, list))
    except Exception:
        pass
    gmail_status = "Connected" if ap_count > 0 else "Awaiting first invoice"
    gmail_icon = ":white_check_mark:" if ap_count > 0 else ":hourglass_flowing_sand:"

    # Check ERP
    erp_connections = db.get_erp_connections(org_id)
    active_erps = [c for c in erp_connections if c.get("is_active")]
    if active_erps:
        erp_names = ", ".join(c.get("erp_type", "?").title() for c in active_erps)
        erp_status = f"Connected ({erp_names})"
        erp_icon = ":white_check_mark:"
    else:
        erp_status = "Not connected"
        erp_icon = ":x:"

    # Check Slack (if we're here, Slack is connected)
    slack_icon = ":white_check_mark:"
    slack_status = "Connected"

    # Check approval channel
    org = db.get_organization(org_id) or {}
    settings = org.get("settings", {})
    if isinstance(settings, str):
        import json as _json
        settings = _json.loads(settings) if settings else {}
    channels = settings.get("slack_channels", {})
    approval_ch = channels.get("invoices", "#finance-approvals")
    channel_icon = ":white_check_mark:" if channels else ":warning:"

    return f"""*Clearledgr Setup*

{gmail_icon} *Gmail:* {gmail_status}
{slack_icon} *Slack:* {slack_status}
{erp_icon} *ERP:* {erp_status}
{channel_icon} *Approval Channel:* {approval_ch}

*Quick Setup:*
{"• `/clearledgr setup erp quickbooks` — Connect QuickBooks" if not active_erps else ""}
{"• `/clearledgr setup erp xero` — Connect Xero" if not active_erps else ""}
{"• `/clearledgr setup erp netsuite` — Connect NetSuite" if not active_erps else ""}
• `/clearledgr config channel #channel` — Set approval channel
• `/clearledgr invite user@email.com admin` — Invite a team member"""


async def handle_config_channel(channel_arg: str, user_id: str) -> str:
    """Set the approval notification channel."""
    from clearledgr.core.database import get_db

    if not channel_arg:
        return "Usage: `/clearledgr config channel #channel-name`"

    # Normalize: strip <# > wrapper that Slack adds for channel mentions
    channel_name = channel_arg.strip()
    if channel_name.startswith("<#") and "|" in channel_name:
        # Format: <#C12345|channel-name>
        channel_name = "#" + channel_name.split("|")[1].rstrip(">")
    elif not channel_name.startswith("#"):
        channel_name = "#" + channel_name

    db = get_db()
    org_id = DEFAULT_ORG_ID

    org = db.get_organization(org_id) or db.ensure_organization(org_id, organization_name=org_id)
    settings = org.get("settings", {})
    if isinstance(settings, str):
        import json as _json
        settings = _json.loads(settings) if settings else {}

    slack_channels = settings.get("slack_channels", {
        "invoices": "#finance-approvals",
        "expenses": "#expense-approvals",
        "exceptions": "#finance-exceptions",
        "notifications": "#finance-notifications",
    })
    slack_channels["invoices"] = channel_name
    settings["slack_channels"] = slack_channels

    db.update_organization(org_id, settings=settings)

    return f"Approval channel set to *{channel_name}*. Invoice notifications will be sent there."


async def handle_setup_erp(erp_type: str, user_id: str) -> str:
    """Start ERP connection flow from Slack."""
    if not erp_type:
        return """*Connect your ERP:*
• `/clearledgr setup erp quickbooks` — QuickBooks Online (OAuth)
• `/clearledgr setup erp xero` — Xero (OAuth)
• `/clearledgr setup erp netsuite` — NetSuite (Token-Based Auth)"""

    erp_type = erp_type.lower().strip()
    if erp_type not in ("quickbooks", "xero", "netsuite"):
        return f"Unknown ERP type: `{erp_type}`. Supported: `quickbooks`, `xero`, `netsuite`."

    if erp_type == "netsuite":
        return """*NetSuite Setup*

NetSuite uses Token-Based Authentication. You'll need:
1. Account ID (e.g., `1234567` or `1234567_SB1`)
2. Consumer Key
3. Consumer Secret
4. Token ID
5. Token Secret

*How to get these:*
In NetSuite: Setup > Company > Enable Features > SuiteCloud > Token-Based Authentication.
Then create an Integration record and generate a Token.

Once you have the credentials, an admin can enter them at:
`/api/workspace/integrations/erp/connect/start` with `erp_type: netsuite`"""

    # OAuth ERPs (QuickBooks, Xero)
    try:
        from clearledgr.api.erp_connections import (
            _oauth_states,
            QUICKBOOKS_CLIENT_ID, QUICKBOOKS_REDIRECT_URI, QUICKBOOKS_AUTH_URL,
            XERO_CLIENT_ID, XERO_REDIRECT_URI, XERO_AUTH_URL,
        )
        from urllib.parse import urlencode as _urlencode
        import secrets as _secrets

        state = _secrets.token_urlsafe(32)
        _oauth_states[state] = {
            "organization_id": DEFAULT_ORG_ID,
            "return_url": "slack",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        if erp_type == "quickbooks":
            if not QUICKBOOKS_CLIENT_ID:
                return "QuickBooks is not configured on this server. Set `QUICKBOOKS_CLIENT_ID` and related env vars."
            params = {
                "client_id": QUICKBOOKS_CLIENT_ID,
                "redirect_uri": QUICKBOOKS_REDIRECT_URI,
                "response_type": "code",
                "scope": "com.intuit.quickbooks.accounting",
                "state": state,
            }
            auth_url = f"{QUICKBOOKS_AUTH_URL}?{_urlencode(params)}"
            return f"*Connect QuickBooks*\n\nClick the link below to authorize Clearledgr:\n{auth_url}"

        if erp_type == "xero":
            if not XERO_CLIENT_ID:
                return "Xero is not configured on this server. Set `XERO_CLIENT_ID` and related env vars."
            params = {
                "client_id": XERO_CLIENT_ID,
                "redirect_uri": XERO_REDIRECT_URI,
                "response_type": "code",
                "scope": "openid profile email accounting.transactions accounting.contacts offline_access",
                "state": state,
            }
            auth_url = f"{XERO_AUTH_URL}?{_urlencode(params)}"
            return f"*Connect Xero*\n\nClick the link below to authorize Clearledgr:\n{auth_url}"

    except ImportError:
        return f"ERP connection module not available. Ensure `clearledgr.api.erp_connections` is installed."


async def handle_invite(email: str, role: str, user_id: str) -> str:
    """Invite a team member."""
    from clearledgr.core.database import get_db
    from datetime import datetime, timezone, timedelta

    if role not in ("admin", "member", "viewer"):
        return f"Invalid role: `{role}`. Use `admin`, `member`, or `viewer`."

    db = get_db()
    org_id = DEFAULT_ORG_ID

    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    try:
        invite = db.create_team_invite(
            organization_id=org_id,
            email=email,
            role=role,
            created_by=user_id,
            expires_at=expires,
        )
        token = invite.get("token", "")
        return f"Invitation sent to *{email}* as *{role}*.\nInvite link expires in 7 days."
    except Exception as e:
        return f"Could not create invite: {str(e)}"


async def process_natural_language(text: str, user_id: str, channel_id: str) -> str:
    """Process a natural language command."""
    from clearledgr.services.natural_language_commands import get_nlp_processor
    
    try:
        processor = get_nlp_processor(DEFAULT_ORG_ID)
        parsed = processor.parse(text)
        
        if parsed.confidence < 0.5:
            # Low confidence - ask for clarification or fall back to help
            if parsed.clarification_needed:
                return f"Clarification needed: {parsed.clarification_needed}"
            return f"I didn't quite understand that. Try `/clearledgr help` for available commands, or try:\n• \"Show pending invoices\"\n• \"Approve all AWS under $500\"\n• \"How much did we pay Stripe last month?\""
        
        # Execute the command
        result = await processor.execute(parsed)
        
        if not result.success:
            return f"Error: {result.message}"
        
        # Format response based on intent
        if parsed.intent.value == "show":
            invoices = result.data.get("invoices", [])
            total = result.data.get("total_amount", 0)
            count = result.data.get("total_count", len(invoices))
            
            if not invoices:
                return "No invoices found matching your criteria."
            
            lines = [f"*Found {count} invoices* (${total:,.2f} total)", ""]
            for inv in invoices[:10]:
                vendor = inv.get("vendor", "Unknown")
                amount = inv.get("amount", 0)
                status = inv.get("status", "unknown")
                lines.append(f"• {vendor}: ${amount:,.2f} ({status})")
            
            if count > 10:
                lines.append(f"_...and {count - 10} more_")
            
            return "\n".join(lines)
        
        elif parsed.intent.value == "approve":
            if result.requires_confirmation:
                count = result.data.get("total_count", 0)
                total = result.data.get("total_amount", 0)
                return f"*Confirm:* {result.confirmation_prompt}\n\nThis will approve {count} invoices totaling ${total:,.2f}.\n\n_Reply \"yes\" to confirm or \"no\" to cancel._"
            return f"{result.message}"
        
        elif parsed.intent.value == "summarize":
            return f"{result.message}"
        
        elif parsed.intent.value == "flag":
            if result.requires_confirmation:
                return f"{result.confirmation_prompt}"
            return f"{result.message}"
        
        elif parsed.intent.value == "help":
            return result.message
        
        else:
            return f"{result.message}"
        
    except Exception as e:
        return f"Error processing command: {str(e)}"


# ==================== EVENT HANDLERS ====================

async def handle_mention(event: Dict):
    """Handle @clearledgr mentions - now with natural language understanding."""
    channel = event.get("channel", "")
    text = event.get("text", "")
    user = event.get("user", "")
    
    # Remove the @mention and get the actual message
    import re
    message = re.sub(r"<@\w+>", "", text).strip()
    
    if not message:
        await send_message(channel, "Hi! I'm Clearledgr. Ask me anything about your finances.\n\nTry:\n• \"Show pending invoices\"\n• \"Approve all AWS under $500\"\n• \"What's my budget status?\"\n• \"Forecast next 30 days\"")
        return
    
    # First try NLP processing for action commands
    action_keywords = ["approve", "reject", "show", "list", "find", "flag", "how much", "what", "forecast", "budget", "queue"]
    is_action = any(kw in message.lower() for kw in action_keywords)
    
    if is_action:
        response = await process_natural_language(message, user, channel)
    else:
        # Fall back to Vita AI for general questions
        response = await ask_vita(message, user)
    
    await send_message(channel, response)


async def handle_dm(event: Dict):
    """Handle direct messages."""
    channel = event.get("channel", "")
    text = event.get("text", "")
    user = event.get("user", "")
    
    # Send to Vita AI
    response = await ask_vita(text, user)
    await send_message(channel, response)


# ==================== INTERACTION HANDLERS ====================

async def handle_invoice_approve(
    gmail_id: str,
    user_id: str,
    channel: str,
    message_ts: str,
    organization_id: str = DEFAULT_ORG_ID,
):
    """Handle invoice approval button click from Slack."""
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    # Immediately show processing state — replaces buttons with status text
    await update_message(
        channel,
        message_ts,
        f"Approved by <@{user_id}> — posting to ERP...",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": f":hourglass_flowing_sand: *Approved by <@{user_id}>* — posting to ERP..."}},
        ],
        organization_id=organization_id,
    )

    try:
        user_email = f"slack:{user_id}"
        workflow = get_invoice_workflow(organization_id, slack_channel=channel)
        result = await workflow.approve_invoice(
            gmail_id=gmail_id,
            approved_by=user_email,
            source_channel="slack",
            source_channel_id=channel,
            source_message_ref=message_ts,
        )

        if result.get("status") != "approved":
            await update_message(
                channel,
                message_ts,
                f"Failed to approve invoice: {result.get('erp_result', {}).get('reason', 'Unknown error')}",
                organization_id=organization_id,
            )

    except Exception as e:
        await update_message(
            channel,
            message_ts,
            f"Error approving invoice: {str(e)}",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": f":x: *Approval failed:* {str(e)}"}},
            ],
            organization_id=organization_id,
        )


async def handle_invoice_reject(
    gmail_id: str,
    user_id: str,
    channel: str,
    message_ts: str,
    organization_id: str = DEFAULT_ORG_ID,
):
    """Handle invoice rejection button click from Slack."""
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    # Immediately show processing state
    await update_message(
        channel,
        message_ts,
        f"Rejected by <@{user_id}>",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": f":no_entry_sign: *Rejected by <@{user_id}>*"}},
        ],
        organization_id=organization_id,
    )

    try:
        user_email = f"slack:{user_id}"
        workflow = get_invoice_workflow(organization_id, slack_channel=channel)
        result = await workflow.reject_invoice(
            gmail_id=gmail_id,
            rejected_by=user_email,
            reason="Rejected via Slack",
            source_channel="slack",
            source_channel_id=channel,
            source_message_ref=message_ts,
        )

        if result.get("status") != "rejected":
            await update_message(
                channel,
                message_ts,
                f"Failed to reject invoice: {result.get('reason', 'Unknown error')}",
                organization_id=organization_id,
            )

    except Exception as e:
        await update_message(
            channel,
            message_ts,
            f"Error rejecting invoice: {str(e)}",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": f":x: *Rejection failed:* {str(e)}"}},
            ],
            organization_id=organization_id,
        )


async def handle_budget_override(
    gmail_id: str,
    user_id: str,
    channel: str,
    message_ts: str,
    organization_id: str = DEFAULT_ORG_ID,
):
    """Handle budget override approval from Slack."""
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    try:
        user_email = f"slack:{user_id}"
        workflow = get_invoice_workflow(organization_id, slack_channel=channel)
        result = await workflow.approve_invoice(
            gmail_id=gmail_id,
            approved_by=user_email,
            source_channel="slack",
            source_channel_id=channel,
            source_message_ref=message_ts,
            allow_budget_override=True,
            override_justification="Approved over budget in Slack",
        )
        if result.get("status") != "approved":
            await send_message(channel, f"Budget override failed: {result.get('reason', 'Unknown error')}", organization_id=organization_id)
    except Exception as e:
        await send_message(channel, f"Error approving budget override: {str(e)}", organization_id=organization_id)


async def handle_budget_adjustment(
    gmail_id: str,
    user_id: str,
    channel: str,
    message_ts: str,
    organization_id: str = DEFAULT_ORG_ID,
):
    """Handle budget adjustment request from Slack."""
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    try:
        user_email = f"slack:{user_id}"
        workflow = get_invoice_workflow(organization_id, slack_channel=channel)
        result = await workflow.request_budget_adjustment(
            gmail_id=gmail_id,
            requested_by=user_email,
            reason="Budget adjustment requested in Slack",
            slack_channel=channel,
            slack_ts=message_ts,
        )
        if result.get("status") != "needs_info":
            await send_message(channel, f"Budget adjustment request failed: {result.get('reason', 'Unknown error')}", organization_id=organization_id)
    except Exception as e:
        await send_message(channel, f"Error requesting budget adjustment: {str(e)}", organization_id=organization_id)


async def handle_budget_reject(
    gmail_id: str,
    user_id: str,
    channel: str,
    message_ts: str,
    organization_id: str = DEFAULT_ORG_ID,
):
    """Handle over-budget rejection from Slack."""
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    try:
        user_email = f"slack:{user_id}"
        workflow = get_invoice_workflow(organization_id, slack_channel=channel)
        result = await workflow.reject_invoice(
            gmail_id=gmail_id,
            rejected_by=user_email,
            reason="Rejected over budget in Slack",
            source_channel="slack",
            source_channel_id=channel,
            source_message_ref=message_ts,
        )
        if result.get("status") != "rejected":
            await send_message(channel, f"Budget rejection failed: {result.get('reason', 'Unknown error')}", organization_id=organization_id)
    except Exception as e:
        await send_message(channel, f"Error rejecting invoice: {str(e)}", organization_id=organization_id)


async def handle_invoice_flag(
    gmail_id: str,
    user_id: str,
    channel: str,
    message_ts: str,
    organization_id: str = DEFAULT_ORG_ID,
):
    """Handle invoice flag-for-review from Slack."""
    from clearledgr.core.database import get_db

    try:
        db = get_db()
        db.update_invoice_status(
            gmail_id=gmail_id,
            status="pending_approval",
            rejection_reason=f"Flagged for review in Slack by {user_id}",
        )
        await update_message(
            channel,
            message_ts,
            f"Invoice `{gmail_id}` flagged for review by <@{user_id}>",
            organization_id=organization_id,
        )
    except Exception as e:
        await send_message(channel, f"Error flagging invoice: {str(e)}", organization_id=organization_id)


async def handle_review_exception(exc_id: str, user_id: str, channel: str, message_ts: str):
    """Handle review exception button click."""
    # Open a thread with exception details
    await send_message(channel, f"<@{user_id}> is reviewing exception `{exc_id}`. Use thread to discuss.", thread_ts=message_ts)


async def handle_dismiss_exception(exc_id: str, user_id: str, channel: str, message_ts: str):
    """Handle dismiss exception button click."""
    await update_message(channel, message_ts, f"Exception `{exc_id}` dismissed by <@{user_id}>")


# ==================== EXPENSE HANDLERS ====================

async def check_for_expense(event: Dict):
    """Check if a Slack message is an expense request."""
    from clearledgr.services.expense_workflow import get_expense_workflow
    
    text = event.get("text", "")
    user_id = event.get("user", "")
    channel_id = event.get("channel", "")
    message_ts = event.get("ts", "")
    files = event.get("files", [])
    
    # Skip if no user or bot message
    if not user_id or event.get("bot_id"):
        return
    
    try:
        workflow = get_expense_workflow(DEFAULT_ORG_ID)
        
        # Check if this looks like an expense request
        if workflow.is_expense_request(text):
            result = await workflow.process_expense_message(
                message_text=text,
                user_id=user_id,
                channel_id=channel_id,
                message_ts=message_ts,
                files=files,
            )
            
            if result.get("status") == "pending_approval":
                # Reply in thread confirming we detected it
                await send_message(
                    channel_id,
                    "Got it! I've sent your expense request for approval.",
                    thread_ts=message_ts
                )
    except Exception as e:
        print(f"[Clearledgr] Error checking expense: {e}")


async def handle_expense_approve(expense_id: str, user_id: str, channel: str, message_ts: str):
    """Handle expense approval button click."""
    from clearledgr.services.expense_workflow import get_expense_workflow
    
    try:
        workflow = get_expense_workflow(DEFAULT_ORG_ID)
        result = await workflow.approve_expense(expense_id, approved_by=f"slack:{user_id}")
        
        if result.get("status") == "success":
            await update_message(
                channel, message_ts,
                f"*Expense Approved & Posted*\n\nApproved by <@{user_id}>\nBill ID: `{result.get('bill_id', 'N/A')}`"
            )
        else:
            await send_message(channel, f"Failed to approve expense: {result.get('reason', 'Unknown error')}")
    except Exception as e:
        await send_message(channel, f"Error approving expense: {str(e)}")


async def handle_expense_reject(
    expense_id: str, user_id: str, channel: str, message_ts: str,
    trigger_id: str = "",
):
    """Handle expense rejection button click — opens a modal for the reason."""
    if not trigger_id:
        # Fallback if trigger_id unavailable (shouldn't happen in practice)
        await update_message(
            channel, message_ts,
            f"*Expense Rejected*\n\nRejected by <@{user_id}>",
        )
        return

    import urllib.parse as _urlparse
    private_metadata = _urlparse.urlencode({"channel": channel, "ts": message_ts})
    await slack_api("views.open", {
        "trigger_id": trigger_id,
        "view": {
            "type": "modal",
            "callback_id": f"expense_reject_modal_{expense_id}",
            "private_metadata": private_metadata,
            "title": {"type": "plain_text", "text": "Reject Expense"},
            "submit": {"type": "plain_text", "text": "Reject"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "reason_block",
                    "label": {"type": "plain_text", "text": "Rejection Reason"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "rejection_reason",
                        "multiline": True,
                        "placeholder": {"type": "plain_text", "text": "Why is this expense being rejected?"},
                    },
                },
            ],
        },
    })


async def handle_need_receipt(
    expense_id: str, user_id: str, channel: str, message_ts: str,
    message: Optional[Dict] = None,
):
    """Handle 'need receipt' button click — DM the original poster."""
    import re

    # Update the channel message
    await update_message(
        channel, message_ts,
        f"*Receipt Requested*\n\n<@{user_id}> requested a receipt for this expense.",
    )

    # Extract original poster from message text/blocks and DM them
    original_poster_id = None
    if message:
        msg_text = message.get("text", "")
        # Slack user mentions look like <@U12345ABC>
        match = re.search(r"<@(U[A-Z0-9]+)>", msg_text)
        if match:
            original_poster_id = match.group(1)

    if original_poster_id and original_poster_id != user_id:
        dm_result = await slack_api("conversations.open", {"users": original_poster_id})
        dm_channel = (dm_result or {}).get("channel", {}).get("id")
        if dm_channel:
            await send_message(
                dm_channel,
                f"<@{user_id}> has requested a receipt for expense `{expense_id}`. "
                f"Please upload or forward the receipt.",
            )


async def handle_approve(item_id: str, user_id: str, channel: str, message_ts: str):
    """Handle draft approval button click."""
    result = await api("POST", "/engine/drafts/approve", {
        "draft_id": item_id,
        "organization_id": DEFAULT_ORG_ID,
        "user_id": user_id,
    })
    
    if result and result.get("status") == "success":
        await update_message(channel, message_ts, f"Draft {item_id} approved by <@{user_id}>")
    else:
        await send_message(channel, f"Failed to approve draft {item_id}")


async def handle_reject(item_id: str, user_id: str, channel: str, message_ts: str):
    """Handle draft rejection button click."""
    result = await api("POST", "/engine/drafts/reject", {
        "draft_id": item_id,
        "organization_id": DEFAULT_ORG_ID,
        "user_id": user_id,
        "reason": "Rejected via Slack",
    })
    
    if result and result.get("status") == "success":
        await update_message(channel, message_ts, f"Draft {item_id} rejected by <@{user_id}>")
    else:
        await send_message(channel, f"Failed to reject draft {item_id}")


async def handle_resolve(exc_id: str, user_id: str, channel: str, message_ts: str):
    """Handle resolve exception button click."""
    result = await api("POST", "/engine/exceptions/resolve", {
        "exception_id": exc_id,
        "organization_id": DEFAULT_ORG_ID,
        "user_id": user_id,
        "resolution_notes": "Resolved via Slack",
    })
    
    if result and result.get("status") == "success":
        await update_message(channel, message_ts, f"[RESOLVED] Exception {exc_id} resolved by <@{user_id}>")
    else:
        await send_message(channel, f"Failed to resolve exception {exc_id}")


# ==================== CLARIFYING QUESTIONS (2026-01-23) ====================

async def handle_clarifying_response(question_id: str, response_value: str, user_id: str, channel: str, message_ts: str):
    """
    Handle response to a clarifying question from the conversational agent.

    Routes through the orchestrator so the agent can learn from answers
    and take autonomous action (approve, reject, flag, request more info).
    """
    from clearledgr.services.conversational_agent import get_conversational_agent
    from clearledgr.services.correction_learning import get_correction_learning
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    try:
        user_email = f"slack:{user_id}"
        agent = get_conversational_agent(DEFAULT_ORG_ID)
        workflow = get_invoice_workflow(DEFAULT_ORG_ID, slack_channel=channel)
        correction_learning = get_correction_learning(DEFAULT_ORG_ID)

        # Process the response
        result = agent.handle_response(
            question_id=question_id,
            response_value=response_value,
            responder=user_email,
        )

        action = result.get("action", "unknown")
        invoice_id = result.get("invoice_id", "")

        # Update the message to show response received
        response_text = f"<@{user_id}> responded: *{response_value}*"

        if action == "proceed":
            response_text += "\nProceeding with invoice processing..."
            if invoice_id:
                approve_result = await workflow.approve_invoice(
                    gmail_id=invoice_id,
                    approved_by=user_email,
                    source_channel="slack",
                    source_channel_id=channel,
                    source_message_ref=message_ts,
                )
                status = approve_result.get("status", "unknown")
                if status in ("approved", "posted"):
                    await send_message(channel, f"Invoice `{invoice_id}` approved and posted.", thread_ts=message_ts)
                else:
                    await send_message(channel, f"Invoice `{invoice_id}` processed — status: {status}", thread_ts=message_ts)

        elif action == "reject":
            reason = result.get("reason", "Rejected after clarification")
            response_text += f"\n{reason}"
            if invoice_id:
                await workflow.reject_invoice(
                    gmail_id=invoice_id,
                    rejected_by=user_email,
                    reason=reason,
                    source_channel="slack",
                    source_channel_id=channel,
                    source_message_ref=message_ts,
                )

        elif action == "flag_for_review":
            response_text += f"\nFlagged for manual review: {result.get('reason', '')}"

        elif action == "hold":
            response_text += "\nInvoice on hold pending further review"

        elif action == "skip":
            response_text += "\nInvoice skipped"

        elif action == "request_info":
            info_needed = result.get("info_needed", "additional information")
            response_text += f"\nPlease provide {info_needed} in a reply"

        elif action == "request_gl":
            response_text += "\nPlease specify the GL code in a reply"
            # Record that we're waiting for GL so the agent can learn
            if invoice_id:
                correction_learning.record_correction(
                    correction_type="gl_code",
                    original_value="unknown",
                    corrected_value="pending_user_input",
                    context={"invoice_id": invoice_id, "source": "clarifying_question"},
                    user_id=user_email,
                    invoice_id=invoice_id,
                )

        await update_message(channel, message_ts, response_text)

        logger.info(f"Clarifying response handled: {question_id} -> {action}")

    except Exception as e:
        logger.error(f"Error handling clarifying response: {e}")
        await send_message(channel, f"Error processing response: {str(e)}")


# ==================== NOTIFICATIONS ====================

async def send_exception_notification(channel: str, exception: Dict):
    """Send exception notification with action buttons."""
    exc_id = exception.get("id", "")
    priority = exception.get("priority", "").upper()
    amount = exception.get("amount", 0)
    vendor = exception.get("vendor", "Unknown")
    exc_type = exception.get("type", "")
    
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*[{priority}] Exception Requires Review*\n\nVendor: {vendor}\nAmount: EUR {amount:,.2f}\nType: {exc_type}"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Resolve"},
                    "style": "primary",
                    "action_id": f"resolve_{exc_id}"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View in Sheets"},
                    "action_id": f"view_{exc_id}"
                }
            ]
        }
    ]
    
    await send_message(channel, f"Exception: {vendor} - EUR {amount:,.2f}", blocks)


async def send_draft_notification(channel: str, draft: Dict):
    """Send draft entry notification with approval buttons."""
    draft_id = draft.get("id", "")
    amount = draft.get("amount", 0)
    desc = draft.get("description", "")
    confidence = draft.get("confidence", 0) * 100
    
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Draft Entry Ready for Approval*\n\nDescription: {desc}\nAmount: EUR {amount:,.2f}\nConfidence: {confidence:.0f}%"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"approve_{draft_id}"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": f"reject_{draft_id}"
                }
            ]
        }
    ]
    
    await send_message(channel, f"Draft: {desc} - EUR {amount:,.2f}", blocks)


async def send_reconciliation_summary(channel: str, result: Dict):
    """Send reconciliation summary notification."""
    matches = result.get("matches", 0)
    exceptions = result.get("exceptions", 0)
    match_rate = result.get("match_rate", 0)
    
    text = f"""*Reconciliation Complete*

Matches: {matches}
Exceptions: {exceptions}
Match Rate: {match_rate:.1f}%

{"Use `/clearledgr exceptions` to review exceptions." if exceptions > 0 else "No exceptions - great job!"}"""
    
    await send_message(channel, text)


# ==================== WEBHOOK FOR BACKEND NOTIFICATIONS ====================

# ==================== LEGACY FUNCTIONS (for backward compatibility) ====================

async def send_daily_summary(channel: str, summary: Dict):
    """Send daily reconciliation summary. Called by temporal activities."""
    await send_reconciliation_summary(channel, summary)


def build_exception_blocks(exceptions: List[Dict]) -> List[Dict]:
    """Build Slack blocks for exceptions. Legacy compatibility."""
    blocks = []
    for exc in exceptions[:5]:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{exc.get('priority', 'MEDIUM')}* - {exc.get('vendor', 'Unknown')}: EUR {exc.get('amount', 0):,.2f}"
            }
        })
    return blocks


@router.post("/notify")
async def notify_webhook(request: Request):
    """
    Receive notifications from backend and send to Slack.
    Backend calls this when events happen (new exception, reconciliation complete, etc.)
    """
    data = await request.json()
    
    notification_type = data.get("type")
    channel = data.get("channel") or os.getenv("SLACK_DEFAULT_CHANNEL", "")
    
    if not channel:
        return {"error": "No channel specified"}
    
    if notification_type == "exception":
        await send_exception_notification(channel, data.get("exception", {}))
    
    elif notification_type == "draft":
        await send_draft_notification(channel, data.get("draft", {}))
    
    elif notification_type == "reconciliation":
        await send_reconciliation_summary(channel, data.get("result", {}))
    
    elif notification_type == "message":
        await send_message(channel, data.get("text", ""))
    
    return {"ok": True}
