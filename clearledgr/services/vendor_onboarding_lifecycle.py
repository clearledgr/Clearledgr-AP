"""Vendor onboarding lifecycle service.

Solden does NOT send emails to vendors. The chase loop tracks stale
sessions and transitions state (escalate at 72h, abandon at 30 days);
operators chase vendors using their own Gmail. ``activate_vendor_in_erp``
handles the terminal ERP create_vendor dispatch.

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
    """Scan all pre-active sessions and transition stale ones.

    Called by the background loop. Scans across ALL organizations in
    a single pass.

    State logic (Solden does not email vendors — operators chase from
    their own Gmail; this loop only adjusts session state):
      - 72h since invite → transition to ``blocked`` (escalation
        signal for the AP Manager in Slack)
      - 30 days since invite → transition to ``closed_unsuccessful``,
        revoke all tokens
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
        state = session.get("state") or ""
        invited_at = session.get("invited_at") or ""

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
                    VendorOnboardingState.CLOSED_UNSUCCESSFUL.value,
                    actor_id="agent",
                    reason=f"No vendor response after {_ABANDON_DAYS} days",
                )
                db.revoke_session_tokens(
                    session_id, revoked_by="agent", reason="session_abandoned"
                )
                result.abandonments += 1
                continue

            # Escalate after 72h.
            if hours >= _ESCALATION_72H and state != "blocked":
                from clearledgr.core.vendor_onboarding_states import (
                    VendorOnboardingState,
                )
                db.transition_onboarding_session_state(
                    session_id,
                    VendorOnboardingState.BLOCKED.value,
                    actor_id="agent",
                    reason="No vendor response after 72 hours",
                )
                result.escalations += 1
                continue

        except Exception as exc:
            logger.warning(
                "[onboarding_lifecycle] chase error for session %s: %s",
                session_id, exc,
            )
            result.errors.append(f"{session_id}: {exc}")

    return result


async def activate_vendor_in_erp(
    session_id: str,
    db: Any = None,
) -> ActivationResult:
    """Transition bank_verified → ready_for_erp → active via the ERP.

    Terminal happy path:
      1. Transition to ``ready_for_erp``
      2. Call ``create_vendor()`` on the ERP dispatcher
      3. Store the ERP vendor ID on the session
      4. Transition to ``active``
      5. Revoke all magic-link tokens
      6. Post Slack confirmation to the customer's finance team

    Solden does NOT email the vendor on completion — operators surface
    activation through Slack and their own follow-up.

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

    # Audit event.
    try:
        db.append_audit_event(
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

    # Step 6: Slack activation confirmation — DESIGN_THESIS.md §9.
    # "Agent posts a confirmation to the finance team's Slack channel."
    # Fire-and-forget; Slack outages don't roll back an already-
    # successful ERP activation.
    try:
        from clearledgr.services.slack_notifications import (
            send_vendor_activated_notification,
        )
        erp_system = ""
        try:
            conns = db.get_erp_connections(org_id) if hasattr(db, "get_erp_connections") else []
            if conns:
                erp_system = str(conns[0].get("erp_type") or "").strip()
        except Exception:
            pass
        await send_vendor_activated_notification(
            vendor_name=vendor_name,
            erp_system=erp_system or "ERP",
            erp_vendor_id=erp_vendor_id,
            organization_id=org_id,
        )
    except Exception as slack_exc:
        logger.warning(
            "[onboarding_lifecycle] activation slack post failed (non-fatal): %s",
            slack_exc,
        )

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
    except Exception:
        # Re-raise so the caller can decide whether to retry.
        raise
