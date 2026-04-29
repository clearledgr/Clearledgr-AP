"""Payment confirmation persistence (Wave 2 / C2 + C3).

One row per (AP item × confirmed/failed payment event). The table
holds the ledger of "did this bill get paid, by what rail, when did
it settle, who recorded it" — the data the AP cycle reference doc
requires for Stage 8/9 traceability + bank reconciliation matching.

Composite uniqueness on (organization_id, source, payment_id,
ap_item_id) makes the table idempotent: duplicate webhook deliveries
from the same ERP for the same (payment, bill) pair never create
two rows, while still allowing a single ERP-native payment to clear
multiple bills (one row per bill). Callers race-tolerantly either
pre-check via ``get_payment_confirmation_by_external_id`` or catch
the integrity violation on insert and re-fetch.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_VALID_STATUSES = frozenset({"confirmed", "failed", "disputed"})


class PaymentConfirmationConflict(Exception):
    """Raised when an attempt to insert a confirmation collides with
    an existing (org, source, payment_id) row."""


class PaymentConfirmationsStore:
    """Mixin: CRUD + read paths for ``payment_confirmations``."""

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_payment_confirmation(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        """Single confirmation by its primary key id."""
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM payment_confirmations WHERE id = %s",
                (confirmation_id,),
            )
            row = cur.fetchone()
        return self._decode_row(row)

    def get_payment_confirmation_by_external_id(
        self,
        organization_id: str,
        source: str,
        payment_id: str,
        ap_item_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Look up by the (org, source, external payment id [, ap_item_id])
        compound key.

        ``ap_item_id`` was added in v60 because one ERP-native payment
        can clear multiple bills (one BillPayment per N Bills); each
        bill becomes its own row. When ``ap_item_id`` is omitted the
        lookup falls back to "any matching row" — useful for the
        operator-facing search where the AP item context isn't carried.
        """
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            if ap_item_id is not None:
                cur.execute(
                    "SELECT * FROM payment_confirmations "
                    "WHERE organization_id = %s AND source = %s "
                    "AND payment_id = %s AND ap_item_id = %s "
                    "LIMIT 1",
                    (organization_id, source, payment_id, ap_item_id),
                )
            else:
                cur.execute(
                    "SELECT * FROM payment_confirmations "
                    "WHERE organization_id = %s AND source = %s "
                    "AND payment_id = %s "
                    "LIMIT 1",
                    (organization_id, source, payment_id),
                )
            row = cur.fetchone()
        return self._decode_row(row)

    def list_payment_confirmations_for_ap_item(
        self, organization_id: str, ap_item_id: str,
    ) -> List[Dict[str, Any]]:
        """All confirmation events for one AP item, newest first.

        An AP item can have multiple rows: a failed attempt followed
        by a successful retry shows the chain.
        """
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM payment_confirmations "
                "WHERE organization_id = %s AND ap_item_id = %s "
                "ORDER BY settlement_at DESC NULLS LAST, created_at DESC",
                (organization_id, ap_item_id),
            )
            rows = cur.fetchall()
        return [d for d in (self._decode_row(r) for r in rows) if d is not None]

    def list_payment_confirmations(
        self,
        organization_id: str,
        *,
        status: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Org-wide payment-confirmation feed with optional filters.

        Used by:
          * dashboard payment-tracking surface (filter by status to
            spot failures / disputes)
          * bank-rec sweep (status=confirmed + date window)
          * compliance export (full feed for a date range)
        """
        self.initialize()
        clauses = ["organization_id = %s"]
        params: List[Any] = [organization_id]
        if status:
            if status not in _VALID_STATUSES:
                raise ValueError(f"invalid status filter: {status!r}")
            clauses.append("status = %s")
            params.append(status)
        if source:
            clauses.append("source = %s")
            params.append(source)
        if from_ts:
            clauses.append("settlement_at >= %s")
            params.append(from_ts)
        if to_ts:
            clauses.append("settlement_at <= %s")
            params.append(to_ts)
        safe_limit = max(1, min(int(limit or 100), 1000))
        params.append(safe_limit)
        sql = (
            "SELECT * FROM payment_confirmations "
            "WHERE " + " AND ".join(clauses) + " "
            "ORDER BY settlement_at DESC NULLS LAST, created_at DESC "
            "LIMIT %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [d for d in (self._decode_row(r) for r in rows) if d is not None]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create_payment_confirmation(
        self,
        *,
        organization_id: str,
        ap_item_id: str,
        payment_id: str,
        source: str,
        status: str = "confirmed",
        settlement_at: Optional[str] = None,
        amount: Optional[Any] = None,
        currency: Optional[str] = None,
        method: Optional[str] = None,
        payment_reference: Optional[str] = None,
        bank_account_last4: Optional[str] = None,
        failure_reason: Optional[str] = None,
        notes: Optional[str] = None,
        created_by: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Insert a new confirmation row.

        Raises ``PaymentConfirmationConflict`` when a row with the same
        (organization_id, source, payment_id) already exists — caller
        decides whether to treat as duplicate (return existing) or
        flag as a race condition.

        Validates ``status`` against the canonical set. Coerces
        ``amount`` to a Decimal at the boundary so callers can pass
        floats / strings / Decimals interchangeably.
        """
        self.initialize()
        if not organization_id:
            raise ValueError("organization_id required")
        if not ap_item_id:
            raise ValueError("ap_item_id required")
        if not payment_id:
            raise ValueError("payment_id required")
        if not source:
            raise ValueError("source required")
        clean_status = (status or "confirmed").strip().lower()
        if clean_status not in _VALID_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}"
            )

        coerced_amount: Optional[Decimal] = None
        if amount is not None and amount != "":
            try:
                coerced_amount = Decimal(str(amount))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"amount must be numeric: {exc}") from exc

        confirmation_id = f"PC-{uuid.uuid4().hex[:24]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata) if metadata else None

        sql = (
            "INSERT INTO payment_confirmations "
            "(id, organization_id, ap_item_id, payment_id, source, status, "
            " settlement_at, amount, currency, method, payment_reference, "
            " bank_account_last4, failure_reason, notes, "
            " created_at, created_by, metadata_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        params = (
            confirmation_id, organization_id, ap_item_id, payment_id, source,
            clean_status, settlement_at, coerced_amount,
            (currency or None), (method or None),
            (payment_reference or None), (bank_account_last4 or None),
            (failure_reason or None), (notes or None),
            now_iso, (created_by or None), metadata_json,
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                conn.commit()
        except Exception as exc:
            msg = str(exc).lower()
            if (
                "duplicate key" in msg
                or "unique constraint" in msg
                or "idx_payment_confirmations_external" in msg
            ):
                raise PaymentConfirmationConflict(
                    f"confirmation already exists for "
                    f"(org={organization_id!r}, source={source!r}, "
                    f"payment_id={payment_id!r})"
                ) from exc
            raise
        row = self.get_payment_confirmation(confirmation_id)
        return row or {
            "id": confirmation_id,
            "organization_id": organization_id,
            "ap_item_id": ap_item_id,
            "payment_id": payment_id,
            "source": source,
            "status": clean_status,
            "settlement_at": settlement_at,
            "amount": coerced_amount,
            "currency": currency,
            "method": method,
            "payment_reference": payment_reference,
            "bank_account_last4": bank_account_last4,
            "failure_reason": failure_reason,
            "notes": notes,
            "created_at": now_iso,
            "created_by": created_by,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _decode_row(self, row) -> Optional[Dict[str, Any]]:
        """Convert a DB row to a public dict (parse metadata_json)."""
        if row is None:
            return None
        out = dict(row)
        raw_meta = out.pop("metadata_json", None)
        if raw_meta:
            try:
                out["metadata"] = (
                    json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                )
            except Exception:
                out["metadata"] = {}
        else:
            out["metadata"] = {}
        # Numeric → Decimal at the API boundary (psycopg returns
        # Decimal for NUMERIC columns; defensive normalize).
        if out.get("amount") is not None and not isinstance(out["amount"], Decimal):
            try:
                out["amount"] = Decimal(str(out["amount"]))
            except Exception:
                out["amount"] = None
        return out
