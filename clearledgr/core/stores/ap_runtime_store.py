"""AP runtime compatibility store mixin.

Contains only DB helpers that are still used by the canonical AP runtime
surfaces. Legacy reconciliation/recurring/draft-entry helpers were removed
from the primary DB inheritance graph.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

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
    metadata TEXT DEFAULT '{}',
    created_at TEXT
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

_CLARIFYING_QUESTIONS_SQL = """
CREATE TABLE IF NOT EXISTS clarifying_questions (
    id TEXT PRIMARY KEY,
    invoice_id TEXT,
    question_type TEXT,
    question_text TEXT,
    options_json TEXT,
    slack_ts TEXT,
    slack_channel TEXT,
    response TEXT,
    status TEXT DEFAULT 'pending',
    organization_id TEXT,
    created_at TEXT,
    responded_at TEXT
)
"""

AP_RUNTIME_COMPAT_TABLES = [
    _TRANSACTIONS_SQL,
    _FINANCE_EMAILS_SQL,
    _GL_CORRECTIONS_SQL,
    _GL_ACCOUNTS_SQL,
    _CLARIFYING_QUESTIONS_SQL,
]


class APRuntimeStore:
    """DB mixin for AP-runtime-compatible helper methods."""

    @staticmethod
    def _decode_json_value(raw: Any, default: Any) -> Any:
        if raw in (None, ""):
            return default
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return default

    def save_transaction(self, tx: Any) -> Any:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql(
                """
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
                """
            )
            cur.execute(
                sql,
                (
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
                ),
            )
            conn.commit()
        return tx

    def get_transactions(
        self,
        organization_id: str,
        status: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> List[Any]:
        from clearledgr.core.models import Transaction, TransactionSource, TransactionStatus

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

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        results: List[Any] = []
        for row in rows:
            r = dict(row)
            try:
                src = TransactionSource(r.get("source", "manual"))
            except ValueError:
                src = TransactionSource.MANUAL
            try:
                state = TransactionStatus(r.get("status", "pending"))
            except ValueError:
                state = TransactionStatus.PENDING
            results.append(
                Transaction(
                    id=r["id"],
                    amount=r.get("amount", 0.0),
                    currency=r.get("currency", "EUR"),
                    date=r.get("date", ""),
                    description=r.get("description", ""),
                    reference=r.get("reference"),
                    source=src,
                    source_id=r.get("source_id"),
                    vendor=r.get("vendor"),
                    status=state,
                    matched_with=json.loads(r.get("matched_with") or "[]"),
                    match_confidence=r.get("match_confidence", 0.0),
                    match_score=r.get("match_score", 0),
                    organization_id=r.get("organization_id"),
                    created_at=r.get("created_at", ""),
                    updated_at=r.get("updated_at", ""),
                    metadata=json.loads(r.get("metadata") or "{}"),
                )
            )
        return results

    def save_finance_email(self, email: Any) -> Any:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql(
                """
                INSERT INTO finance_emails
                    (id, organization_id, gmail_id, subject, sender,
                     received_at, email_type, confidence, vendor, amount,
                     currency, invoice_number, status, processed_at,
                     transaction_id, user_id, metadata, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    organization_id=excluded.organization_id,
                    gmail_id=excluded.gmail_id,
                    subject=excluded.subject,
                    sender=excluded.sender,
                    received_at=excluded.received_at,
                    email_type=excluded.email_type,
                    confidence=excluded.confidence,
                    vendor=excluded.vendor,
                    amount=excluded.amount,
                    currency=excluded.currency,
                    invoice_number=excluded.invoice_number,
                    status=excluded.status,
                    processed_at=excluded.processed_at,
                    transaction_id=excluded.transaction_id,
                    user_id=excluded.user_id,
                    metadata=excluded.metadata
                """
            )
            metadata_json = json.dumps(getattr(email, "metadata", {}) or {})
            cur.execute(
                sql,
                (
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
                    metadata_json,
                    getattr(email, "created_at", now),
                ),
            )
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

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        results: List[Any] = []
        for row in rows:
            r = dict(row)
            results.append(
                FinanceEmail(
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
                    metadata=self._decode_json_value(r.get("metadata"), {}),
                    created_at=r.get("created_at", ""),
                )
            )
        return results

    def list_finance_emails_for_repair(
        self,
        organization_id: str,
        *,
        email_type: Optional[str] = "invoice",
        user_id: Optional[str] = None,
        gmail_ids: Optional[List[str]] = None,
        before_created_at: Optional[str] = None,
        limit: int = 100,
    ) -> List[Any]:
        from clearledgr.core.models import FinanceEmail

        conditions = ["organization_id = ?"]
        params: List[Any] = [organization_id]

        normalized_email_type = str(email_type or "").strip().lower()
        if normalized_email_type:
            conditions.append("LOWER(email_type) = ?")
            params.append(normalized_email_type)

        normalized_user_id = str(user_id or "").strip()
        if normalized_user_id:
            conditions.append("user_id = ?")
            params.append(normalized_user_id)

        normalized_before = str(before_created_at or "").strip()
        if normalized_before:
            conditions.append("created_at < ?")
            params.append(normalized_before)

        normalized_gmail_ids = [
            str(value).strip()
            for value in (gmail_ids or [])
            if str(value or "").strip()
        ]
        if normalized_gmail_ids:
            placeholders = ",".join("?" for _ in normalized_gmail_ids)
            conditions.append(f"gmail_id IN ({placeholders})")
            params.extend(normalized_gmail_ids)

        where = " AND ".join(conditions)
        sql = self._prepare_sql(
            f"SELECT * FROM finance_emails WHERE {where} ORDER BY created_at DESC LIMIT ?"
        )
        params.append(max(1, min(int(limit or 100), 5000)))

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        results: List[Any] = []
        for row in rows:
            r = dict(row)
            results.append(
                FinanceEmail(
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
                    metadata=self._decode_json_value(r.get("metadata"), {}),
                    created_at=r.get("created_at", ""),
                )
            )
        return results

    def get_finance_email_by_gmail_id(self, gmail_id: str) -> Optional[Any]:
        from clearledgr.core.models import FinanceEmail

        sql = self._prepare_sql("SELECT * FROM finance_emails WHERE gmail_id = ? LIMIT 1")
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
            metadata=self._decode_json_value(r.get("metadata"), {}),
            created_at=r.get("created_at", ""),
        )

    def get_invoice_pipeline(self, organization_id: str) -> Dict[str, List[Dict[str, Any]]]:
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
        pipeline: Dict[str, List[Dict[str, Any]]] = {
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

    def get_invoices_by_status(
        self,
        organization_id: str,
        status: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        all_items = self.list_ap_items(organization_id=organization_id, limit=limit)
        if not status:
            return all_items
        state_map = {
            "posted": "posted_to_erp",
            "approved": "approved",
            "pending": "needs_approval",
            "rejected": "rejected",
            "failed": "failed",
        }
        target = state_map.get(status, status)
        return [item for item in all_items if item.get("state") == target]

    def save_gl_correction(self, organization_id: str, correction_dict: Dict[str, Any]) -> Dict[str, Any]:
        import uuid as _uuid
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        correction_id = correction_dict.get("id", str(_uuid.uuid4()))
        correction_dict.setdefault("id", correction_id)
        correction_dict.setdefault("organization_id", organization_id)
        correction_dict.setdefault("corrected_at", now)
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql(
                """
                INSERT INTO gl_corrections
                    (id, invoice_id, vendor, original_gl, corrected_gl, reason,
                     was_correct, corrected_by, organization_id, corrected_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """
            )
            cur.execute(
                sql,
                (
                    correction_id,
                    correction_dict.get("invoice_id", ""),
                    correction_dict.get("vendor", ""),
                    correction_dict.get("original_gl", ""),
                    correction_dict.get("corrected_gl", ""),
                    correction_dict.get("reason", ""),
                    correction_dict.get("was_correct", 0),
                    correction_dict.get("corrected_by", ""),
                    organization_id,
                    now,
                ),
            )
            conn.commit()
        return correction_dict

    def get_gl_corrections(self, organization_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        sql = self._prepare_sql(
            "SELECT id, invoice_id, vendor, original_gl, corrected_gl, reason, "
            "was_correct, confidence_impact, corrected_by, organization_id, corrected_at "
            "FROM gl_corrections WHERE organization_id=? ORDER BY corrected_at DESC LIMIT ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, limit))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_gl_stats(self, organization_id: str) -> Dict[str, Any]:
        from datetime import datetime, timedelta, timezone

        count_sql = self._prepare_sql(
            "SELECT COUNT(*) FROM gl_corrections WHERE organization_id=?"
        )
        vendor_sql = self._prepare_sql(
            "SELECT vendor, COUNT(*) as correction_count "
            "FROM gl_corrections WHERE organization_id=? "
            "GROUP BY vendor ORDER BY correction_count DESC LIMIT 10"
        )
        remap_sql = self._prepare_sql(
            "SELECT original_gl, corrected_gl, COUNT(*) as freq "
            "FROM gl_corrections WHERE organization_id=? "
            "GROUP BY original_gl, corrected_gl ORDER BY freq DESC LIMIT 10"
        )
        now = datetime.now(timezone.utc)
        last_30 = (now - timedelta(days=30)).isoformat()
        prior_30 = (now - timedelta(days=60)).isoformat()
        trend_sql = self._prepare_sql(
            "SELECT "
            "SUM(CASE WHEN corrected_at >= ? THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN corrected_at >= ? AND corrected_at < ? THEN 1 ELSE 0 END) "
            "FROM gl_corrections WHERE organization_id=?"
        )

        with self.connect() as conn:
            cur = conn.cursor()

            cur.execute(count_sql, (organization_id,))
            total = (cur.fetchone() or (0,))[0] or 0

            cur.execute(vendor_sql, (organization_id,))
            by_vendor = [
                {"vendor": row[0], "correction_count": row[1]}
                for row in cur.fetchall()
            ]

            cur.execute(remap_sql, (organization_id,))
            common_remaps = [
                {"original_gl": row[0], "corrected_gl": row[1], "freq": row[2]}
                for row in cur.fetchall()
            ]

            cur.execute(trend_sql, (last_30, prior_30, last_30, organization_id))
            trend_row = cur.fetchone() or (0, 0)

        return {
            "organization_id": organization_id,
            "total_corrections": int(total),
            "by_vendor": by_vendor,
            "common_remaps": common_remaps,
            "trend": {
                "last_30_days": int(trend_row[0] or 0),
                "prior_30_days": int(trend_row[1] or 0),
            },
        }

    def get_gl_accounts(self, organization_id: str) -> List[Dict[str, Any]]:
        sql = self._prepare_sql("SELECT account_data FROM gl_accounts WHERE organization_id=?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        accounts: List[Dict[str, Any]] = []
        for row in rows:
            try:
                raw = row["account_data"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
                accounts.append(json.loads(raw))
            except Exception:
                continue
        return accounts

    # ------------------------------------------------------------------ #
    # Clarifying questions                                                #
    # ------------------------------------------------------------------ #

    def save_clarifying_question(
        self,
        organization_id: str,
        question_id: str,
        invoice_id: str,
        question_type: str,
        question_text: str,
        options: Optional[List[str]] = None,
        slack_ts: Optional[str] = None,
        slack_channel: Optional[str] = None,
    ) -> None:
        """Persist a clarifying question for later retrieval."""
        import uuid as _uuid
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql(
            "INSERT INTO clarifying_questions "
            "(id, invoice_id, question_type, question_text, options_json, "
            "slack_ts, slack_channel, status, organization_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "slack_ts=excluded.slack_ts, slack_channel=excluded.slack_channel"
        )
        try:
            with self.connect() as conn:
                conn.execute(sql, (
                    question_id,
                    invoice_id,
                    question_type,
                    question_text,
                    json.dumps(options or []),
                    slack_ts,
                    slack_channel,
                    "pending",
                    organization_id,
                    now,
                ))
                conn.commit()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "[APRuntimeStore] save_clarifying_question failed: %s", exc
            )

    def get_clarifying_question(self, question_id: str) -> Optional[Dict[str, Any]]:
        """Load a clarifying question by ID."""
        sql = self._prepare_sql(
            "SELECT * FROM clarifying_questions WHERE id = ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (question_id,))
                row = cur.fetchone()
                if row:
                    result = dict(row)
                    if isinstance(result.get("options_json"), str):
                        result["options"] = json.loads(result["options_json"])
                    return result
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "[APRuntimeStore] get_clarifying_question failed: %s", exc
            )
        return None

    def save_gl_account(self, organization_id: str, account_dict: Dict[str, Any]) -> None:
        import uuid as _uuid

        code = account_dict.get("code", "")
        with self.connect() as conn:
            cur = conn.cursor()
            sql = self._prepare_sql(
                """
                INSERT INTO gl_accounts (id, organization_id, code, account_data)
                VALUES (?,?,?,?)
                ON CONFLICT(organization_id, code) DO UPDATE SET account_data=excluded.account_data
                """
            )
            cur.execute(sql, (str(_uuid.uuid4()), organization_id, code, json.dumps(account_dict)))
            conn.commit()
