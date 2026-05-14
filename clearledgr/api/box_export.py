"""Box export endpoint — the sovereignty primitive.

The manifesto promises: "Components should remain whole if you ever
take it out. The bond should be strong but not destructive."

This module backs that promise. Every Box (workflow instance) can be
exported as a single self-contained JSON document containing:

  * the Box's current state and raw fields
  * the complete audit history (every transition, every override,
    every reversal) with the hash-chain links preserved
  * any open exceptions
  * the terminal outcome if the Box has closed
  * parent/child Box links (for child Box types like ``bank_match``)

The output is a stable, versioned schema (``box_schema_version``)
documented at ``docs/BOX_SCHEMA.md``. A third party reading the export
can reconstruct the workflow record without any Solden runtime
present — that's what "removable" means.

This is also the read primitive operators use to satisfy regulator
"give me everything you have on workflow X" requests.

Path::

    GET /api/workspace/ap-items/{ap_item_id}/export

Future: a generic ``GET /api/workspace/box/{box_type}/{box_id}/export``
will replace this thin wrapper once a second BoxType ships
(``bank_match`` is on the roadmap). The current shape is already
generic; only the route prefix is AP-specific.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from clearledgr.core.auth import get_current_user
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


# Bumped whenever the export shape changes in a non-additive way.
# Additive fields are fine without a version bump; consumers must
# tolerate unknown keys. See docs/BOX_SCHEMA.md for the contract.
BOX_SCHEMA_VERSION = "1.0"


router = APIRouter(prefix="/api/workspace", tags=["box-export"])


def _session_org(user: Any) -> str:
    org = str(getattr(user, "organization_id", "") or "").strip()
    if not org:
        raise HTTPException(
            status_code=403, detail="user_missing_organization_id"
        )
    return org


def _normalize_audit_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Shape one audit_events row for export.

    Unlike ``normalize_operator_audit_events``, this preserves every
    column — including the hash chain — because the export is the
    forensic record, not a UI feed. Consumers verifying chain
    integrity need ``prev_hash``, ``hash``, ``chain_seq`` intact.
    """
    return {
        "id": raw.get("id"),
        "ts": raw.get("ts"),
        "event_type": raw.get("event_type"),
        "prev_state": raw.get("prev_state"),
        "new_state": raw.get("new_state"),
        "actor_type": raw.get("actor_type"),
        "actor_id": raw.get("actor_id"),
        "decision_reason": raw.get("decision_reason"),
        "policy_version": raw.get("policy_version"),
        "governance_verdict": raw.get("governance_verdict"),
        "agent_confidence": raw.get("agent_confidence"),
        "source": raw.get("source"),
        "correlation_id": raw.get("correlation_id"),
        "workflow_id": raw.get("workflow_id"),
        "run_id": raw.get("run_id"),
        "payload": raw.get("payload_json") or {},
        "external_refs": raw.get("external_refs") or {},
        "idempotency_key": raw.get("idempotency_key"),
        "entity_id": raw.get("entity_id"),
        # Hash chain — preserved so an offline verifier can
        # reconstruct the chain and prove the export hasn't been
        # tampered with after extraction.
        "prev_hash": raw.get("prev_hash"),
        "hash": raw.get("hash"),
        "chain_seq": raw.get("chain_seq"),
    }


def _normalize_exception(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": raw.get("id"),
        "exception_type": raw.get("exception_type"),
        "severity": raw.get("severity"),
        "reason": raw.get("reason"),
        "metadata": raw.get("metadata") or {},
        "raised_at": raw.get("raised_at") or raw.get("created_at"),
        "resolved_at": raw.get("resolved_at"),
        "resolved_by": raw.get("resolved_by"),
        "resolution_note": raw.get("resolution_note"),
    }


def _box_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """Project the raw ap_items row into the portable ``fields`` block.

    Drops nothing — every persisted column on the AP item lands here
    so the export is genuinely complete. Internal SQL columns
    (``state``, ``id``, ``organization_id``) are surfaced at the
    parent ``box`` level instead, not duplicated.
    """
    return {
        k: v for k, v in item.items()
        if k not in {"id", "state", "organization_id"}
    }


def export_ap_item_box(db: Any, ap_item_id: str, organization_id: str, actor: str) -> Dict[str, Any]:
    """Build the full export document for one AP item Box.

    Pure function over DB reads — no side effects, no audit write.
    The export itself is a read action; callers that want it audited
    should record their own ``box_exported`` event.
    """
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    if str(item.get("organization_id") or "") != organization_id:
        # Don't disclose cross-tenant existence — 404 looks the same
        # as "no such item" to a caller without access.
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    raw_events = db.list_ap_audit_events(ap_item_id) or []
    history = [_normalize_audit_event(e) for e in raw_events]

    exceptions: List[Dict[str, Any]] = []
    if hasattr(db, "list_box_exceptions"):
        try:
            exceptions = [
                _normalize_exception(e)
                for e in db.list_box_exceptions(
                    box_type="ap_item", box_id=ap_item_id,
                )
            ]
        except Exception as exc:
            logger.warning(
                "[box_export] list_box_exceptions failed for %s: %s",
                ap_item_id, exc,
            )

    outcome: Optional[Dict[str, Any]] = None
    if hasattr(db, "get_box_outcome"):
        try:
            outcome = db.get_box_outcome(
                box_type="ap_item", box_id=ap_item_id,
            )
        except Exception as exc:
            logger.warning(
                "[box_export] get_box_outcome failed for %s: %s",
                ap_item_id, exc,
            )

    return {
        "box_schema_version": BOX_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by": actor,
        "box": {
            "type": "ap_item",
            "id": ap_item_id,
            "organization_id": organization_id,
            "entity_id": item.get("entity_id"),
            "state": item.get("state"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "fields": _box_fields(item),
        },
        "history": history,
        "exceptions": exceptions,
        "outcome": outcome,
        "links": {
            "parent_box": None,
            "child_boxes": [],
        },
    }


@router.get("/ap-items/{ap_item_id}/export")
def get_ap_item_export(
    ap_item_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the full, portable, self-contained Box export.

    The shape is documented at ``docs/BOX_SCHEMA.md`` and versioned
    via ``box_schema_version``. Consumers should treat unknown keys
    as additive and version-gate any breaking-change handling on the
    ``box_schema_version`` field.
    """
    organization_id = _session_org(_user)
    actor = str(getattr(_user, "email", "") or getattr(_user, "user_id", "") or "")
    db = get_db()
    return export_ap_item_box(
        db=db,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
        actor=actor,
    )
