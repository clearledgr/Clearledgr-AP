"""Gmail-token-resolved sender for remittance advice (Wave 2 / C5
carry-over).

C5 shipped the renderer + opt-out + idempotency, but left the actual
Gmail-send wiring as a 'no_gmail' audit-only stub when no sender was
injected. This module fills that gap:

  * resolve_gmail_sender_for_org(org_id) returns a callable suitable
    to pass as ``sender`` to send_remittance_advice. The callable
    looks up an active Gmail-connected user in the org, builds a
    GmailAPIClient, sends the email, and returns the Gmail API
    response.
  * If no user in the org has a connected Gmail token, returns None
    so the existing 'no_gmail' audit-only fallback path keeps
    working.
  * Sender selection: prefers the org's owner / admin first
    (consistent return-address per org), falls back to any user
    with a valid Gmail token.

The wiring into payment_tracking.record_payment_confirmation is in
the same commit — when the C5 hook fires (status='confirmed' and
sender is None), the hook resolves a Gmail sender automatically.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


_SENDER_PREFERRED_ROLES = (
    "owner",
    "financial_controller",
    "ap_manager",
    "ap_clerk",
)


def _select_user_for_org(
    db, organization_id: str,
) -> Optional[Dict[str, Any]]:
    """Pick the user to send remittance from. Order of preference:

      1. Active user with a Gmail token AND role in
         (owner, admin, ap_clerk)
      2. Any active user with a Gmail token
      3. None
    """
    try:
        users = db.get_users(organization_id) or []
    except Exception:
        logger.exception(
            "remittance_sender: get_users failed org=%s", organization_id,
        )
        return None
    if not users:
        return None

    # Index Gmail tokens by user_id so we make exactly one query.
    try:
        oauth_rows = db.list_oauth_tokens("gmail") or []
    except Exception:
        oauth_rows = []
    gmail_user_ids = {
        str((dict(r).get("user_id") or "")).strip()
        for r in oauth_rows
    }
    gmail_user_ids.discard("")

    eligible = [u for u in users if u.get("id") in gmail_user_ids]
    if not eligible:
        return None

    by_role: Dict[str, Dict[str, Any]] = {}
    for u in eligible:
        role = str(u.get("role") or "").lower()
        if role not in by_role:
            by_role[role] = u

    for role in _SENDER_PREFERRED_ROLES:
        if role in by_role:
            return by_role[role]
    return eligible[0]


def resolve_gmail_sender_for_org(
    db, organization_id: str,
) -> Optional[Callable[..., Dict[str, Any]]]:
    """Return a synchronous sender callable bound to an active
    Gmail account in the org, or None if none is configured.

    The callable accepts ``to`` / ``subject`` / ``body`` keyword
    args and returns the Gmail API response dict.
    """
    user = _select_user_for_org(db, organization_id)
    if not user:
        return None

    user_id = user.get("id")

    def _send(*, to: str, subject: str, body: str) -> Dict[str, Any]:
        from clearledgr.services.gmail_api import GmailAPIClient

        async def _run():
            client = GmailAPIClient(user_id=user_id)
            ok = await client.ensure_authenticated()
            if not ok:
                return {
                    "status": "error",
                    "reason": "gmail_token_not_authenticated",
                }
            return await client.send_message(
                to=to, subject=subject, body=body,
            )

        # Drive the async send from a sync caller. record_payment_confirmation
        # is sync today; we run the coroutine in a fresh event loop in a
        # thread so we never collide with an outer ASGI loop.
        result_holder: Dict[str, Any] = {}

        def _runner() -> None:
            new_loop = asyncio.new_event_loop()
            try:
                result_holder["value"] = new_loop.run_until_complete(_run())
            except Exception as exc:
                result_holder["error"] = exc
            finally:
                new_loop.close()

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join(timeout=60)
        if "error" in result_holder:
            raise result_holder["error"]
        return result_holder.get("value") or {}

    _send.__name__ = f"gmail_sender_for_{organization_id}"
    return _send
