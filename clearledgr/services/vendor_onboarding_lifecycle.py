"""Vendor onboarding lifecycle service — Phase 3.1.e.

Ties together the state machine (Phase 3.1.a), the chase email dispatch
(Phase 3.1.c), and the ERP vendor-create dispatcher (existing) into
two operational entry points:

  1. **chase_stale_sessions** — called by the background loop every hour.
     Scans all active pre-active sessions, computes hours since invite,
     dispatches 24h/48h chase emails, escalates after 72h, abandons
     after 30 days.

  2. **activate_vendor_in_erp** — called when a session reaches
     ``bank_verified``. Transitions to ``ready_for_erp``, dispatches
     ``create_vendor()`` to the customer's ERP, persists the ERP vendor
     ID, transitions to ``active``, revokes all tokens, and posts a
     Slack confirmation.

Both functions are pure — they take a database handle (or use get_db)
and return structured results. The background loop (agent_background.py)
owns the cadence; this module owns the decision logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Chase thresholds in hours.
_CHASE_24H = 24
_CHASE_48H = 48
_ESCALATION_72H = 72
_ABANDON_DAYS = 30
_ABANDON_HOURS = _ABANDON_DAYS * 24


@dataclass
class ChaseResult:
    """Summary of a single chase-loop run."""

    sessions_scanned: int = 0
    chases_sent: int = 0
    escalations: int = 0
    abandonments: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class ActivationResult:
    """Outcome of an ERP activation attempt."""

    success: bool
    erp_vendor_id: Optional[str] = None
    error: Optional[str] = None


def _hours_since(iso_timestamp: str) -> Optional[float]:
    """Return hours elapsed since the given ISO timestamp."""
    if not iso_timestamp:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_timestamp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 3600
    except (TypeError, ValueError):
        return None


async def chase_stale_sessions(
    db: Any = None,
) -> ChaseResult:
    """Scan all pre-active sessions and dispatch chases / escalations.

    Called by the background loop. Scans across ALL organizations in
    a single pass (the session table has an index on state +
    last_activity_at) so the cost scales with active onboarding
    sessions, not with total organizations.

    Chase logic:
      - 24h since invite with zero chases → send chase_24h
      - 48h since invite with ≤ 1 chase → send chase_48h
      - 72h since invite → escalate (transition to escalated state,
        post Slack notification to AP Manager)
      - 30 days since invite → abandon (terminal state, tokens revoked)

    Chases are idempotent within a cadence window — the session's
    ``chase_count`` and ``last_chase_at`` prevent double-sends.
    """
    from clearledgr.core.database import get_db

    db = db or get_db()
    result = ChaseResult()

    # Fetch all active pre-active sessions (invited, awaiting_kyc,
    # awaiting_bank).
    sessions = db.list_pending_onboarding_sessions()
    result.sessions_scanned = len(sessions)

    for session in sessions:
        session_id = session.get("id") or ""
        org_id = session.get("organization_id") or ""
        vendor_name = session.get("vendor_name") or ""
        state = session.get("state") or ""
        invited_at = session.get("invited_at") or ""
        last_chase_at = session.get("last_chase_at")
        chase_count = int(session.get("chase_count") or 0)
        meta = session.get("metadata") or {}

        hours = _hours_since(invited_at)
        if hours is None:
            continue

        try:
            # Abandon after 30 days.
            if hours >= _ABANDON_HOURS:
                from clearledgr.core.vendor_onboarding_states import (
                    VendorOnboardingState,
                )
                db.transition_onboarding_session_state(
                    session_id,
                    VendorOnboardingState.ABANDONED.value,
                    actor_id="agent",
                    reason=f"No vendor response after {_ABANDON_DAYS} days",
                )
                db.revoke_session_tokens(
                    session_id, revoked_by="agent", reason="session_abandoned"
                )
                result.abandonments += 1
                continue

            # Escalate after 72h.
            if hours >= _ESCALATION_72H and state != "escalated":
                # Only escalate if we haven't already.
                from clearledgr.core.vendor_onboarding_states import (
                    VendorOnboardingState,
                )
                db.transition_onboarding_session_state(
                    session_id,
                    VendorOnboardingState.ESCALATED.value,
                    actor_id="agent",
                    reason="No vendor response after 72 hours",
                )
                # Dispatch the 72h escalation email.
                await _send_chase(db, session, "escalation_72h", hours)
                result.escalations += 1
                continue

            # Chase at 48h (if ≤ 1 prior chase).
            if hours >= _CHASE_48H and chase_count <= 1:
                # Only send 48h chase if we haven't sent one yet
                # (chase_count ≤ 1 means we've sent at most the 24h).
                hours_since_last = _hours_since(last_chase_at) if last_chase_at else hours
                if hours_since_last is None or hours_since_last >= 12:
                    await _send_chase(db, session, "chase_48h", hours)
                    result.chases_sent += 1
                    continue

            # Chase at 24h (if zero prior chases).
            if hours >= _CHASE_24H and chase_count == 0:
                await _send_chase(db, session, "chase_24h", hours)
                result.chases_sent += 1
                continue

        except Exception as exc:
            logger.warning(
                "[onboarding_lifecycle] chase error for session %s: %s",
                session_id, exc,
            )
            result.errors.append(f"{session_id}: {exc}")

    return result


async def _send_chase(
    db: Any,
    session: Dict[str, Any],
    chase_type: str,
    hours_since_invite: float,
) -> None:
    """§6.8 Vendor Onboarding Chase Notices.

    "Before the agent sends a chase email to a vendor who has not responded
    to an onboarding step, it posts a preview to the AP channel. Two buttons:
    [Hold chase] and [Send now]. If no response within 30 minutes, agent
    sends automatically. If held, it asks for a reason and logs it."
    """
    meta = session.get("metadata") or {}
    contact_email = meta.get("invite_email_to") or ""
    vendor_name = session.get("vendor_name") or ""
    org_id = session.get("organization_id") or ""
    session_id = session.get("id") or ""
    days = int(hours_since_invite / 24)

    if not contact_email:
        logger.info("[onboarding_lifecycle] skipping chase for session %s — no contact email", session_id)
        return

    # Determine what's actually missing based on the session's current
    # state. Thesis §6.8 calls for specificity: "About to chase Paystack
    # for their certificate of incorporation" beats "for their
    # onboarding response." AP Managers can only hold-vs-send
    # intelligently if the Slack preview tells them WHAT we're chasing.
    _state_missing = {
        "invited": "onboarding form (they haven't opened the link yet)",
        "awaiting_kyc": "business details — registered address, registration number, directors",
        "awaiting_bank": "bank details (IBAN + account holder)",
        "escalated": "onboarding (already escalated once)",
    }
    missing_doc = _state_missing.get(state, "onboarding response")
    if chase_type == "escalation_72h":
        missing_doc = f"{missing_doc} — escalated after 72h"

    # Post Slack preview with Hold/Send buttons
    try:
        from clearledgr.services.slack_notifications import _post_slack_blocks
        import os

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*About to chase {vendor_name}* ({contact_email}) for their {missing_doc} "
                        f"— {days * 24}h since first request. Sending in 30 minutes unless you hold it."
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Hold chase"},
                        "action_id": f"hold_chase_{session_id}",
                        "value": session_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Send now"},
                        "style": "primary",
                        "action_id": f"send_chase_now_{session_id}",
                        "value": session_id,
                    },
                ],
            },
        ]

        preview_result = await _post_slack_blocks(
            blocks=blocks,
            text=f"About to chase {vendor_name} for {missing_doc}",
            organization_id=org_id,
        )

        if preview_result:
            # Store pending chase in session metadata for the background reaper
            # to send after 30 minutes if not held
            from datetime import datetime, timezone, timedelta
            send_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
            pending = {
                "pending_chase_type": chase_type,
                "pending_chase_send_at": send_at,
                "pending_chase_slack_ts": (preview_result or {}).get("ts"),
                "pending_chase_slack_channel": (preview_result or {}).get("channel"),
            }
            try:
                existing_meta = dict(db.get_onboarding_session_by_id(session_id).get("metadata") or {})
                existing_meta.update(pending)
                db.update_onboarding_session_metadata(session_id, existing_meta)
            except Exception:
                # If we can't store pending state, send immediately as fallback
                await _dispatch_chase_email(db, session, chase_type, hours_since_invite)
            return

    except Exception as exc:
        logger.debug("[onboarding_lifecycle] chase preview failed, sending directly: %s", exc)

    # Fallback: send directly if Slack preview fails
    await _dispatch_chase_email(db, session, chase_type, hours_since_invite)


async def _dispatch_chase_email(
    db: Any,
    session: Dict[str, Any],
    chase_type: str,
    hours_since_invite: float,
) -> None:
    """Actually send the chase email to the vendor."""
    from clearledgr.services.vendor_onboarding_email import dispatch_onboarding_chase

    meta = session.get("metadata") or {}
    contact_email = meta.get("invite_email_to") or ""
    contact_name = meta.get("contact_name") or ""
    customer_name = meta.get("customer_name") or ""
    thread_id = meta.get("invite_thread_id")
    in_reply_to = meta.get("invite_message_id")

    # Build the real magic-link URL from the session's live tokens.
    # Previously shipped the literal placeholder "<your-original-link>"
    # verbatim — vendors got broken chase emails. §9 thesis: magic links
    # must resolve to the vendor's own portal session.
    tokens = db.list_session_tokens(session["id"], include_revoked=False)
    magic_link = ""
    for token_row in tokens or []:
        raw_token = token_row.get("raw_token") or token_row.get("token")
        if raw_token:
            import os as _os
            base = _os.getenv("CLEARLEDGR_PORTAL_BASE_URL", "https://onboard.clearledgr.com").rstrip("/")
            magic_link = f"{base}/onboard/{raw_token}"
            break
    if not magic_link:
        # No retrievable active token (raw tokens are only returned at
        # issuance; we store hashes). Issue a fresh one so the chase
        # still has a working link. This supersedes any prior token
        # for the session — the one-active-token invariant.
        try:
            issued = db.generate_onboarding_token(
                session_id=session["id"],
                issued_by="agent_chase_loop",
                purpose="full_onboarding",
            )
            if issued:
                raw_token, _token_row = issued
                import os as _os
                base = _os.getenv("CLEARLEDGR_PORTAL_BASE_URL", "https://onboard.clearledgr.com").rstrip("/")
                magic_link = f"{base}/onboard/{raw_token}"
        except Exception as token_exc:  # noqa: BLE001
            logger.warning(
                "[onboarding_lifecycle] could not rehydrate magic link for session %s: %s",
                session["id"], token_exc,
            )
        if not magic_link:
            logger.warning(
                "[onboarding_lifecycle] skipping chase for session %s — no valid magic link available",
                session["id"],
            )
            return  # chase with no working link is worse than none

    await dispatch_onboarding_chase(
        organization_id=session.get("organization_id") or "",
        vendor_name=session.get("vendor_name") or "",
        contact_email=contact_email,
        contact_name=contact_name,
        customer_name=customer_name,
        magic_link=magic_link,
        session_id=session.get("id") or "",
        chase_type=chase_type,
        days_waiting=int(hours_since_invite / 24),
        thread_id=thread_id,
        in_reply_to=in_reply_to,
    )


async def activate_vendor_in_erp(
    session_id: str,
    db: Any = None,
) -> ActivationResult:
    """Transition bank_verified → ready_for_erp → active via the ERP.

    This is the terminal happy path:
      1. Transition to ``ready_for_erp``
      2. Call ``create_vendor()`` on the ERP dispatcher
      3. Store the ERP vendor ID on the session
      4. Transition to ``active``
      5. Revoke all magic-link tokens
      6. Send the completion email
      7. Post Slack confirmation

    If the ERP call fails, the session stays in ``ready_for_erp`` and
    the error is recorded. The background loop or a manual retry can
    attempt again — the ERP dispatcher has its own idempotency layer.
    """
    from clearledgr.core.database import get_db
    from clearledgr.core.vendor_onboarding_states import VendorOnboardingState

    db = db or get_db()
    session = db.get_onboarding_session_by_id(session_id)
    if session is None:
        return ActivationResult(success=False, error="session_not_found")
    if not session.get("is_active"):
        return ActivationResult(success=False, error="session_not_active")

    org_id = session.get("organization_id") or ""
    vendor_name = session.get("vendor_name") or ""

    # Step 1: ready_for_erp.
    current = session.get("state") or ""
    if current == "bank_verified":
        db.transition_onboarding_session_state(
            session_id,
            VendorOnboardingState.READY_FOR_ERP.value,
            actor_id="agent",
        )
    elif current != "ready_for_erp":
        return ActivationResult(
            success=False,
            error=f"invalid_state_for_activation:{current}",
        )

    # Step 2: call ERP dispatcher.
    erp_vendor_id = None
    try:
        erp_vendor_id = await _dispatch_erp_create_vendor(
            org_id, vendor_name, db
        )
    except Exception as exc:
        logger.warning(
            "[onboarding_lifecycle] ERP create_vendor failed for %s/%s: %s",
            org_id, vendor_name, exc,
        )
        # Stay in ready_for_erp — retry later.
        return ActivationResult(success=False, error=str(exc))

    # Step 3: store ERP vendor ID.
    if erp_vendor_id:
        db.attach_erp_vendor_id(session_id, erp_vendor_id)

    # Step 4: transition to active.
    db.transition_onboarding_session_state(
        session_id,
        VendorOnboardingState.ACTIVE.value,
        actor_id="agent",
    )

    # Step 5: revoke tokens.
    db.revoke_session_tokens(
        session_id, revoked_by="agent", reason="onboarding_complete"
    )

    # Step 6: send completion email (best-effort).
    try:
        meta = session.get("metadata") or {}
        contact_email = meta.get("invite_email_to") or ""
        if contact_email:
            from clearledgr.services.vendor_onboarding_email import (
                send_onboarding_email,
            )
            from clearledgr.services.vendor_onboarding_email import (
                _get_gmail_client_for_org,
            )
            gmail_client = await _get_gmail_client_for_org(org_id)
            if gmail_client:
                await send_onboarding_email(
                    gmail_client=gmail_client,
                    to=contact_email,
                    template_id="onboarding_complete",
                    context={
                        "contact_name": meta.get("contact_name") or contact_email.split("@")[0],
                        "customer_name": meta.get("customer_name") or org_id,
                    },
                )
    except Exception as email_exc:
        logger.warning(
            "[onboarding_lifecycle] completion email failed (non-fatal): %s",
            email_exc,
        )

    # Audit event.
    try:
        db.append_ap_audit_event(
            {
                "ap_item_id": "",
                "event_type": "vendor_onboarding_activated",
                "actor_type": "agent",
                "actor_id": "agent",
                "reason": (
                    f"Vendor {vendor_name} activated in ERP "
                    f"(erp_vendor_id={erp_vendor_id})"
                ),
                "metadata": {
                    "session_id": session_id,
                    "vendor_name": vendor_name,
                    "erp_vendor_id": erp_vendor_id,
                },
                "organization_id": org_id,
                "source": "vendor_onboarding_lifecycle",
            }
        )
    except Exception:
        pass

    return ActivationResult(success=True, erp_vendor_id=erp_vendor_id)


async def enrich_vendor_on_kyc(
    organization_id: str,
    vendor_name: str,
    registration_number: Optional[str] = None,
    vat_number: Optional[str] = None,
    db: Any = None,
) -> Dict[str, Any]:
    """Best-effort vendor enrichment after KYC submission (§3).

    Called when a vendor transitions from ``awaiting_kyc`` to
    ``awaiting_bank``. Looks up the vendor in Companies House and
    (if a VAT number was provided) the HMRC VAT register, then
    persists the enriched fields on the vendor profile.

    Never raises — all external errors are caught and logged. Returns
    the enrichment result dict (may be empty if all lookups failed).
    """
    from clearledgr.core.database import get_db as _get_db

    db = db or _get_db()

    # If no registration_number / vat_number passed, try reading from
    # the vendor profile (the portal already wrote them via update_vendor_kyc).
    if not registration_number or not vat_number:
        profile = db.get_vendor_profile(organization_id, vendor_name)
        if profile:
            registration_number = registration_number or profile.get("registration_number")
            vat_number = vat_number or profile.get("vat_number")

    try:
        from clearledgr.services.vendor_enrichment import enrich_vendor

        result = await enrich_vendor(
            vendor_name,
            registration_number=registration_number or None,
            vat_number=vat_number or None,
            organization_id=organization_id,
            persist=True,
        )
        sources = result.get("sources") or []
        if sources:
            logger.info(
                "[onboarding_lifecycle] vendor enrichment complete for %s/%s "
                "from %s",
                organization_id, vendor_name, sources,
            )
        return result
    except Exception as exc:
        logger.warning(
            "[onboarding_lifecycle] vendor enrichment failed (non-fatal) "
            "for %s/%s: %s",
            organization_id, vendor_name, exc,
        )
        return {"vendor_name": vendor_name, "sources": [], "error": str(exc)}


async def _dispatch_erp_create_vendor(
    organization_id: str,
    vendor_name: str,
    db: Any,
) -> Optional[str]:
    """Attempt to create the vendor in the customer's ERP.

    Uses the existing ``create_vendor`` dispatcher from
    ``clearledgr.integrations.erp_router``. Returns the ERP vendor ID
    on success, or raises on failure.

    If no ERP is connected, returns a synthetic "no_erp" ID so the
    onboarding flow can complete without blocking on ERP configuration.
    """
    try:
        from clearledgr.integrations.erp_router import create_vendor

        profile = db.get_vendor_profile(organization_id, vendor_name) or {}
        vendor_data = {
            "name": vendor_name,
            "email": (profile.get("metadata") or {}).get("contact_email") or "",
            "organization_id": organization_id,
        }
        result = await create_vendor(organization_id, vendor_data)
        if isinstance(result, dict):
            return result.get("vendor_id") or result.get("id") or str(result)
        return str(result) if result else None
    except ImportError:
        # No ERP router available (test environment or ERP not configured).
        logger.info(
            "[onboarding_lifecycle] no ERP router available for %s — "
            "marking vendor as activated without ERP",
            organization_id,
        )
        return f"local_{vendor_name}"
    except Exception as exc:
        # Re-raise so the caller can decide whether to retry.
        raise
