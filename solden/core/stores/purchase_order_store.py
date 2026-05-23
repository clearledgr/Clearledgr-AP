"""PurchaseOrderStore mixin — DB-backed persistence for POs, GRs, matches.

Replaces the previous in-memory-only implementation in
:class:`PurchaseOrderService`. Before this mixin, POs / GRs / matches
lived in process-local dicts, so:

  * Two Railway workers saw different PO sets
  * Every deploy wiped everything
  * There was no way to import POs from an ERP and have them survive

This mixin owns three tables — purchase_orders, goods_receipts,
three_way_matches — with line items stored as JSON on each parent
row. JSON is the right call here: PO lines are tightly coupled to the
parent PO, rarely queried independently, and Postgres + SQLite both
handle JSON text columns cleanly.

Schema lives in migrations v33 (see migrations.py). This mixin only
provides the CRUD API the service needs.

Design rules:
  * Org-scoping is CALLER-ENFORCED, not store-enforced. The id-keyed
    getters/mutators here (get_purchase_order, update_purchase_order_state,
    set_po_erp_id, amend_purchase_order_box, record_po_receipt,
    get_goods_receipt, get_three_way_match[_by_invoice]) take no org and do
    NOT filter by it — this matches the box_registry generic-dispatch contract
    (get_box(box_type, box_id, db) has no org at that layer). Every reachable
    caller verifies org BEFORE acting: the PO API via _require_po, the
    Slack/Teams callbacks via an org-match-or-404 check, and the agent path via
    procurement_skill._fetch_po. (An earlier version of this docstring claimed
    "never a cross-tenant leak" — false; the store does not enforce it. Do not
    add a new caller that reads a PO by id without checking po.organization_id.)
  * Line items round-trip as Python lists — the mixin takes care of
    JSON encode/decode so the service never sees raw JSON strings.
  * Timestamps are ISO 8601 strings in UTC (matches the rest of the
    codebase).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from solden.core.purchase_order_states import (
    CURRENT_PO_POLICY_VERSION,
    IllegalPurchaseOrderTransitionError,
    validate_po_transition,
)
from solden.services.purchase_orders import POStatus

logger = logging.getLogger(__name__)


# Columns the box-aware state writer is allowed to patch. POs carry a
# rich master-data shape; the Box lifecycle only ever touches state +
# the approval/erp/audit-adjacent columns. Mirrors
# ``_AP_ITEM_ALLOWED_COLUMNS`` / ``_BANK_MATCH_ALLOWED_UPDATE_COLUMNS``.
_PURCHASE_ORDER_ALLOWED_COLUMNS = frozenset({
    "status",
    "approved_by",
    "approved_at",
    "erp_po_id",
    "notes",
    "updated_at",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json_list(raw: Any) -> List[Dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _encode_json(value: Any) -> str:
    return json.dumps(value or [], default=str)


class PurchaseOrderStore:
    """Mixin providing PO / GR / 3-way match persistence for SoldenDB."""

    # ------------------------------------------------------------------
    # Row shape helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _po_row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        data = dict(row) if not isinstance(row, dict) else row
        data["line_items"] = _decode_json_list(data.pop("line_items_json", None))
        # Box-generic alias: the engine, box_summary, and box_registry
        # read ``state`` / ``id``, but the PO table uses ``status`` /
        # ``po_id``. Expose both so a PO rings through the generic
        # primitives without per-reader special-casing.
        data.setdefault("state", data.get("status"))
        data.setdefault("id", data.get("po_id"))
        return data

    @staticmethod
    def _gr_row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        data = dict(row) if not isinstance(row, dict) else row
        data["line_items"] = _decode_json_list(data.pop("line_items_json", None))
        return data

    @staticmethod
    def _match_row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        data = dict(row) if not isinstance(row, dict) else row
        data["exceptions"] = _decode_json_list(data.pop("exceptions_json", None))
        return data

    # ------------------------------------------------------------------
    # Purchase Orders
    # ------------------------------------------------------------------

    def save_purchase_order(self, po: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert a PO by po_id. Returns the persisted row.

        Caller passes a dict with PurchaseOrder.to_dict()-shaped keys
        PLUS organization_id. The store serializes line_items to JSON
        and fills timestamps.
        """
        org_id = str(po.get("organization_id") or "").strip()
        po_id = str(po.get("po_id") or "").strip()
        if not org_id:
            raise ValueError("organization_id is required")
        if not po_id:
            raise ValueError("po_id is required")
        self.initialize()
        now = _now_iso()
        params = (
            po_id,
            org_id,
            str(po.get("po_number") or ""),
            str(po.get("vendor_id") or ""),
            str(po.get("vendor_name") or ""),
            str(po.get("order_date") or ""),
            str(po.get("expected_delivery") or "") or None,
            _encode_json(po.get("line_items") or []),
            float(po.get("subtotal") or 0.0),
            float(po.get("tax_amount") or 0.0),
            float(po.get("total_amount") or 0.0),
            str(po.get("currency") or "") or None,
            str(po.get("status") or "draft"),
            str(po.get("requested_by") or ""),
            str(po.get("approved_by") or "") or None,
            str(po.get("approved_at") or "") or None,
            str(po.get("notes") or ""),
            str(po.get("department") or ""),
            str(po.get("project") or ""),
            str(po.get("ship_to_address") or ""),
            str(po.get("erp_po_id") or ""),
            str(po.get("created_at") or now),
            now,  # updated_at
        )
        sql = (
            """
            INSERT INTO purchase_orders (
                po_id, organization_id, po_number, vendor_id, vendor_name,
                order_date, expected_delivery, line_items_json,
                subtotal, tax_amount, total_amount, currency, status,
                requested_by, approved_by, approved_at,
                notes, department, project, ship_to_address,
                erp_po_id, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (po_id) DO UPDATE SET
                po_number = EXCLUDED.po_number,
                vendor_id = EXCLUDED.vendor_id,
                vendor_name = EXCLUDED.vendor_name,
                order_date = EXCLUDED.order_date,
                expected_delivery = EXCLUDED.expected_delivery,
                line_items_json = EXCLUDED.line_items_json,
                subtotal = EXCLUDED.subtotal,
                tax_amount = EXCLUDED.tax_amount,
                total_amount = EXCLUDED.total_amount,
                currency = EXCLUDED.currency,
                status = EXCLUDED.status,
                requested_by = EXCLUDED.requested_by,
                approved_by = EXCLUDED.approved_by,
                approved_at = EXCLUDED.approved_at,
                notes = EXCLUDED.notes,
                department = EXCLUDED.department,
                project = EXCLUDED.project,
                ship_to_address = EXCLUDED.ship_to_address,
                erp_po_id = EXCLUDED.erp_po_id,
                updated_at = EXCLUDED.updated_at
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
        return self.get_purchase_order(po_id) or {}

    def get_purchase_order(self, po_id: str) -> Optional[Dict[str, Any]]:
        if not po_id:
            return None
        self.initialize()
        sql = "SELECT * FROM purchase_orders WHERE po_id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (po_id,))
            row = cur.fetchone()
        return self._po_row_to_dict(row)

    def get_purchase_order_by_number(
        self, organization_id: str, po_number: str
    ) -> Optional[Dict[str, Any]]:
        if not organization_id or not po_number:
            return None
        self.initialize()
        sql = (
            "SELECT * FROM purchase_orders "
            "WHERE organization_id = %s AND po_number = %s "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, po_number))
            row = cur.fetchone()
        return self._po_row_to_dict(row)

    def list_purchase_orders_for_vendor(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        open_only: bool = True,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List POs for a vendor (fuzzy-matched on lowercased name).

        ``open_only`` filters to statuses where the PO can still be
        invoiced — approved / partially_received / partially_invoiced.
        That matches the cases where 3-way matching is actually
        actionable.
        """
        if not organization_id or not vendor_name:
            return []
        self.initialize()
        open_statuses = ("approved", "partially_received", "partially_invoiced")
        if open_only:
            sql = (
                "SELECT * FROM purchase_orders "
                "WHERE organization_id = %s "
                "AND LOWER(vendor_name) LIKE %s "
                "AND status IN (%s, %s, %s) "
                "ORDER BY created_at DESC LIMIT %s"
            )
            params = (
                organization_id,
                f"%{vendor_name.lower()}%",
                *open_statuses,
                limit,
            )
        else:
            sql = (
                "SELECT * FROM purchase_orders "
                "WHERE organization_id = %s "
                "AND LOWER(vendor_name) LIKE %s "
                "ORDER BY created_at DESC LIMIT %s"
            )
            params = (organization_id, f"%{vendor_name.lower()}%", limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [self._po_row_to_dict(r) for r in rows if r is not None]

    def list_purchase_orders(
        self, organization_id: str, *, limit: int = 200
    ) -> List[Dict[str, Any]]:
        if not organization_id:
            return []
        self.initialize()
        sql = (
            "SELECT * FROM purchase_orders "
            "WHERE organization_id = %s "
            "ORDER BY created_at DESC LIMIT %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, limit))
            rows = cur.fetchall()
        return [self._po_row_to_dict(r) for r in rows if r is not None]

    # ------------------------------------------------------------------
    # Box lifecycle (purchase_order BoxType)
    #
    # These are the box-aware writers the runtime dispatches to via
    # box_registry.create_box / update_box. They sit alongside the
    # legacy save_purchase_order / approve_po (used by ERP import) and
    # add the audit_events funnel + transition validation a Box needs.
    # ------------------------------------------------------------------

    def create_purchase_order_box(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a PO and emit its creation audit row.

        Generic counterpart to ``create_bank_match``: reuses the existing
        upsert, then writes the ``box_type='purchase_order'`` creation
        event so the PO rings through the same audit funnel as AP.
        """
        saved = self.save_purchase_order(payload)
        po_id = str(saved.get("po_id") or "")
        state = str(saved.get("status") or POStatus.DRAFT.value)
        if hasattr(self, "append_audit_event"):
            self.append_audit_event({
                "box_id": po_id,
                "box_type": "purchase_order",
                "event_type": "purchase_order_created",
                "to_state": state,
                "actor_type": "system",
                "actor_id": payload.get("requested_by") or "procurement",
                "organization_id": saved.get("organization_id"),
                "policy_version": CURRENT_PO_POLICY_VERSION,
                "payload_json": {
                    "po_number": saved.get("po_number"),
                    "vendor_name": saved.get("vendor_name"),
                    "total_amount": saved.get("total_amount"),
                },
                "idempotency_key": f"po-created:{po_id}",
            })
        return saved

    def update_purchase_order_state(
        self,
        po_id: str,
        target_state: str,
        *,
        actor_id: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Advance a PO Box to a new state, validating the edge.

        Mirrors ``update_bank_match_state``: a non-empty ``actor_id`` is
        required (the audit trail is the source of truth for who decided
        each transition), the edge is validated against the PO state
        machine, and the audit_events row is written atomically with the
        column patch. Raises
        :class:`IllegalPurchaseOrderTransitionError` on an illegal edge.
        """
        if not str(actor_id or "").strip():
            raise ValueError(
                "update_purchase_order_state requires a non-empty actor_id "
                "for audit-trail integrity"
            )
        existing = self.get_purchase_order(po_id)
        if not existing:
            raise ValueError(f"purchase_order {po_id!r} not found")
        current_state = str(existing.get("status") or "")
        if not validate_po_transition(current_state, target_state):
            raise IllegalPurchaseOrderTransitionError(current_state, target_state)
        now = _now_iso()
        patch: Dict[str, Any] = {"status": target_state, "updated_at": now}
        if target_state == POStatus.APPROVED.value:
            patch["approved_by"] = actor_id
            patch["approved_at"] = now
        self._patch_purchase_order(po_id, patch)

        if hasattr(self, "append_audit_event"):
            self.append_audit_event({
                "box_id": po_id,
                "box_type": "purchase_order",
                "event_type": f"purchase_order_{target_state}",
                "from_state": current_state,
                "to_state": target_state,
                "actor_type": "user",
                "actor_id": actor_id,
                "organization_id": existing.get("organization_id"),
                "policy_version": CURRENT_PO_POLICY_VERSION,
                "decision_reason": reason or None,
                "idempotency_key": f"po-{target_state}:{po_id}:{now}",
            })
        return self.get_purchase_order(po_id)  # type: ignore[return-value]

    def set_po_erp_id(
        self, po_id: str, erp_po_id: str, *, actor_id: str
    ) -> Optional[Dict[str, Any]]:
        """Stamp the ERP-side PO id after issuance + emit an audit row.

        Issuing an approved PO to the ERP is a side effect, not a state
        transition (the box stays APPROVED until goods are received), so
        this patches ``erp_po_id`` directly rather than going through the
        state machine.
        """
        existing = self.get_purchase_order(po_id)
        if not existing:
            raise ValueError(f"purchase_order {po_id!r} not found")
        now = _now_iso()
        self._patch_purchase_order(po_id, {"erp_po_id": erp_po_id, "updated_at": now})
        if hasattr(self, "append_audit_event"):
            self.append_audit_event({
                "box_id": po_id,
                "box_type": "purchase_order",
                "event_type": "purchase_order_issued_to_erp",
                "actor_type": "agent",
                "actor_id": actor_id or "procurement",
                "organization_id": existing.get("organization_id"),
                "policy_version": CURRENT_PO_POLICY_VERSION,
                "payload_json": {"erp_po_id": erp_po_id},
                "idempotency_key": f"po-issued:{po_id}:{erp_po_id}",
            })
        return self.get_purchase_order(po_id)

    _PO_AMENDABLE_FIELDS = frozenset({
        "vendor_name", "vendor_id", "po_number", "order_date", "expected_delivery",
        "line_items", "subtotal", "tax_amount", "total_amount", "currency",
        "notes", "department", "project", "ship_to_address",
    })

    def amend_purchase_order_box(
        self, po_id: str, fields: Dict[str, Any], *, actor_id: str
    ) -> Optional[Dict[str, Any]]:
        """Edit a DRAFT PO's master-data fields + emit an audit row.

        Amendments are master-data edits (vendor, amount, line items), not
        state transitions, so they go through the full upsert rather than
        the state machine. The caller (skill) gates this to ``draft`` —
        editing after submission would bypass approval.
        """
        existing = self.get_purchase_order(po_id)
        if not existing:
            raise ValueError(f"purchase_order {po_id!r} not found")
        merged = dict(existing)
        changed = {}
        for key, value in (fields or {}).items():
            if key in self._PO_AMENDABLE_FIELDS:
                merged[key] = value
                changed[key] = value
        merged["po_id"] = po_id
        merged["organization_id"] = existing.get("organization_id")
        self.save_purchase_order(merged)
        if hasattr(self, "append_audit_event"):
            self.append_audit_event({
                "box_id": po_id,
                "box_type": "purchase_order",
                "event_type": "purchase_order_amended",
                "actor_type": "user",
                "actor_id": actor_id or "procurement",
                "organization_id": existing.get("organization_id"),
                "policy_version": CURRENT_PO_POLICY_VERSION,
                "payload_json": {"changed_fields": sorted(changed.keys())},
                "idempotency_key": f"po-amended:{po_id}:{_now_iso()}",
            })
        return self.get_purchase_order(po_id)

    def record_po_receipt(
        self,
        po_id: str,
        received_lines: Optional[List[Dict[str, Any]]] = None,
        *,
        actor_id: str,
    ) -> Dict[str, Any]:
        """Apply a goods receipt to a PO's lines and report fully-vs-partial.

        ``received_lines`` is a list of ``{"index": i, "quantity_received": q}``
        increments. When omitted, every line is marked fully received.
        Updates each line's ``quantity_received`` and persists. Returns
        ``{"fully_received": bool, "line_items": [...]}``. Does NOT move the
        box state — the caller transitions to partially/fully_received based
        on the returned flag.
        """
        existing = self.get_purchase_order(po_id)
        if not existing:
            raise ValueError(f"purchase_order {po_id!r} not found")
        lines = list(existing.get("line_items") or [])

        if received_lines is None:
            for li in lines:
                li["quantity_received"] = li.get("quantity") or 0
        else:
            by_index = {int(r.get("index", -1)): r for r in received_lines if isinstance(r, dict)}
            for i, li in enumerate(lines):
                r = by_index.get(i)
                if r is not None:
                    prior = float(li.get("quantity_received") or 0)
                    li["quantity_received"] = prior + float(r.get("quantity_received") or 0)

        def _fully(li: Dict[str, Any]) -> bool:
            ordered = float(li.get("quantity") or 0)
            return float(li.get("quantity_received") or 0) >= ordered if ordered else True

        fully_received = all(_fully(li) for li in lines) if lines else True

        merged = dict(existing)
        merged["line_items"] = lines
        merged["po_id"] = po_id
        merged["organization_id"] = existing.get("organization_id")
        self.save_purchase_order(merged)
        return {"fully_received": fully_received, "line_items": lines}

    def _patch_purchase_order(self, po_id: str, kwargs: Dict[str, Any]) -> None:
        bad = set(kwargs.keys()) - _PURCHASE_ORDER_ALLOWED_COLUMNS
        if bad:
            raise ValueError(f"Disallowed columns for purchase_order update: {bad}")
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = %s" for k in kwargs.keys())
        sql = f"UPDATE purchase_orders SET {set_clause} WHERE po_id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*kwargs.values(), po_id))
            conn.commit()

    # ------------------------------------------------------------------
    # Goods Receipts
    # ------------------------------------------------------------------

    def save_goods_receipt(self, gr: Dict[str, Any]) -> Dict[str, Any]:
        org_id = str(gr.get("organization_id") or "").strip()
        gr_id = str(gr.get("gr_id") or "").strip()
        if not org_id:
            raise ValueError("organization_id is required")
        if not gr_id:
            raise ValueError("gr_id is required")
        self.initialize()
        now = _now_iso()
        params = (
            gr_id,
            org_id,
            str(gr.get("gr_number") or ""),
            str(gr.get("po_id") or ""),
            str(gr.get("po_number") or ""),
            str(gr.get("vendor_id") or ""),
            str(gr.get("vendor_name") or ""),
            str(gr.get("receipt_date") or ""),
            str(gr.get("received_by") or ""),
            str(gr.get("delivery_note") or ""),
            str(gr.get("carrier") or ""),
            _encode_json(gr.get("line_items") or []),
            str(gr.get("status") or "pending"),
            str(gr.get("notes") or ""),
            str(gr.get("created_at") or now),
        )
        sql = (
            """
            INSERT INTO goods_receipts (
                gr_id, organization_id, gr_number, po_id, po_number,
                vendor_id, vendor_name, receipt_date, received_by,
                delivery_note, carrier, line_items_json,
                status, notes, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (gr_id) DO UPDATE SET
                gr_number = EXCLUDED.gr_number,
                po_id = EXCLUDED.po_id,
                po_number = EXCLUDED.po_number,
                vendor_id = EXCLUDED.vendor_id,
                vendor_name = EXCLUDED.vendor_name,
                receipt_date = EXCLUDED.receipt_date,
                received_by = EXCLUDED.received_by,
                delivery_note = EXCLUDED.delivery_note,
                carrier = EXCLUDED.carrier,
                line_items_json = EXCLUDED.line_items_json,
                status = EXCLUDED.status,
                notes = EXCLUDED.notes
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
        return self.get_goods_receipt(gr_id) or {}

    def get_goods_receipt(self, gr_id: str) -> Optional[Dict[str, Any]]:
        if not gr_id:
            return None
        self.initialize()
        sql = "SELECT * FROM goods_receipts WHERE gr_id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (gr_id,))
            row = cur.fetchone()
        return self._gr_row_to_dict(row)

    def list_goods_receipts_for_po(self, po_id: str) -> List[Dict[str, Any]]:
        if not po_id:
            return []
        self.initialize()
        sql = (
            "SELECT * FROM goods_receipts "
            "WHERE po_id = %s "
            "ORDER BY created_at DESC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (po_id,))
            rows = cur.fetchall()
        return [self._gr_row_to_dict(r) for r in rows if r is not None]

    # ------------------------------------------------------------------
    # 3-Way Matches
    # ------------------------------------------------------------------

    def save_three_way_match(self, match: Dict[str, Any]) -> Dict[str, Any]:
        org_id = str(match.get("organization_id") or "").strip()
        match_id = str(match.get("match_id") or "").strip()
        if not org_id:
            raise ValueError("organization_id is required")
        if not match_id:
            match_id = uuid.uuid4().hex[:12]
            match["match_id"] = match_id
        self.initialize()
        now = _now_iso()
        params = (
            match_id,
            org_id,
            str(match.get("invoice_id") or ""),
            str(match.get("po_id") or "") or None,
            str(match.get("gr_id") or "") or None,
            str(match.get("status") or "pending"),
            _encode_json(match.get("exceptions") or []),
            float(match.get("po_amount") or 0.0),
            float(match.get("gr_amount") or 0.0),
            float(match.get("invoice_amount") or 0.0),
            float(match.get("price_variance") or 0.0),
            float(match.get("quantity_variance") or 0.0),
            str(match.get("override_by") or "") or None,
            str(match.get("override_reason") or ""),
            str(match.get("matched_at") or now),
            str(match.get("created_at") or now),
        )
        sql = (
            """
            INSERT INTO three_way_matches (
                match_id, organization_id, invoice_id, po_id, gr_id,
                status, exceptions_json,
                po_amount, gr_amount, invoice_amount,
                price_variance, quantity_variance,
                override_by, override_reason,
                matched_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (match_id) DO UPDATE SET
                invoice_id = EXCLUDED.invoice_id,
                po_id = EXCLUDED.po_id,
                gr_id = EXCLUDED.gr_id,
                status = EXCLUDED.status,
                exceptions_json = EXCLUDED.exceptions_json,
                po_amount = EXCLUDED.po_amount,
                gr_amount = EXCLUDED.gr_amount,
                invoice_amount = EXCLUDED.invoice_amount,
                price_variance = EXCLUDED.price_variance,
                quantity_variance = EXCLUDED.quantity_variance,
                override_by = EXCLUDED.override_by,
                override_reason = EXCLUDED.override_reason,
                matched_at = EXCLUDED.matched_at
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
        return self.get_three_way_match(match_id) or {}

    def get_three_way_match(self, match_id: str) -> Optional[Dict[str, Any]]:
        if not match_id:
            return None
        self.initialize()
        sql = "SELECT * FROM three_way_matches WHERE match_id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (match_id,))
            row = cur.fetchone()
        return self._match_row_to_dict(row)

    def get_three_way_match_by_invoice(
        self, invoice_id: str
    ) -> Optional[Dict[str, Any]]:
        if not invoice_id:
            return None
        self.initialize()
        sql = (
            "SELECT * FROM three_way_matches "
            "WHERE invoice_id = %s "
            "ORDER BY matched_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (invoice_id,))
            row = cur.fetchone()
        return self._match_row_to_dict(row)

    def list_three_way_matches(
        self,
        organization_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        if not organization_id:
            return []
        self.initialize()
        if status:
            sql = (
                "SELECT * FROM three_way_matches "
                "WHERE organization_id = %s AND status = %s "
                "ORDER BY matched_at DESC LIMIT %s"
            )
            params: tuple = (organization_id, status, limit)
        else:
            sql = (
                "SELECT * FROM three_way_matches "
                "WHERE organization_id = %s "
                "ORDER BY matched_at DESC LIMIT %s"
            )
            params = (organization_id, limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [self._match_row_to_dict(r) for r in rows if r is not None]
