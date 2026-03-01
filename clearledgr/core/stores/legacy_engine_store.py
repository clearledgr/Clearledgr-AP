"""
Legacy Engine Store mixin.

Provides DB methods required by the legacy ClearledgrEngine reconciliation
module and by the legacy AP workflow services (payment, GL, recurring).

All tables are created lazily in initialize().  Methods intentionally return
model objects (Transaction, Match, etc.) so engine.py can call .to_dict().
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── SQL table definitions ────────────────────────────────────────────────────

_TRANSACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    currency TEXT DEFAULT 'EUR',
    date TEXT,
    description TEXT,
    reference TEXT,
    source TEXT,
    source_id TEXT,
    vendor TEXT,
    status TEXT DEFAULT 'pending',
    matched_with TEXT DEFAULT '[]',
    match_confidence REAL DEFAULT 0,
    match_score INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT
)
"""

_FINANCE_EMAILS_SQL = """
CREATE TABLE IF NOT EXISTS finance_emails (
    id TEXT PRIMARY KEY,
    organization_id TEXT,
    gmail_id TEXT,
    subject TEXT,
    sender TEXT,
    received_at TEXT,
    email_type TEXT,
    confidence REAL DEFAULT 0,
    vendor TEXT,
    amount REAL,
    currency TEXT DEFAULT 'EUR',
    invoice_number TEXT,
    status TEXT DEFAULT 'detected',
    processed_at TEXT,
    transaction_id TEXT,
    user_id TEXT,
    created_at TEXT
)
"""

_EXCEPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS reconciliation_exceptions (
    id TEXT PRIMARY KEY,
    organization_id TEXT,
    transaction_id TEXT,
    transaction_source TEXT,
    exc_type TEXT,
    priority TEXT,
    amount REAL DEFAULT 0,
    currency TEXT DEFAULT 'EUR',
    vendor TEXT,
    near_matches TEXT DEFAULT '[]',
    nearest_amount_diff REAL,
    nearest_date_diff INTEGER,
    ai_explanation TEXT,
    ai_suggested_action TEXT,
    status TEXT DEFAULT 'open',
    resolved_by TEXT,
    resolved_at TEXT,
    resolution_notes TEXT,
    assigned_to TEXT,
    escalated_to TEXT,
    created_at TEXT
)
"""

_MATCHES_SQL = """
CREATE TABLE IF NOT EXISTS reconciliation_matches (
    id TEXT PRIMARY KEY,
    organization_id TEXT,
    gateway_id TEXT,
    bank_id TEXT,
    internal_id TEXT,
    score INTEGER DEFAULT 0,
    confidence REAL DEFAULT 0,
    match_type TEXT DEFAULT 'auto',
    amount_score INTEGER DEFAULT 0,
    date_score INTEGER DEFAULT 0,
    description_score INTEGER DEFAULT 0,
    reference_score INTEGER DEFAULT 0,
    is_three_way INTEGER DEFAULT 0,
    is_approved INTEGER DEFAULT 0,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT
)
"""

_DRAFT_ENTRIES_SQL = """
CREATE TABLE IF NOT EXISTS draft_entries (
    id TEXT PRIMARY KEY,
    organization_id TEXT,
    match_id TEXT,
    debit_account TEXT,
    credit_account TEXT,
    amount REAL DEFAULT 0,
    currency TEXT DEFAULT 'EUR',
    description TEXT,
    posting_date TEXT,
    confidence REAL DEFAULT 0,
    auto_generated INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending',
    approved_by TEXT,
    approved_at TEXT,
    rejection_reason TEXT,
    posted_to_erp INTEGER DEFAULT 0,
    erp_document_id TEXT,
    posted_at TEXT,
    created_at TEXT
)
"""

_VENDOR_BANK_INFO_SQL = """
CREATE TABLE IF NOT EXISTS vendor_bank_info (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    bank_info TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT,
    UNIQUE(organization_id, vendor_id)
)
"""

_GL_CORRECTIONS_SQL = """
CREATE TABLE IF NOT EXISTS gl_corrections (
    id TEXT PRIMARY KEY,
    invoice_id TEXT,
    vendor TEXT,
    original_gl TEXT,
    corrected_gl TEXT,
    reason TEXT,
    was_correct INTEGER DEFAULT 0,
    confidence_impact REAL DEFAULT 0.05,
    corrected_by TEXT,
    organization_id TEXT,
    corrected_at TEXT
)
"""

_GL_ACCOUNTS_SQL = """
CREATE TABLE IF NOT EXISTS gl_accounts (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    code TEXT NOT NULL,
    account_data TEXT NOT NULL DEFAULT '{}',
    UNIQUE(organization_id, code)
)
"""

_RECURRING_RULES_SQL = """
CREATE TABLE IF NOT EXISTS recurring_rules (
    id TEXT PRIMARY KEY,
    vendor_name TEXT,
    vendor_pattern TEXT,
    frequency TEXT DEFAULT 'monthly',
    expected_amount REAL,
    amount_tolerance REAL DEFAULT 0.1,
    gl_code TEXT,
    auto_approve INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    last_matched_at TEXT,
    match_count INTEGER DEFAULT 0,
    organization_id TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

# Public constants for use in database.py initialize()
LEGACY_ENGINE_TABLES = [
    _TRANSACTIONS_SQL,
    _FINANCE_EMAILS_SQL,
    _EXCEPTIONS_SQL,
    _MATCHES_SQL,
    _DRAFT_ENTRIES_SQL,
    _VENDOR_BANK_INFO_SQL,
    _GL_CORRECTIONS_SQL,
    _GL_ACCOUNTS_SQL,
    _RECURRING_RULES_SQL,
]


# ── Mixin class ──────────────────────────────────────────────────────────────

class LegacyEngineStore:
    """DB mixin for legacy engine + AP workflow services."""

    # ── Transactions ──────────────────────────────────────────────────────

    def save_transaction(self, tx: Any) -> Any:
        """Upsert a Transaction dataclass.  Returns the same object."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql("""
                INSERT INTO transactions
                    (id, organization_id, amount, currency, date, description,
                     reference, source, source_id, vendor, status,
                     matched_with, match_confidence, match_score,
                     metadata, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    matched_with=excluded.matched_with,
                    match_confidence=excluded.match_confidence,
                    match_score=excluded.match_score,
                    updated_at=excluded.updated_at
            """)
            cur.execute(sql, (
                tx.id,
                tx.organization_id,
                tx.amount,
                tx.currency,
                tx.date,
                tx.description,
                tx.reference,
                tx.source.value if hasattr(tx.source, "value") else tx.source,
                tx.source_id,
                tx.vendor,
                tx.status.value if hasattr(tx.status, "value") else tx.status,
                json.dumps(tx.matched_with or []),
                tx.match_confidence,
                tx.match_score,
                json.dumps(tx.metadata or {}),
                getattr(tx, "created_at", now),
                now,
            ))
            conn.commit()
        return tx

    def get_transactions(
        self,
        organization_id: str,
        status: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> List[Any]:
        """Return list of Transaction dataclass objects."""
        from clearledgr.core.models import (
            Transaction, TransactionSource, TransactionStatus,
        )

        conditions = ["organization_id = ?"]
        params: List[Any] = [organization_id]
        if status:
            conditions.append("status = ?")
            params.append(status)
        if source:
            conditions.append("source = ?")
            params.append(source)
        where = " AND ".join(conditions)
        sql = self._prepare_sql(
            f"SELECT * FROM transactions WHERE {where} ORDER BY created_at DESC LIMIT ?"
        )
        params.append(limit)

        results = []
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        for row in rows:
            r = dict(row)
            try:
                src = TransactionSource(r.get("source", "manual"))
            except ValueError:
                src = TransactionSource.MANUAL
            try:
                st = TransactionStatus(r.get("status", "pending"))
            except ValueError:
                st = TransactionStatus.PENDING
            tx = Transaction(
                id=r["id"],
                amount=r.get("amount", 0.0),
                currency=r.get("currency", "EUR"),
                date=r.get("date", ""),
                description=r.get("description", ""),
                reference=r.get("reference"),
                source=src,
                source_id=r.get("source_id"),
                vendor=r.get("vendor"),
                status=st,
                matched_with=json.loads(r.get("matched_with") or "[]"),
                match_confidence=r.get("match_confidence", 0.0),
                match_score=r.get("match_score", 0),
                organization_id=r.get("organization_id"),
                created_at=r.get("created_at", ""),
                updated_at=r.get("updated_at", ""),
                metadata=json.loads(r.get("metadata") or "{}"),
            )
            results.append(tx)
        return results

    # ── Finance emails ────────────────────────────────────────────────────

    def save_finance_email(self, email: Any) -> Any:
        """Upsert a FinanceEmail dataclass.  Returns same object."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql("""
                INSERT INTO finance_emails
                    (id, organization_id, gmail_id, subject, sender,
                     received_at, email_type, confidence, vendor, amount,
                     currency, invoice_number, status, processed_at,
                     transaction_id, user_id, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    processed_at=excluded.processed_at,
                    transaction_id=excluded.transaction_id
            """)
            cur.execute(sql, (
                email.id,
                email.organization_id,
                email.gmail_id,
                email.subject,
                email.sender,
                email.received_at,
                email.email_type,
                email.confidence,
                email.vendor,
                email.amount,
                email.currency,
                email.invoice_number,
                email.status,
                email.processed_at,
                email.transaction_id,
                email.user_id,
                getattr(email, "created_at", now),
            ))
            conn.commit()
        return email

    def get_finance_emails(
        self,
        organization_id: str,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Any]:
        from clearledgr.core.models import FinanceEmail

        conditions = ["organization_id = ?"]
        params: List[Any] = [organization_id]
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions)
        sql = self._prepare_sql(
            f"SELECT * FROM finance_emails WHERE {where} ORDER BY created_at DESC LIMIT ?"
        )
        params.append(limit)

        results = []
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        for row in rows:
            r = dict(row)
            fe = FinanceEmail(
                id=r["id"],
                gmail_id=r.get("gmail_id", ""),
                subject=r.get("subject", ""),
                sender=r.get("sender", ""),
                received_at=r.get("received_at", ""),
                email_type=r.get("email_type", ""),
                confidence=r.get("confidence", 0.0),
                vendor=r.get("vendor"),
                amount=r.get("amount"),
                currency=r.get("currency", "EUR"),
                invoice_number=r.get("invoice_number"),
                status=r.get("status", "detected"),
                processed_at=r.get("processed_at"),
                transaction_id=r.get("transaction_id"),
                organization_id=r.get("organization_id"),
                user_id=r.get("user_id"),
                created_at=r.get("created_at", ""),
            )
            results.append(fe)
        return results

    def get_finance_email_by_gmail_id(self, gmail_id: str) -> Optional[Any]:
        """Lookup a finance email by its gmail message ID."""
        from clearledgr.core.models import FinanceEmail

        sql = self._prepare_sql(
            "SELECT * FROM finance_emails WHERE gmail_id = ? LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (gmail_id,))
            row = cur.fetchone()
        if not row:
            return None
        r = dict(row)
        return FinanceEmail(
            id=r["id"],
            gmail_id=r.get("gmail_id", ""),
            subject=r.get("subject", ""),
            sender=r.get("sender", ""),
            received_at=r.get("received_at", ""),
            email_type=r.get("email_type", ""),
            confidence=r.get("confidence", 0.0),
            vendor=r.get("vendor"),
            amount=r.get("amount"),
            currency=r.get("currency", "EUR"),
            invoice_number=r.get("invoice_number"),
            status=r.get("status", "detected"),
            processed_at=r.get("processed_at"),
            transaction_id=r.get("transaction_id"),
            organization_id=r.get("organization_id"),
            user_id=r.get("user_id"),
            created_at=r.get("created_at", ""),
        )

    # ── Exceptions ────────────────────────────────────────────────────────

    def save_exception(self, exc: Any) -> Any:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql("""
                INSERT INTO reconciliation_exceptions
                    (id, organization_id, transaction_id, transaction_source,
                     exc_type, priority, amount, currency, vendor,
                     near_matches, nearest_amount_diff, nearest_date_diff,
                     ai_explanation, ai_suggested_action, status,
                     resolved_by, resolved_at, resolution_notes,
                     assigned_to, escalated_to, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    resolved_by=excluded.resolved_by,
                    resolved_at=excluded.resolved_at,
                    resolution_notes=excluded.resolution_notes
            """)
            cur.execute(sql, (
                exc.id,
                exc.organization_id,
                exc.transaction_id,
                exc.transaction_source.value if hasattr(exc.transaction_source, "value") else exc.transaction_source,
                exc.type.value if hasattr(exc.type, "value") else exc.type,
                exc.priority.value if hasattr(exc.priority, "value") else exc.priority,
                exc.amount,
                exc.currency,
                exc.vendor,
                json.dumps(exc.near_matches or []),
                exc.nearest_amount_diff,
                exc.nearest_date_diff,
                exc.ai_explanation,
                exc.ai_suggested_action,
                exc.status,
                exc.resolved_by,
                exc.resolved_at,
                exc.resolution_notes,
                exc.assigned_to,
                exc.escalated_to,
                getattr(exc, "created_at", now),
            ))
            conn.commit()
        return exc

    def get_exceptions(
        self,
        organization_id: str,
        status: str = "open",
        limit: int = 100,
    ) -> List[Any]:
        from clearledgr.core.models import (
            Exception as ExceptionModel, ExceptionType, ExceptionPriority, TransactionSource,
        )

        conditions = ["organization_id = ?"]
        params: List[Any] = [organization_id]
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions)
        sql = self._prepare_sql(
            f"SELECT * FROM reconciliation_exceptions WHERE {where} ORDER BY created_at DESC LIMIT ?"
        )
        params.append(limit)

        results = []
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        for row in rows:
            r = dict(row)
            try:
                exc_type = ExceptionType(r.get("exc_type", "no_match"))
            except ValueError:
                exc_type = ExceptionType.NO_MATCH
            try:
                priority = ExceptionPriority(r.get("priority", "medium"))
            except ValueError:
                priority = ExceptionPriority.MEDIUM
            try:
                src = TransactionSource(r.get("transaction_source", "manual"))
            except ValueError:
                src = TransactionSource.MANUAL

            exc = ExceptionModel(
                id=r["id"],
                transaction_id=r.get("transaction_id", ""),
                transaction_source=src,
                type=exc_type,
                priority=priority,
                amount=r.get("amount", 0.0),
                currency=r.get("currency", "EUR"),
                vendor=r.get("vendor"),
                near_matches=json.loads(r.get("near_matches") or "[]"),
                nearest_amount_diff=r.get("nearest_amount_diff"),
                nearest_date_diff=r.get("nearest_date_diff"),
                ai_explanation=r.get("ai_explanation"),
                ai_suggested_action=r.get("ai_suggested_action"),
                status=r.get("status", "open"),
                resolved_by=r.get("resolved_by"),
                resolved_at=r.get("resolved_at"),
                resolution_notes=r.get("resolution_notes"),
                assigned_to=r.get("assigned_to"),
                escalated_to=r.get("escalated_to"),
                organization_id=r.get("organization_id"),
                created_at=r.get("created_at", ""),
            )
            results.append(exc)
        return results

    # ── Matches ───────────────────────────────────────────────────────────

    def save_match(self, match: Any) -> Any:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql("""
                INSERT INTO reconciliation_matches
                    (id, organization_id, gateway_id, bank_id, internal_id,
                     score, confidence, match_type,
                     amount_score, date_score, description_score, reference_score,
                     is_three_way, is_approved, approved_by, approved_at, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    is_approved=excluded.is_approved,
                    approved_by=excluded.approved_by,
                    approved_at=excluded.approved_at
            """)
            cur.execute(sql, (
                match.id,
                match.organization_id,
                match.gateway_id,
                match.bank_id,
                match.internal_id,
                match.score,
                match.confidence,
                match.match_type,
                match.amount_score,
                match.date_score,
                match.description_score,
                match.reference_score,
                int(match.is_three_way),
                int(match.is_approved),
                match.approved_by,
                match.approved_at,
                getattr(match, "created_at", now),
            ))
            conn.commit()
        return match

    def get_matches(self, organization_id: str, limit: int = 100) -> List[Any]:
        from clearledgr.core.models import Match

        sql = self._prepare_sql(
            "SELECT * FROM reconciliation_matches WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
        )
        results = []
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, limit))
            rows = cur.fetchall()

        for row in rows:
            r = dict(row)
            m = Match(
                id=r["id"],
                gateway_id=r.get("gateway_id"),
                bank_id=r.get("bank_id"),
                internal_id=r.get("internal_id"),
                score=r.get("score", 0),
                confidence=r.get("confidence", 0.0),
                match_type=r.get("match_type", "auto"),
                amount_score=r.get("amount_score", 0),
                date_score=r.get("date_score", 0),
                description_score=r.get("description_score", 0),
                reference_score=r.get("reference_score", 0),
                is_three_way=bool(r.get("is_three_way", 0)),
                is_approved=bool(r.get("is_approved", 0)),
                approved_by=r.get("approved_by"),
                approved_at=r.get("approved_at"),
                organization_id=r.get("organization_id"),
                created_at=r.get("created_at", ""),
            )
            results.append(m)
        return results

    # ── Draft entries ──────────────────────────────────────────────────────

    def save_draft_entry(self, draft: Any) -> Any:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql("""
                INSERT INTO draft_entries
                    (id, organization_id, match_id, debit_account, credit_account,
                     amount, currency, description, posting_date, confidence,
                     auto_generated, status, approved_by, approved_at,
                     rejection_reason, posted_to_erp, erp_document_id,
                     posted_at, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    approved_by=excluded.approved_by,
                    approved_at=excluded.approved_at,
                    rejection_reason=excluded.rejection_reason,
                    posted_to_erp=excluded.posted_to_erp,
                    erp_document_id=excluded.erp_document_id,
                    posted_at=excluded.posted_at
            """)
            status_val = draft.status.value if hasattr(draft.status, "value") else draft.status
            cur.execute(sql, (
                draft.id,
                draft.organization_id,
                draft.match_id,
                draft.debit_account,
                draft.credit_account,
                draft.amount,
                draft.currency,
                draft.description,
                draft.posting_date,
                draft.confidence,
                int(draft.auto_generated),
                status_val,
                draft.approved_by,
                draft.approved_at,
                draft.rejection_reason,
                int(draft.posted_to_erp),
                draft.erp_document_id,
                draft.posted_at,
                getattr(draft, "created_at", now),
            ))
            conn.commit()
        return draft

    def get_draft_entries(
        self,
        organization_id: str,
        status: str = "pending",
        limit: int = 100,
    ) -> List[Any]:
        from clearledgr.core.models import DraftEntry, ApprovalStatus

        conditions = ["organization_id = ?"]
        params: List[Any] = [organization_id]
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions)
        sql = self._prepare_sql(
            f"SELECT * FROM draft_entries WHERE {where} ORDER BY created_at DESC LIMIT ?"
        )
        params.append(limit)

        results = []
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        for row in rows:
            r = dict(row)
            try:
                s = ApprovalStatus(r.get("status", "pending"))
            except ValueError:
                s = ApprovalStatus.PENDING
            d = DraftEntry(
                id=r["id"],
                match_id=r.get("match_id", ""),
                debit_account=r.get("debit_account", ""),
                credit_account=r.get("credit_account", ""),
                amount=r.get("amount", 0.0),
                currency=r.get("currency", "EUR"),
                description=r.get("description", ""),
                posting_date=r.get("posting_date", ""),
                confidence=r.get("confidence", 0.0),
                auto_generated=bool(r.get("auto_generated", True)),
                status=s,
                approved_by=r.get("approved_by"),
                approved_at=r.get("approved_at"),
                rejection_reason=r.get("rejection_reason"),
                posted_to_erp=bool(r.get("posted_to_erp", False)),
                erp_document_id=r.get("erp_document_id"),
                posted_at=r.get("posted_at"),
                organization_id=r.get("organization_id"),
                created_at=r.get("created_at", ""),
            )
            results.append(d)
        return results

    # ── Stats ─────────────────────────────────────────────────────────────

    def get_stats(self, organization_id: str) -> Dict[str, Any]:
        """Return aggregated stats used by get_dashboard_data()."""
        with self.connect() as conn:
            cur = conn.cursor()

            # Transaction counts
            cur.execute(
                self._prepare_sql("SELECT status, COUNT(*) as n FROM transactions WHERE organization_id=? GROUP BY status"),
                (organization_id,),
            )
            tx_by_status: Dict[str, int] = {row[0]: row[1] for row in cur.fetchall()}

            total_tx = sum(tx_by_status.values())
            matched = tx_by_status.get("matched", 0)
            match_rate = (matched / total_tx * 100) if total_tx else 0.0

            # Open exceptions
            cur.execute(
                self._prepare_sql("SELECT COUNT(*) FROM reconciliation_exceptions WHERE organization_id=? AND status='open'"),
                (organization_id,),
            )
            open_exceptions = (cur.fetchone() or [0])[0]

            # Pending drafts
            cur.execute(
                self._prepare_sql("SELECT COUNT(*) FROM draft_entries WHERE organization_id=? AND status='pending'"),
                (organization_id,),
            )
            pending_drafts = (cur.fetchone() or [0])[0]

        return {
            "transactions": tx_by_status,
            "total_transactions": total_tx,
            "open_exceptions": open_exceptions,
            "pending_approvals": pending_drafts,
            "match_rate": round(match_rate, 1),
        }

    # ── Invoice pipeline ───────────────────────────────────────────────────

    def get_invoice_pipeline(self, organization_id: str) -> Dict[str, List[Dict]]:
        """
        Return AP items grouped by logical pipeline stage.
        Used by the analytics dashboard.
        """
        try:
            items = self.list_ap_items(organization_id=organization_id)
        except Exception:
            items = []

        state_map: Dict[str, str] = {
            "pending_review": "pending_approval",
            "needs_approval": "pending_approval",
            "approved": "approved",
            "auto_approved": "approved",
            "rejected": "rejected",
            "posted_to_erp": "posted",
            "failed_post": "failed",
            "needs_info": "pending_approval",
        }

        pipeline: Dict[str, List[Dict]] = {
            "pending_approval": [],
            "approved": [],
            "posted": [],
            "rejected": [],
            "failed": [],
        }

        for item in items:
            state = item.get("state", "")
            bucket = state_map.get(state, "pending_approval")
            pipeline.setdefault(bucket, []).append(item)

        return pipeline

    # ── Bank / GL / recurring (no-op store backs) ───────────────────────

    def save_vendor_bank_info(
        self, organization_id: str, vendor_id: str, bank_info: Dict[str, Any]
    ) -> None:
        import uuid as _uuid
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql("""
                INSERT INTO vendor_bank_info (id, organization_id, vendor_id, bank_info, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(organization_id, vendor_id) DO UPDATE SET bank_info=excluded.bank_info, updated_at=excluded.updated_at
            """)
            cur.execute(sql, (str(_uuid.uuid4()), organization_id, vendor_id, json.dumps(bank_info), now))
            conn.commit()

    def save_gl_correction(self, organization_id: str, correction_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Save a GL correction using the actual DB schema columns."""
        import uuid as _uuid
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        correction_id = correction_dict.get("id", str(_uuid.uuid4()))
        correction_dict.setdefault("id", correction_id)
        correction_dict.setdefault("organization_id", organization_id)
        correction_dict.setdefault("corrected_at", now)
        with self.connect() as conn:
            cur = conn.cursor()
            # Use actual schema: id, invoice_id, vendor, original_gl, corrected_gl,
            # reason, was_correct, confidence_impact, corrected_by, organization_id, corrected_at
            sql = self._prepare_sql("""
                INSERT INTO gl_corrections
                    (id, invoice_id, vendor, original_gl, corrected_gl, reason,
                     was_correct, confidence_impact, corrected_by, organization_id, corrected_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """)
            cur.execute(sql, (
                correction_id,
                correction_dict.get("invoice_id", ""),
                correction_dict.get("vendor", ""),
                correction_dict.get("original_gl", ""),
                correction_dict.get("corrected_gl", ""),
                correction_dict.get("reason", ""),
                correction_dict.get("was_correct", 0),
                correction_dict.get("confidence_impact", 0.0),
                correction_dict.get("corrected_by", ""),
                organization_id,
                now,
            ))
            conn.commit()
        return correction_dict

    def get_gl_corrections(self, organization_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        sql = self._prepare_sql(
            "SELECT id, invoice_id, vendor, original_gl, corrected_gl, reason, "
            "was_correct, confidence_impact, corrected_by, organization_id, corrected_at "
            "FROM gl_corrections WHERE organization_id=? ORDER BY corrected_at DESC LIMIT ?"
        )
        results = []
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, limit))
            for row in cur.fetchall():
                try:
                    results.append(dict(row))
                except Exception:
                    pass
        return results

    def get_gl_stats(self, organization_id: str) -> Dict[str, Any]:
        corrections = self.get_gl_corrections(organization_id, limit=10000)
        return {
            "organization_id": organization_id,
            "total_corrections": len(corrections),
        }

    def get_gl_accounts(self, organization_id: str) -> List[Dict[str, Any]]:
        sql = self._prepare_sql(
            "SELECT account_data FROM gl_accounts WHERE organization_id=?"
        )
        results = []
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            for row in cur.fetchall():
                try:
                    results.append(json.loads(row[0]))
                except Exception:
                    pass
        return results

    def save_gl_account(self, organization_id: str, account_dict: Dict[str, Any]) -> None:
        import uuid as _uuid

        code = account_dict.get("code", "")
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql("""
                INSERT INTO gl_accounts (id, organization_id, code, account_data)
                VALUES (?,?,?,?)
                ON CONFLICT(organization_id, code) DO UPDATE SET account_data=excluded.account_data
            """)
            cur.execute(sql, (str(_uuid.uuid4()), organization_id, code, json.dumps(account_dict)))
            conn.commit()

    def get_recurring_rules(self, organization_id: str) -> List[Dict[str, Any]]:
        """Return recurring rules using actual schema, normalized to RecurringRule dict format."""
        sql = self._prepare_sql(
            "SELECT id, vendor_name, vendor_pattern, frequency, expected_amount, "
            "amount_tolerance, gl_code, auto_approve, status, last_matched_at, "
            "match_count, organization_id, created_at, updated_at "
            "FROM recurring_rules WHERE organization_id=?"
        )
        results = []
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            for row in cur.fetchall():
                try:
                    r = dict(row)
                    tolerance = r.get("amount_tolerance", 0.05)
                    # Normalize: tolerance stored as fraction (0.05) → pct (5.0)
                    tolerance_pct = tolerance * 100 if tolerance <= 1.0 else tolerance
                    auto_app = r.get("auto_approve", 0)
                    action = "auto_approve" if auto_app else "flag"
                    results.append({
                        "rule_id": r.get("id", ""),
                        "vendor": r.get("vendor_name", ""),
                        "vendor_pattern": r.get("vendor_pattern", ""),
                        "vendor_aliases": [],
                        "expected_frequency": r.get("frequency", "monthly"),
                        "expected_amount": r.get("expected_amount"),
                        "amount_tolerance_pct": tolerance_pct,
                        "action": action,
                        "default_gl_code": r.get("gl_code", ""),
                        "notes": None,
                        "organization_id": r.get("organization_id", organization_id),
                        "created_at": r.get("created_at", ""),
                        "updated_at": r.get("updated_at", ""),
                        "require_amount_match": False,
                        "notify_on_auto_approve": True,
                        "enabled": r.get("status", "active") == "active",
                        "last_invoice_date": r.get("last_matched_at"),
                        "next_expected_date": None,
                        "total_invoices": r.get("match_count", 0),
                        "total_amount": 0.0,
                    })
                except Exception:
                    pass
        return results

    def save_recurring_rule(self, organization_id: str, rule_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Save a recurring rule using the actual schema columns."""
        import uuid as _uuid
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        rule_id = rule_dict.get("rule_id") or str(_uuid.uuid4())
        vendor_name = rule_dict.get("vendor") or rule_dict.get("vendor_name", "")
        frequency = rule_dict.get("expected_frequency") or rule_dict.get("frequency", "monthly")
        expected_amount = rule_dict.get("expected_amount", 0.0)
        tolerance_pct = float(rule_dict.get("amount_tolerance_pct", 5.0))
        # Stored as fraction in DB
        amount_tolerance = tolerance_pct / 100.0 if tolerance_pct > 1.0 else tolerance_pct
        gl_code = rule_dict.get("default_gl_code") or rule_dict.get("gl_code", "")
        action = rule_dict.get("action", "flag")
        auto_approve = 1 if (action == "auto_approve" or rule_dict.get("auto_approve", False)) else 0
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql("""
                INSERT INTO recurring_rules
                    (id, organization_id, vendor_name, frequency, expected_amount,
                     amount_tolerance, gl_code, auto_approve, status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    vendor_name=excluded.vendor_name,
                    frequency=excluded.frequency,
                    expected_amount=excluded.expected_amount,
                    amount_tolerance=excluded.amount_tolerance,
                    gl_code=excluded.gl_code,
                    auto_approve=excluded.auto_approve,
                    updated_at=excluded.updated_at
            """)
            cur.execute(sql, (
                rule_id, organization_id, vendor_name, frequency,
                expected_amount, amount_tolerance, gl_code, auto_approve,
                "active", now, now,
            ))
            conn.commit()
        # Return normalized dict
        return {
            "rule_id": rule_id,
            "vendor": vendor_name,
            "organization_id": organization_id,
            "expected_frequency": frequency,
            "expected_amount": expected_amount,
            "amount_tolerance_pct": tolerance_pct,
            "action": action,
            "default_gl_code": gl_code,
            "enabled": True,
        }

    def delete_recurring_rule(self, organization_id: str, rule_id: str) -> None:
        sql = self._prepare_sql(
            "DELETE FROM recurring_rules WHERE organization_id=? AND id=?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, rule_id))
            conn.commit()

    def get_recurring_summary(self, organization_id: str) -> Dict[str, Any]:
        """Return a simple summary of recurring rules for an org."""
        rules = self.get_recurring_rules(organization_id)
        return {
            "organization_id": organization_id,
            "total_rules": len(rules),
            "rules": rules,
        }

    # =========================================================================
    # get_invoices_by_status — used by analytics + services
    # =========================================================================

    def get_invoices_by_status(
        self,
        organization_id: str,
        status: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Return AP items, optionally filtered by state/status."""
        all_items = self.list_ap_items(organization_id=organization_id, limit=limit)
        if status:
            # normalise: "posted" maps to "posted_to_erp", etc.
            _state_map = {
                "posted": "posted_to_erp",
                "approved": "approved",
                "pending": "needs_approval",
                "rejected": "rejected",
                "failed": "failed",
            }
            target = _state_map.get(status, status)
            return [item for item in all_items if item.get("state") == target]
        return all_items
