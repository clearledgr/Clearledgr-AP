"""
Outlook Autopilot Service

Mirrors gmail_autopilot.py — runs polling loop for Microsoft 365 / Outlook
email scanning using Microsoft Graph API.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from clearledgr.core.database import get_db
from clearledgr.services.outlook_api import (
    OutlookAPIClient,
    outlook_token_store,
    is_outlook_configured,
)

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _parse_iso(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


class OutlookAutopilot:
    """Background Outlook polling loop.  Mirrors GmailAutopilot."""

    def __init__(self):
        self.enabled = _env_bool("OUTLOOK_AUTOPILOT_ENABLED", True) and is_outlook_configured()
        self.poll_interval = int(os.getenv("OUTLOOK_POLL_INTERVAL_SEC", "300"))
        self.poll_max_results = int(os.getenv("OUTLOOK_POLL_MAX_RESULTS", "50"))
        self.poll_seed_hours = int(os.getenv("OUTLOOK_POLL_SEED_HOURS", "24"))
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._db = get_db()
        self._status: Dict[str, Any] = {"state": "idle"}

    def get_status(self) -> Dict[str, Any]:
        return self._status

    async def start(self) -> None:
        if not self.enabled:
            self._status = {"state": "disabled"}
            return
        if self._running:
            return
        self._running = True
        try:
            await self._catchup_rescan()
        except Exception as exc:
            logger.warning("Outlook autopilot startup catch-up failed: %s", exc)
        self._task = asyncio.create_task(self._run_loop())
        self._status = {"state": "running"}
        logger.info("Outlook autopilot started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        self._status = {"state": "stopped"}
        logger.info("Outlook autopilot stopped")

    async def _catchup_rescan(self) -> None:
        """Extend scan window on startup for users whose last_scan_at is stale."""
        tokens = outlook_token_store.list_all()
        now = datetime.now(timezone.utc)
        for token in tokens:
            state = self._db.get_outlook_autopilot_state(token.user_id) or {}
            last_scan = _parse_iso(state.get("last_scan_at"))
            if last_scan and (now - last_scan).total_seconds() > self.poll_interval * 2:
                seed_start = now - timedelta(hours=self.poll_seed_hours)
                self._db.save_outlook_autopilot_state(
                    user_id=token.user_id,
                    email=token.email,
                    last_scan_at=seed_start.isoformat(),
                )
                logger.info("Outlook catch-up: reset scan window for %s", token.email)

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("Outlook autopilot loop error: %s", exc)
                self._status = {"state": "error", "error": str(exc)}
            await asyncio.sleep(self.poll_interval)

    async def _tick(self) -> None:
        tokens = outlook_token_store.list_all()
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
        client = OutlookAPIClient(token.user_id)
        if not await client.ensure_authenticated():
            self._db.save_outlook_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_error="auth_failed",
            )
            return
        await self._poll_messages(token, client)

    async def _poll_messages(self, token, client: OutlookAPIClient) -> None:
        now = datetime.now(timezone.utc)
        state = self._db.get_outlook_autopilot_state(token.user_id) or {}
        last_scan_at = _parse_iso(state.get("last_scan_at"))
        if not last_scan_at:
            last_scan_at = now - timedelta(hours=self.poll_seed_hours)

        # OData filter: received after last scan, has attachments
        since_iso = last_scan_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        filter_query = (
            f"receivedDateTime ge {since_iso} "
            f"and hasAttachments eq true"
        )

        try:
            response = await client.list_messages(
                folder="Inbox",
                filter_query=filter_query,
                top=self.poll_max_results,
            )
            messages = response.get("value", [])
            if not messages:
                self._db.save_outlook_autopilot_state(
                    user_id=token.user_id,
                    email=token.email,
                    last_scan_at=now.isoformat(),
                    last_error=None,
                )
                return

            from clearledgr.services.outlook_email_processor import process_outlook_email

            organization_id = self._resolve_org_id(token.user_id)

            # Subscription limits check
            try:
                from clearledgr.services.subscription import get_subscription_service
                sub_svc = get_subscription_service()
                sub = sub_svc.get_subscription(organization_id)
                current_usage = sub.usage.invoices_this_month if sub.usage else 0
                limit_check = sub_svc.check_limit(organization_id, "invoices_per_month", current_usage)
                if not limit_check.get("allowed", True):
                    logger.warning("Outlook autopilot: subscription limit reached for org %s", organization_id)
                    self._db.save_outlook_autopilot_state(
                        user_id=token.user_id,
                        email=token.email,
                        last_scan_at=now.isoformat(),
                        last_error="subscription_limit_reached",
                    )
                    return
            except Exception as sub_exc:
                logger.warning("Outlook autopilot: subscription check failed, proceeding: %s", sub_exc)

            for msg_data in messages:
                message_id = msg_data.get("id")
                if not message_id:
                    continue
                try:
                    await process_outlook_email(
                        client=client,
                        message_id=message_id,
                        user_id=token.user_id,
                        organization_id=organization_id,
                    )
                except Exception as exc:
                    logger.warning("Outlook email processing failed: %s", exc)

            self._db.save_outlook_autopilot_state(
                user_id=token.user_id,
                email=token.email,
                last_scan_at=now.isoformat(),
                last_error=None,
            )
        except Exception as exc:
            logger.warning("Outlook poll failed for %s: %s", token.email, exc)
            self._db.save_outlook_autopilot_state(
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
            return user["organization_id"]
        return "default"


# ---------------------------------------------------------------------------
# Module-level instance + start/stop helpers
# ---------------------------------------------------------------------------

_autopilot: Optional[OutlookAutopilot] = None


async def start_outlook_autopilot(app=None) -> None:
    global _autopilot
    _autopilot = OutlookAutopilot()
    await _autopilot.start()


async def stop_outlook_autopilot(app=None) -> None:
    if _autopilot:
        await _autopilot.stop()


def get_outlook_autopilot_status() -> Dict[str, Any]:
    if _autopilot:
        return _autopilot.get_status()
    return {"state": "not_initialized"}
