"""
Gmail Autopilot Service for AP v1.

Runs 24/7 Gmail scanning using Gmail API and Pub/Sub watch renewal.
AP-only. No reconciliation or non-AP workflows.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from clearledgr.core.database import get_db
from clearledgr.services.gmail_api import GmailAPIClient, GmailWatchService, token_store, PUBSUB_TOPIC
from clearledgr.api.gmail_webhooks import process_single_email
from clearledgr.services.slack_api import get_slack_client
from clearledgr.services.teams_api import get_teams_client

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


class GmailAutopilot:
    def __init__(self):
        self.enabled = _env_bool("GMAIL_AUTOPILOT_ENABLED", True)
        self.mode = os.getenv("GMAIL_AUTOPILOT_MODE", "both").strip().lower()
        self.poll_interval = int(os.getenv("GMAIL_POLL_INTERVAL_SEC", "300"))
        self.poll_max_results = int(os.getenv("GMAIL_POLL_MAX_RESULTS", "50"))
        self.poll_concurrency = max(1, int(os.getenv("GMAIL_POLL_CONCURRENCY", "5")))
        self.poll_seed_hours = int(os.getenv("GMAIL_POLL_SEED_HOURS", "24"))
        self.watch_refresh_hours = int(os.getenv("GMAIL_WATCH_REFRESH_HOURS", "12"))
        self.approval_sla_minutes = max(1, int(os.getenv("AP_APPROVAL_SLA_MINUTES", "240")))
        self._slack_channel = os.getenv("SLACK_APPROVAL_CHANNEL", "#finance-approvals")
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._db = get_db()
        self._status: Dict[str, Any] = {"state": "idle"}

    def get_status(self) -> Dict[str, Any]:
        return self._status

    def _watch_enabled(self) -> bool:
        return self.mode in {"watch", "both"} and bool(PUBSUB_TOPIC)

    def _poll_enabled(self) -> bool:
        return self.mode in {"poll", "both"}

    async def start(self) -> None:
        if not self.enabled:
            self._status = {"state": "disabled"}
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        self._status = {"state": "running"}
        logger.info("Gmail autopilot started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        self._status = {"state": "stopped"}
        logger.info("Gmail autopilot stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("Gmail autopilot loop error: %s", exc)
                self._status = {"state": "error", "error": str(exc)}
            await asyncio.sleep(self.poll_interval)

    async def _tick(self) -> None:
        tokens = token_store.list_all()
        if not tokens:
            self._status = {"state": "idle", "detail": "no_tokens"}
            return

        self._status = {"state": "running", "users": len(tokens)}
        tasks = [self._process_user(token) for token in tokens]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed_count = 0
        failed_count = 0
        for result in results:
            if isinstance(result, Exception):
                failed_count += 1
                continue
            if isinstance(result, dict):
                processed_count += int(result.get("processed_count") or 0)
                failed_count += int(result.get("failed_count") or 0)

        await self._process_sla_escalations()
        if failed_count > 0:
            self._status = {
                "state": "degraded",
                "users": len(tokens),
                "processed_count": processed_count,
                "failed_count": failed_count,
                "detail": "processing_errors",
                "error": "autopilot_processing_failures",
                "last_run": datetime.now(timezone.utc).isoformat(),
            }
        else:
            self._status = {
                "state": "idle",
                "users": len(tokens),
                "processed_count": processed_count,
                "failed_count": 0,
                "last_run": datetime.now(timezone.utc).isoformat(),
            }

    async def _process_user(self, token) -> Dict[str, int]:
        client = GmailAPIClient(token.user_id)
        if not await client.ensure_authenticated():
            self._db.save_gmail_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_error="auth_failed",
            )
            return {"processed_count": 0, "failed_count": 1}

        if self._watch_enabled():
            await self._ensure_watch(token)

        processed_count = 0
        failed_count = 0
        if self._poll_enabled():
            result = await self._poll_messages(token, client)
            processed_count += int(result.get("processed_count") or 0)
            failed_count += int(result.get("failed_count") or 0)
        return {"processed_count": processed_count, "failed_count": failed_count}

    async def _ensure_watch(self, token) -> None:
        state = self._db.get_gmail_autopilot_state(token.user_id) or {}
        expiration = _parse_iso(state.get("watch_expiration"))
        now = datetime.now(timezone.utc)
        refresh_cutoff = now + timedelta(hours=self.watch_refresh_hours)

        if expiration and expiration > refresh_cutoff:
            return

        try:
            watch_service = GmailWatchService(token.user_id)
            result = await watch_service.setup_watch()
            exp_iso = _parse_watch_expiration(result.get("expiration"))
            self._db.save_gmail_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_history_id=str(result.get("historyId")) if result.get("historyId") else None,
                watch_expiration=exp_iso,
                last_watch_at=now.isoformat(),
                last_error=None,
            )
        except Exception as exc:
            logger.warning("Gmail watch refresh failed for %s: %s", token.email, exc)
            self._db.save_gmail_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_error=f"watch_failed: {exc}",
            )

    async def _poll_messages(self, token, client: GmailAPIClient) -> Dict[str, int]:
        now = datetime.now(timezone.utc)
        state = self._db.get_gmail_autopilot_state(token.user_id) or {}
        last_scan_at = _parse_iso(state.get("last_scan_at"))
        if not last_scan_at:
            last_scan_at = now - timedelta(hours=self.poll_seed_hours)

        default_query = [
            "in:inbox",
            "(has:attachment OR filename:pdf OR filename:png OR filename:jpg OR filename:jpeg OR filename:docx)",
            "(subject:(invoice OR bill OR \"invoice is available\" OR \"your invoice\" OR \"invoice available\" OR \"payment request\" OR \"amount due\" OR \"total due\" OR \"due date\" OR \"payable\") OR \"invoice number\" OR \"amount due\" OR \"total due\")",
            "-subject:(receipt OR confirmation OR paid OR \"payment received\" OR refund OR chargeback OR dispute OR declined OR \"payment failed\" OR \"card declined\" OR \"security alert\" OR \"password\" OR \"verify\" OR newsletter OR promotion OR offer OR webinar OR event)",
            "-category:promotions",
            "-category:social",
            "-category:updates",
        ]
        query = os.getenv("GMAIL_POLL_QUERY", " ".join(default_query))
        if "after:" not in query:
            query = f"{query} after:{int(last_scan_at.timestamp())}".strip()

        try:
            response = await client.list_messages(query=query, max_results=self.poll_max_results)
            messages = response.get("messages", []) or []
            if not messages:
                self._db.save_gmail_autopilot_state(
                    user_id=token.user_id,
                    email=token.email,
                    last_scan_at=now.isoformat(),
                    last_error=None,
                )
                return {"processed_count": 0, "failed_count": 0}

            semaphore = asyncio.Semaphore(self.poll_concurrency)
            counters = {"processed_count": 0, "failed_count": 0}

            async def _process_entry(entry: Dict[str, Any]) -> None:
                async with semaphore:
                    message_id = entry.get("id")
                    if not message_id:
                        return
                    try:
                        await process_single_email(client, message_id, token.user_id, token.email)
                        counters["processed_count"] += 1
                    except Exception as exc:
                        counters["failed_count"] += 1
                        logger.warning("Autopilot email processing failed: %s", exc)

            await asyncio.gather(*[_process_entry(entry) for entry in messages])

            self._db.save_gmail_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_scan_at=now.isoformat(),
                last_error=None if counters["failed_count"] == 0 else "processing_errors",
            )
            return counters
        except Exception as exc:
            logger.warning("Autopilot poll failed for %s: %s", token.email, exc)
            self._db.save_gmail_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_error=f"poll_failed: {exc}",
            )
            return {"processed_count": 0, "failed_count": 1}

    async def _process_sla_escalations(self) -> None:
        now = datetime.now(timezone.utc)
        for org_id in self._db.list_organizations_with_ap_items():
            needs_approval = self._db.list_ap_items(org_id, state="needs_approval", limit=500)
            for item in needs_approval:
                created_at = _parse_iso(item.get("created_at")) or _parse_iso(item.get("updated_at"))
                if not created_at:
                    continue
                age_minutes = (now - created_at).total_seconds() / 60.0
                if age_minutes < self.approval_sla_minutes:
                    continue
                idempotency_key = f"approval_escalated:{item.get('id')}:{self.approval_sla_minutes}"
                if self._db.get_ap_audit_event_by_key(idempotency_key):
                    continue
                await self._send_escalation(org_id, item, age_minutes, idempotency_key)

    async def _send_escalation(self, org_id: str, item: Dict[str, Any], age_minutes: float, idempotency_key: str) -> None:
        vendor = item.get("vendor_name") or "Unknown vendor"
        amount = item.get("amount")
        currency = item.get("currency") or "USD"
        text = (
            f"AP approval SLA breached: {vendor} {currency} {amount if amount is not None else 'N/A'} "
            f"has been waiting {int(age_minutes)} minutes."
        )
        slack_ref = None
        teams_ref = None
        try:
            slack_client = get_slack_client()
            if getattr(slack_client, "bot_token", ""):
                msg = await slack_client.send_message(channel=self._slack_channel, text=text)
                slack_ref = msg.ts
        except Exception as exc:
            logger.warning("Slack escalation failed: %s", exc)

        try:
            teams_client = get_teams_client()
            if (teams_client.webhook_url or "").strip():
                msg = await teams_client.send_approval_message(
                    text=text,
                    ap_item_id=item.get("id"),
                    vendor=vendor,
                    amount=f"{currency} {amount if amount is not None else 'N/A'}",
                    invoice_number=item.get("invoice_number") or "N/A",
                )
                teams_ref = msg.message_id
        except Exception as exc:
            logger.warning("Teams escalation failed: %s", exc)

        self._db.append_ap_audit_event(
            {
                "ap_item_id": item.get("id"),
                "event_type": "approval_escalated",
                "from_state": item.get("state"),
                "to_state": item.get("state"),
                "actor_type": "system",
                "actor_id": "sla_monitor",
                "payload_json": {
                    "reason": "approval_sla_breached",
                    "age_minutes": round(age_minutes, 2),
                    "sla_minutes": self.approval_sla_minutes,
                },
                "external_refs": {
                    "gmail_thread_id": item.get("thread_id"),
                    "gmail_message_id": item.get("message_id"),
                    "slack_message_ts": slack_ref,
                    "teams_message_id": teams_ref,
                },
                "idempotency_key": idempotency_key,
                "organization_id": org_id,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )


def _parse_watch_expiration(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        milliseconds = int(value)
        return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def start_gmail_autopilot(app=None) -> Optional[GmailAutopilot]:
    autopilot = GmailAutopilot()
    await autopilot.start()
    if app is not None:
        app.state.gmail_autopilot = autopilot
    return autopilot


async def stop_gmail_autopilot(app=None) -> None:
    autopilot = None
    if app is not None:
        autopilot = getattr(app.state, "gmail_autopilot", None)
    if autopilot:
        await autopilot.stop()
