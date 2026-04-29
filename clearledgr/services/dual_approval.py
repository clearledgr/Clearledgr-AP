"""Dual approval (two-person rule) service (Wave 6 / H1).

For high-value bills, regulatory + internal-control practice (SOX,
COSO, IIA) requires two distinct approvers. This module:

  * Reads the dual-approval threshold from
    ``settings_json[routing_thresholds][dual_approval_threshold]``
    (set per-org via the threshold-policy API). When the bill's
    gross amount is at or above the threshold, the first approve
    transitions to ``needs_second_approval`` rather than
    ``approved``.

  * Enforces ``second_approver != first_approver``. Self-approval
    is rejected with ``DualApprovalSelfApprovalError`` so the
    audit trail records the attempt without advancing state.

  * Persists ``first_approver`` + ``first_approved_at`` on the AP
    item's metadata so the second approver sees who already
    signed.

  * Emits explicit audit events at each step:
    ``dual_approval_first_signature``, ``dual_approval_second_signature``,
    ``dual_approval_self_approval_blocked``,
    ``dual_approval_revoked``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


_DEFAULT_DUAL_APPROVAL_DISABLED = float("inf")


class DualApprovalError(Exception):
    """Base — caller surfaces as 4xx."""


class DualApprovalSelfApprovalError(DualApprovalError):
    """Second approver must differ from first."""


class DualApprovalNotPendingError(DualApprovalError):
    """AP item is not in needs_second_approval — no second sig to give."""


class DualApprovalRequesterApprovalError(DualApprovalError):
    """The bill's requester (creator) cannot self-approve."""


@dataclass
class DualApprovalResult:
    ap_item_id: str
    new_state: str
    first_approver: Optional[str] = None
    first_approved_at: Optional[str] = None
    second_approver: Optional[str] = None
    second_approved_at: Optional[str] = None
    requires_second_signature: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ap_item_id": self.ap_item_id,
            "new_state": self.new_state,
            "first_approver": self.first_approver,
            "first_approved_at": self.first_approved_at,
            "second_approver": self.second_approver,
            "second_approved_at": self.second_approved_at,
            "requires_second_signature": self.requires_second_signature,
        }


# ── Threshold lookup ───────────────────────────────────────────────


def get_dual_approval_threshold(db, organization_id: str) -> float:
    """Return the org's dual-approval threshold. Returns infinity
    (effectively disabled) when not configured."""
    try:
        org = db.get_organization(organization_id) or {}
    except Exception:
        return _DEFAULT_DUAL_APPROVAL_DISABLED
    settings: Any = org.get("settings") or org.get("settings_json") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except (ValueError, TypeError):
            settings = {}
    if not isinstance(settings, dict):
        return _DEFAULT_DUAL_APPROVAL_DISABLED
    block = settings.get("routing_thresholds") or {}
    if not isinstance(block, dict):
        return _DEFAULT_DUAL_APPROVAL_DISABLED
    raw = block.get("dual_approval_threshold")
    if raw is None:
        return _DEFAULT_DUAL_APPROVAL_DISABLED
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_DUAL_APPROVAL_DISABLED


def set_dual_approval_threshold(
    db, organization_id: str, threshold: Optional[float],
) -> None:
    """Persist the dual-approval threshold. Pass ``None`` to clear
    (revert to disabled)."""
    org = db.get_organization(organization_id) or {}
    settings: Any = org.get("settings") or org.get("settings_json") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except (ValueError, TypeError):
            settings = {}
    if not isinstance(settings, dict):
        settings = {}
    block = settings.get("routing_thresholds") or {}
    if not isinstance(block, dict):
        block = {}
    if threshold is None:
        block.pop("dual_approval_threshold", None)
    else:
        block["dual_approval_threshold"] = max(0.0, float(threshold))
    settings["routing_thresholds"] = block
    db.update_organization(organization_id, settings=settings)


# ── First-signature path ──────────────────────────────────────────


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


def first_approve(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    approver_id: str,
    approver_email: Optional[str] = None,
) -> DualApprovalResult:
    """Operator clicks Approve from the needs_approval state.

    If the bill is below the dual-approval threshold (or the
    threshold is unset), advance to ``approved`` directly.
    Otherwise stamp first_approver and advance to
    ``needs_second_approval``.
    """
    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        raise ValueError(f"ap_item_not_found:{ap_item_id!r}")

    requester = item.get("user_id") or ""
    if requester and approver_id and requester == approver_id:
        # SOX: a bill's creator cannot self-approve. Audit the
        # blocked attempt + raise.
        try:
            db.append_audit_event({
                "ap_item_id": ap_item_id, "box_id": ap_item_id,
                "box_type": "ap_item",
                "event_type": "dual_approval_self_approval_blocked",
                "actor_type": "user", "actor_id": approver_id,
                "organization_id": organization_id,
                "source": "dual_approval",
                "metadata": {
                    "reason": "approver_is_requester",
                    "ap_item_id": ap_item_id,
                },
            })
        except Exception:
            logger.exception("dual_approval: blocked-event audit failed")
        raise DualApprovalRequesterApprovalError(
            "approver cannot be the bill's requester"
        )

    threshold = get_dual_approval_threshold(db, organization_id)
    amount = float(item.get("amount") or 0)
    requires_second = amount >= threshold

    meta = _normalize_meta(item)
    now = _now_iso()

    if not requires_second:
        # Single-signature path — direct approve.
        meta["first_approver"] = approver_id
        meta["first_approved_at"] = now
        db.update_ap_item(
            ap_item_id,
            state="approved",
            metadata=meta,
            approved_by=approver_id,
            approved_at=now,
            _actor_type="user",
            _actor_id=approver_id,
            _source="dual_approval",
            _decision_reason="single_signature_below_threshold",
        )
        return DualApprovalResult(
            ap_item_id=ap_item_id,
            new_state="approved",
            first_approver=approver_id,
            first_approved_at=now,
            requires_second_signature=False,
        )

    # Dual-signature path — first signature only.
    meta["first_approver"] = approver_id
    meta["first_approver_email"] = approver_email
    meta["first_approved_at"] = now
    db.update_ap_item(
        ap_item_id,
        state="needs_second_approval",
        metadata=meta,
        _actor_type="user",
        _actor_id=approver_id,
        _source="dual_approval",
        _decision_reason="first_signature_dual_approval_required",
    )
    try:
        db.append_audit_event({
            "ap_item_id": ap_item_id, "box_id": ap_item_id,
            "box_type": "ap_item",
            "event_type": "dual_approval_first_signature",
            "actor_type": "user", "actor_id": approver_id,
            "organization_id": organization_id,
            "source": "dual_approval",
            "idempotency_key": (
                f"dual_approval_first:{organization_id}:{ap_item_id}:{approver_id}"
            ),
            "metadata": {
                "amount": amount,
                "threshold": threshold,
                "first_approver": approver_id,
                "first_approver_email": approver_email,
            },
        })
    except Exception:
        logger.exception("dual_approval: first-sig audit failed")

    return DualApprovalResult(
        ap_item_id=ap_item_id,
        new_state="needs_second_approval",
        first_approver=approver_id,
        first_approved_at=now,
        requires_second_signature=True,
    )


def second_approve(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    approver_id: str,
    approver_email: Optional[str] = None,
) -> DualApprovalResult:
    """Second approver signs. Must differ from the first approver
    AND from the bill's creator."""
    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        raise ValueError(f"ap_item_not_found:{ap_item_id!r}")

    if str(item.get("state") or "").lower() != "needs_second_approval":
        raise DualApprovalNotPendingError(
            f"ap_item not in needs_second_approval (state={item.get('state')!r})"
        )

    meta = _normalize_meta(item)
    first_approver = meta.get("first_approver")
    if first_approver and first_approver == approver_id:
        try:
            db.append_audit_event({
                "ap_item_id": ap_item_id, "box_id": ap_item_id,
                "box_type": "ap_item",
                "event_type": "dual_approval_self_approval_blocked",
                "actor_type": "user", "actor_id": approver_id,
                "organization_id": organization_id,
                "source": "dual_approval",
                "metadata": {
                    "reason": "second_approver_same_as_first",
                    "first_approver": first_approver,
                },
            })
        except Exception:
            logger.exception("dual_approval: blocked-event audit failed")
        raise DualApprovalSelfApprovalError(
            "second approver must differ from first approver"
        )

    requester = item.get("user_id") or ""
    if requester and requester == approver_id:
        raise DualApprovalRequesterApprovalError(
            "approver cannot be the bill's requester"
        )

    now = _now_iso()
    meta["second_approver"] = approver_id
    meta["second_approver_email"] = approver_email
    meta["second_approved_at"] = now

    db.update_ap_item(
        ap_item_id,
        state="approved",
        metadata=meta,
        approved_by=approver_id,
        approved_at=now,
        _actor_type="user",
        _actor_id=approver_id,
        _source="dual_approval",
        _decision_reason="second_signature_dual_approval_complete",
    )

    try:
        db.append_audit_event({
            "ap_item_id": ap_item_id, "box_id": ap_item_id,
            "box_type": "ap_item",
            "event_type": "dual_approval_second_signature",
            "actor_type": "user", "actor_id": approver_id,
            "organization_id": organization_id,
            "source": "dual_approval",
            "idempotency_key": (
                f"dual_approval_second:{organization_id}:{ap_item_id}:{approver_id}"
            ),
            "metadata": {
                "first_approver": first_approver,
                "second_approver": approver_id,
                "second_approver_email": approver_email,
            },
        })
    except Exception:
        logger.exception("dual_approval: second-sig audit failed")

    return DualApprovalResult(
        ap_item_id=ap_item_id,
        new_state="approved",
        first_approver=first_approver,
        first_approved_at=meta.get("first_approved_at"),
        second_approver=approver_id,
        second_approved_at=now,
        requires_second_signature=False,
    )


def revoke_first_signature(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    actor_id: str,
    reason: Optional[str] = None,
) -> DualApprovalResult:
    """First approver pulls their signature before the second
    approves — bill returns to ``needs_approval``. Useful when the
    first approver realises they've miscoded the bill and wants to
    request more info / rework."""
    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        raise ValueError(f"ap_item_not_found:{ap_item_id!r}")
    if str(item.get("state") or "").lower() != "needs_second_approval":
        raise DualApprovalNotPendingError(
            f"ap_item not in needs_second_approval (state={item.get('state')!r})"
        )
    meta = _normalize_meta(item)
    meta.pop("first_approver", None)
    meta.pop("first_approver_email", None)
    meta.pop("first_approved_at", None)
    db.update_ap_item(
        ap_item_id,
        state="needs_approval",
        metadata=meta,
        _actor_type="user",
        _actor_id=actor_id,
        _source="dual_approval",
        _decision_reason=reason or "first_signature_revoked",
    )
    try:
        db.append_audit_event({
            "ap_item_id": ap_item_id, "box_id": ap_item_id,
            "box_type": "ap_item",
            "event_type": "dual_approval_revoked",
            "actor_type": "user", "actor_id": actor_id,
            "organization_id": organization_id,
            "source": "dual_approval",
            "metadata": {"reason": reason},
        })
    except Exception:
        logger.exception("dual_approval: revoke audit failed")
    return DualApprovalResult(
        ap_item_id=ap_item_id,
        new_state="needs_approval",
        requires_second_signature=False,
    )
