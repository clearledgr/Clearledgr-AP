"""Bank statement persistence (Wave 2 / C6).

Two tables:

  * ``bank_statement_imports`` — one row per uploaded file (CAMT.053
    / OFX). Holds metadata + reconciled-count for the dashboard.
  * ``bank_statement_lines`` — one row per statement transaction.
    Each line gets matched against a ``payment_confirmations`` row
    via the matcher service in ``bank_reconciliation_matcher.py``.

The composite uniqueness on
``(organization_id, import_id, line_index)`` makes re-importing the
same statement a no-op at the row level. The matcher is deliberately
separate so the import path stays cheap.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_VALID_MATCH_STATUSES = frozenset({
    "unmatched", "matched", "reconciled", "ignored",
})


class BankStatementStore:
    """Mixin: CRUD for bank_statement_imports + bank_statement_lines."""

    # ── Imports ────────────────────────────────────────────────────

    def create_bank_statement_import(
        self,
        *,
        organization_id: str,
        filename: Optional[str],
        format: str,
        statement_iban: Optional[str] = None,
        statement_account: Optional[str] = None,
        statement_currency: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        opening_balance: Optional[Any] = None,
        closing_balance: Optional[Any] = None,
        line_count: int = 0,
        uploaded_by: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.initialize()
        import_id = f"BSI-{uuid.uuid4().hex[:24]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        sql = (
            "INSERT INTO bank_statement_imports "
            "(id, organization_id, filename, format, statement_iban, "
            " statement_account, statement_currency, from_date, to_date, "
            " opening_balance, closing_balance, line_count, matched_count, "
            " uploaded_by, uploaded_at, metadata_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        params = (
            import_id, organization_id, filename, format,
            statement_iban, statement_account, statement_currency,
            from_date, to_date,
            (Decimal(str(opening_balance)) if opening_balance is not None else None),
            (Decimal(str(closing_balance)) if closing_balance is not None else None),
            int(line_count or 0), 0,
            uploaded_by, now_iso,
            (json.dumps(metadata) if metadata else None),
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
        return self.get_bank_statement_import(import_id) or {
            "id": import_id,
            "organization_id": organization_id,
            "format": format,
            "filename": filename,
            "uploaded_at": now_iso,
        }

    def get_bank_statement_import(self, import_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM bank_statement_imports WHERE id = %s",
                (import_id,),
            )
            row = cur.fetchone()
        return self._decode_bank_import_row(row)

    def list_bank_statement_imports(
        self, organization_id: str, *, limit: int = 100,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit or 100), 1000))
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM bank_statement_imports "
                "WHERE organization_id = %s "
                "ORDER BY uploaded_at DESC LIMIT %s",
                (organization_id, safe_limit),
            )
            rows = cur.fetchall()
        return [
            d for d in (self._decode_bank_import_row(r) for r in rows)
            if d is not None
        ]

    def update_bank_statement_import_match_count(
        self, import_id: str, matched_count: int,
    ) -> None:
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bank_statement_imports "
                "SET matched_count = %s WHERE id = %s",
                (int(matched_count or 0), import_id),
            )
            conn.commit()

    # ── Lines ──────────────────────────────────────────────────────

    def insert_bank_statement_line(
        self,
        *,
        organization_id: str,
        import_id: str,
        line_index: int,
        amount: Any,
        currency: str,
        value_date: Optional[str] = None,
        booking_date: Optional[str] = None,
        description: Optional[str] = None,
        counterparty: Optional[str] = None,
        counterparty_iban: Optional[str] = None,
        bank_reference: Optional[str] = None,
        end_to_end_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.initialize()
        line_id = f"BSL-{uuid.uuid4().hex[:24]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        sql = (
            "INSERT INTO bank_statement_lines "
            "(id, organization_id, import_id, line_index, value_date, "
            " booking_date, amount, currency, description, counterparty, "
            " counterparty_iban, bank_reference, end_to_end_id, "
            " match_status, created_at, metadata_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            " 'unmatched', %s, %s)"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (
                    line_id, organization_id, import_id, int(line_index),
                    value_date, booking_date,
                    Decimal(str(amount)), currency,
                    description, counterparty, counterparty_iban,
                    bank_reference, end_to_end_id,
                    now_iso,
                    (json.dumps(metadata) if metadata else None),
                ))
                conn.commit()
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate key" in msg or "unique constraint" in msg:
                logger.info(
                    "bank_statement_lines: duplicate line skipped "
                    "import=%s line_index=%s",
                    import_id, line_index,
                )
                return {"duplicate": True}
            raise
        return self.get_bank_statement_line(line_id) or {"id": line_id}

    def get_bank_statement_line(self, line_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM bank_statement_lines WHERE id = %s",
                (line_id,),
            )
            row = cur.fetchone()
        return self._decode_bank_line_row(row)

    def list_bank_statement_lines(
        self,
        organization_id: str,
        *,
        import_id: Optional[str] = None,
        match_status: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        clauses = ["organization_id = %s"]
        params: List[Any] = [organization_id]
        if import_id:
            clauses.append("import_id = %s")
            params.append(import_id)
        if match_status:
            if match_status not in _VALID_MATCH_STATUSES:
                raise ValueError(
                    f"invalid match_status filter: {match_status!r}"
                )
            clauses.append("match_status = %s")
            params.append(match_status)
        safe_limit = max(1, min(int(limit or 500), 5000))
        params.append(safe_limit)
        sql = (
            "SELECT * FROM bank_statement_lines "
            "WHERE " + " AND ".join(clauses) + " "
            "ORDER BY value_date DESC NULLS LAST, line_index ASC "
            "LIMIT %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [
            d for d in (self._decode_bank_line_row(r) for r in rows)
            if d is not None
        ]

    def update_bank_statement_line_match(
        self,
        line_id: str,
        *,
        payment_confirmation_id: Optional[str],
        match_status: str,
        match_confidence: Optional[float] = None,
        match_reason: Optional[str] = None,
        matched_by: Optional[str] = None,
    ) -> None:
        self.initialize()
        if match_status not in _VALID_MATCH_STATUSES:
            raise ValueError(
                f"invalid match_status: {match_status!r}"
            )
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bank_statement_lines "
                "SET payment_confirmation_id = %s, match_status = %s, "
                "    match_confidence = %s, match_reason = %s, "
                "    matched_at = %s, matched_by = %s "
                "WHERE id = %s",
                (
                    payment_confirmation_id, match_status,
                    match_confidence, match_reason,
                    now_iso, matched_by, line_id,
                ),
            )
            conn.commit()

    # ── Helpers ────────────────────────────────────────────────────

    def _decode_bank_import_row(self, row) -> Optional[Dict[str, Any]]:
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
        for col in ("opening_balance", "closing_balance"):
            if out.get(col) is not None and not isinstance(out[col], Decimal):
                try:
                    out[col] = Decimal(str(out[col]))
                except Exception:
                    out[col] = None
        return out

    def _decode_bank_line_row(self, row) -> Optional[Dict[str, Any]]:
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
        if out.get("amount") is not None and not isinstance(out["amount"], Decimal):
            try:
                out["amount"] = Decimal(str(out["amount"]))
            except Exception:
                out["amount"] = None
        return out
