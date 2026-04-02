"""
Gmail Autopilot Service

Runs 24/7 Gmail scanning using Gmail API + Pub/Sub watch renewal.
This enables backend-driven AP detection even when Gmail is closed.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from clearledgr.core.database import get_db
from clearledgr.services.gmail_api import GmailAPIClient, GmailWatchService, token_store, PUBSUB_TOPIC

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


class GmailAutopilot:
    """
    Background Gmail autopilot loop.

    Modes:
    - watch: ensure Gmail watch is active for push notifications
    - poll: fallback polling for new emails
    - both: watch + poll
    """

    def __init__(self):
        self.enabled = _env_bool("GMAIL_AUTOPILOT_ENABLED", True)
        self.mode = os.getenv("GMAIL_AUTOPILOT_MODE", "both").strip().lower()
        self.poll_interval = int(os.getenv("GMAIL_POLL_INTERVAL_SEC", "300"))
        self.poll_max_results = int(os.getenv("GMAIL_POLL_MAX_RESULTS", "50"))
        self.poll_seed_hours = int(os.getenv("GMAIL_POLL_SEED_HOURS", "24"))
        self.watch_refresh_hours = int(os.getenv("GMAIL_WATCH_REFRESH_HOURS", "12"))
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
        # B7: Run catch-up scan on startup to backfill missed emails (PLAN.md §7.7.3)
        try:
            await self._catchup_rescan()
        except Exception as exc:
            logger.warning("Gmail autopilot startup catch-up failed: %s", exc)
        self._task = asyncio.create_task(self._run_loop())
        self._status = {"state": "running"}
        logger.info("Gmail autopilot started")

    async def _catchup_rescan(self) -> None:
        """Rescan emails missed during backend downtime.

        Extends the scan window to ``poll_seed_hours`` for any user whose
        ``last_scan_at`` is older than the current poll interval, ensuring
        emails that arrived during an outage are not silently dropped.
        """
        tokens = token_store.list_all()
        now = datetime.now(timezone.utc)
        catchup_count = 0
        for token in tokens:
            state = self._db.get_gmail_autopilot_state(token.user_id) or {}
            last_scan_at = _parse_iso(state.get("last_scan_at"))
            if last_scan_at and (now - last_scan_at).total_seconds() > self.poll_interval * 2:
                # Stale — widen window to poll_seed_hours for catch-up
                self._db.save_gmail_autopilot_state(
                    user_id=token.user_id,
                    email=token.email,
                    last_scan_at=(now - timedelta(hours=self.poll_seed_hours)).isoformat(),
                )
                catchup_count += 1
        if catchup_count:
            logger.info("Gmail autopilot: reset scan window for %d users after outage", catchup_count)
            # Run one immediate tick to process the backfill
            await self._tick()

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
        await asyncio.gather(*tasks)

        self._status = {
            "state": "idle",
            "users": len(tokens),
            "last_run": datetime.now(timezone.utc).isoformat(),
        }

    async def _process_user(self, token) -> None:
        client = GmailAPIClient(token.user_id)
        if not await client.ensure_authenticated():
            self._db.save_gmail_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_error="auth_failed",
            )
            return

        if self._watch_enabled():
            await self._ensure_watch(token)

        if self._poll_enabled():
            await self._poll_messages(token, client)

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
            logger.info("Gmail watch refreshed for %s", token.email)
        except Exception as exc:
            logger.warning("Gmail watch refresh failed for %s: %s", token.email, exc)
            self._db.save_gmail_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_error=f"watch_failed: {exc}",
            )

    async def _poll_messages(self, token, client: GmailAPIClient) -> None:
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
            "-category:updates"
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
                return

            from clearledgr.api.gmail_webhooks import process_single_email

            organization_id = self._resolve_org_id(token.user_id)

            # D1: Check subscription limits before processing emails
            try:
                from clearledgr.services.subscription import get_subscription_service
                sub_svc = get_subscription_service()
                sub = sub_svc.get_subscription(organization_id)
                current_usage = sub.usage.invoices_this_month if sub.usage else 0
                limit_check = sub_svc.check_limit(organization_id, "invoices_per_month", current_usage)
                if not limit_check.get("allowed", True):
                    logger.warning("Autopilot: subscription limit reached for org %s, skipping", organization_id)
                    self._db.save_gmail_autopilot_state(
                        user_id=token.user_id,
                        email=token.email,
                        last_scan_at=now.isoformat(),
                        last_error="subscription_limit_reached",
                    )
                    return
            except Exception as sub_exc:
                logger.warning("Autopilot: subscription check failed for org %s, proceeding: %s", organization_id, sub_exc)

            for entry in messages:
                message_id = entry.get("id")
                if not message_id:
                    continue
                try:
                    await process_single_email(
                        client=client,
                        message_id=message_id,
                        user_id=token.user_id,
                        organization_id=organization_id,
                    )
                except Exception as exc:
                    logger.warning("Autopilot email processing failed: %s", exc)

            self._db.save_gmail_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_scan_at=now.isoformat(),
                last_error=None,
            )
        except Exception as exc:
            logger.warning("Autopilot poll failed for %s: %s", token.email, exc)
            self._db.save_gmail_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_error=f"poll_failed: {exc}",
            )

    def _resolve_org_id(self, user_id: str) -> str:
        try:
            user = self._db.get_user(user_id)
        except Exception:
            user = None
        if user and user.get("organization_id"):
            return str(user["organization_id"])
        return "default"


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
    if app is not None and hasattr(app.state, "gmail_autopilot"):
        autopilot = app.state.gmail_autopilot
    if autopilot:
        await autopilot.stop()
