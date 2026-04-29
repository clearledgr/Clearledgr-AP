"""Vendor mid-workflow re-validation (Wave 1 / A11).

Per the AP cycle reference doc (Stage 9 concurrency notes):
"Vendor master record updated mid-workflow — re-validate the vendor
match if material attributes (bank account, tax ID, status) changed."

Two concurrent flows that this module handles:

  Eager push (this module's primary job):
    * Triggered when a vendor profile transitions on a material
      attribute (status → blocked / archived; iban_change_pending
      → True; sanctions hit posted).
    * Walks every non-terminal AP item for that vendor.
    * Sets ``exception_code`` + ``exception_severity`` on each
      affected item so the operator surfaces them in the exception
      queue immediately.
    * Audit-emits ``vendor_revalidation_triggered`` per item — the
      trail explains "this AP item entered the exception queue
      because Acme was blocked at 14:32 by alice@".

  Lazy gate-time recheck (already in place):
    * ``erp_router.pre_post_validate`` reads vendor.status at post
      time (Module 4 Pass B); a blocked vendor fails posting.
    * Approve-action handlers consult the role + vendor state via
      role_resolver + iban_verification.

The module is pure — takes a DB handle + tenant + vendor + reason
and returns a structured summary. Caller integrates from the vendor
admin endpoint (status change, IBAN ceremony start, etc.). Idempotent:
re-running with the same reason on items that already carry that
exception_code is a no-op.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Materially-blocking reasons that should drive items to the
# exception queue with high severity. The AP item's existing
# state machine handles the actual transition (NEEDS_INFO etc)
# at gate time; this module just signals.
HIGH_SEVERITY_REASONS = frozenset({
    "vendor_blocked",
    "vendor_sanctions_hit",
})

# Reasons that surface a flag but don't block — the operator can
# acknowledge or proceed once the underlying ceremony completes.
MEDIUM_SEVERITY_REASONS = frozenset({
    "vendor_iban_change_pending",
    "vendor_status_archived",
    "vendor_revalidate_required",
})

# Terminal AP states — items here are immutable and out of scope.
_TERMINAL_AP_STATES = frozenset({
    "rejected", "reversed", "closed", "posted_to_erp",
})


@dataclass
class RevalidationResult:
    """Per-call summary of what re-validation touched.

    ``affected_ap_item_ids`` lists items whose ``exception_code``
    was set to the new reason. ``skipped_terminal`` lists items
    that were already in a terminal state. ``skipped_already_flagged``
    lists items that already carried this exact reason (idempotent
    re-runs).
    """

    organization_id: str
    vendor_name: str
    reason: str
    affected_ap_item_ids: List[str] = field(default_factory=list)
    skipped_terminal: List[str] = field(default_factory=list)
    skipped_already_flagged: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "vendor_name": self.vendor_name,
            "reason": self.reason,
            "affected_ap_item_ids": list(self.affected_ap_item_ids),
            "skipped_terminal_count": len(self.skipped_terminal),
            "skipped_already_flagged_count": len(self.skipped_already_flagged),
            "errors": list(self.errors),
        }


def revalidate_in_flight_ap_items(
    db,
    *,
    organization_id: str,
    vendor_name: str,
    reason: str,
    actor: str,
    days: int = 365,
) -> RevalidationResult:
    """Eagerly propagate a vendor change to in-flight AP items.

    Args:
        db: ClearledgrDB handle.
        organization_id: Tenant scope.
        vendor_name: Canonical vendor name to match.
        reason: Stable token-style reason. Used as exception_code on
            affected AP items + box_type=vendor on the audit event.
        actor: Who triggered the revalidation (email or system id).
            Lands on the audit event's actor_id.
        days: How far back to scan for non-terminal items. Default 365
            so a long-running blocked invoice doesn't get missed —
            terminal items are skipped anyway.

    Never raises — returns ``RevalidationResult`` with errors[]
    populated for individual failures so the caller can render a
    structured outcome.
    """
    result = RevalidationResult(
        organization_id=organization_id,
        vendor_name=vendor_name,
        reason=reason,
    )

    if not vendor_name or not organization_id or not reason:
        result.errors.append("missing_required_args")
        return result

    severity = (
        "high" if reason in HIGH_SEVERITY_REASONS
        else ("medium" if reason in MEDIUM_SEVERITY_REASONS else "low")
    )

    try:
        items = db.get_ap_items_by_vendor(
            organization_id, vendor_name, days=days, limit=500,
        )
    except Exception as exc:
        logger.warning(
            "[vendor_revalidation] get_ap_items_by_vendor failed for org=%s vendor=%s: %s",
            organization_id, vendor_name, exc,
        )
        result.errors.append(f"items_lookup_failed:{exc}")
        return result

    for item in items or []:
        ap_item_id = str(item.get("id") or "")
        if not ap_item_id:
            continue

        state = str(item.get("state") or "").strip().lower()
        if state in _TERMINAL_AP_STATES:
            result.skipped_terminal.append(ap_item_id)
            continue

        existing_exc = str(item.get("exception_code") or "").strip()
        if existing_exc == reason:
            result.skipped_already_flagged.append(ap_item_id)
            continue

        try:
            # The audit-control args on update_ap_item use a leading
            # underscore to distinguish them from column names. Pass
            # actor + source + reason via the underscore-prefixed
            # kwargs so the audit row carries proper attribution.
            db.update_ap_item(
                ap_item_id,
                exception_code=reason,
                exception_severity=severity,
                _actor_type=("user" if actor and "@" in actor else "system"),
                _actor_id=actor or "vendor_revalidation",
                _source="vendor_revalidation",
                _decision_reason=(
                    f"Vendor {vendor_name!r} triggered re-validation "
                    f"({reason}); previous exception_code was "
                    f"{(repr(existing_exc) if existing_exc else '(none)')}."
                ),
            )
            result.affected_ap_item_ids.append(ap_item_id)
        except Exception as exc:
            logger.warning(
                "[vendor_revalidation] update_ap_item failed for %s: %s",
                ap_item_id, exc,
            )
            result.errors.append(f"{ap_item_id}:{exc}")
            continue

        # Audit emit per item — the operator can reconstruct the
        # cascade from the audit log alone.
        try:
            db.append_audit_event({
                "event_type": "vendor_revalidation_triggered",
                "actor_type": "user" if actor and "@" in actor else "system",
                "actor_id": actor or "vendor_revalidation",
                "organization_id": organization_id,
                "box_id": ap_item_id,
                "box_type": "ap_item",
                "source": "vendor_revalidation",
                "payload_json": {
                    "vendor_name": vendor_name,
                    "reason": reason,
                    "severity": severity,
                    "previous_state": state,
                    "previous_exception_code": existing_exc or None,
                },
                "idempotency_key": f"revalidate:{ap_item_id}:{reason}",
            })
        except Exception as exc:
            logger.warning(
                "[vendor_revalidation] audit emit failed for %s: %s",
                ap_item_id, exc,
            )

    return result
