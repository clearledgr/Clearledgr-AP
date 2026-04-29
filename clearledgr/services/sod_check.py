"""Segregation-of-duties enforcement (Wave 1 / D1).

Per AICPA AP audit guidance + IOFM internal control matrix:
"The requester and approver must be different people; the approver
and the AP processor must be different people."

Today (pre-D1): nothing in the approve path enforces this. A user
with ``approve_invoices`` permission can approve invoices they
themselves coded, edited, or originally requested. SOC 2 + every
external auditor flags this.

This module is the central SOD check called at every approve
action. It resolves three identities:

  * **Approver** — passed in by the caller (the user clicking
    Approve).
  * **Processor** — derived from audit_events: the most recent
    user-actor event on this AP item that is NOT an approve /
    reject decision and NOT a system-emitted state transition.
    Typically the AP Clerk who corrected GL coding, ran field
    review, or resolved a validation exception.
  * **Requester** — the ``user_id`` on the AP item, which in our
    current model is the Gmail account owner whose inbox received
    the invoice. Proxy for "who asked for this in our system." When
    the AP item is PO-driven, the PO requester is preferred; the
    PO model isn't yet wired into the resolver in this commit.

SOD checks:
  * **approver_is_processor** (high severity) — the approver did
    the data entry. AICPA accuracy + authorization assertions both
    fail.
  * **approver_is_requester** (medium severity) — the approver is
    the same identity that received / requested the invoice.
    Best-effort because non-PO invoices have a weak requester
    proxy.

Configurable per-tenant via ``settings_json["sod_enforcement"]``
(default True). Operator can downgrade to a *warn-only* mode by
setting it to ``"warn"`` — the check still runs and audit-emits,
but doesn't block the approve action.

The approve handler in invoice_posting.py calls ``check_sod`` at
the top and returns a structured 403 with reason ``sod_violation``
when the result is ``allowed=False``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ─── Audit-event types we treat as "processing" activity ────────────
# These are the user actions on an AP item that count as processor
# touches (data entry, GL coding, field correction, exception
# resolution). Approval / rejection events are NOT processor
# events — they're decision events.
_PROCESSOR_EVENT_TYPES = frozenset({
    "field_review_corrected",
    "field_review_overridden",
    "gl_correction_recorded",
    "ap_item_edited",
    "ap_item_resubmitted",
    "exception_resolved",
    "exception_acknowledged",
    "vendor_assigned",
    "po_match_override",
    "match_exception_resolved",
    "needs_info_resolved",
})

_DECISION_EVENT_TYPES = frozenset({
    "invoice_approved",
    "invoice_rejected",
    "invoice_routed_for_approval",
    "approval_action_lock_acquired",
})


@dataclass(frozen=True)
class SODCheckResult:
    """Structured output of a SOD check."""

    allowed: bool
    mode: str  # "enforced" | "warn" | "disabled"
    violation_reason: Optional[str]
    processor_user_id: Optional[str]
    processor_email: Optional[str]
    requester_user_id: Optional[str]
    approver_user_id: Optional[str]
    approver_email: Optional[str]
    message: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "mode": self.mode,
            "violation_reason": self.violation_reason,
            "processor_user_id": self.processor_user_id,
            "processor_email": self.processor_email,
            "requester_user_id": self.requester_user_id,
            "approver_user_id": self.approver_user_id,
            "approver_email": self.approver_email,
            "message": self.message,
        }


def check_sod(
    db,
    *,
    ap_item_id: str,
    approver_user_id: Optional[str],
    approver_email: Optional[str],
    organization_id: str,
) -> SODCheckResult:
    """Run the SOD check at approve time.

    Idempotent + side-effect-free. Caller (the approve handler)
    decides what to do with ``allowed=False``: typically a 403
    with ``violation_reason`` in the response and an audit emit
    of ``sod_violation_blocked``.

    Tolerant of missing data — if the AP item has no audit events
    yet (synthetic, fresh, brand-new), processor is None and the
    check passes the processor-gate (you can't have processed it
    if no processor event exists).
    """
    mode = _resolve_mode(db, organization_id)
    approver_user_id = (approver_user_id or "").strip() or None
    approver_email = (approver_email or "").strip().lower() or None

    if mode == "disabled":
        return SODCheckResult(
            allowed=True, mode="disabled",
            violation_reason=None,
            processor_user_id=None, processor_email=None,
            requester_user_id=None,
            approver_user_id=approver_user_id,
            approver_email=approver_email,
            message="SOD enforcement disabled for this organization.",
        )

    # Resolve requester proxy from the AP item itself.
    requester_user_id = _resolve_requester(db, ap_item_id)

    # Resolve processor from audit events.
    processor_user_id, processor_email = _resolve_processor(
        db, ap_item_id, organization_id,
    )

    violation: Optional[str] = None
    message: Optional[str] = None

    if processor_user_id and approver_user_id and (
        processor_user_id == approver_user_id
    ):
        violation = "approver_is_processor"
        message = (
            f"Approver {approver_email or approver_user_id} also processed "
            f"this AP item. Segregation of duties requires a separate "
            f"approver."
        )
    elif processor_email and approver_email and (
        processor_email.lower() == approver_email.lower()
    ):
        # Email-only match — covers the case where user_id is missing
        # (legacy AP items, system actors with synthetic ids).
        violation = "approver_is_processor"
        message = (
            f"Approver {approver_email} also processed this AP item. "
            f"Segregation of duties requires a separate approver."
        )
    elif requester_user_id and approver_user_id and (
        requester_user_id == approver_user_id
    ):
        violation = "approver_is_requester"
        message = (
            f"Approver {approver_email or approver_user_id} originally "
            f"received this invoice. Segregation of duties requires a "
            f"different approver from the requester."
        )

    if violation is None:
        return SODCheckResult(
            allowed=True, mode=mode, violation_reason=None,
            processor_user_id=processor_user_id,
            processor_email=processor_email,
            requester_user_id=requester_user_id,
            approver_user_id=approver_user_id,
            approver_email=approver_email,
            message=None,
        )

    # Violation — allow only when in warn-only mode.
    allowed = (mode == "warn")
    return SODCheckResult(
        allowed=allowed, mode=mode, violation_reason=violation,
        processor_user_id=processor_user_id,
        processor_email=processor_email,
        requester_user_id=requester_user_id,
        approver_user_id=approver_user_id,
        approver_email=approver_email,
        message=message,
    )


# ─── Internals ──────────────────────────────────────────────────────


def _resolve_mode(db, organization_id: str) -> str:
    """Read ``settings_json["sod_enforcement"]``.

    Accepted values:
      * True / "true" / "on" / "enforced" → ``"enforced"``
      * "warn" / "warn_only"             → ``"warn"``
      * False / "false" / "off" / "disabled" → ``"disabled"``
      * Missing / unparseable            → ``"enforced"`` (safe default)
    """
    try:
        org = db.get_organization(organization_id) or {}
    except Exception:
        return "enforced"
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            import json
            settings = json.loads(settings)
        except Exception:
            return "enforced"
    if not isinstance(settings, dict):
        return "enforced"
    raw = settings.get("sod_enforcement")
    if raw is None:
        return "enforced"
    if isinstance(raw, bool):
        return "enforced" if raw else "disabled"
    token = str(raw).strip().lower()
    if token in ("warn", "warn_only", "warning"):
        return "warn"
    if token in ("false", "0", "no", "off", "disabled"):
        return "disabled"
    return "enforced"


def _resolve_requester(db, ap_item_id: str) -> Optional[str]:
    """Resolve the requester user_id for an AP item.

    Today: ap_items.user_id is the Gmail OAuth user whose inbox
    received the invoice. That's our proxy. PO-driven invoices in a
    future commit will prefer the PO requester.
    """
    if not hasattr(db, "get_ap_item"):
        return None
    try:
        item = db.get_ap_item(ap_item_id)
    except Exception:
        return None
    if not item:
        return None
    raw = item.get("user_id")
    return str(raw).strip() if raw else None


def _resolve_processor(
    db, ap_item_id: str, organization_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """Find the most-recent processor (user) for an AP item.

    Reads audit_events filtered to user-actor events that are
    processing actions (NOT approve/reject decisions). Returns
    ``(user_id, email)`` of the latest such event, or
    ``(None, None)`` if no processor activity exists.
    """
    if not hasattr(db, "search_audit_events"):
        return (None, None)
    try:
        # search_audit_events returns newest-first by default; no
        # ``order`` kwarg needed (and passing one would TypeError).
        out = db.search_audit_events(
            organization_id=organization_id,
            box_id=ap_item_id,
            box_type="ap_item",
            event_types=list(_PROCESSOR_EVENT_TYPES),
            limit=10,
        )
    except Exception as exc:
        logger.warning(
            "[sod_check] search_audit_events failed for ap_item=%s: %s",
            ap_item_id, exc,
        )
        return (None, None)

    events = (out or {}).get("events") or []
    for event in events:
        actor_type = str(event.get("actor_type") or "").lower()
        if actor_type != "user":
            continue
        actor_id = str(event.get("actor_id") or "").strip() or None
        if not actor_id:
            continue
        # Try to extract email from payload — many events stash the
        # actor's email in payload_json.actor_email.
        payload = event.get("payload_json") or {}
        if isinstance(payload, str):
            try:
                import json
                payload = json.loads(payload)
            except Exception:
                payload = {}
        email = None
        if isinstance(payload, dict):
            email = payload.get("actor_email") or payload.get("email")
        return (actor_id, (str(email).lower() if email else None))
    return (None, None)
