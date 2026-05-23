"""Procurement OperationalSkill for the FinanceAgentRuntime.

The agent's action interface for the purchase_order BoxType. Unlike AP
(whose intent handlers span thousands of lines of extraction +
validation), PO transitions are simple validated state moves, so this
skill is self-contained — it dispatches each intent straight to the
generic box_registry CRUD primitives.

Intents:
  create_purchase_order, submit_purchase_order, approve_purchase_order,
  reject_purchase_order, cancel_purchase_order, close_purchase_order

Approve routing honours the tiered procurement thresholds: a PO at or
below the auto-approve ceiling can be approved autonomously; above the
dual-approval line, autonomy is refused (a human, in fact two, must act).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from solden.core import box_registry
from solden.core.finance_contracts import SkillCapabilityManifest
from solden.core.procurement_thresholds import (
    evaluate_po_approval,
    load_procurement_thresholds,
)
from solden.core.purchase_order_states import (
    VALID_PO_TRANSITIONS,
    IllegalPurchaseOrderTransitionError,
)
from solden.services.finance_skills.base import FinanceSkill
from solden.services.purchase_orders import POStatus

logger = logging.getLogger(__name__)

_BOX_TYPE = "purchase_order"

# intent -> target state for the transition intents (create is special).
_INTENT_TARGET: Dict[str, str] = {
    "submit_purchase_order": POStatus.PENDING_APPROVAL.value,
    "approve_purchase_order": POStatus.APPROVED.value,
    "reject_purchase_order": POStatus.DRAFT.value,
    "cancel_purchase_order": POStatus.CANCELLED.value,
    "close_purchase_order": POStatus.CLOSED.value,
}


class ProcurementFinanceSkill(FinanceSkill):
    """Operational skill for purchase_order intents."""

    _INTENTS = frozenset(
        {
            "create_purchase_order",
            "issue_purchase_order",
            "receive_purchase_order",
            "amend_purchase_order",
            *_INTENT_TARGET.keys(),
        }
    )

    _MANIFEST = SkillCapabilityManifest(
        skill_id="procurement_v1",
        version="1.0",
        state_machine={
            "states": [s.value for s in POStatus],
            "initial": POStatus.DRAFT.value,
            "terminal": [POStatus.CLOSED.value, POStatus.CANCELLED.value],
            "transitions": {
                cur.value: sorted(t.value for t in targets)
                for cur, targets in VALID_PO_TRANSITIONS.items()
            },
        },
        action_catalog=[
            {"id": "create_purchase_order", "label": "Create a purchase order (draft)"},
            {"id": "submit_purchase_order", "label": "Submit a PO for approval"},
            {"id": "approve_purchase_order", "label": "Approve a PO"},
            {"id": "reject_purchase_order", "label": "Reject a PO back to the requester"},
            {"id": "cancel_purchase_order", "label": "Cancel a PO"},
            {"id": "close_purchase_order", "label": "Close a PO"},
        ],
        policy_pack={
            "tiered_approval": {
                "auto_approve_ceiling": "org.procurement_thresholds.auto_approve_ceiling",
                "dual_approval_above": "org.procurement_thresholds.dual_approval_above",
            },
        },
        evidence_schema={
            "material": ["po_id", "vendor_name", "total_amount"],
            "optional": ["line_items", "department", "project"],
        },
        adapter_bindings={"erp": ["write"]},  # PO issuance to ERP lands in Phase 3
        kpi_contract={"promotion_gates": {"approval_cycle_time_max_hours": 72}},
    )

    @property
    def skill_id(self) -> str:
        return "procurement_v1"

    @property
    def intents(self) -> frozenset[str]:
        return self._INTENTS

    @property
    def manifest(self) -> SkillCapabilityManifest:
        return self._MANIFEST

    @staticmethod
    def _resolve_po_id(payload: Dict[str, Any]) -> str:
        return str(
            payload.get("po_id")
            or payload.get("box_id")
            or payload.get("reference")
            or ""
        ).strip()

    @staticmethod
    def _fetch_po(runtime, po_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a PO scoped to the runtime's org. ``get_purchase_order`` is
        id-keyed (the box_registry generic-dispatch contract), so the org check
        lives here — otherwise a procurement intent carrying another tenant's
        po_id would read that PO (vendor master + amounts). The Slack/Teams PO
        callbacks already do this check; this is the agent-intent path's.
        """
        if not po_id:
            return None
        po = runtime.db.get_purchase_order(po_id)
        if not po or str(po.get("organization_id") or "") != str(runtime.organization_id):
            return None
        return po

    def policy_precheck(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized = str(intent or "").strip().lower()
        payload = input_payload if isinstance(input_payload, dict) else {}
        reason_codes: list[str] = []

        if normalized == "create_purchase_order":
            if not str(payload.get("vendor_name") or "").strip():
                reason_codes.append("missing_vendor_name")
            return {
                "intent": normalized,
                "po_id": self._resolve_po_id(payload),
                "target_state": POStatus.DRAFT.value,
                "policy_precheck": {
                    "eligible": not reason_codes,
                    "reason_codes": reason_codes,
                },
            }

        target = _INTENT_TARGET.get(normalized)
        po_id = self._resolve_po_id(payload)
        po = self._fetch_po(runtime, po_id)
        if not po:
            reason_codes.append("purchase_order_not_found")
            return {
                "intent": normalized,
                "po_id": po_id,
                "target_state": target,
                "policy_precheck": {"eligible": False, "reason_codes": reason_codes},
            }

        current = str(po.get("status") or "")
        from solden.core.purchase_order_states import validate_po_transition
        if target and not validate_po_transition(current, target):
            reason_codes.append("illegal_transition")

        if normalized == "issue_purchase_order":
            if current != POStatus.APPROVED.value:
                reason_codes.append("po_not_approved")
            if po.get("erp_po_id"):
                reason_codes.append("already_issued")

        if normalized == "receive_purchase_order":
            if current not in {
                POStatus.APPROVED.value, POStatus.PARTIALLY_RECEIVED.value,
            }:
                reason_codes.append("po_not_receivable")

        if normalized == "amend_purchase_order":
            if current != POStatus.DRAFT.value:
                reason_codes.append("po_not_amendable")

        routing = None
        if normalized == "approve_purchase_order":
            thresholds = load_procurement_thresholds(runtime.organization_id, runtime.db)
            routing = evaluate_po_approval(po.get("total_amount") or 0.0, thresholds)
            autonomous_requested = runtime.is_autonomous_request(payload)
            if autonomous_requested and not routing.auto_approvable:
                reason_codes.append("autonomy_gate_blocked")
                reason_codes.append(f"approval_tier_{routing.tier}")

        return {
            "intent": normalized,
            "po_id": po_id,
            "target_state": target,
            "current_state": current,
            "approval_routing": routing.__dict__ if routing else None,
            "policy_precheck": {
                "eligible": not reason_codes,
                "reason_codes": list(dict.fromkeys(reason_codes)),
            },
        }

    def audit_contract(self, intent: str) -> Dict[str, Any]:
        normalized = str(intent or "").strip().lower()
        return {
            "event_type": f"purchase_order.{normalized}",
            "entity_type": "purchase_order",
        }

    def preview(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized = str(intent or "").strip().lower()
        context = self.policy_precheck(runtime, normalized, input_payload)
        precheck = context["policy_precheck"]
        return {
            "skill_id": self.skill_id,
            "intent": normalized,
            "mode": "preview",
            "status": "eligible" if precheck.get("eligible") else "blocked",
            "organization_id": runtime.organization_id,
            "po_id": context.get("po_id"),
            "target_state": context.get("target_state"),
            "approval_routing": context.get("approval_routing"),
            "policy_precheck": precheck,
            "audit_contract": self.audit_contract(normalized),
            "next_step": "execute_intent",
        }

    async def execute(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized = str(intent or "").strip().lower()
        payload = input_payload if isinstance(input_payload, dict) else {}
        context = self.policy_precheck(runtime, normalized, payload)
        precheck = context["policy_precheck"]
        if not precheck.get("eligible"):
            return {
                "skill_id": self.skill_id,
                "intent": normalized,
                "status": "blocked",
                "po_id": context.get("po_id"),
                "policy_precheck": precheck,
            }

        actor_id = str(getattr(runtime, "actor_id", "") or "agent")

        if normalized == "create_purchase_order":
            po_payload = {
                "po_id": payload.get("po_id") or f"PO-{uuid.uuid4().hex[:16]}",
                "organization_id": runtime.organization_id,
                "po_number": payload.get("po_number", ""),
                "vendor_id": payload.get("vendor_id", ""),
                "vendor_name": payload.get("vendor_name", ""),
                "total_amount": payload.get("total_amount", 0.0),
                "subtotal": payload.get("subtotal", 0.0),
                "tax_amount": payload.get("tax_amount", 0.0),
                "currency": payload.get("currency", ""),
                "line_items": payload.get("line_items", []),
                "status": POStatus.DRAFT.value,
                "requested_by": actor_id,
                "notes": payload.get("notes", ""),
                "department": payload.get("department", ""),
                "project": payload.get("project", ""),
            }
            box = box_registry.create_box(_BOX_TYPE, po_payload, runtime.db)
            return {
                "skill_id": self.skill_id,
                "intent": normalized,
                "status": "created",
                "po_id": box.get("id"),
                "state": box.get("state"),
            }

        if normalized == "issue_purchase_order":
            from solden.integrations.erp_po_write import create_purchase_order
            po = self._fetch_po(runtime, context["po_id"])
            result = await create_purchase_order(
                runtime.organization_id, po, idempotency_key=idempotency_key,
            )
            erp_po_id = result.get("erp_po_id")
            if result.get("status") == "success" and erp_po_id:
                runtime.db.set_po_erp_id(context["po_id"], erp_po_id, actor_id=actor_id)
                return {
                    "skill_id": self.skill_id,
                    "intent": normalized,
                    "status": "issued",
                    "po_id": context["po_id"],
                    "erp_po_id": erp_po_id,
                }
            return {
                "skill_id": self.skill_id,
                "intent": normalized,
                "status": result.get("status", "error"),
                "po_id": context["po_id"],
                "erp_result": result,
            }

        if normalized == "amend_purchase_order":
            box = runtime.db.amend_purchase_order_box(
                context["po_id"], payload.get("fields") or {}, actor_id=actor_id,
            )
            return {
                "skill_id": self.skill_id,
                "intent": normalized,
                "status": "amended",
                "po_id": context["po_id"],
                "state": box.get("state") if isinstance(box, dict) else None,
            }

        if normalized == "receive_purchase_order":
            import uuid as _uuid
            po = self._fetch_po(runtime, context["po_id"])
            # Reconcile received quantities per line. received_lines omitted
            # => receive all. ``partial=True`` forces partial regardless
            # (back-compat / explicit partial delivery).
            recon = runtime.db.record_po_receipt(
                context["po_id"], payload.get("received_lines"), actor_id=actor_id,
            )
            fully = recon.get("fully_received", True)
            if payload.get("partial") is True:
                fully = False
            target_state = (
                POStatus.FULLY_RECEIVED.value if fully
                else POStatus.PARTIALLY_RECEIVED.value
            )
            try:
                runtime.db.save_goods_receipt({
                    "gr_id": f"GR-{_uuid.uuid4().hex[:16]}",
                    "organization_id": runtime.organization_id,
                    "po_id": context["po_id"],
                    "po_number": po.get("po_number", ""),
                    "vendor_name": po.get("vendor_name", ""),
                    "received_by": actor_id,
                    "line_items": recon.get("line_items", []),
                    "status": "received" if fully else "partial",
                })
            except Exception:  # noqa: BLE001
                logger.warning("[procurement] goods receipt save failed for %s", context["po_id"])
            box = box_registry.update_box(
                _BOX_TYPE, context["po_id"], runtime.db,
                state=target_state, actor_id=actor_id,
                reason=str(payload.get("reason") or "goods received"),
            )
            return {
                "skill_id": self.skill_id,
                "intent": normalized,
                "status": "ok",
                "po_id": context["po_id"],
                "state": box.get("state") if isinstance(box, dict) else target_state,
                "fully_received": fully,
            }

        target = context["target_state"]
        try:
            box = box_registry.update_box(
                _BOX_TYPE, context["po_id"], runtime.db,
                state=target, actor_id=actor_id,
                reason=str(payload.get("reason") or ""),
            )
        except IllegalPurchaseOrderTransitionError as exc:
            return {
                "skill_id": self.skill_id,
                "intent": normalized,
                "status": "blocked",
                "po_id": context.get("po_id"),
                "error": str(exc),
            }
        return {
            "skill_id": self.skill_id,
            "intent": normalized,
            "status": "ok",
            "po_id": context.get("po_id"),
            "state": box.get("state") if isinstance(box, dict) else target,
        }
