"""FX rates store — Module 9 (multi-currency conversion).

Mixed into ``SoldenDB``. Provides:

  - CRUD over ``fx_rates`` rows.
  - The conversion-lookup query
    (``find_fx_rate(org, from, to, as_of)``) that powers
    ``services.workspace_fx.convert``.

Lookup precedence inside ``find_fx_rate``:
  1. Manual rate for this exact (from, to, as_of) → preferred
     (operator's last word).
  2. Most recent manual rate where as_of_date <= the requested date.
  3. Most recent ERP-sourced rate where as_of_date <= the requested
     date.
  4. None — caller falls through to inverse / triangulation /
     identity in the conversion service.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


VALID_FX_SOURCES = frozenset({"manual", "erp", "system"})


class FxRateStoreMixin:

    def upsert_fx_rate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or replace a rate for (org, from, to, as_of, source)."""
        from_ccy = (payload.get("from_currency") or "").strip().upper()
        to_ccy = (payload.get("to_currency") or "").strip().upper()
        if len(from_ccy) != 3 or len(to_ccy) != 3:
            raise ValueError("from/to currency must be a 3-letter ISO code")

        source = (payload.get("source") or "manual").strip().lower()
        if source not in VALID_FX_SOURCES:
            raise ValueError(f"invalid fx source: {source!r}")

        rate = Decimal(str(payload.get("rate") or 0))
        if rate <= 0:
            raise ValueError("rate must be > 0")

        as_of = payload.get("as_of_date") or date.today().isoformat()
        if isinstance(as_of, datetime):
            as_of = as_of.date().isoformat()
        elif isinstance(as_of, date):
            as_of = as_of.isoformat()

        rate_id = payload.get("id") or f"fx-{uuid.uuid4().hex[:16]}"
        org_id = payload["organization_id"]

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO fx_rates
                  (id, organization_id, from_currency, to_currency, rate,
                   as_of_date, source, note, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (organization_id, from_currency, to_currency, as_of_date, source)
                DO UPDATE SET rate = EXCLUDED.rate, note = EXCLUDED.note,
                              created_by = EXCLUDED.created_by
                """,
                (
                    rate_id, org_id, from_ccy, to_ccy, str(rate),
                    as_of, source,
                    payload.get("note"),
                    payload.get("created_by") or "",
                ),
            )
            conn.commit()
        return self.get_fx_rate_by_key(
            org_id, from_ccy, to_ccy, as_of, source,
        ) or {}

    def get_fx_rate_by_key(
        self, organization_id: str, from_currency: str, to_currency: str,
        as_of_date: str, source: str,
    ) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM fx_rates "
                "WHERE organization_id = %s AND from_currency = %s "
                "AND to_currency = %s AND as_of_date = %s AND source = %s",
                (organization_id, from_currency.upper(), to_currency.upper(),
                 as_of_date, source),
            )
            row = cur.fetchone()
        return _row_to_rate(row) if row else None

    def list_fx_rates(
        self, organization_id: str, *,
        from_currency: Optional[str] = None,
        to_currency: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        clauses = ["organization_id = %s"]
        args: List[Any] = [organization_id]
        if from_currency:
            clauses.append("from_currency = %s")
            args.append(from_currency.upper())
        if to_currency:
            clauses.append("to_currency = %s")
            args.append(to_currency.upper())
        sql = (
            f"SELECT * FROM fx_rates WHERE {' AND '.join(clauses)} "
            "ORDER BY as_of_date DESC, source LIMIT %s"
        )
        args.append(int(limit))
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(args))
            return [_row_to_rate(r) for r in cur.fetchall()]

    def find_fx_rate(
        self, organization_id: str,
        from_currency: str, to_currency: str,
        as_of_date: str,
    ) -> Optional[Dict[str, Any]]:
        """The conversion-lookup query: latest manual or ERP rate for
        the pair where as_of_date <= the requested date. Manual wins
        on ties."""
        from_ccy = (from_currency or "").upper()
        to_ccy = (to_currency or "").upper()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM fx_rates
                WHERE organization_id = %s
                  AND from_currency = %s AND to_currency = %s
                  AND as_of_date <= %s
                ORDER BY as_of_date DESC,
                         CASE source WHEN 'manual' THEN 0 WHEN 'erp' THEN 1 ELSE 2 END
                LIMIT 1
                """,
                (organization_id, from_ccy, to_ccy, as_of_date),
            )
            row = cur.fetchone()
        return _row_to_rate(row) if row else None

    def delete_fx_rate(self, rate_id: str, organization_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM fx_rates "
                "WHERE id = %s AND organization_id = %s",
                (rate_id, organization_id),
            )
            deleted = cur.rowcount or 0
            conn.commit()
        return bool(deleted)


def _row_to_rate(row: Any) -> Dict[str, Any]:
    if hasattr(row, "_asdict"):
        d = row._asdict()
    elif hasattr(row, "keys"):
        d = dict(row)
    else:
        keys = (
            "id", "organization_id", "from_currency", "to_currency",
            "rate", "as_of_date", "source", "note", "created_by", "created_at",
        )
        d = dict(zip(keys, row))

    rate = d.get("rate")
    if isinstance(rate, Decimal):
        d["rate"] = float(rate)
    elif isinstance(rate, str):
        try:
            d["rate"] = float(rate)
        except (ValueError, TypeError):
            d["rate"] = None

    as_of = d.get("as_of_date")
    if isinstance(as_of, (date, datetime)):
        d["as_of_date"] = as_of.isoformat()[:10]

    created_at = d.get("created_at")
    if isinstance(created_at, datetime):
        d["created_at"] = created_at.astimezone(timezone.utc).isoformat()
    return d
