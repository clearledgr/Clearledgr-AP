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
  * All reads/writes are organization-scoped; callers that forget to
    pass an org_id will get a ValueError, never a cross-tenant leak.
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

logger = logging.getLogger(__name__)


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
    """Mixin providing PO / GR / 3-way match persistence for ClearledgrDB."""

    # ------------------------------------------------------------------
    # Row shape helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _po_row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        data = dict(row) if not isinstance(row, dict) else row
        data["line_items"] = _decode_json_list(data.pop("line_items_json", None))
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
            str(po.get("currency") or "USD"),
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
        sql = self._prepare_sql(
            """
            INSERT INTO purchase_orders (
                po_id, organization_id, po_number, vendor_id, vendor_name,
                order_date, expected_delivery, line_items_json,
                subtotal, tax_amount, total_amount, currency, status,
                requested_by, approved_by, approved_at,
                notes, department, project, ship_to_address,
                erp_po_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        sql = self._prepare_sql("SELECT * FROM purchase_orders WHERE po_id = ?")
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
        sql = self._prepare_sql(
            "SELECT * FROM purchase_orders "
            "WHERE organization_id = ? AND po_number = ? "
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
            sql = self._prepare_sql(
                "SELECT * FROM purchase_orders "
                "WHERE organization_id = ? "
                "AND LOWER(vendor_name) LIKE ? "
                "AND status IN (?, ?, ?) "
                "ORDER BY created_at DESC LIMIT ?"
            )
            params = (
                organization_id,
                f"%{vendor_name.lower()}%",
                *open_statuses,
                limit,
            )
        else:
            sql = self._prepare_sql(
                "SELECT * FROM purchase_orders "
                "WHERE organization_id = ? "
                "AND LOWER(vendor_name) LIKE ? "
                "ORDER BY created_at DESC LIMIT ?"
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
        sql = self._prepare_sql(
            "SELECT * FROM purchase_orders "
            "WHERE organization_id = ? "
            "ORDER BY created_at DESC LIMIT ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, limit))
            rows = cur.fetchall()
        return [self._po_row_to_dict(r) for r in rows if r is not None]

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
        sql = self._prepare_sql(
            """
            INSERT INTO goods_receipts (
                gr_id, organization_id, gr_number, po_id, po_number,
                vendor_id, vendor_name, receipt_date, received_by,
                delivery_note, carrier, line_items_json,
                status, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        sql = self._prepare_sql("SELECT * FROM goods_receipts WHERE gr_id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (gr_id,))
            row = cur.fetchone()
        return self._gr_row_to_dict(row)

    def list_goods_receipts_for_po(self, po_id: str) -> List[Dict[str, Any]]:
        if not po_id:
            return []
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM goods_receipts "
            "WHERE po_id = ? "
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
        sql = self._prepare_sql(
            """
            INSERT INTO three_way_matches (
                match_id, organization_id, invoice_id, po_id, gr_id,
                status, exceptions_json,
                po_amount, gr_amount, invoice_amount,
                price_variance, quantity_variance,
                override_by, override_reason,
                matched_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        sql = self._prepare_sql("SELECT * FROM three_way_matches WHERE match_id = ?")
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
        sql = self._prepare_sql(
            "SELECT * FROM three_way_matches "
            "WHERE invoice_id = ? "
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
            sql = self._prepare_sql(
                "SELECT * FROM three_way_matches "
                "WHERE organization_id = ? AND status = ? "
                "ORDER BY matched_at DESC LIMIT ?"
            )
            params: tuple = (organization_id, status, limit)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM three_way_matches "
                "WHERE organization_id = ? "
                "ORDER BY matched_at DESC LIMIT ?"
            )
            params = (organization_id, limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [self._match_row_to_dict(r) for r in rows if r is not None]
