"""Reclassification JE generator (Wave 6 / H4).

After a bill has posted to the ERP, the operator sometimes realises
the expense was coded to the wrong GL account (e.g. server hosting
landed in "Office supplies" instead of "Cloud infrastructure").
Standard accounting practice is NOT to amend the original posting —
that breaks the audit trail. Instead, post a RECLASSIFICATION
entry that:

  * Debits the correct GL account.
  * Credits the wrong (originally posted) GL account.
  * Same gross amount, same currency, same posting date as the
    original (so per-period totals stay correct).
  * Memo lines reference the original bill's id + JE id so the
    auditor can trace.

This module is the **proposal generator**: produce the canonical
JE shape ready for the org's ERP integration to post. It does NOT
amend the original AP item — it's purely additive. The operator
review surface (workspace) renders the proposal; the post action
calls the existing erp_router.post_to_* with the JE payload.

Audit guarantees:

  * Original bill's metadata gets a back-link to the
    reclassification id (mutable, additive — same pattern as H3).
  * Both the proposal generation and any subsequent post emit
    audit events keyed by reclassification id so re-runs are
    idempotent.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_TWO_PLACES = Decimal("0.01")


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(_TWO_PLACES)
    return Decimal(str(value)).quantize(_TWO_PLACES)


_RECLASSIFIABLE_STATES = frozenset({
    "posted_to_erp", "awaiting_payment", "payment_in_flight",
    "payment_executed", "closed",
})


class ReclassificationError(Exception):
    """Caller surfaces as 4xx."""


class NotPostedError(ReclassificationError):
    """Bill must have posted to ERP before reclassification."""


@dataclass
class ReclassificationLine:
    direction: str            # "debit" | "credit"
    account_code: str
    account_label: str
    amount: Decimal
    currency: str
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "direction": self.direction,
            "account_code": self.account_code,
            "account_label": self.account_label,
            "amount": float(self.amount),
            "currency": self.currency,
            "description": self.description,
        }


@dataclass
class ReclassificationProposal:
    reclassification_id: str
    ap_item_id: str
    organization_id: str
    erp_type: str
    currency: str
    amount: Decimal
    from_account: str
    to_account: str
    reason: str
    posting_date: str
    original_invoice_number: Optional[str] = None
    original_erp_journal_entry_id: Optional[str] = None
    lines: List[ReclassificationLine] = field(default_factory=list)
    debit_total: Decimal = Decimal("0.00")
    credit_total: Decimal = Decimal("0.00")
    balanced: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reclassification_id": self.reclassification_id,
            "ap_item_id": self.ap_item_id,
            "organization_id": self.organization_id,
            "erp_type": self.erp_type,
            "currency": self.currency,
            "amount": float(self.amount),
            "from_account": self.from_account,
            "to_account": self.to_account,
            "reason": self.reason,
            "posting_date": self.posting_date,
            "original_invoice_number": self.original_invoice_number,
            "original_erp_journal_entry_id": self.original_erp_journal_entry_id,
            "lines": [ln.to_dict() for ln in self.lines],
            "debit_total": float(self.debit_total),
            "credit_total": float(self.credit_total),
            "balanced": self.balanced,
        }


# ── Builder ────────────────────────────────────────────────────────


def build_reclassification_proposal(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    to_account: str,
    reason: str,
    from_account: Optional[str] = None,
    amount: Optional[float] = None,
    posting_date: Optional[str] = None,
    erp_type: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> ReclassificationProposal:
    """Generate the reclassification JE proposal.

    ``from_account`` defaults to the AP item's stored expense GL
    code (looked up via the org's ``gl_account_map['expenses']``
    or DEFAULT_ACCOUNT_MAP).

    ``amount`` defaults to the AP item's ``net_amount`` (E2 split)
    if available; falls back to ``amount`` (gross) so legacy bills
    still work. The operator can override with a partial-reclass
    amount if only part of the bill is mis-coded.

    Raises:
      ValueError on missing AP item / cross-org / invalid account /
        non-positive amount / missing reason.
      NotPostedError when the bill hasn't posted to ERP yet.
    """
    if not reason or not reason.strip():
        raise ValueError("reason required")
    if not to_account or not to_account.strip():
        raise ValueError("to_account required")

    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        raise ValueError(f"ap_item_not_found:{ap_item_id!r}")

    state = (item.get("state") or "").lower()
    if state not in _RECLASSIFIABLE_STATES:
        raise NotPostedError(
            f"AP item state {state!r} is not eligible for reclassification "
            f"(requires posted_to_erp or later)"
        )

    from clearledgr.integrations.erp_router import (
        _get_org_gl_map,
        get_account_code,
    )

    resolved_erp = erp_type or _resolve_org_erp_type(
        db, organization_id,
    ) or "xero"
    gl_map = _get_org_gl_map(organization_id) or {}

    resolved_from = (
        from_account
        or gl_map.get("expenses")
        or get_account_code(resolved_erp, "expenses", gl_map)
    )
    resolved_to = to_account.strip()
    if resolved_from == resolved_to:
        raise ValueError(
            f"to_account ({resolved_to!r}) must differ from "
            f"from_account ({resolved_from!r})"
        )

    # Amount source priority: explicit override > net_amount > amount.
    if amount is not None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        gross = _money(amount)
    else:
        if item.get("net_amount") is not None:
            gross = _money(item["net_amount"])
        else:
            gross = _money(item.get("amount") or 0)
    if gross <= 0:
        raise ValueError("ap_item has no usable amount for reclassification")

    currency = str(item.get("currency") or "USD")

    posting = (
        posting_date
        or item.get("erp_posted_at")
        or datetime.now(timezone.utc).isoformat()
    )

    rid = f"RC-{uuid.uuid4().hex[:24]}"
    invoice_no = item.get("invoice_number") or item.get("id")

    lines: List[ReclassificationLine] = [
        ReclassificationLine(
            direction="debit",
            account_code=resolved_to,
            account_label="Reclassification target",
            amount=gross,
            currency=currency,
            description=(
                f"Reclassify expense from {resolved_from} to {resolved_to} "
                f"(orig invoice {invoice_no}). Reason: {reason}"
            ),
        ),
        ReclassificationLine(
            direction="credit",
            account_code=resolved_from,
            account_label="Reclassification source",
            amount=gross,
            currency=currency,
            description=(
                f"Offset original expense posting on bill "
                f"{invoice_no}"
            ),
        ),
    ]

    proposal = ReclassificationProposal(
        reclassification_id=rid,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
        erp_type=resolved_erp,
        currency=currency,
        amount=gross,
        from_account=resolved_from,
        to_account=resolved_to,
        reason=reason,
        posting_date=posting,
        original_invoice_number=item.get("invoice_number"),
        original_erp_journal_entry_id=item.get("erp_journal_entry_id"),
        lines=lines,
        debit_total=gross,
        credit_total=gross,
        balanced=True,
    )

    return proposal


# ── Persist + audit ───────────────────────────────────────────────


def record_reclassification(
    db,
    *,
    organization_id: str,
    proposal: ReclassificationProposal,
    actor_id: str,
) -> Dict[str, Any]:
    """Persist the back-link on the AP item + audit-event the
    reclassification.

    Does NOT post to ERP — that's the integration layer. This is
    the canonical record of the proposal having been accepted by
    the operator (clicked "post"); the ERP-side post lives in a
    sibling integration call that consumes the proposal.
    """
    import json as _json

    item = db.get_ap_item(proposal.ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        raise ValueError(
            f"ap_item_not_found:{proposal.ap_item_id!r}"
        )

    raw_meta = item.get("metadata")
    if isinstance(raw_meta, str):
        try:
            meta = _json.loads(raw_meta) if raw_meta else {}
        except Exception:
            meta = {}
    elif isinstance(raw_meta, dict):
        meta = raw_meta
    else:
        meta = {}

    history = meta.get("reclassifications") or []
    if not isinstance(history, list):
        history = []

    # Idempotency: if a reclassification with this exact
    # (from_account, to_account, amount, reason) already exists,
    # return the existing one rather than recording a duplicate.
    for prior in history:
        if (
            prior.get("from_account") == proposal.from_account
            and prior.get("to_account") == proposal.to_account
            and float(prior.get("amount") or 0) == float(proposal.amount)
            and (prior.get("reason") or "") == proposal.reason
        ):
            return prior

    record = {
        "reclassification_id": proposal.reclassification_id,
        "from_account": proposal.from_account,
        "to_account": proposal.to_account,
        "amount": float(proposal.amount),
        "currency": proposal.currency,
        "reason": proposal.reason,
        "posting_date": proposal.posting_date,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "recorded_by": actor_id,
    }
    history.append(record)
    meta["reclassifications"] = history

    db.update_ap_item(
        proposal.ap_item_id,
        metadata=meta,
        _actor_type="user",
        _actor_id=actor_id,
        _source="reclassification",
        _decision_reason=proposal.reason,
    )

    try:
        db.append_audit_event({
            "ap_item_id": proposal.ap_item_id,
            "box_id": proposal.ap_item_id,
            "box_type": "ap_item",
            "event_type": "reclassification_recorded",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": organization_id,
            "source": "reclassification",
            "decision_reason": proposal.reason,
            "idempotency_key": (
                f"reclassification:{organization_id}:"
                f"{proposal.reclassification_id}"
            ),
            "metadata": {
                "reclassification_id": proposal.reclassification_id,
                "from_account": proposal.from_account,
                "to_account": proposal.to_account,
                "amount": float(proposal.amount),
                "currency": proposal.currency,
                "posting_date": proposal.posting_date,
                "original_erp_journal_entry_id": (
                    proposal.original_erp_journal_entry_id
                ),
            },
        })
    except Exception:
        logger.exception(
            "reclassification: audit emit failed ap_item=%s",
            proposal.ap_item_id,
        )

    return record


def list_reclassifications(
    db, *, organization_id: str, ap_item_id: str,
) -> List[Dict[str, Any]]:
    """Return the history of reclassifications recorded against
    one AP item, newest first."""
    import json as _json

    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        return []
    raw = item.get("metadata")
    if isinstance(raw, str):
        try:
            meta = _json.loads(raw) if raw else {}
        except Exception:
            return []
    elif isinstance(raw, dict):
        meta = raw
    else:
        return []
    history = meta.get("reclassifications") or []
    if not isinstance(history, list):
        return []
    return list(reversed(history))


# ── Renderer ──────────────────────────────────────────────────────


def render_reclassification_text(proposal: ReclassificationProposal) -> str:
    width = 78
    out: List[str] = []
    out.append(
        f"Reclassification JE — bill "
        f"{proposal.original_invoice_number or proposal.ap_item_id}"
    )
    out.append(
        f"From {proposal.from_account} -> {proposal.to_account}    "
        f"Amount {float(proposal.amount):,.2f} {proposal.currency}"
    )
    out.append(f"Posting date: {proposal.posting_date}")
    out.append("-" * width)
    for ln in proposal.lines:
        prefix = "Dr  " if ln.direction == "debit" else "Cr  "
        out.append(
            f"{prefix}{ln.account_label} ({ln.account_code})    "
            f"{float(ln.amount):>10.2f} {ln.currency}"
        )
    out.append("-" * width)
    flag = "balanced" if proposal.balanced else "unbalanced"
    out.append(
        f"Debit: {float(proposal.debit_total):,.2f}    "
        f"Credit: {float(proposal.credit_total):,.2f}    {flag}"
    )
    out.append("")
    out.append(f"Reason: {proposal.reason}")
    if proposal.original_erp_journal_entry_id:
        out.append(
            f"Original ERP JE: {proposal.original_erp_journal_entry_id}"
        )
    return "\n".join(out)


# ── Internals ─────────────────────────────────────────────────────


def _resolve_org_erp_type(db, organization_id: str) -> Optional[str]:
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT erp_type FROM erp_connections "
                "WHERE organization_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (organization_id,),
            )
            row = cur.fetchone()
            if row:
                return str(dict(row).get("erp_type") or "").lower()
    except Exception:
        return None
    return None
