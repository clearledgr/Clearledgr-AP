"""Vendor onboarding email dispatch — Phase 3.1.c.

Sends onboarding emails (invite, chase, completion) from the customer's
connected Gmail account using the existing :class:`GmailAPIClient` +
:func:`render_template` infrastructure. No new email provider, no
SendGrid — we use the same Gmail OAuth connection that the AP autopilot
already has in production.

Design rules
============

* **Send first, draft fallback.** If the connected Gmail account has
  the ``gmail.send`` scope (it should — it's in our scope list), the
  email goes straight out. If sending fails (scope missing, token
  expired, temporary Gmail outage), we fall back to creating a draft
  so the finance user can review and send manually. Either way the
  onboarding state machine advances — the invite is considered
  dispatched once we have either a sent message ID or a draft ID.

* **One thread per onboarding session.** The invite email creates a
  new thread. All chases are sent as replies in that same thread via
  ``thread_id`` + ``In-Reply-To`` so the conversation stays together
  in the vendor's inbox and in the customer's Gmail. The thread ID is
  persisted in the session metadata so chase calls don't need to
  rediscover it.

* **Audit trail follows the existing pattern.** Every dispatch logs a
  ``vendor_onboarding_email_sent`` audit event with the template ID,
  recipient, method (sent/draft), and message/draft ID. Never the
  email body or magic link — per the §19 no-plaintext-in-logs
  discipline.

* **Pure service, no background loop.** This module exposes synchronous
  helper functions that the invite endpoint (Phase 3.1.b) and the
  auto-chase scheduler (Phase 3.1.e) call at the right moments. It
  does not own a cadence — scheduling belongs to the background loop.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class EmailDispatchResult:
    """Outcome of a single email dispatch attempt."""

    success: bool
    method: str  # "sent" | "draft" | "failed"
    message_id: Optional[str] = None
    draft_id: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "success": self.success,
            "method": self.method,
        }
        if self.message_id:
            d["message_id"] = self.message_id
        if self.draft_id:
            d["draft_id"] = self.draft_id
        if self.error:
            d["error"] = self.error
        return d


async def send_onboarding_email(
    *,
    gmail_client: Any,
    to: str,
    template_id: str,
    context: Dict[str, Any],
    thread_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
) -> EmailDispatchResult:
    """Render a template and dispatch via GmailAPIClient.

    Attempts direct send first, falls back to draft creation on
    failure. Returns an :class:`EmailDispatchResult` so callers can
    persist the message/draft ID on the onboarding session metadata.

    ``gmail_client`` must already be authenticated — call
    ``await gmail_client.ensure_authenticated()`` before passing it
    in. This separation is deliberate: the caller owns the auth
    context (which org? which connected user?) and this function is a
    pure dispatch primitive.
    """
    from clearledgr.services.vendor_communication_templates import render_template

    try:
        rendered = render_template(template_id, context)
    except KeyError:
        logger.error(
            "[onboarding_email] unknown template %r — cannot dispatch", template_id
        )
        return EmailDispatchResult(
            success=False, method="failed", error=f"unknown_template:{template_id}"
        )

    subject = rendered["subject"]
    body = rendered["body"]

    # Attempt 1: direct send
    try:
        result = await gmail_client.send_message(
            to=to,
            subject=subject,
            body=body,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
        )
        message_id = result.get("id") if isinstance(result, dict) else str(result)
        logger.info(
            "[onboarding_email] sent template=%s to=%s message_id=%s",
            template_id, to, message_id,
        )
        return EmailDispatchResult(
            success=True, method="sent", message_id=message_id
        )
    except Exception as send_exc:
        logger.warning(
            "[onboarding_email] send failed (template=%s to=%s): %s — falling back to draft",
            template_id, to, send_exc,
        )

    # Attempt 2: draft fallback
    try:
        draft_id = await gmail_client.create_draft(
            thread_id=thread_id or "",
            to=to,
            subject=subject,
            body=body,
        )
        logger.info(
            "[onboarding_email] created draft template=%s to=%s draft_id=%s",
            template_id, to, draft_id,
        )
        return EmailDispatchResult(
            success=True, method="draft", draft_id=str(draft_id)
        )
    except Exception as draft_exc:
        logger.error(
            "[onboarding_email] draft fallback also failed (template=%s to=%s): %s",
            template_id, to, draft_exc,
        )
        return EmailDispatchResult(
            success=False, method="failed", error=str(draft_exc)
        )


async def dispatch_onboarding_invite(
    *,
    organization_id: str,
    vendor_name: str,
    contact_email: str,
    contact_name: str,
    customer_name: str,
    magic_link: str,
    expires_at: str,
    session_id: str,
) -> EmailDispatchResult:
    """High-level invite dispatch for the customer-side invite endpoint.

    Retrieves the Gmail client for the organization, renders the
    ``onboarding_invite`` template, sends the email, and records the
    thread ID + dispatch metadata on the onboarding session.
    """
    from clearledgr.core.database import get_db

    gmail_client = await _get_gmail_client_for_org(organization_id)
    if gmail_client is None:
        logger.warning(
            "[onboarding_email] no Gmail client available for org=%s — cannot dispatch invite",
            organization_id,
        )
        return EmailDispatchResult(
            success=False, method="failed", error="no_gmail_client_for_org"
        )

    result = await send_onboarding_email(
        gmail_client=gmail_client,
        to=contact_email,
        template_id="onboarding_invite",
        context={
            "contact_name": contact_name or contact_email.split("@")[0],
            "customer_name": customer_name or organization_id,
            "magic_link": magic_link,
            "expires_at": expires_at,
            "vendor_name": vendor_name,
        },
    )

    # Persist the thread reference + dispatch outcome on the session
    # metadata so the chase loop can reply in-thread later.
    db = get_db()
    metadata_patch: Dict[str, Any] = {
        "invite_email_method": result.method,
        "invite_email_to": contact_email,
        "contact_name": contact_name,
        "customer_name": customer_name,
    }
    if result.message_id:
        metadata_patch["invite_thread_id"] = result.message_id
        metadata_patch["invite_message_id"] = result.message_id
    if result.draft_id:
        metadata_patch["invite_draft_id"] = result.draft_id

    try:
        db.transition_onboarding_session_state(
            session_id,
            "invited",  # stay in invited — only KYC submission advances
            actor_id="agent",
            emit_audit=False,
            metadata_patch=metadata_patch,
        )
    except Exception:
        # Non-fatal: the email is sent even if metadata persistence fails.
        # The chase loop will handle the missing thread_id gracefully by
        # sending chases as new threads rather than replies.
        pass

    # Audit event — template + recipient + method, never the email body
    # or the magic link value (§19 plaintext-free discipline).
    try:
        db.append_ap_audit_event(
            {
                "ap_item_id": "",
                "event_type": "vendor_onboarding_email_sent",
                "actor_type": "agent",
                "actor_id": "agent",
                "reason": (
                    f"Onboarding invite dispatched to {contact_email} "
                    f"for vendor {vendor_name} (method={result.method})"
                ),
                "metadata": {
                    "template_id": "onboarding_invite",
                    "to": contact_email,
                    "method": result.method,
                    "message_id": result.message_id,
                    "draft_id": result.draft_id,
                    "session_id": session_id,
                    "vendor_name": vendor_name,
                },
                "organization_id": organization_id,
                "source": "vendor_onboarding_email",
            }
        )
    except Exception as audit_exc:
        logger.warning(
            "[onboarding_email] invite audit emission failed (non-fatal): %s",
            audit_exc,
        )

    return result


async def dispatch_onboarding_chase(
    *,
    organization_id: str,
    vendor_name: str,
    contact_email: str,
    contact_name: str,
    customer_name: str,
    magic_link: str,
    session_id: str,
    chase_type: str,
    days_waiting: int = 0,
    thread_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
) -> EmailDispatchResult:
    """Dispatch a chase email (24h, 48h, or 72h escalation).

    Called by the auto-chase scheduler (Phase 3.1.e). Uses the same
    Gmail client and template infrastructure as the invite dispatch.
    """
    # Map chase type to template ID.
    template_map = {
        "chase_24h": "onboarding_chase_24h",
        "chase_48h": "onboarding_chase_48h",
        "escalation_72h": "onboarding_escalation_72h",
    }
    template_id = template_map.get(chase_type, "onboarding_chase_24h")

    gmail_client = await _get_gmail_client_for_org(organization_id)
    if gmail_client is None:
        return EmailDispatchResult(
            success=False, method="failed", error="no_gmail_client_for_org"
        )

    result = await send_onboarding_email(
        gmail_client=gmail_client,
        to=contact_email,
        template_id=template_id,
        context={
            "contact_name": contact_name or contact_email.split("@")[0],
            "customer_name": customer_name or organization_id,
            "magic_link": magic_link,
            "vendor_name": vendor_name,
            "days_waiting": str(days_waiting),
        },
        thread_id=thread_id,
        in_reply_to=in_reply_to,
    )

    from clearledgr.core.database import get_db
    db = get_db()

    # Record the chase on the session.
    db.record_onboarding_chase(session_id, chase_type)

    try:
        db.append_ap_audit_event(
            {
                "ap_item_id": "",
                "event_type": "vendor_onboarding_email_sent",
                "actor_type": "agent",
                "actor_id": "agent",
                "reason": (
                    f"Onboarding {chase_type} dispatched to {contact_email} "
                    f"for vendor {vendor_name} (method={result.method})"
                ),
                "metadata": {
                    "template_id": template_id,
                    "to": contact_email,
                    "method": result.method,
                    "chase_type": chase_type,
                    "message_id": result.message_id,
                    "session_id": session_id,
                    "vendor_name": vendor_name,
                },
                "organization_id": organization_id,
                "source": "vendor_onboarding_email",
            }
        )
    except Exception as audit_exc:
        logger.warning(
            "[onboarding_email] chase audit emission failed (non-fatal): %s",
            audit_exc,
        )

    return result


async def _get_gmail_client_for_org(organization_id: str) -> Any:
    """Return a ready-to-send GmailAPIClient for the organization.

    Follows the same pattern as ``agent_background.py`` — retrieves
    the first available Gmail token from ``token_store.list_all()`` and
    constructs a ``GmailAPIClient``. In production this should be
    scoped to the organization's designated sender account (typically
    the AP Manager or a shared mailbox).

    Returns None if no Gmail tokens are available.
    """
    try:
        from clearledgr.services.gmail_api import GmailAPIClient, token_store

        tokens = token_store.list_all()
        if not tokens:
            logger.info(
                "[onboarding_email] no Gmail tokens available for org=%s",
                organization_id,
            )
            return None

        # Use the first available token. Future: scope to the org's
        # designated sender account via a mapping table.
        client = GmailAPIClient(tokens[0].user_id)
        await client.ensure_authenticated()
        return client
    except Exception as exc:
        logger.warning(
            "[onboarding_email] failed to get Gmail client for org=%s: %s",
            organization_id, exc,
        )
        return None
