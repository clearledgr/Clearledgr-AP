"""Approval revert — generalized reversibility within a bounded window.

Manifesto §"History" promises: "Every override. Every reversal. Every
retry. Reconstructable. Auditable. Reversible."

Until this module, the only reversal Solden actually supported was
the ERP-level ``posted_to_erp -> reversed`` path inside the override
window (see ``services/override_window.py``). This module extends
that pattern to a second class of reversal: an operator-initiated
revert of an approval that has not yet posted to the ERP.

Flow::

    NEEDS_APPROVAL --approve--> APPROVED  (now)
                                    |
                                    | (operator: "wait, undo")
                                    v
                              NEEDS_APPROVAL  (within window)

The window duration is governed by org settings; defaults to 15
minutes. Once the AP item enters ``POSTED_TO_ERP``, this path stops
being available — the ERP-level reversal pattern takes over.

Why this isn't routed through OverrideWindowService:

  * The ERP override-window service is tightly coupled to the
    external ``reverse_bill`` call. An approval revert has no
    external side effect — it's a pure in-Solden state change.
  * Coupling the two would require either dispatching by action_type
    inside attempt_reversal (which bloats a critical path) or
    factoring out a shared base. Both are reasonable but bigger
    refactors. This module reuses the *semantics* (time-bounded
    reversibility + audit + state machine) without entangling
    the ERP path.

A future commit can factor out the shared time-window logic into a
``BoundedReversal`` primitive that both services compose with. For
now, the duplication is small and the boundary is clear.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from clearledgr.core.ap_states import (
    APState,
    transition_or_raise,
)

logger = logging.getLogger(__name__)


DEFAULT_APPROVAL_REVERT_WINDOW_MINUTES = 15
MIN_APPROVAL_REVERT_WINDOW_MINUTES = 1
MAX_APPROVAL_REVERT_WINDOW_MINUTES = 24 * 60  # 24h


# States from which an approval revert is meaningful.
REVERTIBLE_APPROVED_STATES = frozenset({
    APState.APPROVED.value,
    APState.READY_TO_POST.value,
})


@dataclass(frozen=True)
class ApprovalRevertOutcome:
    """Structured result of an attempted approval revert."""

    status: str  # "reverted" | "expired" | "invalid_state" | "not_found"
    ap_item_id: Optional[str]
    new_state: Optional[str] = None
    window_seconds_remaining: int = 0
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "ap_item_id": self.ap_item_id,
            "new_state": self.new_state,
            "window_seconds_remaining": self.window_seconds_remaining,
            "message": self.message,
        }


def _approval_revert_window_minutes(db: Any, organization_id: str) -> int:
    """Read org settings for the approval-revert window duration.

    Lookup: ``settings_json.workflow_controls.approval_revert_window_minutes``.
    Falls back to the module default. Clamped to [MIN, MAX].
    """
    try:
        org = db.get_organization(organization_id) if hasattr(db, "get_organization") else None
    except Exception:
        return DEFAULT_APPROVAL_REVERT_WINDOW_MINUTES
    if not org:
        return DEFAULT_APPROVAL_REVERT_WINDOW_MINUTES
    raw = org.get("settings_json") or org.get("settings") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return DEFAULT_APPROVAL_REVERT_WINDOW_MINUTES
    if not isinstance(raw, dict):
        return DEFAULT_APPROVAL_REVERT_WINDOW_MINUTES
    workflow_controls = raw.get("workflow_controls") or {}
    if not isinstance(workflow_controls, dict):
        return DEFAULT_APPROVAL_REVERT_WINDOW_MINUTES
    try:
        minutes = int(workflow_controls.get("approval_revert_window_minutes") or 0)
    except (TypeError, ValueError):
        return DEFAULT_APPROVAL_REVERT_WINDOW_MINUTES
    if minutes <= 0:
        return DEFAULT_APPROVAL_REVERT_WINDOW_MINUTES
    return max(
        MIN_APPROVAL_REVERT_WINDOW_MINUTES,
        min(MAX_APPROVAL_REVERT_WINDOW_MINUTES, minutes),
    )


def _seconds_remaining(approved_at_iso: str, window_minutes: int, as_of: Optional[datetime] = None) -> int:
    if not approved_at_iso:
        return 0
    try:
        approved_dt = datetime.fromisoformat(str(approved_at_iso).replace("Z", "+00:00"))
    except ValueError:
        return 0
    if approved_dt.tzinfo is None:
        approved_dt = approved_dt.replace(tzinfo=timezone.utc)
    expires = approved_dt + timedelta(minutes=window_minutes)
    now = as_of or datetime.now(timezone.utc)
    return max(0, int((expires - now).total_seconds()))


def attempt_approval_revert(
    *,
    db: Any,
    ap_item_id: str,
    organization_id: str,
    actor_id: str,
    reason: str,
    as_of: Optional[datetime] = None,
) -> ApprovalRevertOutcome:
    """Revert an approval back to ``needs_approval`` within the window.

    Returns an :class:`ApprovalRevertOutcome`. Caller maps to HTTP
    status: ``reverted`` → 200, ``expired`` → 409, ``invalid_state``
    → 409, ``not_found`` → 404.
    """
    item = db.get_ap_item(ap_item_id)
    if not item or str(item.get("organization_id") or "") != organization_id:
        return ApprovalRevertOutcome(
            status="not_found",
            ap_item_id=ap_item_id,
            message="AP item not found in this organization.",
        )

    current_state = str(item.get("state") or "")
    if current_state not in REVERTIBLE_APPROVED_STATES:
        return ApprovalRevertOutcome(
            status="invalid_state",
            ap_item_id=ap_item_id,
            new_state=current_state,
            message=(
                f"Approval revert only valid from {sorted(REVERTIBLE_APPROVED_STATES)}; "
                f"current state is {current_state!r}."
            ),
        )

    approved_at = str(item.get("approved_at") or "")
    if not approved_at:
        # Distinct from 'expired': the window can't be evaluated at all
        # because the data needed to compute it (approved_at) is missing.
        # Conflating this with 'expired' would blame the operator for
        # missing a window that was never measurable — typically on
        # legacy AP items approved before approved_at was populated.
        # Auditors reading the trail need the cases distinguishable.
        return ApprovalRevertOutcome(
            status="invalid_state",
            ap_item_id=ap_item_id,
            new_state=current_state,
            window_seconds_remaining=0,
            message=(
                "No approval timestamp recorded for this AP item — "
                "the revert window cannot be evaluated. Use the manual "
                "exception path for human review."
            ),
        )

    window_minutes = _approval_revert_window_minutes(db, organization_id)
    remaining = _seconds_remaining(approved_at, window_minutes, as_of=as_of)
    if remaining <= 0:
        return ApprovalRevertOutcome(
            status="expired",
            ap_item_id=ap_item_id,
            new_state=current_state,
            window_seconds_remaining=0,
            message=(
                f"Approval revert window ({window_minutes} min) has expired. "
                "Use the ERP-level reversal path if the bill has already posted, "
                "or open a manual exception for human review."
            ),
        )

    # State-machine validation: the transition is on the VALID list,
    # but be defensive — a future state-machine edit could remove it.
    transition_or_raise(current_state, APState.NEEDS_APPROVAL.value, ap_item_id=ap_item_id)

    db.update_ap_item(
        ap_item_id,
        state=APState.NEEDS_APPROVAL.value,
        approved_at=None,
        approved_by=None,
    )

    # Defensive cleanup of state that downstream observers of
    # READY_TO_POST might rely on. As of 2026-05-14 the only such
    # state is the metadata.payment_scheduled flag set by
    # CoordinationEngine._handle_schedule_payment — which actually
    # fires AFTER post_bill (POSTED_TO_ERP), not on READY_TO_POST,
    # so in practice the flag is never set when this revert runs.
    # Clearing it anyway is belt-and-braces against a future state-
    # machine refactor that moves the flag earlier in the sequence.
    # Routed through ``update_ap_item_metadata_remove_keys`` rather
    # than a read-modify-write because the latter would clobber any
    # concurrent metadata write that landed between our read at the
    # top of the function and the write here. The helper does
    # SELECT ... FOR UPDATE so concurrent merges serialize.
    # The webhook layer (services/webhook_delivery.py) does NOT need
    # a counter-signal — every audit row fans out via
    # dispatch_audit_webhooks, so subscribers receive an
    # approval_reverted event with from_state=READY_TO_POST,
    # to_state=NEEDS_APPROVAL automatically.
    if hasattr(db, "update_ap_item_metadata_remove_keys"):
        try:
            db.update_ap_item_metadata_remove_keys(
                ap_item_id,
                keys=["payment_scheduled", "payment_scheduled_at"],
            )
        except Exception as exc:
            logger.warning(
                "[approval_revert] metadata cleanup failed for %s: %s",
                ap_item_id, exc,
            )
    db.append_audit_event({
        "box_id": ap_item_id,
        "box_type": "ap_item",
        "event_type": "approval_reverted",
        "from_state": current_state,
        "to_state": APState.NEEDS_APPROVAL.value,
        "actor_type": "user",
        "actor_id": actor_id,
        "organization_id": organization_id,
        "decision_reason": reason or "approval revert within window",
        "payload_json": {
            "approved_at": approved_at,
            "window_minutes": window_minutes,
            "seconds_remaining_at_revert": remaining,
            "reason": reason,
        },
        "idempotency_key": f"approval-revert:{ap_item_id}:{approved_at}",
    })
    logger.info(
        "[ApprovalRevert] AP item %s reverted from %s back to needs_approval "
        "(actor=%s, remaining=%ds, window=%dm)",
        ap_item_id, current_state, actor_id, remaining, window_minutes,
    )
    return ApprovalRevertOutcome(
        status="reverted",
        ap_item_id=ap_item_id,
        new_state=APState.NEEDS_APPROVAL.value,
        window_seconds_remaining=remaining,
        message="Approval reverted; AP item is back in needs_approval.",
    )
