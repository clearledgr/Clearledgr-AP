"""Dispute reopen ceremony (Wave 6 / H3).

A bill that has already been closed / paid sometimes needs to be
revisited — vendor flags an overcharge, duplicate billing, fraud,
or a returned good post-payment. The AP cycle reference doc Stage 9
mandates a CONTROLLED reopen so the audit trail stays intact:

  * The **original** AP item stays in its terminal state (closed,
    payment_executed, or reversed). Its row never moves backward —
    that's a SOX-compliance requirement; the audit_events trigger
    enforces append-only at the DB layer too.
  * A **new** AP item is created representing the dispute
    correction. Linked to the original via
    ``metadata['dispute_reopen']['original_ap_item_id']``.
  * The new item walks the normal AP workflow but tagged with a
    ``disposition='dispute_credit'`` flag and a negative-direction
    intent that the JE preview / posting layer surfaces as a
    credit note rather than a fresh bill.
  * A ``disputes`` row tracks the timeline alongside the existing
    DisputeService surface.

Two reopen kinds:
  * ``credit_note`` — vendor agrees to credit; we'll receive the
    refund. New AP item is essentially a NEGATIVE invoice.
  * ``rebill`` — original was wrong; vendor cancels + reissues at
    a different amount. New AP item is the corrected positive
    invoice.

Either way, the canonical audit chain links closed-original →
new-correction so an auditor can reconstruct WHY the credit was
recorded.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


_VALID_REOPEN_KINDS = frozenset({"credit_note", "rebill"})

_REOPENABLE_TERMINAL_STATES = frozenset({
    "closed", "payment_executed", "reversed",
})


class DisputeReopenError(Exception):
    """Caller surfaces as 4xx."""


class OriginalNotReopenableError(DisputeReopenError):
    """Original AP item must be in a terminal/post-payment state."""


@dataclass
class DisputeReopenResult:
    original_ap_item_id: str
    correction_ap_item_id: str
    reopen_kind: str
    correction_amount: float
    dispute_id: Optional[str] = None
    audit_event_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_ap_item_id": self.original_ap_item_id,
            "correction_ap_item_id": self.correction_ap_item_id,
            "reopen_kind": self.reopen_kind,
            "correction_amount": self.correction_amount,
            "dispute_id": self.dispute_id,
            "audit_event_id": self.audit_event_id,
        }


def _normalize_meta(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item.get("metadata")
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Entry point ────────────────────────────────────────────────────


def reopen_for_dispute(
    db,
    *,
    organization_id: str,
    original_ap_item_id: str,
    reopen_kind: str,
    correction_amount: float,
    reason: str,
    actor_id: str,
    rebill_invoice_number: Optional[str] = None,
) -> DisputeReopenResult:
    """Spawn a correction AP item for the disputed original.

    ``correction_amount`` is the GROSS amount of the correction. For
    credit_note, this is what the vendor will refund (Clearledgr
    treats it as a negative-impact AP item via metadata, the JE
    posting layer translates to a Cr expense / Dr AP entry).

    ``reopen_kind``:
      ``credit_note``: net negative impact, original stays closed
      ``rebill``: vendor reissuing — new positive invoice replacing
        the original

    Raises:
      ValueError on missing AP item / cross-org access / invalid
        reopen_kind.
      OriginalNotReopenableError when the original isn't in a
        post-payment terminal state.
    """
    if reopen_kind not in _VALID_REOPEN_KINDS:
        raise ValueError(
            f"reopen_kind must be one of {sorted(_VALID_REOPEN_KINDS)}; "
            f"got {reopen_kind!r}"
        )
    if not reason or not reason.strip():
        raise ValueError("reason required")
    if correction_amount <= 0:
        raise ValueError(
            "correction_amount must be positive (signed direction is "
            "set by reopen_kind)"
        )

    original = db.get_ap_item(original_ap_item_id)
    if (
        original is None
        or original.get("organization_id") != organization_id
    ):
        raise ValueError(f"ap_item_not_found:{original_ap_item_id!r}")

    state = (original.get("state") or "").lower()
    if state not in _REOPENABLE_TERMINAL_STATES:
        raise OriginalNotReopenableError(
            f"original AP item is in state {state!r}; reopen requires "
            f"one of {sorted(_REOPENABLE_TERMINAL_STATES)}"
        )

    # Prevent double-reopen by checking the correction-link metadata.
    orig_meta = _normalize_meta(original)
    existing_link = orig_meta.get("dispute_reopen") or {}
    existing_correction_id = existing_link.get("correction_ap_item_id")
    if existing_correction_id:
        # Idempotent: same call twice returns the existing correction.
        return DisputeReopenResult(
            original_ap_item_id=original_ap_item_id,
            correction_ap_item_id=existing_correction_id,
            reopen_kind=existing_link.get("kind") or reopen_kind,
            correction_amount=float(
                existing_link.get("correction_amount") or correction_amount
            ),
            dispute_id=existing_link.get("dispute_id"),
        )

    # 1. Create the correction AP item in 'received' state.
    correction_id = f"AP-{uuid.uuid4().hex}"
    invoice_number = (
        rebill_invoice_number
        or f"DISPUTE-{original.get('invoice_number') or original_ap_item_id}"
    )
    correction_meta: Dict[str, Any] = {
        "intake_source": "dispute_reopen",
        "dispute_reopen": {
            "kind": reopen_kind,
            "original_ap_item_id": original_ap_item_id,
            "original_invoice_number": original.get("invoice_number"),
            "correction_amount": float(correction_amount),
            "reason": reason,
        },
    }
    correction_payload = {
        "id": correction_id,
        "organization_id": organization_id,
        "vendor_name": original.get("vendor_name"),
        "amount": float(correction_amount),
        "currency": original.get("currency"),
        "invoice_number": invoice_number,
        "state": "received",
        "user_id": actor_id,
        "metadata": correction_meta,
        "po_number": original.get("po_number") or "",
        "bill_country": original.get("bill_country"),
    }
    db.create_ap_item(correction_payload)

    # 2. Stamp the back-link on the original. The AP-items table is
    #    NOT SOX-immutable (only audit_events is) — metadata updates
    #    are explicitly allowed for cross-references like this.
    orig_meta["dispute_reopen"] = {
        "kind": reopen_kind,
        "correction_ap_item_id": correction_id,
        "correction_amount": float(correction_amount),
        "reason": reason,
        "reopened_at": _now_iso(),
        "reopened_by": actor_id,
    }
    try:
        db.update_ap_item(
            original_ap_item_id,
            metadata=orig_meta,
            _actor_type="user",
            _actor_id=actor_id,
            _source="dispute_reopen",
            _decision_reason=reason,
        )
    except Exception:
        logger.exception(
            "dispute_reopen: stamp back-link on original failed",
        )

    # 3. Open a dispute row alongside (uses the existing
    #    DisputeService surface so the AP-team dispute queue picks
    #    it up like any other vendor-side dispute).
    dispute_id: Optional[str] = None
    try:
        from clearledgr.services.dispute_service import (
            get_dispute_service,
        )
        dispute = get_dispute_service(organization_id).open_dispute(
            ap_item_id=original_ap_item_id,
            dispute_type=f"post_close_{reopen_kind}",
            description=reason,
            vendor_name=original.get("vendor_name") or "",
        )
        dispute_id = dispute.get("id")
    except Exception:
        logger.exception(
            "dispute_reopen: dispute row create failed (non-fatal)"
        )

    # 4. Append-only audit events on BOTH boxes.
    audit_event_id: Optional[str] = None
    try:
        ev = db.append_audit_event({
            "ap_item_id": original_ap_item_id,
            "box_id": original_ap_item_id,
            "box_type": "ap_item",
            "event_type": "dispute_reopened",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": organization_id,
            "source": "dispute_reopen",
            "decision_reason": reason,
            "idempotency_key": (
                f"dispute_reopen:{organization_id}:{original_ap_item_id}"
            ),
            "metadata": {
                "reopen_kind": reopen_kind,
                "correction_ap_item_id": correction_id,
                "correction_amount": float(correction_amount),
                "dispute_id": dispute_id,
            },
        })
        audit_event_id = (ev or {}).get("id")
    except Exception:
        logger.exception("dispute_reopen: audit on original failed")

    try:
        db.append_audit_event({
            "ap_item_id": correction_id,
            "box_id": correction_id,
            "box_type": "ap_item",
            "event_type": "dispute_correction_created",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": organization_id,
            "source": "dispute_reopen",
            "decision_reason": reason,
            "idempotency_key": (
                f"dispute_correction_created:{organization_id}:{correction_id}"
            ),
            "metadata": {
                "reopen_kind": reopen_kind,
                "original_ap_item_id": original_ap_item_id,
                "correction_amount": float(correction_amount),
                "dispute_id": dispute_id,
            },
        })
    except Exception:
        logger.exception("dispute_reopen: audit on correction failed")

    return DisputeReopenResult(
        original_ap_item_id=original_ap_item_id,
        correction_ap_item_id=correction_id,
        reopen_kind=reopen_kind,
        correction_amount=float(correction_amount),
        dispute_id=dispute_id,
        audit_event_id=audit_event_id,
    )


def get_correction_chain(
    db, *, organization_id: str, ap_item_id: str,
) -> Dict[str, Any]:
    """Return the dispute-reopen chain anchored on this AP item.

    Useful for the workspace UI that shows "this bill was reopened
    on 2026-04-29; correction AP-XXX is in needs_approval".
    """
    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        return {}
    meta = _normalize_meta(item)
    block = meta.get("dispute_reopen") or {}
    return dict(block)
