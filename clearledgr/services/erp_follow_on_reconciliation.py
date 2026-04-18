"""Reconciliation check for ERP follow-on split-brain state.

Detects and repairs mismatches between a source AP item's
``non_invoice_resolution.erp_follow_on.status`` and the related
invoice's ``vendor_credit_summary.erp_application_status`` or
``cash_application_summary.erp_settlement_status``.

This can happen when the process crashes between the two sequential
``update_ap_item`` calls in ``_apply_erp_follow_on_result``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.database import ClearledgrDB, get_db

logger = logging.getLogger(__name__)

_APPLIED_STATUSES = {"applied", "success", "completed", "already_applied"}


def _parse_meta(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def reconcile_erp_follow_on_state(
    db: Optional[ClearledgrDB] = None,
    *,
    organization_id: str = "default",
    limit: int = 500,
) -> Dict[str, Any]:
    """Scan non-invoice AP items with follow-on status and repair mismatches.

    Returns a summary dict with counts of items checked, mismatches found,
    and repairs applied.
    """
    db = db or get_db()
    checked = 0
    mismatches: List[Dict[str, str]] = []
    repaired = 0
    errors = 0

    try:
        all_items = db.list_ap_items_all(organization_id, limit=limit)
    except Exception:
        logger.exception("reconciliation: failed to list AP items for org=%s", organization_id)
        return {"checked": 0, "mismatches": 0, "repaired": 0, "errors": 1}

    for item in all_items or []:
        meta = _parse_meta(item.get("metadata"))
        resolution = meta.get("non_invoice_resolution")
        if not isinstance(resolution, dict):
            continue
        follow_on = resolution.get("erp_follow_on")
        if not isinstance(follow_on, dict):
            continue

        source_status = str(follow_on.get("status") or "").strip().lower()
        if not source_status:
            continue

        related_id = str(resolution.get("related_ap_item_id") or "").strip()
        if not related_id:
            continue

        action_type = str(follow_on.get("action_type") or "").strip().lower()
        checked += 1

        try:
            related_item = db.get_ap_item(related_id)
        except Exception:
            logger.warning("reconciliation: failed to fetch related item %s", related_id)
            errors += 1
            continue

        if not related_item:
            continue

        related_meta = _parse_meta(related_item.get("metadata"))

        if action_type == "apply_credit_note":
            summary = related_meta.get("vendor_credit_summary")
            if not isinstance(summary, dict):
                summary = {}
            related_status = str(summary.get("erp_application_status") or "").strip().lower()
        else:
            summary = related_meta.get("cash_application_summary")
            if not isinstance(summary, dict):
                summary = {}
            related_status = str(summary.get("erp_settlement_status") or "").strip().lower()

        if source_status == related_status:
            continue

        # Mismatch detected
        source_id = str(item.get("id") or "")
        mismatches.append({
            "source_id": source_id,
            "related_id": related_id,
            "action_type": action_type,
            "source_status": source_status,
            "related_status": related_status,
        })

        # Repair: propagate source status to related item's summary
        try:
            if action_type == "apply_credit_note":
                summary["erp_application_status"] = source_status
                summary["erp_application_mode"] = follow_on.get("execution_mode")
                summary["erp_application_reference"] = follow_on.get("erp_reference")
                summary["erp_reconciled_at"] = datetime.now(timezone.utc).isoformat()
                related_meta["vendor_credit_summary"] = summary
            else:
                summary["erp_settlement_status"] = source_status
                summary["erp_settlement_mode"] = follow_on.get("execution_mode")
                summary["erp_settlement_reference"] = follow_on.get("erp_reference")
                summary["erp_reconciled_at"] = datetime.now(timezone.utc).isoformat()
                related_meta["cash_application_summary"] = summary

            db.update_ap_item(
                related_id,
                metadata=related_meta,
                _actor_type="system",
                _actor_id="erp_follow_on_reconciliation",
                _source="reconciliation_check",
                _decision_reason=f"split_brain_repair_{action_type}",
            )
            db.append_audit_event({
                "ap_item_id": related_id,
                "event_type": "erp_follow_on_reconciliation_repair",
                "actor_type": "system",
                "actor_id": "erp_follow_on_reconciliation",
                "organization_id": organization_id,
                "source": "reconciliation_check",
                "reason": "split_brain_repair",
                "metadata": {
                    "source_ap_item_id": source_id,
                    "action_type": action_type,
                    "source_status": source_status,
                    "stale_related_status": related_status,
                },
            })
            repaired += 1
            logger.info(
                "reconciliation: repaired split-brain for source=%s related=%s "
                "action=%s source_status=%s stale_related_status=%s",
                source_id, related_id, action_type, source_status, related_status,
            )
        except Exception:
            logger.exception(
                "reconciliation: failed to repair related=%s for source=%s",
                related_id, source_id,
            )
            errors += 1

    result = {
        "checked": checked,
        "mismatches": len(mismatches),
        "repaired": repaired,
        "errors": errors,
        "details": mismatches if mismatches else [],
    }
    if mismatches:
        logger.warning("reconciliation: found %d mismatches, repaired %d", len(mismatches), repaired)
    else:
        logger.debug("reconciliation: checked %d items, no mismatches", checked)
    return result


async def run_erp_follow_on_reconciliation_check(
    organization_id: str = "default",
) -> int:
    """Async entry point for startup registration. Returns count of items checked."""
    result = reconcile_erp_follow_on_state(organization_id=organization_id)
    return result["checked"]
