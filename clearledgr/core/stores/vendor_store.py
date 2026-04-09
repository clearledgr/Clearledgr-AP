"""Vendor intelligence store mixin for ClearledgrDB.

Tracks vendor profiles (patterns, risk signals) and per-vendor invoice history
so the AP reasoning layer can make context-aware decisions.

``VendorStore`` is a mixin — no ``__init__``, expects:
  self.connect(), self._prepare_sql(), self.use_postgres
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TABLE_VENDOR_PROFILES = """
CREATE TABLE IF NOT EXISTS vendor_profiles (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    vendor_aliases TEXT NOT NULL DEFAULT '[]',
    sender_domains TEXT NOT NULL DEFAULT '[]',
    typical_gl_code TEXT,
    requires_po INTEGER NOT NULL DEFAULT 0,
    contract_amount REAL,
    payment_terms TEXT,
    invoice_count INTEGER NOT NULL DEFAULT 0,
    last_invoice_date TEXT,
    last_invoice_amount REAL,
    avg_invoice_amount REAL,
    amount_stddev REAL,
    typical_invoice_day INTEGER,
    bank_details_changed_at TEXT,
    always_approved INTEGER NOT NULL DEFAULT 0,
    approval_override_rate REAL NOT NULL DEFAULT 0.0,
    anomaly_flags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    -- Phase 2.1.a: Fernet-encrypted bank details (DESIGN_THESIS.md §19).
    -- Never store plaintext IBANs or account numbers in metadata.
    bank_details_encrypted TEXT,
    UNIQUE(organization_id, vendor_name)
)
"""

_TABLE_VENDOR_INVOICE_HISTORY = """
CREATE TABLE IF NOT EXISTS vendor_invoice_history (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    ap_item_id TEXT NOT NULL,
    invoice_number TEXT,
    invoice_date TEXT,
    amount REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    final_state TEXT,
    exception_code TEXT,
    was_approved INTEGER NOT NULL DEFAULT 0,
    approval_override INTEGER NOT NULL DEFAULT 0,
    agent_recommendation TEXT,
    human_decision TEXT,
    created_at TEXT NOT NULL
)
"""

_TABLE_VENDOR_DECISION_FEEDBACK = """
CREATE TABLE IF NOT EXISTS vendor_decision_feedback (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    ap_item_id TEXT,
    human_decision TEXT NOT NULL,
    agent_recommendation TEXT,
    decision_override INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    source_channel TEXT,
    actor_id TEXT,
    correlation_id TEXT,
    action_outcome TEXT,
    created_at TEXT NOT NULL
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except Exception:
            pass
    return None


class VendorStore:
    """Mixin providing vendor intelligence persistence methods."""

    # ------------------------------------------------------------------ #
    # Schema SQL (consumed by database.py initialize())                   #
    # ------------------------------------------------------------------ #

    VENDOR_PROFILE_TABLE_SQL = _TABLE_VENDOR_PROFILES
    VENDOR_INVOICE_HISTORY_TABLE_SQL = _TABLE_VENDOR_INVOICE_HISTORY
    VENDOR_DECISION_FEEDBACK_TABLE_SQL = _TABLE_VENDOR_DECISION_FEEDBACK

    # ------------------------------------------------------------------ #
    # vendor_profiles                                                      #
    # ------------------------------------------------------------------ #

    def get_vendor_profile(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, Any]]:
        """Return the vendor profile dict or None if not seen before."""
        sql = self._prepare_sql(
            "SELECT * FROM vendor_profiles WHERE organization_id = ? AND vendor_name = ?"
        )
        try:
            with self.connect() as conn:
                if self.use_postgres:
                    cur = conn.cursor()
                    cur.execute(sql, (organization_id, vendor_name))
                    row = cur.fetchone()
                    if row is None:
                        return None
                    parsed = dict(row)
                    for key, default in (
                        ("vendor_aliases", []),
                        ("sender_domains", []),
                        ("anomaly_flags", []),
                        ("metadata", {}),
                    ):
                        decoded = _loads(parsed.get(key))
                        parsed[key] = decoded if decoded is not None else default
                    return parsed
                else:
                    conn.row_factory = __import__("sqlite3").Row
                    cur = conn.cursor()
                    cur.execute(sql, (organization_id, vendor_name))
                    row = cur.fetchone()
                    if row is None:
                        return None
                    parsed = dict(row)
                    for key, default in (
                        ("vendor_aliases", []),
                        ("sender_domains", []),
                        ("anomaly_flags", []),
                        ("metadata", {}),
                    ):
                        decoded = _loads(parsed.get(key))
                        parsed[key] = decoded if decoded is not None else default
                    return parsed
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_profile failed: %s", exc)
            return None

    def get_vendor_profiles_bulk(
        self,
        organization_id: str,
        vendor_names: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Return vendor profiles keyed by canonical vendor name."""
        normalized_names = [
            str(name or "").strip()
            for name in (vendor_names or [])
            if str(name or "").strip()
        ]
        if not normalized_names:
            return {}

        placeholders = ", ".join("?" for _ in normalized_names)
        sql = self._prepare_sql(
            "SELECT * FROM vendor_profiles "
            f"WHERE organization_id = ? AND vendor_name IN ({placeholders})"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, *normalized_names))
                rows = cur.fetchall()
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_profiles_bulk failed: %s", exc)
            return {}

        profiles: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            parsed = dict(row)
            for key, default in (
                ("vendor_aliases", []),
                ("sender_domains", []),
                ("anomaly_flags", []),
                ("metadata", {}),
            ):
                decoded = _loads(parsed.get(key))
                parsed[key] = decoded if decoded is not None else default
            profiles[str(parsed.get("vendor_name") or "").strip()] = parsed
        return profiles

    def upsert_vendor_profile(
        self,
        organization_id: str,
        vendor_name: str,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Create or update a vendor profile with the given fields.

        Only whitelisted fields are written to prevent injection.
        Returns the updated profile dict.
        """
        _ALLOWED = {
            "vendor_aliases", "sender_domains", "typical_gl_code",
            "requires_po", "contract_amount", "payment_terms",
            "invoice_count", "last_invoice_date", "last_invoice_amount",
            "avg_invoice_amount", "amount_stddev", "typical_invoice_day",
            "bank_details_changed_at", "always_approved",
            "approval_override_rate", "anomaly_flags", "metadata",
            # Phase 2.1.a: Fernet ciphertext column. Direct callers must
            # pass the already-encrypted value; the typed accessors below
            # (set_vendor_bank_details / get_vendor_bank_details) handle
            # encryption + decryption + masking correctly.
            "bank_details_encrypted",
        }
        safe_fields = {k: v for k, v in fields.items() if k in _ALLOWED}

        # JSON-encode list/dict values
        for key in ("vendor_aliases", "sender_domains", "anomaly_flags", "metadata"):
            if key in safe_fields and isinstance(safe_fields[key], (list, dict)):
                safe_fields[key] = json.dumps(safe_fields[key])

        now = _now()
        existing = self.get_vendor_profile(organization_id, vendor_name)

        if existing is None:
            row_id = str(uuid.uuid4())
            cols = ["id", "organization_id", "vendor_name", "created_at", "updated_at"] + list(safe_fields.keys())
            vals = [row_id, organization_id, vendor_name, now, now] + list(safe_fields.values())
            placeholders = ", ".join(["?"] * len(cols))
            sql = self._prepare_sql(
                f"INSERT INTO vendor_profiles ({', '.join(cols)}) VALUES ({placeholders})"
            )
            try:
                with self.connect() as conn:
                    conn.execute(sql, vals)
                    conn.commit()
            except Exception as exc:
                logger.warning("[VendorStore] upsert insert failed: %s", exc)
        else:
            if not safe_fields:
                return existing
            set_clause = ", ".join(f"{k} = ?" for k in safe_fields)
            vals = list(safe_fields.values()) + [now, organization_id, vendor_name]
            sql = self._prepare_sql(
                f"UPDATE vendor_profiles SET {set_clause}, updated_at = ? "
                f"WHERE organization_id = ? AND vendor_name = ?"
            )
            try:
                with self.connect() as conn:
                    conn.execute(sql, vals)
                    conn.commit()
            except Exception as exc:
                logger.warning("[VendorStore] upsert update failed: %s", exc)

        return self.get_vendor_profile(organization_id, vendor_name) or {}

    # ------------------------------------------------------------------ #
    # Phase 2.1.a: Bank-details typed accessors                            #
    # ------------------------------------------------------------------ #

    def get_vendor_bank_details(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, str]]:
        """Decrypt and return the bank-details dict for a vendor.

        Returns the canonical normalized shape or ``None`` when no bank
        details are stored. Caller is responsible for masking before
        returning to user-facing surfaces.
        """
        from clearledgr.core.stores.bank_details import decrypt_bank_details

        sql = self._prepare_sql(
            "SELECT bank_details_encrypted FROM vendor_profiles "
            "WHERE organization_id = ? AND vendor_name = ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, vendor_name))
                row = cur.fetchone()
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_bank_details failed: %s", exc)
            return None
        if not row:
            return None
        ciphertext = (
            row["bank_details_encrypted"]
            if hasattr(row, "keys")
            else (row[0] if row else None)
        )
        return decrypt_bank_details(ciphertext, decrypt_fn=self._decrypt_secret)

    def get_vendor_bank_details_masked(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, str]]:
        """Return the masked-for-display bank-details dict for a vendor.

        API-safe accessor — output is what every outbound API surface
        should return when surfacing vendor bank details.
        """
        from clearledgr.core.stores.bank_details import mask_bank_details

        plaintext = self.get_vendor_bank_details(organization_id, vendor_name)
        return mask_bank_details(plaintext)

    def set_vendor_bank_details(
        self,
        organization_id: str,
        vendor_name: str,
        bank_details: Optional[Dict[str, Any]],
        *,
        actor_id: Optional[str] = None,
    ) -> bool:
        """Encrypt + persist bank details on a vendor profile.

        Pass ``None`` to clear the column. Also bumps the vendor's
        ``bank_details_changed_at`` timestamp via ``upsert_vendor_profile``
        so the validation gate's bank-details-mismatch check has a
        signal even when reading just the timestamp without decrypting.
        """
        from clearledgr.core.stores.bank_details import (
            encrypt_bank_details,
            normalize_bank_details,
        )

        cleaned = normalize_bank_details(bank_details)
        if cleaned is None:
            ciphertext = None
        else:
            try:
                ciphertext = encrypt_bank_details(
                    cleaned, encrypt_fn=self._encrypt_secret
                )
            except Exception as exc:
                logger.error(
                    "set_vendor_bank_details encryption failed for %s/%s: %s",
                    organization_id, vendor_name, exc,
                )
                return False
        now = _now()
        try:
            self.upsert_vendor_profile(
                organization_id,
                vendor_name,
                bank_details_encrypted=ciphertext,
                bank_details_changed_at=now,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[VendorStore] set_vendor_bank_details upsert failed: %s", exc
            )
            return False

    # ------------------------------------------------------------------ #
    # vendor_invoice_history                                               #
    # ------------------------------------------------------------------ #

    def get_vendor_invoice_history(
        self, organization_id: str, vendor_name: str, limit: int = 6
    ) -> List[Dict[str, Any]]:
        """Return the last N invoice history records for a vendor (newest first)."""
        sql = self._prepare_sql(
            "SELECT * FROM vendor_invoice_history "
            "WHERE organization_id = ? AND vendor_name = ? "
            "ORDER BY created_at DESC LIMIT ?"
        )
        try:
            with self.connect() as conn:
                if self.use_postgres:
                    cur = conn.cursor()
                    cur.execute(sql, (organization_id, vendor_name, limit))
                    return [dict(r) for r in cur.fetchall()]
                else:
                    conn.row_factory = __import__("sqlite3").Row
                    cur = conn.cursor()
                    cur.execute(sql, (organization_id, vendor_name, limit))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_invoice_history failed: %s", exc)
            return []

    def record_vendor_invoice(
        self,
        organization_id: str,
        vendor_name: str,
        ap_item_id: str,
        *,
        invoice_number: Optional[str] = None,
        invoice_date: Optional[str] = None,
        amount: Optional[float] = None,
        currency: str = "USD",
        final_state: Optional[str] = None,
        exception_code: Optional[str] = None,
        was_approved: bool = False,
        approval_override: bool = False,
        agent_recommendation: Optional[str] = None,
        human_decision: Optional[str] = None,
    ) -> None:
        """Insert one invoice outcome into vendor_invoice_history."""
        sql = self._prepare_sql(
            "INSERT INTO vendor_invoice_history "
            "(id, organization_id, vendor_name, ap_item_id, invoice_number, "
            "invoice_date, amount, currency, final_state, exception_code, "
            "was_approved, approval_override, agent_recommendation, human_decision, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        try:
            with self.connect() as conn:
                conn.execute(sql, (
                    str(uuid.uuid4()), organization_id, vendor_name, ap_item_id,
                    invoice_number, invoice_date, amount, currency,
                    final_state, exception_code,
                    1 if was_approved else 0,
                    1 if approval_override else 0,
                    agent_recommendation, human_decision,
                    _now(),
                ))
                conn.commit()
        except Exception as exc:
            logger.warning("[VendorStore] record_vendor_invoice failed: %s", exc)

    def record_vendor_decision_feedback(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        ap_item_id: Optional[str] = None,
        human_decision: str,
        agent_recommendation: Optional[str] = None,
        decision_override: bool = False,
        reason: Optional[str] = None,
        source_channel: Optional[str] = None,
        actor_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        action_outcome: Optional[str] = None,
    ) -> None:
        """Persist one human AP decision outcome for vendor-level learning."""
        sql = self._prepare_sql(
            "INSERT INTO vendor_decision_feedback "
            "(id, organization_id, vendor_name, ap_item_id, human_decision, "
            "agent_recommendation, decision_override, reason, source_channel, actor_id, "
            "correlation_id, action_outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        try:
            with self.connect() as conn:
                conn.execute(
                    sql,
                    (
                        str(uuid.uuid4()),
                        organization_id,
                        vendor_name,
                        ap_item_id,
                        str(human_decision or "").strip().lower(),
                        (str(agent_recommendation).strip().lower() if agent_recommendation else None),
                        1 if decision_override else 0,
                        reason,
                        source_channel,
                        actor_id,
                        correlation_id,
                        action_outcome,
                        _now(),
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.warning("[VendorStore] record_vendor_decision_feedback failed: %s", exc)

    def get_vendor_decision_feedback_summary(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        limit: int = 200,
        window_days: int = 180,
    ) -> Dict[str, Any]:
        """Return aggregate learning signals from recent human decisions.

        This summary is fed into AP decision routing so future recommendations
        adapt per tenant/vendor based on real human outcomes.
        """
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=max(1, int(window_days)))).isoformat()
        sql = self._prepare_sql(
            "SELECT * FROM vendor_decision_feedback "
            "WHERE organization_id = ? AND vendor_name = ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?"
        )
        rows: List[Dict[str, Any]] = []
        try:
            with self.connect() as conn:
                if self.use_postgres:
                    cur = conn.cursor()
                    cur.execute(sql, (organization_id, vendor_name, cutoff, limit))
                    rows = [dict(r) for r in cur.fetchall()]
                else:
                    conn.row_factory = __import__("sqlite3").Row
                    cur = conn.cursor()
                    cur.execute(sql, (organization_id, vendor_name, cutoff, limit))
                    rows = [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_decision_feedback_summary failed: %s", exc)
            rows = []

        if not rows:
            return {
                "window_days": window_days,
                "total_feedback": 0,
                "approve_count": 0,
                "reject_count": 0,
                "request_info_count": 0,
                "override_count": 0,
                "override_rate": 0.0,
                "strictness_bias": "neutral",
                "reject_after_approve_count": 0,
                "request_info_after_approve_count": 0,
                "latest_human_decision": None,
                "latest_action_outcome": None,
                "recent_reasons": [],
            }

        approve_count = 0
        reject_count = 0
        request_info_count = 0
        override_count = 0
        reject_after_approve = 0
        request_info_after_approve = 0
        recent_reasons: List[str] = []

        for row in rows:
            human_decision = str(row.get("human_decision") or "").strip().lower()
            agent_rec = str(row.get("agent_recommendation") or "").strip().lower()
            if human_decision == "approve":
                approve_count += 1
            elif human_decision == "reject":
                reject_count += 1
            elif human_decision == "request_info":
                request_info_count += 1

            if bool(row.get("decision_override")):
                override_count += 1
            if human_decision == "reject" and agent_rec == "approve":
                reject_after_approve += 1
            if human_decision == "request_info" and agent_rec == "approve":
                request_info_after_approve += 1

            reason = str(row.get("reason") or "").strip()
            if reason and reason not in recent_reasons and len(recent_reasons) < 3:
                recent_reasons.append(reason)

        total_feedback = len(rows)
        override_rate = round(override_count / total_feedback, 4) if total_feedback else 0.0
        strictness_ratio = (reject_count + request_info_count) / total_feedback if total_feedback else 0.0
        approve_ratio = approve_count / total_feedback if total_feedback else 0.0
        if strictness_ratio >= 0.45:
            strictness_bias = "strict"
        elif approve_ratio >= 0.75 and override_rate <= 0.25:
            strictness_bias = "permissive"
        else:
            strictness_bias = "neutral"

        latest = rows[0]
        return {
            "window_days": window_days,
            "total_feedback": total_feedback,
            "approve_count": approve_count,
            "reject_count": reject_count,
            "request_info_count": request_info_count,
            "override_count": override_count,
            "override_rate": override_rate,
            "strictness_bias": strictness_bias,
            "reject_after_approve_count": reject_after_approve,
            "request_info_after_approve_count": request_info_after_approve,
            "latest_human_decision": latest.get("human_decision"),
            "latest_action_outcome": latest.get("action_outcome"),
            "recent_reasons": recent_reasons,
        }

    def update_vendor_profile_from_outcome(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        ap_item_id: str,
        final_state: str,
        was_approved: bool,
        approval_override: bool = False,
        agent_recommendation: Optional[str] = None,
        human_decision: Optional[str] = None,
        amount: Optional[float] = None,
        invoice_date: Optional[str] = None,
        exception_code: Optional[str] = None,
    ) -> None:
        """Record an invoice outcome and recompute vendor profile statistics.

        Called after an AP item reaches a terminal state (posted_to_erp, rejected).
        This is how the vendor intelligence layer accumulates knowledge.
        """
        # 1. Write history row
        self.record_vendor_invoice(
            organization_id, vendor_name, ap_item_id,
            invoice_date=invoice_date,
            amount=amount,
            final_state=final_state,
            exception_code=exception_code,
            was_approved=was_approved,
            approval_override=approval_override,
            agent_recommendation=agent_recommendation,
            human_decision=human_decision,
        )

        # 2. Recompute statistics from full history
        history = self.get_vendor_invoice_history(organization_id, vendor_name, limit=200)
        if not history:
            return

        amounts = [h["amount"] for h in history if h.get("amount") is not None]
        approved_count = sum(1 for h in history if h.get("was_approved"))
        override_count = sum(1 for h in history if h.get("approval_override"))
        invoice_count = len(history)

        avg_amount = sum(amounts) / len(amounts) if amounts else None
        stddev = None
        if avg_amount is not None and len(amounts) > 1:
            variance = sum((a - avg_amount) ** 2 for a in amounts) / len(amounts)
            stddev = math.sqrt(variance)

        always_approved = (approved_count == invoice_count and invoice_count >= 3)
        override_rate = round(override_count / invoice_count, 4) if invoice_count else 0.0

        last_date = history[0].get("created_at") if history else None
        last_amount = history[0].get("amount") if history else None

        # Invoice day-of-month pattern
        days = []
        for h in history:
            d = h.get("invoice_date") or h.get("created_at") or ""
            try:
                days.append(datetime.fromisoformat(d[:10]).day)
            except Exception:
                pass
        typical_day = None
        if days:
            # mode: most common day
            from collections import Counter
            typical_day = Counter(days).most_common(1)[0][0]

        self.upsert_vendor_profile(
            organization_id, vendor_name,
            invoice_count=invoice_count,
            last_invoice_date=last_date,
            last_invoice_amount=last_amount,
            avg_invoice_amount=avg_amount,
            amount_stddev=stddev,
            typical_invoice_day=typical_day,
            always_approved=1 if always_approved else 0,
            approval_override_rate=override_rate,
        )
        logger.debug(
            "[VendorStore] Updated profile for %r: count=%d avg=%.2f always_approved=%s",
            vendor_name, invoice_count, avg_amount or 0, always_approved,
        )

    # ------------------------------------------------------------------ #
    # Payment lateness analysis                                           #
    # ------------------------------------------------------------------ #

    def get_vendor_payment_lateness(
        self, organization_id: str, vendor_name: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Check if vendor invoices were paid late (due_date < posted date).

        Returns recent invoice history rows with a computed ``was_late`` flag
        for each row that has both a due_date-like field and a created_at.
        """
        sql = self._prepare_sql(
            "SELECT ap_item_id, invoice_date, amount, final_state, created_at "
            "FROM vendor_invoice_history "
            "WHERE organization_id = ? AND vendor_name = ? AND final_state = 'posted_to_erp' "
            "ORDER BY created_at DESC LIMIT ?"
        )
        try:
            with self.connect() as conn:
                if self.use_postgres:
                    cur = conn.cursor()
                    cur.execute(sql, (organization_id, vendor_name, limit))
                    rows = [dict(r) for r in cur.fetchall()]
                else:
                    conn.row_factory = __import__("sqlite3").Row
                    cur = conn.cursor()
                    cur.execute(sql, (organization_id, vendor_name, limit))
                    rows = [dict(r) for r in cur.fetchall()]

            # Compute was_late: invoice_date (proxy for due) < created_at (proxy for pay)
            for row in rows:
                inv_date = row.get("invoice_date") or ""
                posted_date = row.get("created_at") or ""
                was_late = False
                try:
                    if inv_date and posted_date:
                        inv_dt = datetime.fromisoformat(inv_date[:10])
                        post_dt = datetime.fromisoformat(posted_date[:10])
                        # If posted more than 30 days after invoice date, consider late
                        was_late = (post_dt - inv_dt).days > 30
                except (ValueError, TypeError):
                    pass
                row["was_late"] = was_late

            return rows
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_payment_lateness failed: %s", exc)
            return []
